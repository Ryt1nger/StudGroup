"""Durable, identity-only queue for a paced university account crawl."""

import uuid
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import aliased

from studgroup.models import Group, Membership, RanepaCrawlJob
from studgroup.ranepa_crawl_plan import CrawlCandidate, fair_crawl_order

LEASE = timedelta(hours=1)
REFRESH = timedelta(days=1)
FAILURES = {
    "login_failed",
    "captcha",
    "source_unavailable",
    "identity_unverified",
    "parse_failed",
    "internal",
}


async def enqueue_week(
    db,
    week_start: date,
    candidates: list[CrawlCandidate],
    now=None,
    *,
    force_refresh: bool = False,
) -> int:
    """Add jobs or refresh completed jobs; manual pilot may override its cooldown."""
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
        # A student's 24-hour cooldown spans group memberships and week boundaries.
        # finished_at remains on a refreshed job until its next completion.
        last_completed_at = await db.scalar(
            select(func.max(RanepaCrawlJob.finished_at)).where(
                RanepaCrawlJob.user_id == candidate.user_id
            )
        )
        existing = await db.scalar(
            select(RanepaCrawlJob).where(
                RanepaCrawlJob.group_id == candidate.group_id,
                RanepaCrawlJob.user_id == candidate.user_id,
                RanepaCrawlJob.week_start == week_start,
            )
        )
        if last_completed_at is not None and last_completed_at.tzinfo is None:
            # SQLite tests return naive datetimes despite timezone=True.
            last_completed_at = last_completed_at.replace(tzinfo=UTC)
        if existing is not None:
            # The explicit manual pilot can retry its *selected* completed job
            # immediately. Queue workers retain the 24-hour cooldown. Never
            # reset an already queued or currently running job.
            if existing.state in {"succeeded", "failed"} and (
                force_refresh
                or (last_completed_at is not None and last_completed_at <= now - REFRESH)
            ):
                existing.state = "queued"
                existing.priority = priority
                existing.failure_code = None
                # Keep the completion timestamp as a durable not-before time.
                # claim_next rechecks the latest finish across all weeks.
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
                finished_at=last_completed_at,
            )
        )
        created += 1
    await db.flush()
    return created


async def claim_next(db, now=None, *, group_id=None, user_id=None):
    """Claim one job under a short DB lock; optionally restrict a single-account pilot."""
    if (group_id is None) != (user_id is None):
        raise ValueError("pilot_scope_requires_both_ids")
    scope = []
    if group_id is not None:
        scope = [
            RanepaCrawlJob.group_id == group_id,
            RanepaCrawlJob.user_id == user_id,
        ]
    now = now or datetime.now(UTC)
    stale = await db.scalar(
        select(RanepaCrawlJob)
        .where(
            RanepaCrawlJob.state == "running",
            RanepaCrawlJob.claimed_at < now - LEASE,
            *scope,
        )
        .order_by(RanepaCrawlJob.priority)
        .with_for_update(skip_locked=True)
        .limit(1)
    )
    if stale is not None:
        stale.state = "queued"
        stale.claimed_at = None
    # Filter not-yet-due accounts IN the DB query, not after claiming the
    # first row; one cooling down must never block another eligible account.
    # A second membership or next week's job cannot bypass the 24-hour clock.
    completed = aliased(RanepaCrawlJob)
    running = aliased(RanepaCrawlJob)
    due = []
    if group_id is None:
        due.append(
            ~select(completed.id)
            .where(
                completed.user_id == RanepaCrawlJob.user_id,
                completed.finished_at > now - REFRESH,
            )
            .exists()
        )
    due.append(
        ~select(running.id)
        .where(
            running.user_id == RanepaCrawlJob.user_id,
            running.id != RanepaCrawlJob.id,
            running.state == "running",
            running.claimed_at >= now - LEASE,
        )
        .exists()
    )
    job = await db.scalar(
        select(RanepaCrawlJob)
        .where(
            RanepaCrawlJob.state == "queued",
            *scope,
            *due,
        )
        .order_by(
            RanepaCrawlJob.week_start.desc(),
            RanepaCrawlJob.attempts,
            RanepaCrawlJob.priority,
            RanepaCrawlJob.id,
        )
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
