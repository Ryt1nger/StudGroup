"""One owner-authorized CAPTCHA recovery pilot; never restarts the full queue.

The job selector uses only consented active accounts that previously failed
with CAPTCHA. No student identifiers are rendered in GitHub Actions logs.
A successful, source-verified pilot clears the durable pause through the
existing guard; an unsuccessful pilot leaves it in place.
"""

import asyncio
import json
import sys
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from studgroup.main import Settings
from studgroup.models import Group, Membership, RanepaCrawlJob, UserProfile
from studgroup.ranepa_crawl_guard import load_guard
from studgroup.ranepa_worker import create_worker_engine, run_single_pilot


async def choose_recovery_candidate(engine) -> tuple[UUID, UUID]:
    async with AsyncSession(engine) as db:
        guard = await load_guard(db)
        if not guard.paused or not guard.recovery_requested:
            raise RuntimeError("owner_recovery_pilot_not_authorized")
        scope = (
            select(RanepaCrawlJob.group_id, RanepaCrawlJob.user_id)
            .join(Group, Group.id == RanepaCrawlJob.group_id)
            .join(
                Membership,
                (Membership.group_id == RanepaCrawlJob.group_id)
                & (Membership.user_id == RanepaCrawlJob.user_id),
            )
            .join(UserProfile, UserProfile.user_id == RanepaCrawlJob.user_id)
            .where(
                RanepaCrawlJob.state == "failed",
                RanepaCrawlJob.failure_code == "captcha",
                Group.status == "active",
                Group.academic_group_code.is_not(None),
                Membership.status == "active",
                UserProfile.sdu_credential_id.is_not(None),
            )
            .order_by(RanepaCrawlJob.finished_at.desc(), RanepaCrawlJob.id)
            .limit(1)
        )
        row = (await db.execute(scope)).first()
        if row is None:
            raise RuntimeError("no_eligible_failed_captcha_account")
        return row.group_id, row.user_id


async def main() -> int:
    settings = Settings()
    if not settings.ranepa_crawl_enabled or not settings.ranepa_captcha_guard_enabled:
        raise RuntimeError("guarded_recovery_pilot_requires_enabled_features")
    engine = create_worker_engine(settings)
    try:
        group_id, user_id = await choose_recovery_candidate(engine)
    finally:
        await engine.dispose()
    result = await run_single_pilot(settings, group_id=group_id, user_id=user_id)
    print("RANEPA_RECOVERY_PILOT_RESULT " + result, flush=True)
    return 0 if result == "succeeded" else 1


async def choose_failed_captcha_accounts(engine, *, max_accounts: int = 3) -> list[tuple[UUID, UUID]]:
    """Snapshot only the remaining CAPTCHA failures, never the queued students.

    A newly closed global breaker fails closed. The normal single-pilot path
    independently checks active membership, legal consent and credentials.
    """
    if max_accounts < 1 or max_accounts > 3:
        raise ValueError("unsafe_recovery_batch_size")
    async with AsyncSession(engine) as db:
        guard = await load_guard(db)
        if guard.paused or guard.recovery_requested:
            raise RuntimeError("captcha_recovery_batch_requires_open_guard")
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
                    RanepaCrawlJob.state == "failed",
                    RanepaCrawlJob.failure_code == "captcha",
                    Group.status == "active",
                    Group.academic_group_code.is_not(None),
                    Membership.status == "active",
                    UserProfile.sdu_credential_id.is_not(None),
                )
                .order_by(RanepaCrawlJob.finished_at, RanepaCrawlJob.id)
                .limit(30)
            )
        ).all()
    selected: list[tuple[UUID, UUID]] = []
    seen_users: set[UUID] = set()
    for row in rows:
        if row.user_id in seen_users:
            continue
        selected.append((row.group_id, row.user_id))
        seen_users.add(row.user_id)
        if len(selected) == max_accounts:
            break
    return selected


async def retry_failed_captcha_accounts() -> int:
    """Once-only owner-authorized retry, capped at the three known failures."""
    settings = Settings()
    if not settings.ranepa_crawl_enabled or not settings.ranepa_captcha_guard_enabled:
        raise RuntimeError("guarded_recovery_batch_requires_enabled_features")
    if (
        not settings.ranepa_captcha_enabled
        or not settings.ranepa_captcha_v2_pair_enabled
        or not settings.ranepa_manual_captcha_enabled
        or not settings.captcha_api_key.get_secret_value().strip()
    ):
        raise RuntimeError("rucaptcha_first_and_owner_fallback_are_required")
    engine = create_worker_engine(settings)
    selected = 0
    attempted = 0
    succeeded = 0
    failed = 0
    remaining = 0
    try:
        candidates = await choose_failed_captcha_accounts(engine)
        selected = len(candidates)
        for group_id, user_id in candidates:
            # Revalidate the guard and the original failure immediately before
            # touching each account. All GitHub crawler jobs are also serialized.
            async with AsyncSession(engine) as db:
                guard = await load_guard(db)
                if guard.paused:
                    raise RuntimeError("captcha_recovery_batch_paused")
                still_failed = await db.scalar(
                    select(RanepaCrawlJob.id)
                    .where(
                        RanepaCrawlJob.group_id == group_id,
                        RanepaCrawlJob.user_id == user_id,
                        RanepaCrawlJob.state == "failed",
                        RanepaCrawlJob.failure_code == "captcha",
                    )
                    .limit(1)
                )
            if still_failed is None:
                continue
            result = await run_single_pilot(settings, group_id=group_id, user_id=user_id)
            attempted += 1
            if result == "succeeded":
                succeeded += 1
            else:
                failed += 1
        async with AsyncSession(engine) as db:
            remaining = len(
                (
                    await db.scalars(
                        select(RanepaCrawlJob.id).where(
                            RanepaCrawlJob.state == "failed",
                            RanepaCrawlJob.failure_code == "captcha",
                        )
                    )
                ).all()
            )
    finally:
        await engine.dispose()
    # Keep account identifiers and all university credentials out of logs.
    print(
        "RANEPA_FAILED_CAPTCHA_RETRY_RESULT "
        + json.dumps(
            {
                "selected": selected,
                "attempted": attempted,
                "succeeded": succeeded,
                "failed": failed,
                "remaining_captcha_failures": remaining,
            }
        ),
        flush=True,
    )
    return 0 if failed == 0 and remaining == 0 else 1


if __name__ == "__main__":
    if sys.argv[1:] == ["--retry-failed"]:
        raise SystemExit(asyncio.run(retry_failed_captcha_accounts()))
    if sys.argv[1:]:
        raise SystemExit("unsupported_recovery_option")
    raise SystemExit(asyncio.run(main()))
