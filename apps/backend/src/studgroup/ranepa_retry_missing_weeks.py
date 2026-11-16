"""One owner-authorized retry of prior failures still missing a verified current week.

Keep account IDs and credentials out of CI logs. Never rescan a member whose
current week has already been verified, even if an older job failed.
"""

import asyncio
import json
import sys
from datetime import date, datetime
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from studgroup.main import Settings
from studgroup.models import (
    Group,
    Membership,
    RanepaCrawlJob,
    RanepaPersonalSnapshot,
    UserProfile,
)
from studgroup.ranepa_crawl_guard import load_guard
from studgroup.ranepa_worker import create_worker_engine, run_single_pilot


async def choose_unverified_failures(
    engine, *, today: date, max_accounts: int = 8
) -> list[tuple[UUID, UUID]]:
    """Select only active/credentialed old failures with no verified current week."""
    if not 1 <= max_accounts <= 8:
        raise ValueError("unsafe_retry_batch_size")
    monday = today.fromordinal(today.toordinal() - today.weekday())
    async with AsyncSession(engine) as db:
        guard = await load_guard(db)
        if guard.paused or guard.recovery_requested:
            raise RuntimeError("retry_requires_open_captcha_guard")
        verified_week = (
            select(RanepaPersonalSnapshot.group_id)
            .where(
                RanepaPersonalSnapshot.group_id == RanepaCrawlJob.group_id,
                RanepaPersonalSnapshot.user_id == RanepaCrawlJob.user_id,
                RanepaPersonalSnapshot.week_start == monday,
                RanepaPersonalSnapshot.identity_verified.is_(True),
            )
            .exists()
        )
        # Use an aliased current-week job: a past failure must not override a
        # newer successful, or currently running, crawl for this student.
        from sqlalchemy.orm import aliased

        current_job = aliased(RanepaCrawlJob)
        current_completed_or_running = (
            select(current_job.id)
            .where(
                current_job.user_id == RanepaCrawlJob.user_id,
                current_job.week_start == monday,
                current_job.state.in_(("succeeded", "running")),
            )
            .exists()
        )
        rows = (
            await db.execute(
                select(RanepaCrawlJob.group_id, RanepaCrawlJob.user_id)
                .join(Group, Group.id == RanepaCrawlJob.group_id)
                .join(
                    Membership,
                    (Membership.group_id == RanepaCrawlJob.group_id)
                    & (Membership.user_id == RanepaCrawlJob.user_id),
                )
                .join(UserProfile, UserProfile.user_id == RanepaCrawlJob.user_id)
                .where(
                    RanepaCrawlJob.week_start < monday,
                    RanepaCrawlJob.state == "failed",
                    RanepaCrawlJob.failure_code.in_(
                        (
                            "captcha",
                            "parse_failed",
                            "source_unavailable",
                            "identity_unverified",
                            "login_failed",
                            "internal",
                        )
                    ),
                    Group.status == "active",
                    Group.pilot_authorized.is_(True),
                    Group.academic_group_code.is_not(None),
                    Membership.status == "active",
                    UserProfile.sdu_credential_id.is_not(None),
                    ~verified_week,
                    ~current_completed_or_running,
                )
                .order_by(RanepaCrawlJob.finished_at.desc(), RanepaCrawlJob.id)
                .limit(64)
            )
        ).all()
    selected: list[tuple[UUID, UUID]] = []
    seen_users: set[UUID] = set()
    for row in rows:
        if row.user_id in seen_users:
            continue
        selected.append((row.group_id, row.user_id))
        seen_users.add(row.user_id)
        if len(selected) >= max_accounts:
            break
    return selected


async def retry_unverified_failures() -> int:
    """One-off explicit release: replay only missing current-week failures."""
    settings = Settings()
    if not settings.ranepa_crawl_enabled or not settings.ranepa_captcha_guard_enabled:
        raise RuntimeError("guarded_crawl_required")
    if not settings.ranepa_access_approved:
        raise RuntimeError("university_access_approval_required")
    if (
        not settings.ranepa_captcha_enabled
        or not settings.ranepa_captcha_v2_pair_enabled
        or not settings.ranepa_manual_captcha_enabled
        or not settings.captcha_api_key.get_secret_value().strip()
    ):
        raise RuntimeError("paid_captcha_and_owner_fallback_required")
    engine = create_worker_engine(settings)
    attempted = succeeded = failed = 0
    try:
        today = datetime.now(ZoneInfo("Europe/Moscow")).date()
        candidates = await choose_unverified_failures(engine, today=today)
        for group_id, user_id in candidates:
            # A prior pilot can establish the week; recheck before each access.
            still_missing = await choose_unverified_failures(engine, today=today)
            if (group_id, user_id) not in still_missing:
                continue
            result = await run_single_pilot(settings, group_id=group_id, user_id=user_id)
            attempted += 1
            if result == "succeeded":
                succeeded += 1
            else:
                failed += 1
        remaining = len(await choose_unverified_failures(engine, today=today))
    finally:
        await engine.dispose()
    print(
        "RANEPA_MISSING_WEEK_RETRY_RESULT "
        + json.dumps(
            {
                "selected": len(candidates),
                "attempted": attempted,
                "succeeded": succeeded,
                "failed": failed,
                "remaining_eligible": remaining,
            }
        ),
        flush=True,
    )
    return 0 if failed == 0 and remaining == 0 else 1


if __name__ == "__main__":
    if sys.argv[1:]:
        raise SystemExit("unsupported_retry_option")
    raise SystemExit(asyncio.run(retry_unverified_failures()))
