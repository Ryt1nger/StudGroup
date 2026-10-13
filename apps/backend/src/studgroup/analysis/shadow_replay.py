"""Owner-controlled historical shadow replay without product side effects."""

from collections import Counter
from datetime import UTC
from decimal import Decimal

from sqlalchemy import func, select

from studgroup.analysis.pilot import transition_mode
from studgroup.analysis.routing import AnalysisMode, get_route
from studgroup.analysis.shadow import failure_stage
from studgroup.models import Group, GroupAnalysisSettings, RawMessage, ShadowEvaluationRun


def _utc(value):
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


async def replay_summary(db, group_id, now):
    group = await db.get(Group, group_id)
    if group is None or group.bot_added_at is None:
        return None
    eligible = int(
        await db.scalar(
            select(func.count(RawMessage.id)).where(
                RawMessage.group_id == group_id,
                RawMessage.message_date >= group.bot_added_at,
                RawMessage.delete_at > now,
            )
        )
        or 0
    )
    existing = int(
        await db.scalar(
            select(func.count(ShadowEvaluationRun.id)).where(
                ShadowEvaluationRun.group_id == group_id
            )
        )
        or 0
    )
    return {"since": group.bot_added_at, "eligible": eligible, "existing": existing}


async def begin_replay(
    db,
    *,
    group_id,
    now,
    actor,
    daily_budget_usd,
    sample_percent,
):
    """Enter shadow and expose retained post-installation sources to its durable queue."""
    group = await db.get(Group, group_id, with_for_update=True)
    if (
        group is None
        or group.status != "active"
        or not group.pilot_authorized
        or group.bot_added_at is None
    ):
        raise ValueError("shadow_replay_group_not_available")
    existing = int(
        await db.scalar(
            select(func.count(ShadowEvaluationRun.id)).where(
                ShadowEvaluationRun.group_id == group_id
            )
        )
        or 0
    )
    if existing:
        raise ValueError("shadow_replay_already_started")
    route = await get_route(db, group_id)
    if route.mode != AnalysisMode.LEGACY:
        raise ValueError("shadow_replay_requires_legacy_mode")
    await transition_mode(
        db,
        group_id=group_id,
        target_mode=AnalysisMode.SHADOW,
        actor=actor,
        reason="owner-approved shadow replay from bot_added_at",
        now=now,
        shadow_daily_budget_usd=Decimal(daily_budget_usd),
        shadow_sample_percent=sample_percent,
    )
    settings = await db.get(GroupAnalysisSettings, group_id, with_for_update=True)
    # This timestamp is the durable source-eligibility boundary used by the queue.
    # The audited transition time remains in GroupAnalysisModeHistory.
    settings.updated_at = _utc(group.bot_added_at)
    await db.flush()
    return await replay_summary(db, group_id, now)


async def status(db, group_id):
    route = await get_route(db, group_id)
    rows = (
        await db.execute(
            select(
                ShadowEvaluationRun.state,
                func.count(ShadowEvaluationRun.id),
                func.coalesce(func.sum(ShadowEvaluationRun.prompt_tokens), 0),
                func.coalesce(func.sum(ShadowEvaluationRun.completion_tokens), 0),
                func.coalesce(func.sum(ShadowEvaluationRun.charged_usd), 0),
            )
            .where(ShadowEvaluationRun.group_id == group_id)
            .group_by(ShadowEvaluationRun.state)
        )
    ).all()
    counts = Counter({row[0]: int(row[1]) for row in rows})
    errors = dict(
        (
            await db.execute(
                select(
                    ShadowEvaluationRun.last_error,
                    func.count(ShadowEvaluationRun.id),
                )
                .where(
                    ShadowEvaluationRun.group_id == group_id,
                    ShadowEvaluationRun.last_error.is_not(None),
                )
                .group_by(ShadowEvaluationRun.last_error)
            )
        ).all()
    )
    errors_by_stage = Counter()
    for code, count in errors.items():
        errors_by_stage[failure_stage(code)] += int(count)
    return {
        "mode": route.mode.value,
        "budget": route.shadow_daily_budget_usd,
        "sample": route.shadow_sample_percent,
        "counts": counts,
        "errors": errors,
        "errors_by_stage": errors_by_stage,
        "prompt_tokens": sum(int(row[2]) for row in rows),
        "completion_tokens": sum(int(row[3]) for row in rows),
        "charged_usd": sum((Decimal(row[4]) for row in rows), Decimal(0)),
    }
