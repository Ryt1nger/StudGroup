"""Read-only startup counters for owner testing; no source text, credentials or paid calls."""

import asyncio
import json
import math
import re
from collections import Counter
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from studgroup.models import (
    AcademicDeadline,
    AIAttempt,
    AICandidate,
    AIControl,
    AIJob,
    BotOutbox,
    BotPanel,
    CanonicalAcademicItem,
    Group,
    GroupAIActivity,
    GroupAnalysisSettings,
    Homework,
    Notification,
    RawMessage,
    ShadowEvaluationRun,
)

CANARY_INCREMENTAL_CAP_USD = Decimal("0.04000000")
CONTEXT_OUTPUT_REENCODING_FACTOR = 8


def _request_size(request):
    payload = json.dumps(request.user_payload, ensure_ascii=False)
    return len(request.system_prompt.encode("utf-8")) + len(payload.encode("utf-8"))


async def _group_bot_outbox_count(db, telegram_chat_id):
    """Resolve outbox ownership internally without returning payloads or chat identifiers."""
    rows = (
        await db.execute(
            select(BotOutbox.id, BotOutbox.payload, BotPanel.chat_id).outerjoin(
                BotPanel, BotPanel.id == BotOutbox.panel_id
            )
        )
    ).all()
    matched = set()
    for row_id, payload_json, panel_chat_id in rows:
        if panel_chat_id == telegram_chat_id:
            matched.add(row_id)
            continue
        try:
            payload = json.loads(payload_json)
        except (TypeError, ValueError):
            continue
        if isinstance(payload, dict) and payload.get("chat_id") == telegram_chat_id:
            matched.add(row_id)
    return len(matched)


def _canary_envelope(row):
    """Return value-free request sizing for one retained shadow run."""
    from studgroup.analysis.context_strategy import prepare_context_request
    from studgroup.analysis.contracts import AnalysisInput
    from studgroup.analysis.extraction_strategy import prepare_extraction_request

    analysis_input = AnalysisInput.model_validate_json(row.input_json)
    memory = json.loads(row.memory_json or "[]")
    context_request = prepare_context_request(analysis_input, memory)
    extraction_request = prepare_extraction_request(analysis_input, {})
    context_bytes = _request_size(context_request)
    extraction_base_bytes = _request_size(extraction_request)
    context_output_cap = context_request.max_output_tokens
    completion_cap = context_output_cap + extraction_request.max_output_tokens

    # bytes/3 is a practical estimate for this mixed Russian/JSON payload. The
    # separate upper bound treats every prepared byte as one token and permits
    # bounded expansion when pass-one JSON is parsed and embedded in pass two.
    estimated_prompt_tokens = (
        math.ceil(context_bytes / 3)
        + math.ceil(extraction_base_bytes / 3)
        + context_output_cap
    )
    prompt_token_upper_bound = (
        context_bytes
        + extraction_base_bytes
        + context_output_cap * CONTEXT_OUTPUT_REENCODING_FACTOR
    )
    estimated_cost = (
        Decimal(estimated_prompt_tokens) * Decimal("0.30")
        + Decimal(completion_cap) * Decimal("1.20")
    ) / Decimal(1_000_000)
    cost_upper_bound = (
        Decimal(prompt_token_upper_bound) * Decimal("0.30")
        + Decimal(completion_cap) * Decimal("1.20")
    ) / Decimal(1_000_000)
    return {
        "message_count": len(analysis_input.messages),
        "attachment_count": sum(len(message.attachments) for message in analysis_input.messages),
        "source_text_utf8_bytes": sum(
            len(message.text.encode("utf-8")) for message in analysis_input.messages
        ),
        "analysis_input_json_bytes": len(row.input_json.encode("utf-8")),
        "memory_json_bytes": len((row.memory_json or "[]").encode("utf-8")),
        "context_request_bytes": context_bytes,
        "extraction_request_base_bytes": extraction_base_bytes,
        "estimated_prompt_tokens": estimated_prompt_tokens,
        "prompt_token_upper_bound": prompt_token_upper_bound,
        "completion_token_cap": completion_cap,
        "estimated_cost_usd": str(estimated_cost.quantize(Decimal("0.00000001"))),
        "cost_upper_bound_usd": str(cost_upper_bound.quantize(Decimal("0.00000001"))),
    }


