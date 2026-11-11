"""Worker selects encrypted accounts by active group without moving secrets into jobs."""

import asyncio
from datetime import UTC, date, datetime
from uuid import uuid4

from cryptography.fernet import Fernet
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from studgroup.legal import VERSION, document_hash
from studgroup.main import Settings
from studgroup.models import (
    Base,
    Group,
    LegalConsentEvent,
    Membership,
    RanepaCrawlJob,
    User,
    UserProfile,
)
from studgroup.ranepa_worker import (
    collect_bounded,
    create_worker_engine,
    prepare_week,
    process_one,
)


def test_worker_engine_uses_bounded_supabase_pool(monkeypatch):
    captured = {}

    def fake_engine(url, **kwargs):
        captured.update(url=url, **kwargs)
        return object()

    monkeypatch.setattr("studgroup.ranepa_worker.create_async_engine", fake_engine)
    settings = Settings(
        _env_file=None,
        database_url=(
            "postgresql+asyncpg://postgres.project:placeholder@"
            "aws-0-us-west-1.pooler.supabase.com:5432/postgres"
        ),
    )
    create_worker_engine(settings)
    assert captured["pool_size"] == 1
    assert captured["max_overflow"] == 0
    assert captured["pool_timeout"] == 10
    assert captured["hide_parameters"] is True


def test_account_collection_has_a_hard_runtime_limit(monkeypatch):
    async def never_finishes(*_args):
        await asyncio.Event().wait()

    monkeypatch.setattr("studgroup.ranepa_worker._collect", never_finishes)
    monkeypatch.setattr("studgroup.ranepa_worker.ACCOUNT_TIMEOUT_SECONDS", 0.001)
    result = asyncio.run(
        collect_bounded(None, uuid4(), uuid4(), "A-001", "login", "password", date(2026, 11, 11))
    )
    assert result == "source_unavailable"


def test_worker_claims_only_connected_members_and_keeps_credentials_out_of_queue(
    tmp_path, monkeypatch
):
    async def scenario():
        path = tmp_path / "worker.db"
        engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
        key = Fernet.generate_key()
        credential_id = uuid4()
        encrypted = Fernet(key).encrypt(b"password").decode()

        class FakeStore:
            async def get(self, item_id):
                assert item_id == credential_id
                return ("login@ranepa.ru", encrypted, "v1")

        monkeypatch.setattr("studgroup.ranepa_worker.configured_store", lambda _: FakeStore())
        settings = Settings(
            database_url=f"sqlite+aiosqlite:///{path}",
            profile_encryption_key=SecretStr(key.decode()),
            legal_operator_name="Test operator",
            legal_operator_address="Test address",
            legal_operator_contact="test@example.test",
            legal_data_region="Moscow",
            legal_documents_approved=True,
            ranepa_access_approved=True,
        )
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
                        UserProfile(
                            user_id=user.id,
                            sdu_credential_id=credential_id,
                            updated_at=datetime.now(UTC),
                        ),
                        LegalConsentEvent(
                            user_id=user.id,
                            purpose="sdo",
                            action="accept",
                            document_version=VERSION,
                            document_sha256=document_hash("sdo", settings),
                            source="web",
                            created_at=datetime.now(UTC),
                        ),
                    ]
                )
                await db.commit()
            settings.ranepa_access_approved = False
            assert await prepare_week(engine, date(2026, 11, 6), settings) == 0
            settings.ranepa_access_approved = True
            assert await prepare_week(engine, date(2026, 11, 6), settings) == 1
            assert await prepare_week(engine, date(2026, 11, 6), settings) == 0
            async with AsyncSession(engine) as db:
                jobs = (await db.scalars(select(RanepaCrawlJob))).all()
                assert len(jobs) == 1 and jobs[0].user_id == user.id
                assert "password" not in str(jobs[0].__dict__)
            seen = []

            async def fake_collect(engine_, group_id, user_id, code, login, password, today):
                seen.append((group_id, user_id, code, login, password))

            monkeypatch.setattr("studgroup.ranepa_worker._collect", fake_collect)
            assert await process_one(engine, settings, date(2026, 11, 6)) == "succeeded"
            assert seen == [(group.id, user.id, "A-001", "login", "password")]
            # Revoking consent excludes this account from the next weekly round.
            async with AsyncSession(engine) as db:
                db.add(
                    LegalConsentEvent(
                        user_id=user.id,
                        purpose="sdo",
                        action="revoke",
                        document_version=VERSION,
                        document_sha256=document_hash("sdo", settings),
                        source="web",
                        created_at=datetime.now(UTC),
                    )
                )
                await db.commit()
            assert await prepare_week(engine, date(2026, 11, 9), settings) == 0
        finally:
            await engine.dispose()

    asyncio.run(scenario())
