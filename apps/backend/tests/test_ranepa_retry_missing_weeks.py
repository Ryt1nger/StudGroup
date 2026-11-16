"""Retries must not touch students with a verified week or a newer running job."""

import asyncio
from datetime import UTC, date, datetime
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from studgroup.models import (
    Base,
    Group,
    Membership,
    RanepaCrawlGuard,
    RanepaCrawlJob,
    RanepaPersonalSnapshot,
    User,
    UserProfile,
)
from studgroup.ranepa_retry_missing_weeks import choose_unverified_failures


def test_select_only_missing_failed_accounts_with_open_guard(tmp_path):
    async def scenario():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/missing_retry.db")
        now = datetime(2026, 11, 16, 6, tzinfo=UTC)
        monday = date(2026, 11, 16)
        old = date(2026, 11, 9)
        group = Group(
            id=uuid4(),
            telegram_chat_id=-900091,
            name="БИ пример",
            academic_group_code="TEST-BI",
            timezone="Europe/Moscow",
            status="active",
            pilot_authorized=True,
        )
        users = [User(id=uuid4(), telegram_user_id=100100 + i, display_name="Test") for i in range(6)]
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with AsyncSession(engine) as db:
                db.add(group)
                db.add(RanepaCrawlGuard(
                    id=1,
                    paused=False,
                    recovery_requested=False,
                    consecutive_captcha=0,
                    updated_at=now,
                ))
                db.add_all(users)
                await db.flush()
                for i, user in enumerate(users):
                    db.add(Membership(
                        group_id=group.id, user_id=user.id, status="active", role="student"
                    ))
                    db.add(UserProfile(
                        user_id=user.id,
                        sdu_credential_id=None if i == 5 else uuid4(),
                        updated_at=now,
                    ))
                    db.add(RanepaCrawlJob(
                        id=uuid4(), group_id=group.id, user_id=user.id,
                        week_start=old, priority=i,
                        state="failed" if i != 4 else "succeeded",
                        attempts=1, created_at=now, finished_at=now,
                        failure_code="parse_failed" if i != 4 else None,
                    ))
                    db.add(RanepaCrawlJob(
                        id=uuid4(), group_id=group.id, user_id=user.id,
                        week_start=monday, priority=i,
                        state="running" if i == 2 else "succeeded" if i == 3 else "queued",
                        attempts=1 if i in (2, 3) else 0,
                        created_at=now,
                        claimed_at=now if i == 2 else None,
                    ))
                db.add(RanepaPersonalSnapshot(
                    group_id=group.id, user_id=users[1].id,
                    week_start=monday, week_end=date(2026, 11, 22),
                    data_digest="confirmed", rows_json="[]",
                    identity_verified=True, checked_at=now,
                ))
                await db.commit()
            result = await choose_unverified_failures(engine, today=monday)
            assert result == [(group.id, users[0].id)]
            with pytest.raises(ValueError, match="unsafe_retry_batch_size"):
                await choose_unverified_failures(engine, today=monday, max_accounts=9)
            async with AsyncSession(engine) as db:
                guard = await db.get(RanepaCrawlGuard, 1)
                guard.paused = True
                await db.commit()
            with pytest.raises(RuntimeError, match="retry_requires_open_captcha_guard"):
                await choose_unverified_failures(engine, today=monday)
        finally:
            await engine.dispose()

    asyncio.run(scenario())
