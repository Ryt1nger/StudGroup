"""Read-only preflight for a bounded backfill wave: counts, sizes and cost only.

Nothing here writes, calls a model or reads attachment contents; the output holds ids, dates,
counts and money, never message text. The unit is exactly the one ``backfill.enqueue_window``
queues: one stored source revision = one package = one paid run.
"""

import json
from collections import Counter
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import func, select

from studgroup.analysis import backfill
from studgroup.models import (
    Group,
    GroupAnalysisSettings,
    MessageAttachment,
    RawMessage,
    ShadowEvaluationRun,
)

PACKAGE_CAP_USD = Decimal("0.04")
DAILY_FALLBACK_USD = Decimal(0)


def _utc(value):
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _marker(row):
    try:
        value = json.loads(row.metrics_json or "{}")
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def _covered(raw=RawMessage):
    return (
        select(ShadowEvaluationRun.id)
        .where(
            ShadowEvaluationRun.raw_message_id == raw.id,
            ShadowEvaluationRun.source_revision == raw.revision,
            ShadowEvaluationRun.analysis_generation == raw.analysis_generation,
        )
        .exists()
    )


def _coverage(rows):
    """Where a selection falls: days and topics only, with the message ids for the owner."""
    days = Counter(_utc(row.message_date).date().isoformat() for row in rows)
    topics = Counter(row.message_thread_id for row in rows)
    ordered = sorted(rows, key=lambda row: (row.message_date, row.telegram_message_id))
    return {
        "distinct_days": len(days),
        "distinct_topics": len(topics),
        "per_day": dict(sorted(days.items())),
        "per_topic": {("general" if key is None else str(key)): n for key, n in topics.items()},
        "telegram_message_ids": [row.telegram_message_id for row in ordered],
    }


def _composition(rows, attachment_counts):
    texts = [(row.text or "").strip() for row in rows]
    with_text = sum(1 for text in texts if text)
    files_only = sum(
        1 for row, text in zip(rows, texts, strict=True) if not text and attachment_counts.get(row.id)
    )
    return {
        "messages": len(rows),
        "with_text": with_text,
        "files_without_text": files_only,
        "empty_without_files": len(rows) - with_text - files_only,
        "text_under_30_chars": sum(1 for text in texts if 0 < len(text) < 30),
        "text_30_to_200_chars": sum(1 for text in texts if 30 <= len(text) <= 200),
        "text_over_200_chars": sum(1 for text in texts if len(text) > 200),
        "replies": sum(1 for row in rows if row.reply_to_message_id is not None),
        "with_own_files": sum(1 for row in rows if attachment_counts.get(row.id)),
    }


