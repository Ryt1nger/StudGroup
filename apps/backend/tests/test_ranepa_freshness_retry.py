"""P0-3/P1-3/P1-4: SLA, per-source freshness and bounded retry policy."""

import asyncio
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from studgroup.models import Base, Group, Membership, RanepaCrawlJob, User
from studgroup.ranepa_crawl_jobs import (
    MAX_ATTEMPTS,
    REFRESH,
    claim_next,
    enqueue_week,
    finish,
    retry_at,
)
from studgroup.ranepa_crawl_plan import CrawlCandidate


@pytest.mark.parametrize(
    ("failure", "expected"),
    [
        (None, timedelta(hours=6)),
        ("parse_failed", timedelta(minutes=5)),
        ("source_unavailable", timedelta(minutes=30)),
        ("captcha", timedelta(hours=3)),
        ("identity_unverified", timedelta(hours=2)),
        ("internal", timedelta(hours=1)),
    ],
)
def test_backoff_is_reasoned_and_bounded(failure, expected):
    now = datetime(2026, 11, 16, 10, tzinfo=UTC)
    assert retry_at(now, failure_code=failure, attempts=1) == now + expected
    if failure is not None:
        before_terminal = max(1, MAX_ATTEMPTS[failure] - 1)
        assert retry_at(now, failure_code=failure, attempts=before_terminal) <= now + REFRESH
        assert retry_at(now, failure_code=failure, attempts=MAX_ATTEMPTS[failure]) is None
    assert retry_at(now, failure_code="login_failed", attempts=1) is None


def test_retry_across_week_boundaries_and_manual_override(tmp_path):
    async def scenario():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/six-hour.db")
        now = datetime(2026, 11, 16, 10, tzinfo=UTC)
        monday = date(2026, 11, 16)
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            async with AsyncSession(engine, expire_on_commit=False) as db:
                group = Group(
                    id=uuid4(),
                    name="Freshness",
                    telegram_chat_id=-66016,
                    academic_group_code="Fresh",
                    timezone="Europe/Moscow",
                )
                user = User(id=uuid4(), telegram_user_id=66116, display_name="Student")
                db.add_all([group, user])
                await db.flush()
                db.add(
                    Membership(
                        id=uuid4(),
                        group_id=group.id,
                        user_id=user.id,
                        status="active",
                        role="student",
                    )
                )
                await db.flush()
                candidate = CrawlCandidate(group.id, user.id)
                assert await enqueue_week(db, monday, [candidate], now=now) == 1
                claim = await claim_next(db, now=now)
                assert claim is not None
                assert await finish(db, claim.id, group.id, user.id, now=now)
                assert claim.next_attempt_at == now + timedelta(hours=6)
                await enqueue_week(db, monday, [candidate], now=now + timedelta(hours=5))
                assert claim.state == "succeeded"
                await enqueue_week(db, monday, [candidate], now=now + timedelta(hours=6))
                assert claim.state == "queued"
                again = await claim_next(db, now=now + timedelta(hours=6))
                assert again.id == claim.id
                assert await finish(
                    db,
                    claim.id,
                    group.id,
                    user.id,
                    failure_code="parse_failed",
                    now=now + timedelta(hours=6),
                )
                assert claim.retry_reason == "parse_failed"
                assert claim.next_attempt_at == now + timedelta(hours=6, minutes=10)
                await db.commit()
            async with AsyncSession(engine, expire_on_commit=False) as db:
                assert await claim_next(db, now=now + timedelta(hours=6, minutes=9)) is None
                await enqueue_week(
                    db, monday, [candidate], now=now + timedelta(hours=6, minutes=10)
                )
                due = await claim_next(db, now=now + timedelta(hours=6, minutes=10))
                assert due is not None
                assert await finish(
                    db,
                    due.id,
                    group.id,
                    user.id,
                    failure_code="login_failed",
                    now=now + timedelta(hours=6, minutes=10),
                )
                assert due.retry_reason == "credentials_required"
                assert due.next_attempt_at is None
                await enqueue_week(db, monday, [candidate], now=now + timedelta(days=7))
                assert due.state == "failed"
                await enqueue_week(
                    db,
                    monday,
                    [candidate],
                    now=now + timedelta(days=7),
                    force_refresh=True,
                )
                pilot = await claim_next(
                    db,
                    now=now + timedelta(days=7),
                    group_id=group.id,
                    user_id=user.id,
                )
                assert pilot is not None
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_migration_model_has_retry_fields():
    assert hasattr(RanepaCrawlJob, "next_attempt_at")
    assert hasattr(RanepaCrawlJob, "retry_reason")


