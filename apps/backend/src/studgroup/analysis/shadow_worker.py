"""Durable C+ shadow queue. This module has no product publication dependency."""

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import delete, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from studgroup.ai import ProviderFailure
from studgroup.analysis.context import assemble_memory_context
from studgroup.analysis.contracts import AnalysisInput, SourceMessage
from studgroup.analysis.routing import get_route, sampled
from studgroup.analysis.shadow import (
    SHADOW_PACKAGE_CAP_USD,
    ShadowPassFailure,
    run_shadow,
    source_snapshot_sha256,
    usage_cost_usd,
)
from studgroup.models import (
    Group,
    GroupAnalysisSettings,
    GroupKnowledgeFact,
    RawMessage,
    ShadowEvaluationRun,
)

SHADOW_RESERVATION_USD = SHADOW_PACKAGE_CAP_USD
SHADOW_MAX_ATTEMPTS = 3
SHADOW_LEASE = timedelta(minutes=3)


CARRIED_METRICS = ("history", "attempt_base", "requeue_version", "wave")


def _metrics(row):
    try:
        value = json.loads(row.metrics_json or "{}")
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def attempt_base(row):
    """Attempts already spent before the latest owner requeue (attempts stay monotonic)."""
    value = _metrics(row).get("attempt_base", 0)
    return value if isinstance(value, int) and value >= 0 else 0


def _utc(value):
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


_cost = usage_cost_usd


async def purge_expired_shadow_runs(engine, now=None):
    now = _utc(now or datetime.now(UTC))
    async with AsyncSession(engine) as db:
        result = await db.execute(
            delete(ShadowEvaluationRun).where(ShadowEvaluationRun.delete_at <= now)
        )
        await db.commit()
        return result.rowcount or 0


async def enqueue_shadow(db, *, group, job, raw, context, now, screening=False):
    """Persist the exact legacy context as a replayable C+ input, at most once per job."""
    route = await get_route(db, group.id)
    sample_key = str(job.id) if job is not None else f"{raw.id}:{raw.revision}"
    if screening or not route.shadow_runs:
        return None
    existing = await db.scalar(
        select(ShadowEvaluationRun).where(
            ShadowEvaluationRun.raw_message_id == raw.id,
            ShadowEvaluationRun.source_revision == raw.revision,
            ShadowEvaluationRun.analysis_generation == raw.analysis_generation,
        )
    )
    if existing is not None:
        return existing
    source_ids = [row["message_id"] for row in context]
    raws = (
        await db.scalars(
            select(RawMessage).where(
                RawMessage.group_id == group.id,
                RawMessage.telegram_message_id.in_(source_ids),
            )
        )
    ).all()
    revisions = {row.telegram_message_id: row.revision for row in raws}
    messages = [
        SourceMessage(
            telegram_message_id=row["message_id"],
            revision=revisions[row["message_id"]],
            message_date=datetime.fromisoformat(row["message_date"]),
            text=row["text"],
            reply_to_message_id=row.get("reply_to"),
            topic_id=row.get("topic_id"),
            topic_name=row.get("topic_name"),
            attachments=row.get("attachments", []),
        )
        for row in context
    ]
    analysis_input = AnalysisInput(
        group_id=group.id,
        group_timezone=group.timezone,
        source_revision=raw.revision,
        target_message_ids=[raw.telegram_message_id],
        messages=messages,
    )
    facts = (
        await db.scalars(
            select(GroupKnowledgeFact).where(GroupKnowledgeFact.group_id == group.id)
        )
    ).all()
    memory = assemble_memory_context(
        group.id, " ".join(row.text for row in messages), facts
    )
    selected = sampled(group.id, sample_key, route.shadow_sample_percent)
    row = ShadowEvaluationRun(
        group_id=group.id,
        batch_id=None,
        job_id=job.id if job is not None else None,
        raw_message_id=raw.id,
        source_revision=raw.revision,
        analysis_generation=raw.analysis_generation,
        source_snapshot_sha256=source_snapshot_sha256(analysis_input),
        engine_version=route.engine_version,
        state="queued" if selected else "sample_skipped",
        input_json=json.dumps(
            analysis_input.model_dump(mode="json"), ensure_ascii=False, sort_keys=True
        ),
        memory_json=json.dumps(memory, ensure_ascii=False, sort_keys=True),
        attempts=0,
        available_at=now,
        prompt_tokens=0,
        completion_tokens=0,
        prompt_cache_hit_tokens=0,
        reserved_usd=Decimal(0),
        charged_usd=Decimal(0),
        created_at=now,
        completed_at=None if selected else now,
        delete_at=now + timedelta(days=30),
    )
    db.add(row)
    await db.flush()
    return row


