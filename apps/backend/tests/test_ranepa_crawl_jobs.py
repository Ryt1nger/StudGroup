"""A restart preserves fair, group-scoped crawl progress."""

import asyncio
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from studgroup.models import Base, Group, Membership, RanepaCrawlJob, User
from studgroup.ranepa_crawl_jobs import claim_next, enqueue_week, finish
from studgroup.ranepa_crawl_plan import CrawlCandidate


def test_fair_durable_crawl_and_group_guard(tmp_path):
    async def scenario():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/crawl.db")
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            async with AsyncSession(engine, expire_on_commit=False) as db:
                groups = [
                    Group(
                        id=uuid4(),
                        telegram_chat_id=-9000 - i,
                        name=f"G{i}",
                        academic_group_code=f"G{i}",
                        timezone="Europe/Moscow",
                    )
                    for i in range(2)
                ]
                users = [
                    User(id=uuid4(), telegram_user_id=500 + i, display_name=f"U{i}")
                    for i in range(4)
                ]
                db.add_all(groups + users)
                await db.flush()
                candidates = []
                for i, user in enumerate(users):
                    group = groups[i // 2]
                    db.add(
                        Membership(
                            user_id=user.id, group_id=group.id, role="student", status="active"
                        )
                    )
                    candidates.append(CrawlCandidate(group.id, user.id, frozenset({uuid4()})))
                await db.flush()
                monday = date(2026, 11, 2)
                assert await enqueue_week(db, monday, candidates) == 4
                assert await enqueue_week(db, monday, candidates) == 0
                await db.commit()
            async with AsyncSession(engine, expire_on_commit=False) as db:
                claimed = [await claim_next(db) for _ in range(4)]
                assert [row.group_id for row in claimed] == sorted(
                    [group.id for group in groups]
                ) * 2
                await db.commit()
            async with AsyncSession(engine, expire_on_commit=False) as db:
                first = claimed[0]
                assert not await finish(db, first.id, claimed[1].group_id, first.user_id)
                assert await finish(db, first.id, first.group_id, first.user_id)
                second = claimed[1]
                second.claimed_at = datetime.now(UTC) - timedelta(minutes=70)
                await db.merge(second)
                await db.commit()
            async with AsyncSession(engine, expire_on_commit=False) as db:
                retried = await claim_next(db)
                assert retried.id == claimed[1].id
                assert retried.attempts == 2
                assert await finish(
                    db, retried.id, retried.group_id, retried.user_id, failure_code="captcha"
                )
                assert (await db.scalars(select(RanepaCrawlJob))).all().__len__() == 4
                await db.commit()
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_new_week_precedes_unfinished_old_backlog(tmp_path):
    async def scenario():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/fresh.db")
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            async with AsyncSession(engine, expire_on_commit=False) as db:
                group = Group(
                    id=uuid4(),
                    telegram_chat_id=-9100,
                    name="G",
                    academic_group_code="G",
                    timezone="Europe/Moscow",
                )
                user = User(id=uuid4(), telegram_user_id=9100, display_name="U")
                db.add_all([group, user])
                await db.flush()
                db.add(
                    Membership(user_id=user.id, group_id=group.id, role="student", status="active")
                )
                await db.flush()
                candidate = CrawlCandidate(group.id, user.id)
                await enqueue_week(db, date(2026, 11, 2), [candidate])
                await enqueue_week(db, date(2026, 11, 9), [candidate])
                await db.commit()
            async with AsyncSession(engine) as db:
                assert (await claim_next(db)).week_start == date(2026, 11, 9)
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_daily_round_requeues_only_finished_jobs_without_crossing_groups(tmp_path):
    async def scenario():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/round.db")
        now = datetime(2026, 11, 10, 12, tzinfo=UTC)
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            async with AsyncSession(engine, expire_on_commit=False) as db:
                groups = [
                    Group(
                        id=uuid4(),
                        telegram_chat_id=-9200 - i,
                        name=f"G{i}",
                        academic_group_code=f"G{i}",
                        timezone="Europe/Moscow",
                    )
                    for i in range(2)
                ]
                users = [
                    User(id=uuid4(), telegram_user_id=9200 + i, display_name=f"U{i}")
                    for i in range(2)
                ]
                db.add_all(groups + users)
                await db.flush()
                for group, user in zip(groups, users):
                    db.add(
                        Membership(
                            user_id=user.id, group_id=group.id, role="student", status="active"
                        )
                    )
                await db.flush()
                candidates = [
                    CrawlCandidate(group.id, user.id) for group, user in zip(groups, users)
                ]
                monday = date(2026, 11, 9)
                assert await enqueue_week(db, monday, candidates, now=now) == 2
                first = await claim_next(db, now=now)
                assert await finish(db, first.id, first.group_id, first.user_id, now=now)
                assert (
                    await enqueue_week(db, monday, candidates, now=now + timedelta(hours=23)) == 0
                )
                assert first.state == "succeeded"
                assert await enqueue_week(db, monday, candidates, now=now + timedelta(days=1)) == 0
                assert first.state == "queued"
                # The unfinished first pass remains ahead of a refreshed account.
                other = await claim_next(db, now=now + timedelta(days=1))
                assert other.user_id != first.user_id
                assert other.group_id != first.group_id
                await db.commit()
        finally:
            await engine.dispose()

    asyncio.run(scenario())