async def _shadow_canary_preflight(db, group, settings, now):
    """Read-only canary eligibility snapshot using the application's DB session."""
    from studgroup.analysis.shadow_worker import SHADOW_RESERVATION_USD

    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    route = await db.get(GroupAnalysisSettings, group.id)
    recoverable = (
        await db.scalars(
            select(ShadowEvaluationRun)
            .where(
                ShadowEvaluationRun.group_id == group.id,
                ShadowEvaluationRun.state == "running",
                ShadowEvaluationRun.lease_until.is_not(None),
                ShadowEvaluationRun.lease_until <= now,
                ShadowEvaluationRun.delete_at > now,
            )
            .order_by(ShadowEvaluationRun.created_at, ShadowEvaluationRun.id)
        )
    ).all()
    active_shadow = await db.scalar(
        select(func.count())
        .select_from(ShadowEvaluationRun)
        .where(
            ShadowEvaluationRun.state == "running",
            or_(
                ShadowEvaluationRun.lease_until.is_(None),
                ShadowEvaluationRun.lease_until > now,
            ),
        )
    )
    active_legacy = await db.scalar(
        select(func.count())
        .select_from(AIJob)
        .where(
            AIJob.state == "running",
            or_(AIJob.lease_until.is_(None), AIJob.lease_until > now),
        )
    )
    dormant_backlog = await db.scalar(
        select(func.count())
        .select_from(ShadowEvaluationRun)
        .where(
            ShadowEvaluationRun.group_id == group.id,
            ShadowEvaluationRun.state.in_(["queued", "retry", "budget_deferred"]),
            ShadowEvaluationRun.delete_at > now,
        )
    )
    charged_today = Decimal(
        await db.scalar(
            select(func.coalesce(func.sum(ShadowEvaluationRun.charged_usd), 0)).where(
                ShadowEvaluationRun.group_id == group.id,
                ShadowEvaluationRun.created_at >= day_start,
            )
        )
        or 0
    )
    reserved_today = Decimal(
        await db.scalar(
            select(func.coalesce(func.sum(ShadowEvaluationRun.reserved_usd), 0)).where(
                ShadowEvaluationRun.group_id == group.id,
                ShadowEvaluationRun.created_at >= day_start,
            )
        )
        or 0
    )
    selected = recoverable[0] if len(recoverable) == 1 else None
    selected_reservation = Decimal(selected.reserved_usd or 0) if selected else Decimal(0)
    reserved_elsewhere = max(Decimal(0), reserved_today - selected_reservation)
    budget = Decimal(route.shadow_daily_budget_usd) if route else Decimal(0)
    budget_available = max(Decimal(0), budget - charged_today - reserved_elsewhere)
    envelope = None
    envelope_error = None
    if selected is not None:
        try:
            envelope = _canary_envelope(selected)
        except Exception:  # noqa: BLE001 - emit a stable code, never retained values
            envelope_error = "canary_input_invalid"

    blockers = []
    if settings.ai_enabled:
        blockers.append("legacy_ai_not_disabled")
    if settings.ai_shadow_enabled:
        blockers.append("shadow_not_disabled_during_preflight")
    if not settings.ai_shadow_canary_only:
        blockers.append("canary_only_disabled")
    if route is None or route.analysis_mode != "shadow":
        blockers.append("route_not_shadow")
    if len(recoverable) != 1:
        blockers.append("recoverable_canary_count_mismatch")
    if active_shadow or active_legacy:
        blockers.append("active_ai_lease_present")
    if envelope_error:
        blockers.append(envelope_error)
    if budget_available < SHADOW_RESERVATION_USD:
        blockers.append("shadow_route_budget_insufficient")
    if envelope is not None and Decimal(envelope["cost_upper_bound_usd"]) > (
        CANARY_INCREMENTAL_CAP_USD
    ):
        blockers.append("owner_canary_cap_exceeded")

    launch_requirements = []
    if not settings.ai_shadow_enabled:
        launch_requirements.append("enable_shadow_for_bounded_window")
    if settings.ai_shadow_enabled_until is None or settings.ai_shadow_enabled_until <= now:
        launch_requirements.append("set_future_shadow_cutoff")

    return {
        "status": "ready_for_controlled_activation" if not blockers else "blocked",
        "blocking_codes": blockers,
        "launch_requirements": launch_requirements,
        "analysis_mode": route.analysis_mode if route else "unconfigured",
        "engine_version": route.engine_version if route else None,
        "recoverable_canary_count": len(recoverable),
        "active_shadow_lease_count": int(active_shadow or 0),
        "active_legacy_lease_count": int(active_legacy or 0),
        "dormant_shadow_backlog_count": int(dormant_backlog or 0),
        "selected_run_attempts": selected.attempts if selected else None,
        "selected_run_existing_prompt_tokens": selected.prompt_tokens if selected else None,
        "selected_run_existing_completion_tokens": selected.completion_tokens if selected else None,
        "selected_run_existing_charged_usd": str(selected.charged_usd) if selected else None,
        "selected_run_existing_reserved_usd": str(selected_reservation) if selected else None,
        "envelope": envelope,
        "budget": {
            "route_daily_limit_usd": str(budget),
            "charged_today_usd": str(charged_today),
            "reserved_today_usd": str(reserved_today),
            "reserved_elsewhere_usd": str(reserved_elsewhere),
            "available_for_canary_usd": str(budget_available),
            "worker_reservation_usd": str(SHADOW_RESERVATION_USD),
            "owner_incremental_cap_usd": str(CANARY_INCREMENTAL_CAP_USD),
        },
        "automatic_paid_retry_count": 0,
    }


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
    reasons = Counter()
    missing_fields = Counter()
    additional_fields = Counter()
    error_cases = Counter()
    error_occurrences = Counter()
    latest = []
    for created_at, input_json, output_json in rows:
        try:
            analysis_input = AnalysisInput.model_validate_json(input_json)
            diagnostic = diagnose_context_output(output_json, analysis_input)
        except Exception:  # noqa: BLE001 - diagnostic must remain non-blocking and value-free
            diagnostic = {"reason": "diagnostic_input_unavailable"}
        reasons[diagnostic["reason"]] += 1
        missing_fields.update(diagnostic.get("missing_top_level", []))
        additional_fields.update(diagnostic.get("additional_top_level", []))
        compact_errors = Counter()
        for error, occurrences in diagnostic.get("validation_errors", {}).items():
            compact = re.sub(r"\.\d+(?=\.|:)", "[]", error)
            compact_errors[compact] += occurrences
        error_cases.update(compact_errors.keys())
        error_occurrences.update(compact_errors)
        if len(latest) < 3:
            latest.append(
                {
                    "created_at": created_at.isoformat(),
                    "reason": diagnostic["reason"],
                    "missing_top_level": diagnostic.get("missing_top_level", []),
                    "additional_top_level": diagnostic.get("additional_top_level", []),
                    "validation_errors": dict(sorted(compact_errors.items())),
                }
            )
    return {
        "total": len(rows),
        "reason_cases": dict(sorted(reasons.items())),
        "missing_top_level_cases": dict(sorted(missing_fields.items())),
        "additional_top_level_cases": dict(sorted(additional_fields.items())),
        "validation_error_cases": dict(sorted(error_cases.items())),
        "validation_error_occurrences": dict(sorted(error_occurrences.items())),
        "latest_three": latest,
    }


async def snapshot(db, settings, now):
    from studgroup.processing import RESERVATION

    control = await db.get(AIControl, 1)
    spent = control.spent_usd if control else Decimal(0)
    result = {
        "ai_enabled": settings.ai_enabled,
        "ai_shadow_enabled": settings.ai_shadow_enabled,
        "ai_shadow_canary_only": settings.ai_shadow_canary_only,
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
        canary_preflight = await _shadow_canary_preflight(db, group, settings, now)
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
                "notifications": await count(Notification),
                "bot_outbox": await _group_bot_outbox_count(db, group.telegram_chat_id),
                "canonical_items": await count(CanonicalAcademicItem),
                "review": await count(AICandidate, AICandidate.state == "review"),
                "shadow_states": shadow_states,
                "shadow_errors": shadow_errors,
                "shadow_context_diagnostics": shadow_context_diagnostics,
                "shadow_canary_preflight": canary_preflight,
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