async def enqueue_next_shadow(
    engine, now=None, *, preparation_key=None, preparation_message_id=None
):
    """Capture one new/edited Telegram source even if legacy relevance rejects it."""
    now = _utc(now or datetime.now(UTC))
    async with AsyncSession(engine) as db:
        preparation_marker = None
        preparation_group_id = None
        if preparation_key is not None:
            preparation_marker = json.dumps(
                {"preparation_key": preparation_key},
                sort_keys=True,
            )
            route_row = await db.scalar(
                select(GroupAnalysisSettings)
                .join(Group, Group.id == GroupAnalysisSettings.group_id)
                .where(
                    GroupAnalysisSettings.analysis_mode == "shadow",
                    Group.status == "active",
                    Group.pilot_authorized.is_(True),
                    Group.bot_added_at.is_not(None),
                )
                .order_by(GroupAnalysisSettings.group_id)
                .with_for_update()
                .limit(1)
            )
            if route_row is None:
                return "preparation_group_unavailable"
            preparation_group_id = route_row.group_id
            existing_preparation = await db.scalar(
                select(ShadowEvaluationRun).where(
                    ShadowEvaluationRun.group_id == preparation_group_id,
                    ShadowEvaluationRun.metrics_json == preparation_marker,
                    ShadowEvaluationRun.delete_at > now,
                )
            )
            if existing_preparation is not None:
                return "prepared_existing"
        raw = await db.scalar(
            select(RawMessage)
            .join(Group, Group.id == RawMessage.group_id)
            .join(
                GroupAnalysisSettings,
                GroupAnalysisSettings.group_id == RawMessage.group_id,
            )
            .where(
                GroupAnalysisSettings.analysis_mode == "shadow",
                GroupAnalysisSettings.shadow_sample_percent > 0,
                Group.status == "active",
                Group.pilot_authorized.is_(True),
                Group.bot_added_at.is_not(None),
                RawMessage.message_date >= Group.bot_added_at,
                RawMessage.delete_at > now,
                (
                    RawMessage.group_id == preparation_group_id
                    if preparation_group_id is not None
                    else True
                ),
                (
                    # An owner-chosen canary message replaces the "newer than the shadow
                    # switch" window; every other guard (bot_added_at, retention, no
                    # existing run for this revision, one marked package) still applies.
                    RawMessage.telegram_message_id == preparation_message_id
                    if preparation_message_id is not None and preparation_key is not None
                    else or_(
                        RawMessage.live_received_at >= GroupAnalysisSettings.updated_at,
                        (
                            RawMessage.imported.is_(True)
                            & (RawMessage.version_date >= GroupAnalysisSettings.updated_at)
                        ),
                    )
                ),
                ~exists().where(
                    ShadowEvaluationRun.raw_message_id == RawMessage.id,
                    ShadowEvaluationRun.source_revision == RawMessage.revision,
                    ShadowEvaluationRun.analysis_generation == RawMessage.analysis_generation,
                ),
            )
            .order_by(RawMessage.version_date, RawMessage.id)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if raw is None:
            return "preparation_empty" if preparation_key is not None else False
        group = await db.get(Group, raw.group_id)
        from studgroup.processing import context_for

        context, _schedule = await context_for(
            db, raw, group, now, semantic_neighborhood=True
        )
        row = await enqueue_shadow(
            db,
            group=group,
            job=None,
            raw=raw,
            context=context,
            now=now,
        )
        if row is not None and preparation_marker is not None:
            row.metrics_json = preparation_marker
        state = row.state if row is not None else None
        await db.commit()
        return f"prepared_{state}" if preparation_marker is not None else state


async def prepare_shadow_canary(engine, settings, now=None):
    """Enqueue one marked canary candidate without enabling either AI processor."""
    if not settings.ai_shadow_prepare_once_key:
        return "disabled"
    if settings.ai_enabled or settings.ai_shadow_enabled:
        return "blocked_ai_enabled"
    return await enqueue_next_shadow(
        engine,
        now=now,
        preparation_key=settings.ai_shadow_prepare_once_key,
        preparation_message_id=getattr(settings, "ai_shadow_prepare_message_id", None),
    )


async def prepare_backfill_wave(engine, settings, now=None):
    """Queue one bounded, marked wave of stored messages (oldest first) without enabling AI.

    The marked rows are claimed only by a canary-only worker, so the wave size is the hard
    bound on paid packages. The wave key is carried on the rows, so a restart with the same key
    never queues a second wave.
    """
    from studgroup.analysis import backfill

    key, limit = settings.ai_shadow_backfill_key, settings.ai_shadow_backfill_limit
    if not key or not limit:
        return "disabled"
    if settings.ai_enabled or settings.ai_shadow_enabled:
        return "blocked_ai_enabled"
    now = _utc(now or datetime.now(UTC))
    marker = {"preparation_key": key, "wave": key}
    async with AsyncSession(engine) as db:
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
            .with_for_update(of=Group)
            .limit(1)
        )
        if group is None:
            return "wave_group_unavailable"
        existing = await db.scalar(
            select(func.count(ShadowEvaluationRun.id)).where(
                ShadowEvaluationRun.group_id == group.id,
                ShadowEvaluationRun.metrics_json.contains(f'"wave": "{key}"'),
            )
        )
        if existing:
            return "wave_exists"
        result = await backfill.enqueue_window(
            db,
            group_id=group.id,
            since=group.bot_added_at,
            now=now,
            limit=limit,
            apply=True,
            marker=marker,
        )
        await db.commit()
        return f"wave_prepared_{result['enqueued']}"