async def wave_preflight(db, settings, now=None, *, since=None, wave_size=25):
    now = _utc(now or datetime.now(UTC))
    group = await db.scalar(
        select(Group)
        .join(GroupAnalysisSettings, GroupAnalysisSettings.group_id == Group.id)
        .where(
            GroupAnalysisSettings.analysis_mode == "shadow",
            Group.status == "active",
            Group.pilot_authorized.is_(True),
            Group.bot_added_at.is_not(None),
        )
        .order_by(Group.id)
        .limit(1)
    )
    if group is None:
        return {"status": "group_unavailable"}
    since = since or datetime(2026, 10, 8, tzinfo=UTC)
    lower, now = backfill._window(group, since, now)
    route = await db.get(GroupAnalysisSettings, group.id)
    eligible_filter = backfill._eligible_raw(group.id, lower, now)

    eligible = int(
        await db.scalar(select(func.count(RawMessage.id)).where(*eligible_filter)) or 0
    )
    day_counts = Counter()
    for (moment,) in (
        await db.execute(select(RawMessage.message_date).where(*eligible_filter))
    ).all():
        day_counts[_utc(moment).date().isoformat()] += 1
    covered_states = {
        state: int(count)
        for state, count in (
            await db.execute(
                select(ShadowEvaluationRun.state, func.count(ShadowEvaluationRun.id))
                .join(RawMessage, RawMessage.id == ShadowEvaluationRun.raw_message_id)
                .where(
                    *eligible_filter,
                    ShadowEvaluationRun.source_revision == RawMessage.revision,
                    ShadowEvaluationRun.analysis_generation == RawMessage.analysis_generation,
                )
                .group_by(ShadowEvaluationRun.state)
            )
        ).all()
    }
    stale_failed = int(
        await db.scalar(
            select(func.count(ShadowEvaluationRun.id))
            .join(RawMessage, RawMessage.id == ShadowEvaluationRun.raw_message_id)
            .where(*eligible_filter, ShadowEvaluationRun.state == "failed")
        )
        or 0
    )
    uncovered_total = int(
        await db.scalar(
            select(func.count(RawMessage.id)).where(*eligible_filter, ~_covered())
        )
        or 0
    )
    all_uncovered_rows = (
        await db.scalars(select(RawMessage).where(*eligible_filter, ~_covered()))
    ).all()
    spread = bool(getattr(settings, "ai_shadow_backfill_spread", False))
    if spread:
        next_rows = backfill.select_spread(all_uncovered_rows, wave_size)
    else:
        next_rows = sorted(
            all_uncovered_rows, key=lambda row: (row.message_date, row.telegram_message_id)
        )[:wave_size]
    attachment_counts = {
        raw_id: count
        for raw_id, count in (
            await db.execute(
                select(MessageAttachment.raw_message_id, func.count())
                .where(
                    MessageAttachment.group_id == group.id, MessageAttachment.active.is_(True)
                )
                .group_by(MessageAttachment.raw_message_id)
            )
        ).all()
    }
    average = await db.scalar(
        select(func.avg(ShadowEvaluationRun.charged_usd)).where(
            ShadowEvaluationRun.group_id == group.id,
            ShadowEvaluationRun.state == "completed",
            ShadowEvaluationRun.charged_usd > 0,
        )
    )
    measured = Decimal(str(average)) if average is not None else backfill.FALLBACK_RUN_COST_USD
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
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
    daily_limit = Decimal(route.shadow_daily_budget_usd) if route else DAILY_FALLBACK_USD
    available = max(Decimal(0), daily_limit - charged_today - reserved_today)

    wave = await _wave_rows(db, group, now)
    blockers = []
    if settings.ai_enabled:
        blockers.append("legacy_ai_not_disabled")
    if settings.ai_shadow_enabled:
        blockers.append("shadow_not_disabled_during_preflight")
    if not settings.ai_shadow_canary_only:
        blockers.append("canary_only_disabled")
    if getattr(settings, "ai_output_repair_enabled", False):
        blockers.append("ai_repair_enabled")
    if route is None or route.analysis_mode != "shadow":
        blockers.append("route_not_shadow")
    if route is not None and route.shadow_sample_percent < 100:
        blockers.append("sample_percent_below_100_would_skip_packages")
    if wave["active_leases"]:
        blockers.append("active_ai_lease_present")
    if wave["foreign_prepared_rows"]:
        blockers.append("conflicting_prepared_rows")
    if wave["claimable_failed_rows"]:
        blockers.append("failed_rows_claimable")
    if wave["prepared"] and wave["prepared"] != wave_size:
        blockers.append("wave_size_mismatch")
    if wave["max_package_upper_bound_usd"] and (
        Decimal(wave["max_package_upper_bound_usd"]) > PACKAGE_CAP_USD
    ):
        blockers.append("package_cap_exceeded")
    if wave["prepared"] and Decimal(wave["sum_upper_bound_usd"]) > available:
        blockers.append("route_budget_below_wave_upper_bound")
    if wave["envelope_errors"]:
        blockers.append("package_input_invalid")

    return {
        "status": "blocked" if blockers else "ready" if wave["prepared"] else "not_prepared",
        "blocking_codes": blockers,
        "window_start": lower.isoformat(),
        "unit": "one stored source revision = one package = one paid run",
        "eligible_messages": eligible,
        "messages_per_day": dict(sorted(day_counts.items())),
        "current_revision_runs_by_state": covered_states,
        "failed_runs_in_window": stale_failed,
        "failed_runs_are_excluded_and_not_retried": True,
        "uncovered_packages_total": uncovered_total,
        "uncovered_composition": _composition(all_uncovered_rows, attachment_counts),
        "next_wave_if_prepared": {
            "size": len(next_rows),
            "first_message_date": _utc(next_rows[0].message_date).isoformat() if next_rows else None,
            "last_message_date": _utc(next_rows[-1].message_date).isoformat() if next_rows else None,
            "first_telegram_message_id": next_rows[0].telegram_message_id if next_rows else None,
            "last_telegram_message_id": next_rows[-1].telegram_message_id if next_rows else None,
            "selection": "spread" if spread else "oldest_first",
            "composition": _composition(next_rows, attachment_counts),
            "coverage": _coverage(next_rows),
        },
        "cost": {
            "measured_cost_per_run_usd": str(measured.quantize(Decimal("0.00000001"))),
            "safety_factor": str(backfill.COST_SAFETY_FACTOR),
            "wave_estimate_usd": str(
                (measured * min(wave_size, uncovered_total) * backfill.COST_SAFETY_FACTOR).quantize(
                    Decimal("0.0001")
                )
            ),
            "all_uncovered_estimate_usd": str(
                (measured * uncovered_total * backfill.COST_SAFETY_FACTOR).quantize(
                    Decimal("0.0001")
                )
            ),
            "package_hard_cap_usd": str(PACKAGE_CAP_USD),
            "wave_hard_cap_usd": str(PACKAGE_CAP_USD * min(wave_size, uncovered_total)),
        },
        "budget": {
            "route_daily_limit_usd": str(daily_limit),
            "charged_today_usd": str(charged_today),
            "reserved_today_usd": str(reserved_today),
            "available_today_usd": str(available),
            "shadow_sample_percent": route.shadow_sample_percent if route else None,
        },
        "prepared_wave": wave,
    }


