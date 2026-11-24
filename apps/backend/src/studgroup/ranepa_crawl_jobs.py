"""Durable, identity-only queue for a paced university account crawl."""

import json
import uuid
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import case, func, select, text, update
from sqlalchemy.orm import aliased

from studgroup.models import (
    Group,
    Membership,
    MemberSubgroup,
    RanepaCrawlJob,
    RanepaMaterializationReceipt,
    RanepaPersonalSnapshot,
    SubjectSplit,
    UrgentSDOTarget,
)
from studgroup.ranepa_crawl_plan import CrawlCandidate, fair_crawl_order
from studgroup.ranepa_schedule import subgroup_choice_changed_since_check

LEASE = timedelta(hours=1)
REFRESH = timedelta(hours=6)
RETRY_BACKOFF = {
    "source_unavailable": timedelta(minutes=30),
    "parse_failed": timedelta(minutes=5),
    "identity_unverified": timedelta(hours=2),
    "internal": timedelta(hours=1),
    "captcha": timedelta(hours=3),
}
MAX_ATTEMPTS = {
    "source_unavailable": 5,
    "parse_failed": 4,
    "identity_unverified": 3,
    "internal": 3,
    "captcha": 5,
}


def terminal_failure(failure_code: str | None, attempts: int) -> bool:
    """Stop automatic work when credentials or a bounded retry budget is exhausted."""
    if failure_code == "login_failed":
        return True
    return failure_code in MAX_ATTEMPTS and attempts >= MAX_ATTEMPTS[failure_code]


def retry_at(now: datetime, *, failure_code: str | None, attempts: int) -> datetime | None:
    """Bound every retry and require user action for invalid credentials."""
    if terminal_failure(failure_code, attempts):
        return None
    if failure_code is None:
        return now + REFRESH
    if failure_code not in RETRY_BACKOFF:
        raise ValueError("unsafe_failure_code")
    delay = RETRY_BACKOFF[failure_code] * min(2 ** max(0, attempts - 1), 8)
    return now + min(delay, timedelta(hours=6))