def test_retry_budget_moves_repeated_failure_to_dead_letter(tmp_path):
    async def scenario():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/dead-letter.db")
        now = datetime(2026, 11, 16, 10, tzinfo=UTC)
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            async with AsyncSession(engine, expire_on_commit=False) as db:
                group = Group(
                    id=uuid4(),
                    name="Dead letter",
                    telegram_chat_id=-66120,
                    academic_group_code="DL",
                    timezone="Europe/Moscow",
                )
                user = User(id=uuid4(), telegram_user_id=66120, display_name="Student")
                db.add_all([group, user])
                await db.flush()
                db.add(
                    Membership(
                        id=uuid4(),
                        group_id=group.id,
                        user_id=user.id,
                        status="active",
                        role="student",
                    )
                )
                await db.flush()
                await enqueue_week(
                    db, date(2026, 11, 16), [CrawlCandidate(group.id, user.id)], now=now
                )
                job = await claim_next(db, now=now)
                assert job is not None
                job.attempts = MAX_ATTEMPTS["captcha"]
                assert await finish(db, job.id, group.id, user.id, failure_code="captcha", now=now)
                assert job.state == "dead_letter"
                assert job.next_attempt_at is None
                assert job.retry_reason == "attempts_exhausted:captcha"

                await enqueue_week(
                    db,
                    date(2026, 11, 16),
                    [CrawlCandidate(group.id, user.id)],
                    now=now + timedelta(days=1),
                )
                assert job.state == "dead_letter"
                assert await claim_next(db, now=now + timedelta(days=1)) is None

                await enqueue_week(
                    db,
                    date(2026, 11, 16),
                    [CrawlCandidate(group.id, user.id)],
                    now=now + timedelta(days=1),
                    force_refresh=True,
                )
                assert job.state == "queued"
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_repeated_expired_lease_is_dead_lettered_without_blocking_next_job(tmp_path):
    async def scenario():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/stale-lease.db")
        now = datetime(2026, 11, 16, 10, tzinfo=UTC)
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            async with AsyncSession(engine, expire_on_commit=False) as db:
                group = Group(
                    id=uuid4(),
                    name="Lease",
                    telegram_chat_id=-66121,
                    academic_group_code="LEASE",
                    timezone="Europe/Moscow",
                )
                users = [
                    User(id=uuid4(), telegram_user_id=66121 + i, display_name=f"U{i}")
                    for i in range(2)
                ]
                db.add_all([group, *users])
                await db.flush()
                for user in users:
                    db.add(
                        Membership(
                            id=uuid4(),
                            group_id=group.id,
                            user_id=user.id,
                            status="active",
                            role="student",
                        )
                    )
                await db.flush()
                await enqueue_week(
                    db,
                    date(2026, 11, 16),
                    [CrawlCandidate(group.id, user.id) for user in users],
                    now=now,
                )
                stale = await claim_next(db, now=now)
                assert stale is not None
                stale.attempts = MAX_ATTEMPTS["internal"]
                stale.claimed_at = now - timedelta(hours=2)
                next_job = await claim_next(db, now=now)
                assert next_job is not None and next_job.id != stale.id
                assert stale.state == "dead_letter"
                assert stale.retry_reason == "lease_expired_exhausted"
                assert stale.next_attempt_at is None
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_later_success_recovers_from_historical_login_failure(tmp_path):
    """Only an unresolved login failure should block scheduled jobs for the account."""

    async def scenario():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/login-recovery.db")
        now = datetime(2026, 11, 16, 10, tzinfo=UTC)
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            async with AsyncSession(engine, expire_on_commit=False) as db:
                group = Group(
                    id=uuid4(),
                    name="Login recovery",
                    telegram_chat_id=-66017,
                    academic_group_code="Recovery",
                    timezone="Europe/Moscow",
                )
                user = User(id=uuid4(), telegram_user_id=66117, display_name="Student")
                db.add_all([group, user])
                await db.flush()
                db.add(
                    Membership(
                        id=uuid4(),
                        group_id=group.id,
                        user_id=user.id,
                        status="active",
                        role="student",
                    )
                )
                await db.flush()
                candidate = CrawlCandidate(group.id, user.id)

                old_week = date(2026, 11, 9)
                await enqueue_week(db, old_week, [candidate], now=now - timedelta(days=7))
                old = await claim_next(db, now=now - timedelta(days=7))
                assert old is not None
                assert await finish(
                    db,
                    old.id,
                    group.id,
                    user.id,
                    failure_code="login_failed",
                    now=now - timedelta(days=7),
                )

                # A new scheduled attempt is forbidden while credentials are bad.
                await enqueue_week(db, date(2026, 11, 16), [candidate], now=now)
                assert await claim_next(db, now=now) is None

                # The owner manually verifies corrected credentials this week.
                pilot = await claim_next(db, now=now, group_id=group.id, user_id=user.id)
                assert pilot is not None
                assert await finish(db, pilot.id, group.id, user.id, now=now)
                assert old.state == "failed"

                # Old failure history must not block a later ordinary cycle.
                later = now + timedelta(days=7)
                await enqueue_week(db, date(2026, 11, 23), [candidate], now=later)
                due = await claim_next(db, now=later)
                assert due is not None and due.week_start == date(2026, 11, 23)
                await db.commit()
        finally:
            await engine.dispose()

    asyncio.run(scenario())