async def _wave_rows(db, group, now):
    from studgroup.processing_diagnostics import _canary_envelope

    rows = (
        await db.scalars(
            select(ShadowEvaluationRun).where(
                ShadowEvaluationRun.group_id == group.id,
                ShadowEvaluationRun.state == "queued",
                ShadowEvaluationRun.delete_at > now,
            )
        )
    ).all()
    wave_rows, foreign = [], 0
    for row in rows:
        marker = _marker(row)
        if marker.get("wave"):
            wave_rows.append(row)
        elif isinstance(marker.get("preparation_key"), str):
            foreign += 1
    envelopes, errors = [], 0
    for row in wave_rows:
        try:
            envelopes.append(_canary_envelope(row))
        except Exception:  # noqa: BLE001 -- stable code only, never retained values
            errors += 1
    claimable_failed = int(
        await db.scalar(
            select(func.count(ShadowEvaluationRun.id)).where(
                ShadowEvaluationRun.group_id == group.id,
                ShadowEvaluationRun.state == "failed",
                ShadowEvaluationRun.metrics_json.contains('"preparation_key"'),
            )
        )
        or 0
    )
    active = int(
        await db.scalar(
            select(func.count(ShadowEvaluationRun.id)).where(
                ShadowEvaluationRun.state == "running",
                ShadowEvaluationRun.lease_until.is_not(None),
                ShadowEvaluationRun.lease_until > now,
            )
        )
        or 0
    )

    def total(key):
        return sum((Decimal(item[key]) for item in envelopes), Decimal(0))

    targets = []
    if wave_rows:
        targets = (
            await db.scalars(
                select(RawMessage).where(RawMessage.id.in_([row.raw_message_id for row in wave_rows]))
            )
        ).all()
    memberships = []
    for row in wave_rows:
        try:
            memberships.append(
                {item["telegram_message_id"] for item in json.loads(row.input_json)["messages"]}
            )
        except (TypeError, ValueError, KeyError):
            memberships.append(set())
    appearances = Counter(mid for members in memberships for mid in members)
    shared = {mid for mid, count in appearances.items() if count > 1}
    by_raw = {row.id: row.telegram_message_id for row in targets}
    inside_other = sum(
        1
        for index, row in enumerate(wave_rows)
        if any(
            by_raw.get(row.raw_message_id) in members
            for other, members in enumerate(memberships)
            if other != index
        )
    )

    return {
        "prepared": len(wave_rows),
        "foreign_prepared_rows": foreign,
        "claimable_failed_rows": claimable_failed,
        "active_leases": active,
        "envelope_errors": errors,
        "coverage": _coverage(targets) if targets else None,
        "context_overlap": {
            "distinct_messages_in_all_packages": len(appearances),
            "messages_shared_by_several_packages": len(shared),
            "packages_sharing_context": sum(1 for members in memberships if members & shared),
            "targets_inside_another_package": inside_other,
        },
        "messages_in_packages": sum(item["message_count"] for item in envelopes),
        "attachments_in_packages": sum(item["attachment_count"] for item in envelopes),
        "largest_package_messages": max((item["message_count"] for item in envelopes), default=0),
        "source_text_utf8_bytes": sum(item["source_text_utf8_bytes"] for item in envelopes),
        "estimated_prompt_tokens": sum(item["estimated_prompt_tokens"] for item in envelopes),
        "sum_estimated_cost_usd": str(total("estimated_cost_usd")),
        "sum_upper_bound_usd": str(total("cost_upper_bound_usd")),
        "max_package_upper_bound_usd": str(
            max((Decimal(item["cost_upper_bound_usd"]) for item in envelopes), default=Decimal(0))
        ),
    }
