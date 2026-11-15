"""One owner-authorized CAPTCHA recovery pilot; never restarts the full queue.

The job selector uses only consented active accounts that previously failed
with CAPTCHA. No student identifiers are rendered in GitHub Actions logs.
A successful, source-verified pilot clears the durable pause through the
existing guard; an unsuccessful pilot leaves it in place.
"""

import asyncio
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


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
