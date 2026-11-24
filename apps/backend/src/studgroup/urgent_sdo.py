"""Durable urgent SDO requests use existing jobs, consent, cooldown and retry limits."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from studgroup.legal import has_consent, legal_ready
from studgroup.models import (
    BackgroundProcess,
    Group,
    Membership,
    MemberSubgroup,
    RanepaCrawlJob,
    UrgentSDORun,
    UrgentSDOTarget,
    UserProfile,
)
from studgroup.process_control import ensure_process
from studgroup.process_runtime import ALERT_PREFIX, utc
from studgroup.ranepa_crawl_jobs import enqueue_week
from studgroup.ranepa_crawl_plan import CrawlCandidate, fair_crawl_order

ACTIVE = ("requested", "running")


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


async def request_run(db):
    # Serializes simultaneous clicks; a second request returns the same active run.
    await ensure_process(db, "sdo")
    await db.get(BackgroundProcess, "sdo", with_for_update=True)
    row = await db.scalar(
        select(UrgentSDORun)
        .where(UrgentSDORun.state.in_(ACTIVE))
        .order_by(UrgentSDORun.requested_at.desc())
        .limit(1)
    )
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
    return {
        "id": str(row.id),
        "state": row.state,
        "requested_at": row.requested_at,
        "started_at": row.started_at,
        "finished_at": row.finished_at,
        "found": row.found_count,
        "queued": row.queued_count,
        "completed": succeeded + failed,
        "succeeded": succeeded,
        "failed": failed,
        "blocked": counts.get("blocked", 0),
        "remaining": counts.get("pending", 0),
    }


async def latest_run(db):
    row = await db.scalar(select(UrgentSDORun).order_by(UrgentSDORun.requested_at.desc()).limit(1))
    return await run_json(db, row) if row is not None else None


async def urgent_requested(engine):
    async with AsyncSession(engine) as db:
        return (
            await db.scalar(
                select(UrgentSDORun.id).where(UrgentSDORun.state == "requested").limit(1)
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


async def prepare_urgent(engine, settings, today):
    if not legal_ready(settings) or not settings.ranepa_access_approved:
        return
    async with AsyncSession(engine, expire_on_commit=False) as db:
        row = await db.scalar(
            select(UrgentSDORun)
            .where(UrgentSDORun.state == "requested")
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if row is None:
            return
        ordered = fair_crawl_order(await eligible_candidates(db, settings))
        distinct = {}
        for candidate in ordered:
            distinct.setdefault(candidate.user_id, candidate)
        candidates = list(distinct.values())
        monday = today - timedelta(days=today.weekday())
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
        row.started_at = now
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
        await db.flush()
        result = await run_json(db, row)
        if result["remaining"]:
            await db.commit()
            return
        row.state = "completed"
        row.finished_at = datetime.now(UTC)
        owner = settings.bot_admin_user_id
        if type(owner) is int and owner > 0:
            text = (
                "✅ Срочный SDO-прогон завершён. "
                f"Найдено: {result['found']}; в очереди: {result['queued']}; "
                f"выполнено: {result['completed']}; успешно: {result['succeeded']}; "
                f"ошибок: {result['failed']}; заблокировано защитой: {result['blocked']}; "
                "осталось: 0."
            )
            await enqueue_once(
                db,
                "sendMessage",
                {"chat_id": owner, "text": text},
                f"{ALERT_PREFIX}urgent:{row.id}:completed",
            )
        await db.commit()
