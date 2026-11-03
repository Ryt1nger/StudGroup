"""One-time operator authorization for the first BI 1.2 screenshot pilot.

Only the explicit Render environment approval can activate this. It uses the
existing immutable release-budget ledger and never enables Telegram backfill,
automatic schedule publication or general AI-mode transitions.
"""

import hashlib
import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from studgroup.analysis.release_budget import configure
from studgroup.models import Group, GroupAnalysisSettings

GROUP_NAME = "би 1.2"
AMOUNT = Decimal("0.05")
MAX_WINDOW = timedelta(hours=48)
APPROVAL_MARKER = (
    "StudGroup|БИ 1.2|2026-11-03|user-approved|USD0.05|manual-vision-only|no-backfill"
)
APPROVAL_DIGEST = hashlib.sha256(APPROVAL_MARKER.encode()).hexdigest()
ENV_APPROVAL = "STUDGROUP_BI12_IMAGE_TEST_APPROVAL_SHA256"
ENV_SINCE = "STUDGROUP_BI12_IMAGE_TEST_SINCE_UTC"
ENV_UNTIL = "STUDGROUP_BI12_IMAGE_TEST_UNTIL_UTC"


async def apply(db, *, approval, since, until, now):
    """Require a unique active pilot group and an immutable 5-cent funding window."""
    if approval != APPROVAL_DIGEST:
        raise ValueError("bi12_vision_approval_mismatch")
    if any(dt.utcoffset() is None for dt in (since, until, now)):
        raise ValueError("bi12_vision_window_naive")
    since, until, now = (dt.astimezone(UTC) for dt in (since, until, now))
    if not since <= now < until or not timedelta(0) < until - since <= MAX_WINDOW:
        raise ValueError("bi12_vision_window_closed")
    groups = (
        await db.scalars(
            select(Group)
            .where(func.lower(func.trim(Group.name)) == GROUP_NAME)
            .with_for_update()
        )
    ).all()
    if len(groups) != 1:
        raise ValueError("bi12_vision_group_not_unique")
    group = groups[0]
    if group.status != "active" or not group.pilot_authorized:
        raise ValueError("bi12_vision_group_not_authorized")
    route = await db.get(GroupAnalysisSettings, group.id, with_for_update=True)
    if route is None:
        # A legacy-mode route is sufficient for paid staff-only uploads.
        # Zero shadow sampling prevents an accidental background rollout.
        route = GroupAnalysisSettings(
            group_id=group.id,
            analysis_mode="legacy",
            shadow_daily_budget_usd=AMOUNT,
            shadow_sample_percent=0,
            updated_at=now,
        )
        db.add(route)
        await db.flush()
    if route.paid_outcome_hold:
        raise ValueError("bi12_vision_paid_outcome_held")
    # All provider calls, including manual and automated, continue sharing the
    # already existing 5-cent total and hourly/daily ledger safeguards.
    if route.shadow_daily_budget_usd < AMOUNT:
        route.shadow_daily_budget_usd = AMOUNT
        route.updated_at = now
    await configure(
        db, group.id, amount=AMOUNT, since=since, until=until, receipt=approval
    )
    return {"state": "configured", "until": until.isoformat(), "max_usd": "0.05"}


async def startup(engine):
    """Opt in via Render env only; absent approval means zero writes."""
    approval = os.getenv(ENV_APPROVAL, "")
    if not approval:
        return
    since = datetime.fromisoformat(os.environ[ENV_SINCE])
    until = datetime.fromisoformat(os.environ[ENV_UNTIL])
    async with AsyncSession(engine) as db:
        outcome = await apply(db, approval=approval, since=since, until=until, now=datetime.now(UTC))
        await db.commit()
    print(
        f"BI12_MANUAL_VISION_BUDGET {outcome['state']} max_usd={outcome['max_usd']}"
        f" expires={outcome['until']}",
        flush=True,
    )
