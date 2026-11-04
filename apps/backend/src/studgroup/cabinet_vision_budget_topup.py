"""One-time approved USD 1 top-up for BI 1.2 manual screenshot analysis.

The historical paid-call ledger is never reset. Funding stops on ambiguous
provider outcomes and remains idempotent across multiple worker restarts.
"""

import hashlib
import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from studgroup.analysis.release_budget import spent, utc
from studgroup.cabinet_vision_budget_bootstrap import APPROVAL_DIGEST as FIRST_RECEIPT
from studgroup.models import AnalysisBudgetCall, Group, GroupAnalysisSettings

GROUP_NAME = "би 1.2"
TOPUP = Decimal("1.00")
MARKER = "StudGroup|БИ 1.2|2026-11-04|user-approved|USD1.00|one-time-manual-vision-topup"
RECEIPT = hashlib.sha256(MARKER.encode()).hexdigest()
ENV = "STUDGROUP_BI12_MANUAL_VISION_TOPUP_SHA256"


async def apply(db, *, approval, now):
    if approval != RECEIPT:
        raise ValueError("bi12_topup_approval_mismatch")
    now = utc(now)
    groups = (
        await db.scalars(
            select(Group).where(func.lower(func.trim(Group.name)) == GROUP_NAME).with_for_update()
        )
    ).all()
    if len(groups) != 1:
        raise ValueError("bi12_topup_group_not_unique")
    group = groups[0]
    if group.status != "active" or not group.pilot_authorized:
        raise ValueError("bi12_topup_group_not_authorized")
    route = await db.get(GroupAnalysisSettings, group.id, with_for_update=True)
    if route is None:
        raise ValueError("bi12_topup_route_missing")

    # A second startup must never grant another dollar or extend the window.
    if route.release_budget_receipt == RECEIPT:
        return {"state": "already_configured", "limit": str(route.release_budget_usd)}
    if route.release_budget_receipt != FIRST_RECEIPT:
        raise ValueError("bi12_topup_unexpected_prior_receipt")
    if route.paid_outcome_hold:
        raise ValueError("bi12_topup_paid_outcome_held")
    unsettled = await db.scalar(
        select(AnalysisBudgetCall.id)
        .where(
            AnalysisBudgetCall.group_id == group.id,
            AnalysisBudgetCall.state.in_(("unknown", "reserved")),
        )
        .limit(1)
    )
    if unsettled:
        raise ValueError("bi12_topup_unreconciled_financial_call")

    previous_spend = await spent(db, group.id)
    # Enforce exactly one extra dollar in the same cumulative financial ledger.
    route.release_budget_usd = previous_spend + TOPUP
    route.release_budget_since = now
    route.release_budget_until = now + timedelta(hours=24)
    route.release_budget_receipt = RECEIPT
    # Cabinet-specific daily cap is doubled in RequestAccounting. A $1 soft
    # allowance is sufficient without changing caps for other analysis routes.
    route.shadow_daily_budget_usd = max(route.shadow_daily_budget_usd, Decimal("1.00"))
    route.updated_at = now
    return {"state": "configured", "limit": str(route.release_budget_usd)}


async def startup(engine):
    approval = os.environ.get(ENV, "")
    if not approval:
        return
    async with AsyncSession(engine) as db:
        outcome = await apply(db, approval=approval, now=datetime.now(UTC))
        await db.commit()
    print(f"BI12_MANUAL_TOPUP {outcome['state']} new_usd=1.00", flush=True)