def retry_reason(failure_code: str | None, attempts: int) -> str | None:
    if failure_code == "login_failed":
        return "credentials_required"
    if terminal_failure(failure_code, attempts):
        return f"attempts_exhausted:{failure_code}"
    return failure_code


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
    urgent_refresh: bool = False,
    bootstrap: bool = False,
) -> int:
    """Add jobs or refresh completed jobs; manual pilot may override its cooldown."""
    if week_start.weekday() != 0:
        raise ValueError("week_start_must_be_monday")
    if urgent_refresh and force_refresh:
        raise ValueError("incompatible_refresh_modes")
    if db.get_bind().dialect.name == "postgresql":
        await db.execute(text("SELECT pg_advisory_xact_lock(832610071)"))
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
        if bootstrap and not await db.scalar(
            select(RanepaMaterializationReceipt.group_id)
            .where(RanepaMaterializationReceipt.group_id == candidate.group_id)
            .limit(1)
        ):
            priority -= len(ordered)
        if existing is not None and existing.state == "queued" and bootstrap:
            existing.priority = priority
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
                existing.state in {"succeeded", "failed", "covered", "dead_letter"}
                and can_retry
                and (force_refresh or ((due or urgent_refresh) and existing.state != "dead_letter"))
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
    """Skip only after fresh, atomic, recipient-specific materialization proof.

    A peer's source snapshot alone never proves another account was updated.
    """
    member = await db.scalar(
        select(Membership).where(
            Membership.group_id == candidate.group_id,
            Membership.user_id == candidate.user_id,
            Membership.status == "active",
        )
    )
    if member is None:
        return False
    target = await db.get(
        RanepaPersonalSnapshot,
        (candidate.group_id, candidate.user_id, candidate.week_start),
    )
    if target is None:
        return False
    splits = (
        await db.scalars(select(SubjectSplit).where(SubjectSplit.group_id == candidate.group_id))
    ).all()
    choices = (
        await db.scalars(select(MemberSubgroup).where(MemberSubgroup.membership_id == member.id))
    ).all()
    wanted = {row.split_id: row.subgroup_id for row in choices}
    # Unknown subgroup is not proven by another student's common-only view.
    if any(split.id not in wanted for split in splits):
        return False

    receipts = (
        await db.scalars(
            select(RanepaMaterializationReceipt).where(
                RanepaMaterializationReceipt.group_id == candidate.group_id,
                RanepaMaterializationReceipt.recipient_user_id == candidate.user_id,
                RanepaMaterializationReceipt.week_start == candidate.week_start,
            )
        )
    ).all()
    if not receipts:
        return False
    covered_common = False
    covered_splits = set()

    def utc(value):
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)

    for receipt in receipts:
        source_member = await db.scalar(
            select(Membership).where(
                Membership.group_id == candidate.group_id,
                Membership.user_id == receipt.source_user_id,
                Membership.status == "active",
            )
        )
        if source_member is None or receipt.source_user_id == candidate.user_id:
            continue
        source = await db.get(
            RanepaPersonalSnapshot,
            (candidate.group_id, receipt.source_user_id, candidate.week_start),
        )
        if (
            source is None
            or not source.identity_verified
            or not source.source_complete
            or not source.source_digest
            or source.source_digest != receipt.source_digest
            or utc(source.checked_at) != utc(receipt.source_checked_at)
            or utc(source.checked_at) < utc(now) - REFRESH
        ):
            continue
        try:
            coverage = json.loads(receipt.coverage_json)
            rows = json.loads(source.rows_json)
        except (ValueError, TypeError):
            continue
        if not isinstance(coverage, dict) or not isinstance(rows, list):
            continue
        if not any(
            isinstance(row, dict) and row.get("source_scope") not in {"common", "subgroup"}
            for row in rows
        ):
            continue
        if coverage.get("common") is True:
            covered_common = True
        source_choices = (
            await db.scalars(
                select(MemberSubgroup).where(MemberSubgroup.membership_id == source_member.id)
            )
        ).all()
        by_split = {row.split_id: row for row in source_choices}
        if not isinstance(coverage.get("subgroups"), list):
            continue
        for item in coverage["subgroups"]:
            if not isinstance(item, list) or len(item) != 2:
                continue
            split_id, subgroup_id = item
            for split in splits:
                if (
                    str(split.id) == split_id
                    and str(wanted.get(split.id)) == subgroup_id
                    and split.id in by_split
                    and str(by_split[split.id].subgroup_id) == subgroup_id
                    and not subgroup_choice_changed_since_check(
                        by_split[split.id].updated_at, source.checked_at
                    )
                ):
                    covered_splits.add((split.id, wanted[split.id]))
    return covered_common and all(
        (split_id, subgroup_id) in covered_splits for split_id, subgroup_id in wanted.items()
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
    if db.get_bind().dialect.name == "postgresql":
        # Serialize the check-and-claim across updated VPS and recovery workers
        # using the existing transaction; a live durable lease prevents a
        # second browser even when another process has already committed.
        acquired = await db.scalar(text("SELECT pg_try_advisory_xact_lock(832610069)"))
        if not acquired:
            return None
        live = await db.scalar(
            select(RanepaCrawlJob.id)
            .where(
                RanepaCrawlJob.state == "running",
                RanepaCrawlJob.claimed_at >= now - LEASE,
            )
            .limit(1)
        )
        if live is not None:
            return None
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
        if stale.attempts >= MAX_ATTEMPTS["internal"]:
            stale.state = "dead_letter"
            stale.failure_code = "internal"
            stale.retry_reason = "lease_expired_exhausted"
            stale.next_attempt_at = None
            stale.finished_at = now
            await db.execute(
                update(UrgentSDOTarget)
                .where(UrgentSDOTarget.job_id == stale.id, UrgentSDOTarget.state == "pending")
                .values(state="failed", failure_code="internal", completed_at=now)
            )
        else:
            stale.state = "queued"
            stale.retry_reason = "lease_expired"
            stale.next_attempt_at = now
        stale.claimed_at = None
    # Filter not-yet-due accounts IN the DB query, not after claiming the
    # first row; one cooling down must never block another eligible account.
    # A second membership or next week's job cannot bypass the six-hour clock.
    completed = aliased(RanepaCrawlJob)
    locked = aliased(RanepaCrawlJob)
    recovered = aliased(RanepaCrawlJob)
    running = aliased(RanepaCrawlJob)
    urgent = (
        select(UrgentSDOTarget.run_id)
        .where(
            UrgentSDOTarget.job_id == RanepaCrawlJob.id,
            UrgentSDOTarget.state == "pending",
        )
        .exists()
    )
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
                case((RanepaCrawlJob.priority < 0, 0), (urgent, 1), else_=2),
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
        urgent_target = await db.scalar(
            select(UrgentSDOTarget.run_id)
            .where(UrgentSDOTarget.job_id == job.id, UrgentSDOTarget.state == "pending")
            .limit(1)
        )
        if (
            group_id is None
            and not rescan_all
            and urgent_target is None
            and await _coverage_satisfied(db, job, now)
        ):
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
    exhausted = failure_code != "login_failed" and terminal_failure(failure_code, job.attempts)
    job.state = (
        "dead_letter" if failure_code and exhausted else "failed" if failure_code else "succeeded"
    )
    job.failure_code = failure_code
    job.finished_at = now or datetime.now(UTC)
    job.next_attempt_at = retry_at(
        job.finished_at, failure_code=failure_code, attempts=job.attempts
    )
    job.retry_reason = retry_reason(failure_code, job.attempts)
    job.claimed_at = None
    await db.execute(
        update(UrgentSDOTarget)
        .where(
            UrgentSDOTarget.job_id == job.id,
            UrgentSDOTarget.state == "pending",
            UrgentSDOTarget.baseline_attempts < job.attempts,
        )
        .values(
            state="failed" if failure_code else "succeeded",
            failure_code=failure_code,
            completed_at=job.finished_at,
        )
    )
    await db.flush()
    return True


async def enqueue_new_group_account(db, user_id, settings):
    """Queue first verified group import in the same SQL transaction as credential saving."""
    from studgroup.legal import has_consent, legal_ready
    from studgroup.models import UserProfile

    if not legal_ready(settings) or not settings.ranepa_access_approved:
        return
    await db.flush()
    profile = await db.get(UserProfile, user_id)
    if profile is None or not profile.sdu_credential_id:
        return
    if not await has_consent(db, user_id, "sdo", settings):
        return
    groups = (
        await db.scalars(
            select(Group.id)
            .join(Membership, Membership.group_id == Group.id)
            .where(
                Membership.user_id == user_id,
                Membership.status == "active",
                Group.status == "active",
                Group.academic_group_code.is_not(None),
                ~select(RanepaMaterializationReceipt.group_id)
                .where(RanepaMaterializationReceipt.group_id == Group.id)
                .exists(),
            )
        )
    ).all()
    if not groups:
        return
    from zoneinfo import ZoneInfo

    today = datetime.now(ZoneInfo("Europe/Moscow")).date()
    monday = today - timedelta(days=today.weekday())
    await enqueue_week(
        db, monday, [CrawlCandidate(group_id, user_id) for group_id in groups], bootstrap=True
    )
