"""Enroll approved Telegram groups in the owner-funded pilot without paid activation.

Only routes missing from the database are created. Existing explicit routing choices are
left intact; operator can still turn the whole pilot off via OWNER_FUNDED_PILOT=false.
This helper never authorizes a group: only the verified headman claim can do that.
"""

from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from studgroup.analysis.contracts import ANALYSIS_ENGINE_VERSION
from studgroup.models import Group, GroupAnalysisSettings


async def ensure_route(db, group, now=None):
    """Idempotently create the live C+ route for an already authorized group."""
    # Serialize a fresh enrollment with other app instances and headman claims.
    # The locked row is the authority: a group may have been revoked since lookup.
    locked = await db.scalar(select(Group).where(Group.id == group.id).with_for_update())
    if locked is None or locked.status != "active" or not locked.pilot_authorized:
        return None
    route = await db.get(GroupAnalysisSettings, locked.id, with_for_update=True)
    if route is not None:
        return route
    route = GroupAnalysisSettings(
        group_id=group.id,
        analysis_mode="new",
        engine_version=ANALYSIS_ENGINE_VERSION,
        # Retained for schema compatibility; owner-funded requests are accounted
        # without a separately funded release or per-group monetary ceiling.
        shadow_daily_budget_usd=Decimal(0),
        shadow_sample_percent=0,
        updated_at=now or datetime.now(UTC),
    )
    db.add(route)
    await db.flush()
    return route


async def enroll_existing(engine):
    """Recover approved groups connected before automatic pilot enrollment existed."""
    async with AsyncSession(engine) as db:
        groups = (
            await db.scalars(
                select(Group)
                .where(Group.status == "active", Group.pilot_authorized.is_(True))
                .order_by(Group.id)
            )
        ).all()
        created = 0
        for group in groups:
            if await db.get(GroupAnalysisSettings, group.id) is None:
                await ensure_route(db, group)
                created += 1
        await db.commit()
    return created
