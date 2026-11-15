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
