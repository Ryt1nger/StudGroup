"""Recovery pilot must select only a previously failed, active account."""

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
    User,
    UserProfile,
)
from studgroup.ranepa_recovery_pilot import choose_recovery_candidate


def test_owner_recovery_selects_exactly_one_prior_captcha_failure(tmp_path):
    async def scenario():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/recovery.db")
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            now = datetime(2026, 11, 15, 16, tzinfo=UTC)
            active_group = Group(
                id=uuid4(),
                telegram_chat_id=-900011,
                name="Пилот",
                academic_group_code="TEST-001",
                timezone="Europe/Moscow",
                status="active",
            )
            suspended_group = Group(
                id=uuid4(),
                telegram_chat_id=-900012,
                name="Приостановлена",
                academic_group_code="TEST-002",
                timezone="Europe/Moscow",
                status="suspended",
            )
            selected_user = User(id=uuid4(), telegram_user_id=900011, display_name="Тест")
            suspended_user = User(id=uuid4(), telegram_user_id=900012, display_name="Тест 2")
            active_job = RanepaCrawlJob(
                id=uuid4(),
                group_id=active_group.id,
                user_id=selected_user.id,
                week_start=date(2026, 11, 9),
                priority=0,
                state="failed",
                attempts=1,
                failure_code="captcha",
                created_at=now,
                finished_at=now,
            )
            inactive_job = RanepaCrawlJob(
                id=uuid4(),
                group_id=suspended_group.id,
                user_id=suspended_user.id,
                week_start=date(2026, 11, 9),
                priority=0,
                state="failed",
                attempts=1,
                failure_code="captcha",
                created_at=now,
                finished_at=now,
            )
            target = (active_group.id, selected_user.id)
            async with AsyncSession(engine) as db:
                db.add_all(
                    [
                        active_group,
                        suspended_group,
                        selected_user,
                        suspended_user,
                        RanepaCrawlGuard(
                            id=1,
                            paused=True,
                            consecutive_captcha=4,
                            recovery_requested=False,
                            updated_at=now,
                        ),
                    ]
                )
                await db.flush()
                db.add_all(
                    [
                        Membership(
                            group_id=active_group.id,
                            user_id=selected_user.id,
                            status="active",
                            role="student",
                        ),
                        Membership(
                            group_id=suspended_group.id,
                            user_id=suspended_user.id,
                            status="active",
                            role="student",
                        ),
                        UserProfile(
                            user_id=selected_user.id,
                            sdu_credential_id=uuid4(),
                            updated_at=now,
                        ),
                        UserProfile(
                            user_id=suspended_user.id,
                            sdu_credential_id=uuid4(),
                            updated_at=now,
                        ),
                        active_job,
                        inactive_job,
                    ]
                )
                await db.commit()
            with pytest.raises(RuntimeError, match="owner_recovery_pilot_not_authorized"):
                await choose_recovery_candidate(engine)
            async with AsyncSession(engine) as db:
                state = await db.get(RanepaCrawlGuard, 1)
                state.recovery_requested = True
                await db.commit()
            assert await choose_recovery_candidate(engine) == target
        finally:
            await engine.dispose()

    asyncio.run(scenario())
