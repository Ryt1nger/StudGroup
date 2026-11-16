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
                    await enqueue_week(db, monday, candidates, now=now + timedelta(hours=5)) == 0
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


def test_manual_pilot_claim_never_touches_another_account(tmp_path):
    async def scenario():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/pilot_scope.db")
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            async with AsyncSession(engine, expire_on_commit=False) as db:
                groups = [
                    Group(
                        id=uuid4(),
                        telegram_chat_id=-9601 - i,
                        name=f"Pilot{i}",
                        academic_group_code=f"P{i}",
                        timezone="Europe/Moscow",
                    )
                    for i in range(2)
                ]
                users = [
                    User(id=uuid4(), telegram_user_id=9601 + i, display_name=f"U{i}")
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
                await enqueue_week(
                    db,
                    date(2026, 11, 9),
                    [CrawlCandidate(group.id, user.id) for group, user in zip(groups, users)],
                )
                await db.commit()
            async with AsyncSession(engine, expire_on_commit=False) as db:
                assert await claim_next(db, group_id=groups[0].id, user_id=users[1].id) is None
                selected = await claim_next(db, group_id=groups[1].id, user_id=users[1].id)
                assert selected is not None
                assert (selected.group_id, selected.user_id) == (groups[1].id, users[1].id)
                await db.commit()
            async with AsyncSession(engine) as db:
                all_jobs = (await db.scalars(select(RanepaCrawlJob))).all()
                other = next(job for job in all_jobs if job.user_id == users[0].id)
                assert other.state == "queued" and other.attempts == 0
                targeted = next(job for job in all_jobs if job.user_id == users[1].id)
                assert targeted.state == "running" and targeted.attempts == 1
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_manual_pilot_can_retry_one_finished_job_without_affecting_other_members(tmp_path):
    async def scenario():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/pilot_refresh.db")
        now = datetime(2026, 11, 13, 11, tzinfo=UTC)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with AsyncSession(engine, expire_on_commit=False) as db:
                group = Group(
                    id=uuid4(),
                    telegram_chat_id=-99011,
                    name="Pilot",
                    academic_group_code="P-001",
                    timezone="Europe/Moscow",
                )
                users = [
                    User(id=uuid4(), telegram_user_id=99011 + i, display_name=f"U{i}")
                    for i in range(2)
                ]
                db.add_all([group, *users])
                await db.flush()
                for user in users:
                    db.add(
                        Membership(
                            user_id=user.id,
                            group_id=group.id,
                            role="student",
                            status="active",
                        )
                    )
                await db.flush()
                week = date(2026, 11, 9)
                candidates = [CrawlCandidate(group.id, user.id) for user in users]
                assert await enqueue_week(db, week, candidates, now=now) == 2

                first = await claim_next(db, now=now, group_id=group.id, user_id=users[0].id)
                assert first is not None
                assert await finish(
                    db, first.id, group.id, users[0].id, failure_code="captcha", now=now
                )
                second = await claim_next(db, now=now, group_id=group.id, user_id=users[1].id)
                assert second is not None
                assert await finish(db, second.id, group.id, users[1].id, now=now)

                # Normal queue remains subject to its bounded CAPTCHA backoff.
                soon = now + timedelta(minutes=5)
                assert await enqueue_week(db, week, candidates, now=soon) == 0
                assert first.state == "failed" and first.failure_code == "captcha"
                assert second.state == "succeeded"

                # Manual retry only reopens the explicitly selected account.
                assert (
                    await enqueue_week(
                        db,
                        week,
                        [candidates[0]],
                        now=soon,
                        force_refresh=True,
                    )
                    == 0
                )
                assert first.state == "queued"
                assert first.failure_code is None
                # Retain completion for the normal shared retry clock even on a pilot reset.
                assert first.finished_at == now
                assert first.attempts == 1
                assert second.state == "succeeded"
                assert second.attempts == 1

                refreshed = await claim_next(db, now=soon, group_id=group.id, user_id=users[0].id)
                assert refreshed is not None
                assert refreshed.id == first.id
                assert refreshed.attempts == 2
                assert (
                    await enqueue_week(
                        db,
                        week,
                        [candidates[0]],
                        now=soon,
                        force_refresh=True,
                    )
                    == 0
                )
                # A manual retry must not take over an in-progress account.
                assert refreshed.state == "running"
                assert (
                    await claim_next(db, now=soon, group_id=group.id, user_id=users[1].id) is None
                )
                await db.commit()
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_six_hour_cooldown_skips_account_at_week_boundary(tmp_path):
    async def scenario():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/daily-queue.db")
        sunday = datetime(2026, 11, 8, 23, 55, tzinfo=UTC)
        monday = sunday + timedelta(minutes=6)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with AsyncSession(engine, expire_on_commit=False) as db:
                group = Group(
                    id=uuid4(),
                    telegram_chat_id=-92018,
                    name="Daily queue",
                    academic_group_code="D-001",
                    timezone="Europe/Moscow",
                )
                early = User(id=uuid4(), telegram_user_id=92180, display_name="Early")
                new = User(id=uuid4(), telegram_user_id=92181, display_name="New")
                db.add_all([group, early, new])
                await db.flush()
                for user in (early, new):
                    db.add(
                        Membership(
                            user_id=user.id,
                            group_id=group.id,
                            role="student",
                            status="active",
                        )
                    )
                await db.flush()
                early_candidate = CrawlCandidate(group.id, early.id)
                new_candidate = CrawlCandidate(group.id, new.id)
                assert await enqueue_week(db, date(2026, 11, 2), [early_candidate], now=sunday) == 1
                first = await claim_next(db, now=sunday)
                assert first.user_id == early.id
                assert await finish(db, first.id, group.id, early.id, now=sunday)
                await db.commit()

            # Monday creates a fresh weekly job, but not a new six-hour allowance.
            async with AsyncSession(engine, expire_on_commit=False) as db:
                assert (
                    await enqueue_week(
                        db,
                        date(2026, 11, 9),
                        [early_candidate, new_candidate],
                        now=monday,
                    )
                    == 2
                )
                claimed = await claim_next(db, now=monday)
                assert claimed.user_id == new.id
                assert await finish(db, claimed.id, group.id, new.id, now=monday)
                assert await claim_next(db, now=monday + timedelta(hours=5)) is None
                await db.commit()

            # After a process restart, exactly six hours makes the first account due.
            async with AsyncSession(engine, expire_on_commit=False) as db:
                assert (
                    await claim_next(db, now=sunday + timedelta(hours=6) - timedelta(seconds=1))
                    is None
                )
                due = await claim_next(db, now=sunday + timedelta(hours=6))
                assert due is not None and due.user_id == early.id
                assert await finish(
                    db, due.id, group.id, early.id, now=sunday + timedelta(hours=6)
                )
                assert (
                    await claim_next(db, now=monday + timedelta(hours=6) - timedelta(seconds=1))
                    is None
                )
                # Every scheduled batch refreshes completed jobs before claiming.
                # At exactly six hours the second account is requeued without delay.
                assert (
                    await enqueue_week(
                        db,
                        date(2026, 11, 9),
                        [early_candidate, new_candidate],
                        now=monday + timedelta(hours=6),
                    )
                    == 0
                )
                next_due = await claim_next(db, now=monday + timedelta(hours=6))
                assert next_due is not None and next_due.user_id == new.id
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_daily_limit_applies_across_memberships_and_requeued_jobs(tmp_path):
    async def scenario():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/memberships.db")
        now = datetime(2026, 11, 10, 12, tzinfo=UTC)
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with AsyncSession(engine, expire_on_commit=False) as db:
                groups = [
                    Group(
                        id=uuid4(),
                        telegram_chat_id=-93010 - n,
                        name=f"G{n}",
                        academic_group_code=f"G-{n:03}",
                        timezone="Europe/Moscow",
                    )
                    for n in range(2)
                ]
                user = User(id=uuid4(), telegram_user_id=93010, display_name="Both")
                db.add_all([*groups, user])
                await db.flush()
                for group in groups:
                    db.add(
                        Membership(
                            user_id=user.id,
                            group_id=group.id,
                            role="student",
                            status="active",
                        )
                    )
                await db.flush()
                candidates = [CrawlCandidate(group.id, user.id) for group in groups]
                assert await enqueue_week(db, date(2026, 11, 9), candidates, now=now) == 2
                first = await claim_next(db, now=now)
                # Same account cannot run concurrently in another group.
                assert await claim_next(db, now=now + timedelta(minutes=1)) is None
                assert await finish(
                    db, first.id, first.group_id, first.user_id, now=now, failure_code="captcha"
                )
                assert await claim_next(db, now=now + timedelta(hours=2)) is None
                await db.commit()

            # New session: the other group cannot bypass the completed attempt.
            async with AsyncSession(engine, expire_on_commit=False) as db:
                due = await claim_next(db, now=now + timedelta(days=1))
                assert due is not None and due.user_id == user.id
                assert due.group_id != first.group_id
                assert await finish(
                    db,
                    due.id,
                    due.group_id,
                    user.id,
                    now=now + timedelta(days=1),
                    failure_code="source_unavailable",
                )
                assert (
                    await enqueue_week(
                        db, date(2026, 11, 9), candidates, now=now + timedelta(days=1, minutes=1)
                    )
                    == 0
                )
                assert await claim_next(db, now=now + timedelta(days=1, minutes=1)) is None
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_explicit_full_rescan_preserves_history_and_claims_each_user_once(tmp_path):
    async def scenario():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/full-rescan.db")
        base = datetime(2026, 11, 15, 14, tzinfo=UTC)
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            async with AsyncSession(engine, expire_on_commit=False) as db:
                groups = [
                    Group(
                        id=uuid4(),
                        telegram_chat_id=-99011 - i,
                        name=f"Group{i}",
                        academic_group_code=f"G-{i}",
                        timezone="Europe/Moscow",
                    )
                    for i in range(2)
                ]
                users = [
                    User(id=uuid4(), telegram_user_id=99011 + i, display_name=f"U{i}")
                    for i in range(2)
                ]
                db.add_all([*groups, *users])
                await db.flush()
                # One user belongs to two groups, but must be scanned once.
                candidates = [
                    CrawlCandidate(groups[0].id, users[0].id),
                    CrawlCandidate(groups[1].id, users[0].id),
                    CrawlCandidate(groups[1].id, users[1].id),
                ]
                for candidate in candidates:
                    db.add(
                        Membership(
                            group_id=candidate.group_id,
                            user_id=candidate.user_id,
                            role="student",
                            status="active",
                        )
                    )
                await db.flush()
                assert await enqueue_week(db, date(2026, 11, 9), candidates, now=base) == 3
                # Seed three historic completed jobs through isolated pilot scope;
                # regular queue selection correctly forbids two scans of one user.
                for index, candidate in enumerate(candidates):
                    row = await claim_next(
                        db,
                        now=base,
                        group_id=candidate.group_id,
                        user_id=candidate.user_id,
                    )
                    assert row is not None
                    assert await finish(
                        db,
                        row.id,
                        row.group_id,
                        row.user_id,
                        failure_code="parse_failed" if index == 1 else None,
                        now=base,
                    )
                await db.commit()

            shortly_after = base + timedelta(minutes=10)
            async with AsyncSession(engine, expire_on_commit=False) as db:
                assert (
                    await enqueue_week(
                        db,
                        date(2026, 11, 9),
                        candidates,
                        now=shortly_after,
                        force_refresh=True,
                    )
                    == 0
                )
                rows = (await db.scalars(select(RanepaCrawlJob))).all()
                assert all(row.state == "queued" for row in rows)
                assert all(
                    row.finished_at is not None
                    and (
                        row.finished_at.replace(tzinfo=UTC)
                        if row.finished_at.tzinfo is None
                        else row.finished_at
                    )
                    == base
                    for row in rows
                )
                assert await claim_next(db, now=shortly_after) is None
                allowed = {user.id for user in users}
                done = set()
                for _ in users:
                    row = await claim_next(
                        db,
                        now=shortly_after,
                        rescan_all=True,
                        allowed_user_ids=allowed,
                        processed_user_ids=done,
                    )
                    assert row is not None
                    assert row.user_id not in done
                    done.add(row.user_id)
                    assert await finish(
                        db,
                        row.id,
                        row.group_id,
                        row.user_id,
                        now=shortly_after,
                    )
                assert done == allowed
                assert (
                    await claim_next(
                        db,
                        now=shortly_after,
                        rescan_all=True,
                        allowed_user_ids=allowed,
                        processed_user_ids=done,
                    )
                    is None
                )
                # The ordinary scheduler still respects the new 24h clock.
                assert await claim_next(db, now=shortly_after) is None
                await db.commit()
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_full_rescan_never_overrides_unselected_account(tmp_path):
    async def scenario():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/scope.db")
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            async with AsyncSession(engine, expire_on_commit=False) as db:
                group = Group(
                    id=uuid4(),
                    telegram_chat_id=-99331,
                    name="Two students",
                    academic_group_code="TEST",
                    timezone="Europe/Moscow",
                )
                a = User(id=uuid4(), telegram_user_id=99331, display_name="A")
                b = User(id=uuid4(), telegram_user_id=99332, display_name="B")
                db.add_all([group, a, b])
                await db.flush()
                db.add_all(
                    [
                        Membership(group_id=group.id, user_id=u.id, role="student", status="active")
                        for u in (a, b)
                    ]
                )
                await db.flush()
                await enqueue_week(
                    db, date(2026, 11, 9), [CrawlCandidate(group.id, u.id) for u in (a, b)]
                )
                selected = await claim_next(db, rescan_all=True, allowed_user_ids={a.id})
                assert selected.user_id == a.id
                assert (
                    await claim_next(
                        db,
                        rescan_all=True,
                        allowed_user_ids={a.id},
                        processed_user_ids={a.id},
                    )
                    is None
                )
                assert await claim_next(db, rescan_all=True, allowed_user_ids=set()) is None
                await db.commit()
        finally:
            await engine.dispose()

    asyncio.run(scenario())