async def _claim(engine, now, *, canary_only=False):
    async with AsyncSession(engine) as db:
        eligible_state = (
            or_(
                (
                    (ShadowEvaluationRun.state == "running")
                    & (ShadowEvaluationRun.lease_until <= now)
                ),
                (
                    (ShadowEvaluationRun.state == "queued")
                    & ShadowEvaluationRun.metrics_json.contains('"preparation_key"')
                ),
            )
            if canary_only
            else or_(
                (
                    ShadowEvaluationRun.state.in_(["queued", "retry", "budget_deferred"])
                    & (ShadowEvaluationRun.available_at <= now)
                ),
                (
                    ShadowEvaluationRun.state == "running"
                )
                & (ShadowEvaluationRun.lease_until <= now),
            )
        )
        row = await db.scalar(
            select(ShadowEvaluationRun)
            .join(
                GroupAnalysisSettings,
                GroupAnalysisSettings.group_id == ShadowEvaluationRun.group_id,
            )
            .where(
                GroupAnalysisSettings.analysis_mode == "shadow",
                ShadowEvaluationRun.delete_at > now,
                eligible_state,
            )
            .order_by(ShadowEvaluationRun.created_at, ShadowEvaluationRun.id)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if row is None:
            return None
        route_row = await db.scalar(
            select(GroupAnalysisSettings)
            .where(GroupAnalysisSettings.group_id == row.group_id)
            .with_for_update()
        )
        # The settings-row lock serializes reserve decisions for this group's cap.
        day = _utc(now)
        day_start = datetime(day.year, day.month, day.day, tzinfo=UTC)
        charged = await db.scalar(
            select(func.coalesce(func.sum(ShadowEvaluationRun.charged_usd), 0)).where(
                ShadowEvaluationRun.group_id == row.group_id,
                ShadowEvaluationRun.created_at >= day_start,
            )
        )
        reserved_elsewhere = await db.scalar(
            select(func.coalesce(func.sum(ShadowEvaluationRun.reserved_usd), 0)).where(
                ShadowEvaluationRun.group_id == row.group_id,
                ShadowEvaluationRun.created_at >= day_start,
                ShadowEvaluationRun.id != row.id,
            )
        )
        committed = Decimal(charged or 0) + Decimal(reserved_elsewhere or 0)
        if committed + SHADOW_RESERVATION_USD > Decimal(
            route_row.shadow_daily_budget_usd
        ):
            row.state = "budget_deferred"
            row.last_error = "shadow_budget_exhausted"
            row.available_at = day_start + timedelta(days=1)
            row.lease_until = None
            row.reserved_usd = Decimal(0)
            await db.commit()
            return "budget_deferred"
        row.state = "running"
        row.attempts += 1
        row.available_at = now
        row.lease_until = now + SHADOW_LEASE
        row.last_error = None
        row.reserved_usd = SHADOW_RESERVATION_USD
        identity = row.id, row.attempts
        await db.commit()
        return identity


async def process_shadow_next(engine, settings, provider=None, now=None):
    """Execute one reserved shadow item and persist diagnostics, never product data."""
    now = _utc(now or datetime.now(UTC))
    if not settings.ai_enabled and not settings.ai_shadow_enabled:
        return "disabled"
    if (
        settings.ai_shadow_enabled_until is not None
        and _utc(settings.ai_shadow_enabled_until) <= now
    ):
        return "disabled"
    claimed = await _claim(
        engine,
        now,
        canary_only=settings.ai_shadow_canary_only,
    )
    if claimed is None or isinstance(claimed, str):
        return claimed or "idle"
    run_id, generation = claimed
    async with AsyncSession(engine) as db:
        row = await db.get(ShadowEvaluationRun, run_id)
        input_json, memory_json = row.input_json, row.memory_json
    failure = None
    result = None
    try:
        analysis_input = AnalysisInput.model_validate_json(input_json)
        memory = json.loads(memory_json or "[]")
        if provider is None:
            from studgroup.ai import DeepSeekProvider

            provider = DeepSeekProvider(
                settings.deepseek_api_key.get_secret_value(),
                model=settings.deepseek_model,
                base_url=settings.deepseek_base_url,
                allow_technical_repair=settings.ai_technical_repair_enabled,
            )
        result = await run_shadow(
            provider,
            analysis_input,
            memory,
            repair_enabled=getattr(settings, "ai_output_repair_enabled", False),
        )
    except ShadowPassFailure as error:
        failure = error
    except ProviderFailure as error:
        failure = ShadowPassFailure(
            error.code,
            error.retryable,
            usage=error.usage,
            usage_complete=error.usage is not None or error.reservation_releasable,
            reservation_releasable=error.reservation_releasable,
        )
    except Exception:  # noqa: BLE001 - durable boundary must recover unknown worker faults
        # No exception text is persisted because provider errors may contain private data.
        failure = ShadowPassFailure("shadow_pipeline_error", True)
    async with AsyncSession(engine) as db:
        row = await db.scalar(
            select(ShadowEvaluationRun)
            .where(ShadowEvaluationRun.id == run_id)
            .with_for_update()
        )
        if row.state != "running" or row.attempts != generation:
            return "superseded"
        usage = result if result is not None else failure.usage
        actual = _cost(usage) + (result.extra_charge_usd if result is not None else 0)
        row.prompt_tokens += usage.prompt_tokens
        row.completion_tokens += usage.completion_tokens
        row.prompt_cache_hit_tokens += usage.prompt_cache_hit_tokens
        row.reserved_usd = Decimal(0)
        row.lease_until = None
        if result is not None:
            row.charged_usd += actual
            row.context_result_json = result.context.model_dump_json()
            row.validation_result_json = result.validation.model_dump_json()
            row.provider_payload_hashes = json.dumps(result.provider_payload_hashes)
            carried = {k: v for k, v in _metrics(row).items() if k in CARRIED_METRICS}
            row.metrics_json = json.dumps(
                carried
                | {
                    "accepted_items": len(result.validation.accepted),
                    "rejected_items": len(result.validation.rejected),
                    "raw_proposal_count": result.validation.raw_proposal_count,
                    "adapter_actions": result.validation.adapter_actions,
                    "repair": result.repair,
                },
                sort_keys=True,
            )
            row.latency_ms = result.latency_ms
            row.state = "completed"
            row.completed_at = now
            await db.commit()
            return "completed"
        # If the provider did not report usage, keep the full reservation as a
        # conservative charge unless it explicitly proved that no request ran.
        row.charged_usd += (
            actual
            if failure.usage_complete
            else max(actual, SHADOW_RESERVATION_USD)
        )
        row.provider_payload_hashes = json.dumps(failure.hashes)
        if failure.raw_results:
            row.context_result_json = failure.raw_results[0]
        if len(failure.raw_results) > 1:
            row.validation_result_json = failure.raw_results[1]
        row.last_error = failure.code
        if failure.retryable and row.attempts - attempt_base(row) < SHADOW_MAX_ATTEMPTS:
            row.state = "retry"
            row.available_at = now + timedelta(seconds=15 * 2 ** (row.attempts - 1))
            outcome = "retry"
        else:
            row.state = "failed"
            row.completed_at = now
            outcome = "failed"
        await db.commit()
        return outcome
