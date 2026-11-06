"""Durable, identity-only queue for a paced university account crawl."""

import uuid
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select

from studgroup.models import Group, Membership, RanepaCrawlJob
from studgroup.ranepa_crawl_plan import CrawlCandidate, fair_crawl_order

LEASE = timedelta(minutes=8)
FAILURES = {
    "login_failed",
    "captcha",
    "source_unavailable",
    "identity_unverified",
    "parse_failed",
    "internal",
}


async def enqueue_week(db, week_start: date, candidates: list[CrawlCandidate], now=None) -> int:
    """Add one ordered job per active group member, preserving earlier progress."""
    if week_start.weekday() != 0:
        raise ValueError("week_start_must_be_monday")
    now = now or datetime.now(UTC)
    ordered = fair_crawl_order(candidates)
    created = 0
    for priority, candidate in enumerate(ordered):
        member = await db.scalar(
            select(Membership).where(
                Membership.group_id == candidate.group_id,
                Membership.user_id == candidate.user_id,
                Membership.status == "active",
            )
        )
        group = await db.get(Group, candidate.group_id)
        if (
            member is None
            or group is None
            or group.status != "active"
            or not group.academic_group_code
        ):
            continue
        existing = await db.scalar(
            select(RanepaCrawlJob).where(
                RanepaCrawlJob.group_id == candidate.group_id,
                RanepaCrawlJob.user_id == candidate.user_id,
                RanepaCrawlJob.week_start == week_start,
            )
        )
        if existing is not None:
            continue
        db.add(
            RanepaCrawlJob(
                id=uuid.uuid4(),
                group_id=candidate.group_id,
                user_id=candidate.user_id,
                week_start=week_start,
                priority=priority,
                state="queued",
                attempts=0,
                created_at=now,
            )
        )
        created += 1
    await db.flush()
    return created


async def claim_next(db, now=None):
    """Claim one job under a short DB lock; caller commits before opening a browser."""
    now = now or datetime.now(UTC)
    stale = await db.scalar(
        select(RanepaCrawlJob)
        .where(
            RanepaCrawlJob.state == "running",
            RanepaCrawlJob.claimed_at < now - LEASE,
        )
        .order_by(RanepaCrawlJob.priority)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if stale is not None:
        stale.state = "queued"
        stale.claimed_at = None
    job = await db.scalar(
        select(RanepaCrawlJob)
        .where(
            RanepaCrawlJob.state == "queued",
        )
        .order_by(RanepaCrawlJob.week_start, RanepaCrawlJob.priority, RanepaCrawlJob.id)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if job is None:
        await db.flush()
        return None
    job.state = "running"
    job.attempts += 1
    job.claimed_at = now
    await db.flush()
    return job


async def finish(db, job_id, group_id, user_id, *, failure_code=None, now=None) -> bool:
    """Finalize only the claimed account and group; error text never enters SQL."""
    if failure_code is not None and failure_code not in FAILURES:
        raise ValueError("unsafe_failure_code")
    job = await db.get(RanepaCrawlJob, job_id, with_for_update=True)
    if job is None or job.state != "running" or job.group_id != group_id or job.user_id != user_id:
        return False
    job.state = "failed" if failure_code else "succeeded"
    job.failure_code = failure_code
    job.finished_at = now or datetime.now(UTC)
    job.claimed_at = None
    await db.flush()
    return True
