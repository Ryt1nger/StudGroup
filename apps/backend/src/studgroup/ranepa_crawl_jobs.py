"""Durable, identity-only queue for a paced university account crawl."""

import json
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
from studgroup.ranepa_schedule import (
    has_unverified_subgroup_hint,
    normalized,
    subgroup_choice_changed_since_check,
)

LEASE = timedelta(hours=1)
REFRESH = timedelta(hours=6)
RETRY_BACKOFF = {
    "source_unavailable": timedelta(minutes=30),
    "parse_failed": timedelta(minutes=5),
    "identity_unverified": timedelta(hours=2),
    "internal": timedelta(hours=1),
    "captcha": timedelta(hours=3),
}


def retry_at(now: datetime, *, failure_code: str | None, attempts: int) -> datetime | None:
    """Bound every retry and require user action for invalid credentials."""
    if failure_code == "login_failed":
        return None
    if failure_code is None:
        return now + REFRESH
    if failure_code not in RETRY_BACKOFF:
        raise ValueError("unsafe_failure_code")
    delay = RETRY_BACKOFF[failure_code] * min(2 ** max(0, attempts - 1), 8)
    return now + min(delay, timedelta(hours=6))


def retry_reason(failure_code: str | None) -> str | None:
    return "credentials_required" if failure_code == "login_failed" else failure_code


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
        # A student's six-hour cooldown spans group memberships and week boundaries.
        # finished_at remains on a refreshed job until its next completion.
        last_completed_at = await db.scalar(
            select(func.max(RanepaCrawlJob.finished_at)).where(
                RanepaCrawlJob.user_id == candidate.user_id
            )
        )
        last_retry_at = await db.scalar(
            select(func.max(RanepaCrawlJob.next_attempt_at)).where(
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
        if last_retry_at is not None and last_retry_at.tzinfo is None:
            last_retry_at = last_retry_at.replace(tzinfo=UTC)
        if existing is not None:
            # Account and week cooldown must be identical; a bad login
            # must not trigger automatic repeated password attempts.
            due = last_retry_at is None or last_retry_at <= now
            can_retry = existing.failure_code != "login_failed" or force_refresh
            if (
                existing.state in {"succeeded", "failed", "covered"}
                and can_retry
                and (force_refresh or due)
            ):
                existing.state = "queued"
                existing.priority = priority
                existing.failure_code = None
                existing.retry_reason = None
                existing.next_attempt_at = last_retry_at
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
                next_attempt_at=last_retry_at,
                retry_reason=None,
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
    choice_updated = {}
    for choice in choices:
        by_member.setdefault(choice.membership_id, {})[choice.split_id] = choice.subgroup_id
        choice_updated[(choice.membership_id, choice.split_id)] = choice.updated_at
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
        rows = [
            row
            for row in json.loads(snapshot.rows_json)
            if row.get("source_scope") not in {"common", "subgroup"}
        ]
        if not rows:
            continue
        source_member_id = by_user[snapshot.user_id].id
        source_choices = by_member.get(source_member_id, {})
        observed = {normalized(row["subject"]) for row in rows}
        if any(
            normalized(row["subject"]) not in split_subjects
            and not has_unverified_subgroup_hint(row.get("subgroup_hint"))
            for row in rows
        ):
            covered_common = True
        for split_id, subgroup_id in source_choices.items():
            if (
                split_name.get(split_id) in observed
                and not subgroup_choice_changed_since_check(
                    choice_updated.get((source_member_id, split_id)),
                    snapshot.checked_at,
                )
            ):
                covered_split.add((split_id, subgroup_id))
    # Do not suppress another account based solely on an unrelated subgroup.
    return (covered_common or bool(covered_split)) and all(
        (split_id, subgroup_id) in covered_split for split_id, subgroup_id in wanted.items()
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

    An explicit full rescan skips the usual six-hour filter for this claim only.
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
    # A second membership or next week's job cannot bypass the six-hour clock.
    completed = aliased(RanepaCrawlJob)
    locked = aliased(RanepaCrawlJob)
    recovered = aliased(RanepaCrawlJob)
    running = aliased(RanepaCrawlJob)
    due = []
    if group_id is None and not rescan_all:
        due.append(
            ~select(locked.id)
            .where(
                locked.user_id == RanepaCrawlJob.user_id,
                locked.state == "failed",
                locked.failure_code == "login_failed",
                # An older invalid-password failure must not permanently
                # block ordinary runs after a newer successful manual pilot.
                ~select(recovered.id)
                .where(
                    recovered.user_id == locked.user_id,
                    recovered.state == "succeeded",
                    recovered.finished_at > locked.finished_at,
                )
                .exists(),
            )
            .exists()
        )
        due.append(
            ~select(completed.id)
            .where(
                completed.user_id == RanepaCrawlJob.user_id,
                completed.next_attempt_at > now,
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
                (
                    RanepaCrawlJob.next_attempt_at.is_(None)
                    | (RanepaCrawlJob.next_attempt_at <= now)
                    | (group_id is not None)
                    | rescan_all
                ),
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
            job.next_attempt_at = now + REFRESH
            job.retry_reason = "covered_by_peers"
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
    job.next_attempt_at = retry_at(
        job.finished_at, failure_code=failure_code, attempts=job.attempts
    )
    job.retry_reason = retry_reason(failure_code)
    job.claimed_at = None
    await db.flush()
    return True
