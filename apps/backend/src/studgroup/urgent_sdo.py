"""Durable urgent SDO requests use existing jobs, consent, cooldown and retry limits."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from studgroup.legal import has_consent, legal_ready
from studgroup.models import (
    BackgroundProcess,
    Group,
    LegalConsentEvent,
    Membership,
    MemberSubgroup,
    RanepaCrawlJob,
    RanepaCrawlObservation,
    UrgentSDORun,
    UrgentSDOTarget,
    User,
    UserProfile,
)
from studgroup.process_control import ensure_process
from studgroup.process_runtime import ALERT_PREFIX, utc
from studgroup.ranepa_crawl_jobs import (
    CONSENT_REVALIDATED,
    CREDENTIALS_UPDATED,
    LEASE,
    RETRY_BACKOFF,
    enqueue_week,
    terminal_failure,
)
from studgroup.ranepa_crawl_plan import CrawlCandidate, fair_crawl_order

ACTIVE = ("requested", "initializing", "running")


async def eligible_candidates(db, settings, *, pilot_group_id=None, pilot_user_id=None):
    query = (
        select(Membership, Group)
        .join(Group, Group.id == Membership.group_id)
        .join(UserProfile, UserProfile.user_id == Membership.user_id)
        .where(
            Membership.status == "active",
            Group.status == "active",
            Group.academic_group_code.is_not(None),
        )
    )
    if pilot_group_id is not None:
        query = query.where(
            Membership.group_id == pilot_group_id, Membership.user_id == pilot_user_id
        )
    candidates = []
    for member, group in (await db.execute(query)).all():
        profile = await db.get(UserProfile, member.user_id)
        if not profile or not profile.sdu_credential_id:
            continue
        if not await has_consent(db, member.user_id, "sdo", settings):
            continue
        subgroups = (
            await db.scalars(
                select(MemberSubgroup.subgroup_id).where(MemberSubgroup.membership_id == member.id)
            )
        ).all()
        candidates.append(CrawlCandidate(group.id, member.user_id, frozenset(subgroups)))
    return candidates


async def request_run(db, *, replace=False):
    # Ordinary retries deduplicate. A confirmed replacement supersedes the old run.
    await ensure_process(db, "sdo")
    await db.get(BackgroundProcess, "sdo", with_for_update=True)
    row = await db.scalar(
        select(UrgentSDORun)
        .where(UrgentSDORun.state.in_(ACTIVE))
        .order_by(UrgentSDORun.requested_at.desc())
        .with_for_update()
        .limit(1)
    )
    if row is not None and replace:
        now = datetime.now(UTC)
        row.state = "cancelled"
        row.finished_at = now
        await db.execute(
            update(UrgentSDOTarget)
            .where(UrgentSDOTarget.run_id == row.id, UrgentSDOTarget.state == "pending")
            .values(state="cancelled", completed_at=now, failure_code="superseded")
        )
        row = None
    if row is None:
        row = UrgentSDORun(state="requested", requested_at=datetime.now(UTC))
        db.add(row)
        await db.flush()
    return row


async def run_json(db, row):
    counts = dict(
        (
            await db.execute(
                select(UrgentSDOTarget.state, func.count())
                .where(UrgentSDOTarget.run_id == row.id)
                .group_by(UrgentSDOTarget.state)
            )
        ).all()
    )
    succeeded = counts.get("succeeded", 0)
    failed = counts.get("failed", 0)
    # Owner-only progress: show durable per-account state, not browser guesses.
    # A historical job may be refreshed later; never display that as this run.
    targets = (
        await db.execute(
            select(UrgentSDOTarget, RanepaCrawlJob, User.display_name, Group.name)
            .outerjoin(RanepaCrawlJob, RanepaCrawlJob.id == UrgentSDOTarget.job_id)
            .join(User, User.id == UrgentSDOTarget.user_id)
            .join(Group, Group.id == UrgentSDOTarget.group_id)
            .where(UrgentSDOTarget.run_id == row.id)
            .order_by(Group.name, User.display_name, UrgentSDOTarget.user_id)
        )
    ).all()
    progress = []
    now = datetime.now(UTC)
    for target, job, name, group in targets:
        state = target.state
        started_at = next_attempt_at = None
        if state == "pending" and job is not None:
            if job.state == "running" and job.attempts > target.baseline_attempts:
                state = "running"
                started_at = utc(job.claimed_at) if job.claimed_at else None
            elif job.next_attempt_at and utc(job.next_attempt_at) > now:
                state = "cooldown"
                next_attempt_at = utc(job.next_attempt_at)
            else:
                state = "queued"
        progress.append(
            {
                "student": name,
                "group": group,
                "state": state,
                "reason": target.failure_code,
                "started_at": started_at,
                "next_attempt_at": next_attempt_at,
                "completed_at": utc(target.completed_at) if target.completed_at else None,
            }
        )
    return {
        "id": str(row.id),
        "state": row.state,
        "requested_at": utc(row.requested_at) if row.requested_at else None,
        "started_at": utc(row.started_at) if row.started_at else None,
        "finished_at": utc(row.finished_at) if row.finished_at else None,
        "found": row.found_count,
        "queued": row.queued_count,
        "completed": succeeded + failed,
        "succeeded": succeeded,
        "failed": failed,
        "blocked": counts.get("blocked", 0),
        "cancelled": counts.get("cancelled", 0),
        "remaining": counts.get("pending", 0),
        "targets": progress,
    }


async def latest_run(db):
    row = await db.scalar(select(UrgentSDORun).order_by(UrgentSDORun.requested_at.desc()).limit(1))
    return await run_json(db, row) if row is not None else None


async def urgent_requested(engine):
    async with AsyncSession(engine) as db:
        return (
            await db.scalar(
                select(UrgentSDORun.id)
                .where(UrgentSDORun.state.in_(("requested", "initializing")))
                .limit(1)
            )
            is not None
        )


async def active_run(engine):
    async with AsyncSession(engine) as db:
        return (
            await db.scalar(select(UrgentSDORun.id).where(UrgentSDORun.state.in_(ACTIVE)).limit(1))
            is not None
        )


async def previous_login_block(db, user_id):
    failed = await db.scalar(
        select(func.max(RanepaCrawlJob.finished_at)).where(
            RanepaCrawlJob.user_id == user_id,
            RanepaCrawlJob.state == "failed",
            RanepaCrawlJob.failure_code == "login_failed",
            RanepaCrawlJob.retry_reason.is_distinct_from(CREDENTIALS_UPDATED),
            RanepaCrawlJob.retry_reason.is_distinct_from(CONSENT_REVALIDATED),
        )
    )
    if failed is None:
        return False
    success = await db.scalar(
        select(func.max(RanepaCrawlJob.finished_at)).where(
            RanepaCrawlJob.user_id == user_id, RanepaCrawlJob.state == "succeeded"
        )
    )
    return success is None or utc(success) <= utc(failed)


async def release_legacy_login_blocks(db, user_id):
    """One owner-requested recheck after later consent and profile save.

    Older workers recorded preflight or ambiguous login timeouts as login_failed.
    Never release an observed rejection, and never reclassify a new failure.
    The caller already verified current consent and active membership.
    """
    profile = await db.get(UserProfile, user_id)
    if profile is None or not profile.sdu_credential_id:
        return False
    consent = await db.scalar(
        select(LegalConsentEvent)
        .where(LegalConsentEvent.user_id == user_id, LegalConsentEvent.purpose == "sdo")
        .order_by(LegalConsentEvent.created_at.desc(), LegalConsentEvent.id.desc())
        .limit(1)
    )
    if (
        consent is None
        or consent.action != "grant"
        or utc(profile.updated_at) < utc(consent.created_at)
    ):
        return False
    failures = (
        await db.scalars(
            select(RanepaCrawlJob)
            .where(
                RanepaCrawlJob.user_id == user_id,
                RanepaCrawlJob.state == "failed",
                RanepaCrawlJob.failure_code == "login_failed",
                RanepaCrawlJob.retry_reason.is_distinct_from(CREDENTIALS_UPDATED),
                RanepaCrawlJob.retry_reason.is_distinct_from(CONSENT_REVALIDATED),
            )
            .with_for_update()
        )
    ).all()
    if not failures:
        return False
    for job in failures:
        if job.finished_at is None or utc(consent.created_at) <= utc(job.finished_at):
            return False
        observation = await db.get(RanepaCrawlObservation, (job.id, job.attempts))
        if observation is not None and observation.failure_detail not in {
            "consent_missing",
            "credentials_missing",
            "profile_missing",
            "legal_not_ready",
            "access_not_approved",
        }:
            return False
    for job in failures:
        job.retry_reason = CONSENT_REVALIDATED
    await db.flush()
    return True


async def prepare_urgent(engine, settings, today):
    if not legal_ready(settings) or not settings.ranepa_access_approved:
        return
    async with AsyncSession(engine, expire_on_commit=False) as db:
        row = await db.scalar(
            select(UrgentSDORun)
            .where(UrgentSDORun.state.in_(("requested", "initializing")))
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if row is None:
            return
        # Do not attribute an in-flight attempt from the previous run to its replacement.
        live_job = await db.scalar(
            select(RanepaCrawlJob.id)
            .where(
                RanepaCrawlJob.state == "running",
                RanepaCrawlJob.claimed_at >= datetime.now(UTC) - LEASE,
            )
            .limit(1)
        )
        if live_job is not None:
            return
        row.state = "initializing"
        if row.started_at is None:
            row.started_at = datetime.now(UTC)
        run_id = row.id
        await db.commit()
    # Publish the real initialization phase before enumerating accounts.
    # A worker restart can resume this phase; a replacement may supersede it.
    async with AsyncSession(engine, expire_on_commit=False) as db:
        row = await db.scalar(
            select(UrgentSDORun)
            .where(UrgentSDORun.id == run_id, UrgentSDORun.state == "initializing")
            .with_for_update(skip_locked=True)
        )
        if row is None:
            return
        ordered = fair_crawl_order(await eligible_candidates(db, settings))
        distinct = {}
        for candidate in ordered:
            distinct.setdefault(candidate.user_id, candidate)
        candidates = list(distinct.values())
        monday = today - timedelta(days=today.weekday())
        if db.get_bind().dialect.name == "postgresql":
            await db.execute(text("SELECT pg_advisory_xact_lock(832610071)"))
        for candidate in candidates:
            await release_legacy_login_blocks(db, candidate.user_id)
        # This mode may queue an account before its cooldown expires, but never
        # overrides next_attempt_at, invalid credentials, or exhausted retries.
        await enqueue_week(db, monday, candidates, urgent_refresh=True)
        now = datetime.now(UTC)
        for candidate in candidates:
            job = await db.scalar(
                select(RanepaCrawlJob).where(
                    RanepaCrawlJob.user_id == candidate.user_id,
                    RanepaCrawlJob.group_id == candidate.group_id,
                    RanepaCrawlJob.week_start == monday,
                )
            )
            blocked = (
                job is None
                or job.state == "dead_letter"
                or await previous_login_block(db, candidate.user_id)
            )
            db.add(
                UrgentSDOTarget(
                    run_id=row.id,
                    user_id=candidate.user_id,
                    group_id=candidate.group_id,
                    job_id=job.id if job else None,
                    baseline_attempts=(
                        max(0, job.attempts - int(job.state == "running")) if job else 0
                    ),
                    state="blocked" if blocked else "pending",
                    failure_code="retry_policy_blocked" if blocked else None,
                    completed_at=now if blocked else None,
                )
            )
            if not blocked:
                row.queued_count += 1
        row.found_count = len(candidates)
        row.state = "running"
        await db.commit()


async def finalize_urgent(engine, settings):
    from studgroup.bot_admin import enqueue_once

    async with AsyncSession(engine) as db:
        row = await db.scalar(
            select(UrgentSDORun)
            .where(UrgentSDORun.state == "running")
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if row is None:
            return
        # A removed queue item cannot be attempted; account for it, don't hang.
        targets = (
            await db.scalars(
                select(UrgentSDOTarget).where(
                    UrgentSDOTarget.run_id == row.id,
                    UrgentSDOTarget.state == "pending",
                    UrgentSDOTarget.job_id.is_(None),
                )
            )
        ).all()
        for target in targets:
            target.state = "blocked"
            target.failure_code = "queue_item_unavailable"
            target.completed_at = datetime.now(UTC)
        # Reconsider legacy blocks in an already requested run after an upgrade.
        if db.get_bind().dialect.name == "postgresql":
            await db.execute(text("SELECT pg_advisory_xact_lock(832610071)"))
        candidates = await eligible_candidates(db, settings)
        eligible = {(candidate.user_id, candidate.group_id) for candidate in candidates}
        blocked_targets = (
            await db.scalars(
                select(UrgentSDOTarget).where(
                    UrgentSDOTarget.run_id == row.id,
                    UrgentSDOTarget.state == "blocked",
                    UrgentSDOTarget.failure_code == "retry_policy_blocked",
                )
            )
        ).all()
        for target in blocked_targets:
            if (target.user_id, target.group_id) not in eligible:
                continue
            if not await release_legacy_login_blocks(db, target.user_id):
                continue
            if await previous_login_block(db, target.user_id):
                continue
            today = datetime.now(UTC).date()
            monday = today - timedelta(days=today.weekday())
            await enqueue_week(
                db, monday, [CrawlCandidate(target.group_id, target.user_id)], urgent_refresh=True
            )
            job = await db.scalar(
                select(RanepaCrawlJob).where(
                    RanepaCrawlJob.group_id == target.group_id,
                    RanepaCrawlJob.user_id == target.user_id,
                    RanepaCrawlJob.week_start == monday,
                    RanepaCrawlJob.state == "queued",
                )
            )
            if job is None:
                continue
            target.job_id = job.id
            target.baseline_attempts = job.attempts
            target.state = "pending"
            target.failure_code = None
            target.completed_at = None
            row.queued_count += 1
        await db.flush()
        result = await run_json(db, row)
        if result["remaining"]:
            await db.commit()
            return
        # Only after every primary/previous-round target has finalized, reopen
        # retryable failures. Preserve the durable account-wide retry clock and
        # attempt budget; a refused login is never retried here.
        eligible_users = {user_id for user_id, _group_id in eligible}
        failed_targets = (
            await db.scalars(
                select(UrgentSDOTarget).where(
                    UrgentSDOTarget.run_id == row.id,
                    UrgentSDOTarget.state == "failed",
                )
            )
        ).all()
        reopened = 0
        for target in failed_targets:
            if target.user_id not in eligible_users or target.job_id is None:
                continue
            job = await db.get(RanepaCrawlJob, target.job_id, with_for_update=True)
            code = target.failure_code
            if (
                job is None
                or job.state not in {"failed", "queued"}
                or code not in RETRY_BACKOFF
                or terminal_failure(code, job.attempts)
                or job.next_attempt_at is None
                or await previous_login_block(db, target.user_id)
            ):
                continue
            job.state = "queued"
            job.failure_code = None
            job.retry_reason = code
            target.baseline_attempts = job.attempts
            target.state = "pending"
            target.failure_code = None
            target.completed_at = None
            reopened += 1
        if reopened:
            await db.commit()
            return
        row.state = "completed"
        row.finished_at = datetime.now(UTC)
        owner = settings.bot_admin_user_id
        if type(owner) is int and owner > 0:
            message = (
                "✅ Срочный SDO-прогон завершён. "
                f"Найдено: {result['found']}; в очереди: {result['queued']}; "
                f"выполнено: {result['completed']}; успешно: {result['succeeded']}; "
                f"ошибок: {result['failed']}; заблокировано защитой: {result['blocked']}; "
                "осталось: 0."
            )
            await enqueue_once(
                db,
                "sendMessage",
                {"chat_id": owner, "text": message},
                f"{ALERT_PREFIX}urgent:{row.id}:completed",
            )
        await db.commit()
