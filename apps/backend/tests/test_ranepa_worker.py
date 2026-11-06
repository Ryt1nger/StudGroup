"""Worker selects encrypted accounts by active group without moving secrets into jobs."""

import asyncio
from datetime import UTC, date, datetime
from uuid import uuid4

from cryptography.fernet import Fernet
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from studgroup.main import Settings
from studgroup.models import Base, Group, Membership, RanepaAccount, RanepaCrawlJob, User
from studgroup.ranepa_worker import prepare_week, process_one


def test_worker_claims_only_connected_members_and_keeps_credentials_out_of_queue(
    tmp_path, monkeypatch
):
    async def scenario():
        path = tmp_path / "worker.db"
        engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
        key = Fernet.generate_key()
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            async with AsyncSession(engine, expire_on_commit=False) as db:
                group = Group(
                    id=uuid4(),
                    telegram_chat_id=-98001,
                    name="A",
                    academic_group_code="A-001",
                    timezone="Europe/Moscow",
                )
                user = User(id=uuid4(), telegram_user_id=6001, display_name="Student")
                other = User(id=uuid4(), telegram_user_id=6002, display_name="Other")
                db.add_all([group, user, other])
                await db.flush()
                db.add_all(
                    [
                        Membership(
                            group_id=group.id, user_id=user.id, role="student", status="active"
                        ),
                        Membership(
                            group_id=group.id, user_id=other.id, role="student", status="active"
                        ),
                        RanepaAccount(
                            user_id=user.id,
                            login_encrypted=Fernet(key).encrypt(b"login").decode(),
                            password_encrypted=Fernet(key).encrypt(b"password").decode(),
                            updated_at=datetime.now(UTC),
                        ),
                    ]
                )
                await db.commit()
            assert await prepare_week(engine, date(2026, 11, 6)) == 1
            assert await prepare_week(engine, date(2026, 11, 6)) == 0
            async with AsyncSession(engine) as db:
                jobs = (await db.scalars(select(RanepaCrawlJob))).all()
                assert len(jobs) == 1 and jobs[0].user_id == user.id
                assert "password" not in str(jobs[0].__dict__)
            seen = []

            async def fake_collect(engine_, group_id, user_id, code, login, password, today):
                seen.append((group_id, user_id, code, login, password))

            monkeypatch.setattr("studgroup.ranepa_worker._collect", fake_collect)
            settings = Settings(
                database_url=f"sqlite+aiosqlite:///{path}",
                profile_encryption_key=SecretStr(key.decode()),
            )
            assert await process_one(engine, settings, date(2026, 11, 6)) == "succeeded"
            assert seen == [(group.id, user.id, "A-001", "login", "password")]
        finally:
            await engine.dispose()

    asyncio.run(scenario())
