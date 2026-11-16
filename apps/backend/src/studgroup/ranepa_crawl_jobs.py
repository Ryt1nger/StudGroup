"""Durable, identity-only queue for a paced university account crawl."""

import json
import re
import uuid
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import aliased

from studgroup.models import (
    Group,
    Membership,
    MemberSubgroup,
    RanepaCrawlJob,
    RanepaPersonalSnapshot,
    SubjectSplit,
)
from studgroup.ranepa_crawl_plan import CrawlCandidate, fair_crawl_order
from studgroup.ranepa_schedule import normalized

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
            if existing.state in {"succeeded", "failed", "covered"} and (
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


async def _coverage_satisfied(db, candidate: RanepaCrawlJob, now: datetime) -> bool:
    """Require fresh identity-verified reads covering the candidate's segments.

    An empty/partial source or a source from a departed member never proves
    coverage. Missing split-subject evidence keeps backups eligible.
    """
    snapshots = (
        await db.scalars(
            select(RanepaPersonalSnapshot).where(
                RanepaPersonalSnapshot.group_id == candidate.group_id,
                RanepaPersonalSnapshot.week_start == candidate.week_start,
                RanepaPersonalSnapshot.identity_verified.is_(True),
                RanepaPersonalSnapshot.checked_at >= now - REFRESH,
            )
        )
    ).all()
    if not snapshots:
        return False
    members = (
        await db.scalars(
            select(Membership).where(
                Membership.group_id == candidate.group_id,
                Membership.status == "active",
            )
        )
    ).all()
    by_user = {m.user_id: m for m in members}
    if candidate.user_id not in by_user:
        return False
    choices = (
        await db.scalars(
            select(MemberSubgroup).where(MemberSubgroup.membership_id.in_([m.id for m in members]))
        )
    ).all()
    by_member = {}
    for choice in choices:
        by_member.setdefault(choice.membership_id, {})[choice.split_id] = choice.subgroup_id
    wanted = by_member.get(by_user[candidate.user_id].id, {})
    splits = (
        await db.scalars(select(SubjectSplit).where(SubjectSplit.group_id == candidate.group_id))
    ).all()
    split_name = {split.id: normalized(split.subject_name) for split in splits}
    split_subjects = set(split_name.values())
    covered_common = False
    covered_split = set()
    for snapshot in snapshots:
        if snapshot.user_id == candidate.user_id or snapshot.user_id not in by_user:
            continue
        rows = json.loads(snapshot.rows_json)
        if not rows:
            continue
        source_choices = by_member.get(by_user[snapshot.user_id].id, {})
        observed = {normalized(row["subject"]) for row in rows}
        if any(
            normalized(row["subject"]) not in split_subjects
            and not re.search(
                r"подгрупп|сг[ая]|поток",
                normalized(row.get("subgroup_hint")),
            )
            for row in rows
        ):
            covered_common = True
        for split_id, subgroup_id in source_choices.items():
            if split_name.get(split_id) in observed:
                covered_split.add((split_id, subgroup_id))
    # Do not suppress another account based solely on an unrelated subgroup.
    return (covered_common or bool(covered_split)) and all(
        (split_id, subgroup_id) in covered_split
        for split_id, subgroup_id in wanted.items()
    )


async def claim_next(
    db,
    now=None,
    *,
    group_id=None,
    user_id=None,
    rescan_all: bool = False,
    allowed_user_ids=None,
    processed_user_ids=None,
):
    """Claim a due account, or one per user during the owner-approved rescan.

    An explicit full rescan skips the usual 24-hour filter for this claim only.
    The latest finished_at remains persisted, so regular runs stay throttled.
    """
    if (group_id is None) != (user_id is None):
        raise ValueError("pilot_scope_requires_both_ids")
    if rescan_all and group_id is not None:
        raise ValueError("rescan_all_cannot_be_a_pilot")
    scope = []
    if group_id is not None:
        scope = [
            RanepaCrawlJob.group_id == group_id,
            RanepaCrawlJob.user_id == user_id,
        ]
    if allowed_user_ids is not None:
        if not allowed_user_ids:
            return None
        scope.append(RanepaCrawlJob.user_id.in_(allowed_user_ids))
    if processed_user_ids:
        scope.append(RanepaCrawlJob.user_id.not_in(processed_user_ids))
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
    if group_id is None and not rescan_all:
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
    while True:
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
        if group_id is None and not rescan_all and await _coverage_satisfied(db, job, now):
            # No browser/payment needed. This is NOT a successful account crawl.
            job.state = "covered"
            job.finished_at = now
            job.failure_code = None
            job.claimed_at = None
            await db.flush()
            continue
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
