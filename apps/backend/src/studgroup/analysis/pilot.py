"""Audited pilot transitions and an immediate non-destructive rollback path."""

from decimal import Decimal

from studgroup.analysis.routing import AnalysisMode, configure_group_mode, get_route
from studgroup.models import GroupAnalysisModeHistory

ALLOWED_TRANSITIONS = {
    AnalysisMode.LEGACY: {AnalysisMode.SHADOW},
    AnalysisMode.SHADOW: {AnalysisMode.LEGACY, AnalysisMode.NEW},
    AnalysisMode.NEW: {AnalysisMode.LEGACY, AnalysisMode.SHADOW},
}


async def transition_mode(
    db,
    *,
    group_id,
    target_mode,
    actor,
    reason,
    now,
    gate_approved=False,
    shadow_daily_budget_usd=Decimal(0),
    shadow_sample_percent=100,
):
    if not actor.strip() or not reason.strip():
        raise ValueError("mode_transition_requires_actor_and_reason")
    current = await get_route(db, group_id)
    target = AnalysisMode(target_mode)
    if target == current.mode:
        return current
    if target not in ALLOWED_TRANSITIONS[current.mode]:
        raise ValueError("analysis_mode_transition_not_allowed")
    route = await configure_group_mode(
        db,
        group_id,
        target,
        now,
        shadow_daily_budget_usd=shadow_daily_budget_usd,
        shadow_sample_percent=shadow_sample_percent,
        new_writer_gate_approved=gate_approved,
    )
    db.add(
        GroupAnalysisModeHistory(
            group_id=group_id,
            previous_mode=current.mode.value,
            new_mode=target.value,
            actor=actor,
            reason=reason,
            created_at=now,
        )
    )
    return route


async def emergency_rollback(db, *, group_id, actor, reason, now):
    current = await get_route(db, group_id)
    if current.mode == AnalysisMode.LEGACY:
        return current
    return await transition_mode(
        db,
        group_id=group_id,
        target_mode=AnalysisMode.LEGACY,
        actor=actor,
        reason=reason,
        now=now,
    )
