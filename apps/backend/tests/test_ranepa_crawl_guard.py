"""CAPTCHA protection must preserve group-fair queue and prevent bot spam."""

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from studgroup.models import Base, BotOutbox, RanepaCrawlGuard
from studgroup.ranepa_crawl_guard import (
    guard_is_paused,
    load_guard,
    record_crawl_outcome,
    request_recovery,
    reserve_daily_paid_tasks,
)


def test_four_consecutive_unresolved_captchas_pause_across_sessions(tmp_path):
    async def scenario():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/guard.db")
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            group_id = uuid4()
            user_ids = [uuid4() for _ in range(5)]
            job_ids = [uuid4() for _ in range(5)]
            for index in range(4):
                # Each context simulates a fresh hosted GitHub runner shift.
                async with AsyncSession(engine) as db:
                    paused, streak = await record_crawl_outcome(
                        db,
                        owner_id=9001,
                        job_id=job_ids[index],
                        group_id=group_id,
                        user_id=user_ids[index],
                        attempt=1,
                        failure_code="captcha",
                    )
                    assert (paused, streak) == (index == 3, index + 1)
                    await db.commit()
                async with AsyncSession(engine) as db:
                    state = await load_guard(db)
                    assert state.consecutive_captcha == index + 1
                    await db.commit()
            async with AsyncSession(engine) as db:
                assert await guard_is_paused(db)
                pending = (await db.scalars(select(BotOutbox))).all()
                assert len(pending) == 4
                assert all(row.method == "sendMessage" for row in pending)
                assert all("password" not in row.payload for row in pending)
                assert all("cookie" not in row.payload.lower() for row in pending)
                assert "ПРИОСТАНОВЛЕН" in pending[-1].payload
                # Repeated outcome for a previously reported job is deduplicated.
                await record_crawl_outcome(
                    db,
                    owner_id=9001,
                    job_id=job_ids[3],
                    group_id=group_id,
                    user_id=user_ids[3],
                    attempt=1,
                    failure_code="captcha",
                    manual_pilot=True,
                )
                await db.commit()
            async with AsyncSession(engine) as db:
                assert len((await db.scalars(select(BotOutbox))).all()) == 4
                assert await request_recovery(db)
                await db.commit()
            async with AsyncSession(engine) as db:
                state = await load_guard(db)
                assert state.paused and state.recovery_requested
                await record_crawl_outcome(
                    db,
                    owner_id=9001,
                    job_id=job_ids[4],
                    group_id=group_id,
                    user_id=user_ids[4],
                    attempt=1,
                    failure_code="captcha",
                    manual_pilot=True,
                )
                assert state.paused and not state.recovery_requested
                await db.commit()
            async with AsyncSession(engine) as db:
                assert await request_recovery(db)
                await record_crawl_outcome(
                    db,
                    owner_id=9001,
                    job_id=job_ids[4],
                    group_id=group_id,
                    user_id=user_ids[4],
                    attempt=2,
                    failure_code=None,
                    manual_pilot=True,
                )
                state = await load_guard(db)
                assert not state.paused
                assert not state.recovery_requested
                assert state.consecutive_captcha == 0
                assert not await request_recovery(db)
                await db.commit()
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_non_captcha_outcome_resets_streak_and_manual_pilot_does_not_count(tmp_path):
    async def scenario():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/reset.db")
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            async with AsyncSession(engine) as db:
                kw = {"owner_id": None, "group_id": uuid4(), "user_id": uuid4(), "attempt": 1}
                await record_crawl_outcome(db, **kw, job_id=uuid4(), failure_code="captcha")
                await record_crawl_outcome(db, **kw, job_id=uuid4(), failure_code="captcha")
                state = await load_guard(db)
                assert state.consecutive_captcha == 2
                await record_crawl_outcome(
                    db, **kw, job_id=uuid4(), failure_code="source_unavailable"
                )
                assert state.consecutive_captcha == 0
                await record_crawl_outcome(
                    db, **kw, job_id=uuid4(), failure_code="captcha", manual_pilot=True
                )
                assert state.consecutive_captcha == 0
                assert not state.paused
                assert (await db.scalars(select(BotOutbox))).all() == []
                await db.commit()
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_owner_recovery_cannot_clear_pause_without_successful_single_pilot(tmp_path):
    async def scenario():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/proof.db")
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            async with AsyncSession(engine) as db:
                state = await load_guard(db, lock=True)
                state.paused = True
                state.consecutive_captcha = 4
                state.updated_at = datetime.now(UTC)
                assert await request_recovery(db)
                assert state.paused
                await db.commit()
            async with AsyncSession(engine) as db:
                state = await load_guard(db)
                assert state.paused and state.recovery_requested
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_paid_budget_is_shared_across_separate_runner_shifts(tmp_path):
    async def scenario():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/paid.db")
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            assert await reserve_daily_paid_tasks(engine, count=2, daily_limit=6)
            assert await reserve_daily_paid_tasks(engine, count=2, daily_limit=6)
            assert await reserve_daily_paid_tasks(engine, count=2, daily_limit=6)
            assert not await reserve_daily_paid_tasks(engine, count=2, daily_limit=6)
            assert not await reserve_daily_paid_tasks(engine, count=1, daily_limit=6)
            async with AsyncSession(engine) as db:
                state = await load_guard(db)
                assert state.paid_day == datetime.now(UTC).date()
                assert state.paid_tasks_reserved == 6
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_paid_budget_invalid_reservations_never_charge(tmp_path):
    async def scenario():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/rejected.db")
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            for bad_count in (0, -1, 3, True):
                assert not await reserve_daily_paid_tasks(engine, count=bad_count, daily_limit=6)
            for bad_limit in (0, -1, True):
                assert not await reserve_daily_paid_tasks(engine, count=2, daily_limit=bad_limit)
            async with AsyncSession(engine) as db:
                assert (await db.scalars(select(RanepaCrawlGuard))).all() == []
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_paused_guard_does_not_claim_existing_fair_queue_job(tmp_path):
    from studgroup.main import Settings
    from studgroup.models import Group, Membership, RanepaCrawlJob, User
    from studgroup.ranepa_crawl_jobs import enqueue_week
    from studgroup.ranepa_crawl_plan import CrawlCandidate
    from studgroup.ranepa_worker import process_one

    async def scenario():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/queue.db")
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            group = Group(
                id=uuid4(),
                telegram_chat_id=-32001,
                name="Queued",
                academic_group_code="Q-001",
                timezone="Europe/Moscow",
            )
            user = User(id=uuid4(), telegram_user_id=32001, display_name="Student")
            async with AsyncSession(engine, expire_on_commit=False) as db:
                db.add_all([group, user])
                await db.flush()
                db.add(
                    Membership(
                        group_id=group.id,
                        user_id=user.id,
                        role="student",
                        status="active",
                    )
                )
                await enqueue_week(
                    db,
                    datetime(2026, 11, 9, tzinfo=UTC).date(),
                    [CrawlCandidate(group.id, user.id)],
                )
                guard = await load_guard(db)
                guard.paused = True
                guard.consecutive_captcha = 4
                await db.commit()
            settings = Settings(_env_file=None, ranepa_captcha_guard_enabled=True)
            assert await process_one(engine, settings, datetime.now(UTC).date()) == "paused"
            async with AsyncSession(engine) as db:
                jobs = (await db.scalars(select(RanepaCrawlJob))).all()
                assert len(jobs) == 1
                assert jobs[0].state == "queued"
                assert jobs[0].attempts == 0
                assert jobs[0].priority == 0
            # A manual pilot must not bypass the pause without owner approval.
            assert (
                await process_one(
                    engine,
                    settings,
                    datetime.now(UTC).date(),
                    pilot_group_id=group.id,
                    pilot_user_id=user.id,
                )
                == "paused"
            )
            async with AsyncSession(engine) as db:
                assert await request_recovery(db)
                await db.commit()
            # The one authorized pilot fails its account preflight; the
            # circuit must stay paused and the authorization is consumed.
            assert (
                await process_one(
                    engine,
                    settings,
                    datetime.now(UTC).date(),
                    pilot_group_id=group.id,
                    pilot_user_id=user.id,
                )
                == "failed"
            )
            async with AsyncSession(engine) as db:
                guard = await load_guard(db)
                assert guard.paused and not guard.recovery_requested
                jobs = (await db.scalars(select(RanepaCrawlJob))).all()
                assert jobs[0].attempts == 1
                assert jobs[0].state == "failed"
                await db.commit()
        finally:
            await engine.dispose()

    asyncio.run(scenario())
