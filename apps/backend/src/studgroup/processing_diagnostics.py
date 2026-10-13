"""Read-only startup counters for owner testing; no source text, credentials or paid calls."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from studgroup.models import (
    AcademicDeadline,
    AIAttempt,
    AICandidate,
    AIControl,
    Group,
    GroupAIActivity,
    Homework,
    RawMessage,
    ShadowEvaluationRun,
)


async def _shadow_context_diagnostics(db, group_id):
    """Aggregate retained invalid outputs without emitting source or result values."""
    from studgroup.analysis.context_strategy import diagnose_context_output
    from studgroup.analysis.contracts import AnalysisInput

    rows = (
        await db.execute(
            select(
                ShadowEvaluationRun.created_at,
                ShadowEvaluationRun.input_json,
                ShadowEvaluationRun.context_result_json,
            )
            .where(
                ShadowEvaluationRun.group_id == group_id,
                ShadowEvaluationRun.last_error.in_(
                    ["invalid_shadow_context", "context_schema_invalid"]
                ),
            )
            .order_by(ShadowEvaluationRun.created_at.desc())
        )
    ).all()
    signatures = {}
    latest = []
    for created_at, input_json, output_json in rows:
        try:
            analysis_input = AnalysisInput.model_validate_json(input_json)
            diagnostic = diagnose_context_output(output_json, analysis_input)
        except Exception:  # noqa: BLE001 - diagnostic must remain non-blocking and value-free
            diagnostic = {"reason": "diagnostic_input_unavailable"}
        signature = json.dumps(diagnostic, ensure_ascii=True, sort_keys=True)
        signatures[signature] = signatures.get(signature, 0) + 1
        if len(latest) < 3:
            latest.append(
                {
                    "created_at": created_at.isoformat(),
                    "diagnostic": diagnostic,
                }
            )
    return {
        "total": len(rows),
        "signatures": [
            {"count": count, "diagnostic": json.loads(signature)}
            for signature, count in sorted(signatures.items())
        ],
        "latest_three": latest,
    }


async def snapshot(db, settings, now):
    from studgroup.processing import RESERVATION

    control = await db.get(AIControl, 1)
    spent = control.spent_usd if control else Decimal(0)
    result = {
        "ai_enabled": settings.ai_enabled,
        "ai_shadow_enabled": settings.ai_shadow_enabled,
        "ai_shadow_enabled_until": settings.ai_shadow_enabled_until.isoformat()
        if settings.ai_shadow_enabled_until
        else None,
        "schedule_enabled": settings.ai_schedule_enabled,
        "live_two_pass": settings.ai_live_two_pass,
        "mode": settings.processing_mode,
        "key_configured": bool(settings.deepseek_api_key.get_secret_value()),
        "spent_or_reserved_usd": str(spent),
        "total_limit_usd": settings.ai_total_budget_usd,
        "daily_limit_usd": settings.ai_daily_group_budget_usd,
        "hourly_limit_usd": settings.ai_hourly_group_budget_usd,
        "circuit_failure_streak": control.provider_failure_streak if control else 0,
        "circuit_open_until": control.provider_circuit_open_until.isoformat()
        if control and control.provider_circuit_open_until
        else None,
        "circuit_reason": control.provider_circuit_reason if control else None,
        "groups": [],
    }
    for group in (
        await db.scalars(
            select(Group).where(Group.pilot_authorized.is_(True), Group.status == "active")
        )
    ).all():

        async def count(model, *conditions, group_id=group.id):
            return await db.scalar(
                select(func.count())
                .select_from(model)
                .where(model.group_id == group_id, *conditions)
            )

        daily = await db.scalar(
            select(func.coalesce(func.sum(AIAttempt.charged_usd), 0)).where(
                AIAttempt.group_id == group.id,
                AIAttempt.created_at >= now.replace(hour=0, minute=0, second=0, microsecond=0),
            )
        )
        hourly = await db.scalar(
            select(func.coalesce(func.sum(AIAttempt.charged_usd), 0)).where(
                AIAttempt.group_id == group.id,
                AIAttempt.created_at >= now - timedelta(hours=1),
            )
        )
        latest = await db.scalar(
            select(func.max(RawMessage.message_date)).where(RawMessage.group_id == group.id)
        )
        live_latest = await db.scalar(
            select(func.max(RawMessage.live_received_at)).where(
                RawMessage.group_id == group.id, RawMessage.imported.is_(False)
            )
        )
        activity = await db.get(GroupAIActivity, group.id)
        shadow_states = dict(
            (
                await db.execute(
                    select(
                        ShadowEvaluationRun.state,
                        func.count(ShadowEvaluationRun.id),
                    )
                    .where(ShadowEvaluationRun.group_id == group.id)
                    .group_by(ShadowEvaluationRun.state)
                )
            ).all()
        )
        shadow_errors = dict(
            (
                await db.execute(
                    select(
                        ShadowEvaluationRun.last_error,
                        func.count(ShadowEvaluationRun.id),
                    )
                    .where(
                        ShadowEvaluationRun.group_id == group.id,
                        ShadowEvaluationRun.last_error.is_not(None),
                    )
                    .group_by(ShadowEvaluationRun.last_error)
                )
            ).all()
        )
        shadow_cost = await db.scalar(
            select(func.coalesce(func.sum(ShadowEvaluationRun.charged_usd), 0)).where(
                ShadowEvaluationRun.group_id == group.id
            )
        )
        shadow_context_diagnostics = await _shadow_context_diagnostics(db, group.id)
        result["groups"].append(
            {
                "id": str(group.id),
                "chat_id": group.telegram_chat_id,
                "raw": await count(RawMessage),
                "imported": await count(RawMessage, RawMessage.imported.is_(True)),
                "live": await count(RawMessage, RawMessage.imported.is_(False)),
                "bot_added_at": group.bot_added_at.isoformat() if group.bot_added_at else None,
                "bootstrap_scope": await count(
                    RawMessage,
                    RawMessage.message_date >= group.bot_added_at
                    if group.bot_added_at is not None
                    else False,
                ),
                "pending_live": await count(
                    RawMessage,
                    RawMessage.imported.is_(False),
                    RawMessage.processing_state == "pending",
                    RawMessage.delete_at > now,
                ),
                "live_missing_receipt": await count(
                    RawMessage,
                    RawMessage.imported.is_(False),
                    RawMessage.live_received_at.is_(None),
                ),
                "pending_imported": await count(
                    RawMessage,
                    RawMessage.imported.is_(True),
                    RawMessage.processing_state.in_(["pending", "needs_context"]),
                ),
                "homework": await count(Homework),
                "deadlines": await count(AcademicDeadline),
                "review": await count(AICandidate, AICandidate.state == "review"),
                "shadow_states": shadow_states,
                "shadow_errors": shadow_errors,
                "shadow_context_diagnostics": shadow_context_diagnostics,
                "shadow_charged_usd": str(shadow_cost),
                "last_source_at": latest.isoformat() if latest else None,
                "last_live_receipt": live_latest.isoformat() if live_latest else None,
                "last_activity_signal": activity.last_signal_at.isoformat() if activity else None,
                "activity_signals": activity.signal_count if activity else 0,
                "daily_spent_or_reserved_usd": str(daily),
                "hourly_spent_or_reserved_usd": str(hourly),
                "budget_blocks_next_request": spent + RESERVATION
                > Decimal(str(settings.ai_total_budget_usd))
                or Decimal(daily) + RESERVATION > Decimal(str(settings.ai_daily_group_budget_usd))
                or Decimal(hourly) + RESERVATION
                > Decimal(str(settings.ai_hourly_group_budget_usd)),
            }
        )
    return result


async def startup(engine, settings):
    if not settings.owner_update_notifications_enabled:
        return
    try:
        async with asyncio.timeout(5):
            async with AsyncSession(engine) as db:
                result = await snapshot(db, settings, datetime.now(UTC))
        print("AI_STARTUP_DIAGNOSTICS " + json.dumps(result, ensure_ascii=False), flush=True)
    except Exception as error:  # noqa: BLE001 -- diagnostics must not block startup or log private exception text
        print("AI_STARTUP_DIAGNOSTICS_UNAVAILABLE " + type(error).__name__, flush=True)
