"""Owner-controlled historical shadow replay without product side effects."""

import json
from collections import Counter
from datetime import UTC
from decimal import Decimal

from sqlalchemy import func, select

from studgroup.analysis.contracts import ProposalSchemaIssue
from studgroup.analysis.pilot import transition_mode
from studgroup.analysis.routing import AnalysisMode, get_route
from studgroup.analysis.shadow import failure_stage
from studgroup.models import Group, GroupAnalysisSettings, RawMessage, ShadowEvaluationRun


def _utc(value):
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _json_object(value):
    if not value:
        return None
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _non_negative_int(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _metric(metrics, validation, name):
    value = _non_negative_int((metrics or {}).get(name))
    if value is not None:
        return value
    if not validation:
        return None
    if name == "raw_proposal_count":
        return _non_negative_int(validation.get(name))
    collection = validation.get("accepted" if name == "accepted_items" else "rejected")
    return len(collection) if isinstance(collection, list) else None


def _saved_pass_status(raw_value, parsed_value):
    if not raw_value:
        return "not_recorded"
    return "completed" if parsed_value is not None else "invalid_saved_result"


def _latest_completed_summary(row):
    if row is None:
        return None
    input_data = _json_object(row.input_json)
    context = _json_object(row.context_result_json)
    validation = _json_object(row.validation_result_json)
    metrics = _json_object(row.metrics_json)
    messages = (input_data or {}).get("messages")
    rejected = (validation or {}).get("rejected")
    rejection_reasons = Counter()
    schema_diagnostics = []
    schema_diagnostics_truncated = False
    if isinstance(rejected, list):
        for item in rejected:
            code = item.get("reason_code") if isinstance(item, dict) else None
            if isinstance(code, str) and code:
                rejection_reasons[code] += 1
            if not isinstance(item, dict) or code != "proposal_schema_invalid":
                continue
            ordinal = item.get("proposal_ordinal")
            raw_issues = item.get("schema_issues")
            if item.get("schema_issues_truncated") is True:
                schema_diagnostics_truncated = True
            if not isinstance(ordinal, int) or not 0 <= ordinal < 40:
                continue
            if not isinstance(raw_issues, list):
                continue
            for raw_issue in raw_issues:
                if len(schema_diagnostics) >= 8:
                    schema_diagnostics_truncated = True
                    break
                try:
                    issue = ProposalSchemaIssue.model_validate(raw_issue)
                except (TypeError, ValueError):
                    continue
                schema_diagnostics.append(
                    {
                        "proposal_ordinal": ordinal,
                        **issue.model_dump(mode="json"),
                    }
                )
    return {
        "processed_messages": len(messages) if isinstance(messages, list) else None,
        "found_entities": _metric(metrics, validation, "raw_proposal_count"),
        "accepted_items": _metric(metrics, validation, "accepted_items"),
        "rejected_items": _metric(metrics, validation, "rejected_items"),
        "rejection_reasons": dict(rejection_reasons),
        "schema_diagnostics": schema_diagnostics,
        "schema_diagnostics_truncated": schema_diagnostics_truncated,
        "prompt_tokens": row.prompt_tokens,
        "completion_tokens": row.completion_tokens,
        "charged_usd": row.charged_usd,
        "context_pass": _saved_pass_status(row.context_result_json, context),
        "extraction_pass": _saved_pass_status(row.validation_result_json, validation),
    }


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
    latest_completed = await db.scalar(
        select(ShadowEvaluationRun)
        .where(
            ShadowEvaluationRun.group_id == group_id,
            ShadowEvaluationRun.state == "completed",
        )
        .order_by(
            ShadowEvaluationRun.completed_at.desc(),
            ShadowEvaluationRun.created_at.desc(),
        )
        .limit(1)
    )
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
        "latest_completed": _latest_completed_summary(latest_completed),
    }
