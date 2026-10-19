"""Live package writer: the new engine's worker for groups whose analysis mode is ``new``.

One iteration plans at most one package (a contiguous window of the chat with up to 24 target
messages plus read-only context) and stores it as a durable ``ShadowEvaluationRun`` row without a
raw message (package runs and per-message shadow runs never pick each other's rows). The run is
then leased, analysed in two passes by ``run_shadow`` under a budget reservation, and - in the SAME
transaction that stores the result - its accepted proposals go through the canonical resolver and
writer (``promote_run``) with the group's timetable subjects and intelligent-deadline context.
The target messages are marked ``completed`` only in that transaction, so a crash or a lost lease
re-runs the package and never publishes twice (idempotent resolver, one open run per group).

The legacy engine never touches ``new`` groups (``processing.claim`` excludes them), and this
worker never touches legacy/shadow groups: exactly one writer per group.
"""

import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from studgroup.ai import ProviderFailure
from studgroup.ai_schedule import window
from studgroup.analysis.backfill import promote_run
from studgroup.analysis.context import assemble_memory_context
from studgroup.analysis.contracts import AnalysisInput, SourceMessage
from studgroup.analysis.package_run import DEFAULT_MAX_TARGETS, plan_packages
from studgroup.analysis.shadow import ShadowPassFailure, run_shadow, usage_cost_usd
from studgroup.analysis.shadow_worker import (
    SHADOW_MAX_ATTEMPTS,
    SHADOW_RESERVATION_USD,
    _claim,
    _metrics,
    _utc,
    attempt_base,
)
from studgroup.analysis.subject_catalog import catalog_summary, load_subject_catalog
from studgroup.analysis.subject_resolver import Lesson
from studgroup.deadline_context import build_deadline_context
from studgroup.models import (
    CanonicalAcademicItem,
    Group,
    GroupAnalysisSettings,
    GroupKnowledgeFact,
    GroupTopic,
    Membership,
    MessageAttachment,
    RawMessage,
    SchedulePattern,
    ShadowEvaluationRun,
    User,
)
from studgroup.processing import incident, incident_once

OPEN_STATES = ("queued", "running", "retry", "budget_deferred")
CONTEXT_BEFORE = 10  # read-only messages before the first target
CONTEXT_AFTER = 8  # read-only messages after the last target (if they exist yet)
RUN_RETENTION = timedelta(days=30)
EXISTING_ITEMS_LIMIT = 30  # active cards offered to the model as update/cancel targets
EXISTING_ITEMS_WINDOW = timedelta(days=30)


def _gate(settings, now):
    if not settings.new_engine_enabled:
        return False
    until = settings.new_engine_enabled_until
    return until is None or _utc(until) > now


def _source_message(row, *, roles, topics, attachments):
    return SourceMessage(
        telegram_message_id=row.telegram_message_id,
        revision=row.revision,
        message_date=_utc(row.message_date),
        text=row.text,
        reply_to_message_id=row.reply_to_message_id,
        topic_id=row.message_thread_id,
        topic_name=topics.get(row.message_thread_id),
        author_alias=(
            f"member-{uuid.uuid5(row.group_id, str(row.sender_id)).hex[:12]}"
            if row.sender_id is not None
            else None
        ),
        author_role=roles.get(row.sender_id),
        attachments=attachments.get(row.id, []),
    )


async def _source_messages(db, group, rows):
    ids = [row.id for row in rows]
    attachments = {}
    for attachment in (
        await db.scalars(
            select(MessageAttachment).where(
                MessageAttachment.raw_message_id.in_(ids), MessageAttachment.active.is_(True)
            )
        )
    ).all():
        attachments.setdefault(attachment.raw_message_id, []).append(
            {
                "kind": attachment.kind,
                "mime_type": attachment.mime_type,
                "size_bytes": attachment.size_bytes,
                "media_group_id": attachment.media_group_id,
                "processing_state": attachment.processing_state,
                "file_name": (attachment.file_name or None) and attachment.file_name[:255],
            }
        )
    topics = {
        row.telegram_thread_id: row.name
        for row in (
            await db.scalars(select(GroupTopic).where(GroupTopic.group_id == group.id))
        ).all()
    }
    senders = {row.sender_id for row in rows if row.sender_id is not None}
    roles = {}
    if senders:
        for telegram_user_id, role in (
            await db.execute(
                select(User.telegram_user_id, Membership.role)
                .join(Membership, Membership.user_id == User.id)
                .where(
                    Membership.group_id == group.id,
                    Membership.status == "active",
                    User.telegram_user_id.in_(senders),
                )
            )
        ).all():
            roles[telegram_user_id] = role
    return [
        _source_message(row, roles=roles, topics=topics, attachments=attachments) for row in rows
    ]


def _eligible(group, settings, now):
    """Filters for pending targets; with the hourly schedule only messages before the slot."""
    filters = [
        RawMessage.group_id == group.id,
        RawMessage.processing_state == "pending",
        RawMessage.imported.is_(False),
        RawMessage.delete_at > now,
    ]
    if settings.ai_schedule_enabled:
        timing = window(now, settings.ai_schedule_timezone)
        if timing is None:
            return None
        filters += [
            RawMessage.live_received_at.is_not(None),
            RawMessage.live_received_at <= timing[0],
        ]
    if group.bot_added_at is not None:
        filters.append(RawMessage.message_date >= group.bot_added_at)
    return filters


async def _plan_next(db, group, route, settings, now):
    filters = _eligible(group, settings, now)
    if filters is None:
        return None
    pending = (
        await db.scalars(
            select(RawMessage)
            .where(*filters)
            .order_by(RawMessage.message_date, RawMessage.telegram_message_id)
            .limit(DEFAULT_MAX_TARGETS * 2)
        )
    ).all()
    if not pending:
        return None
    revision = pending[0].revision
    targets = [row for row in pending if row.revision == revision][:DEFAULT_MAX_TARGETS]
    first, last = targets[0].message_date, targets[-1].message_date
    live = [RawMessage.group_id == group.id, RawMessage.delete_at > now]
    if group.bot_added_at is not None:
        live.append(RawMessage.message_date >= group.bot_added_at)
    before = (
        await db.scalars(
            select(RawMessage)
            .where(*live, RawMessage.message_date < first)
            .order_by(RawMessage.message_date.desc(), RawMessage.telegram_message_id.desc())
            .limit(CONTEXT_BEFORE)
        )
    ).all()
    middle = (
        await db.scalars(
            select(RawMessage).where(
                *live, RawMessage.message_date >= first, RawMessage.message_date <= last
            )
        )
    ).all()
    after = (
        await db.scalars(
            select(RawMessage)
            .where(*live, RawMessage.message_date > last)
            .order_by(RawMessage.message_date, RawMessage.telegram_message_id)
            .limit(CONTEXT_AFTER)
        )
    ).all()
    rows = {row.id: row for row in [*before, *middle, *after]}
    messages = await _source_messages(db, group, list(rows.values()))
    facts = (
        await db.scalars(select(GroupKnowledgeFact).where(GroupKnowledgeFact.group_id == group.id))
    ).all()
    catalog = await load_subject_catalog(db, group.id, facts)
    packages = plan_packages(
        messages,
        group_id=group.id,
        timezone=group.timezone,
        target_ids=[row.telegram_message_id for row in targets],
        source_revision=revision,
        subject_catalog=catalog,
    )
    package = packages[0]
    # A reopened window (owner re-analysis) bumps the generation, so the same messages get a new
    # run key instead of being treated as the package that already ran.
    generation = max(
        row.analysis_generation for row in targets if row.telegram_message_id in package.target_ids
    )
    run_key = hashlib.sha256(f"{package.key}:{generation}".encode()).hexdigest()
    memory = assemble_memory_context(group.id, " ".join(row.text for row in messages), facts)
    return package, memory, revision, run_key, generation


async def enqueue_next_package(engine, settings, now=None):
    """Create the next package run of one ``new`` group; at most one open run per group."""
    now = _utc(now or datetime.now(UTC))
    async with AsyncSession(engine) as db:
        groups = (
            await db.execute(
                select(Group, GroupAnalysisSettings)
                .join(GroupAnalysisSettings, GroupAnalysisSettings.group_id == Group.id)
                .where(
                    GroupAnalysisSettings.analysis_mode == "new",
                    Group.status == "active",
                    Group.pilot_authorized.is_(True),
                )
                .order_by(Group.id)
            )
        ).all()
        for group, _route in groups:
            # The settings-row lock serializes enqueue/reserve decisions for this group.
            route = await db.scalar(
                select(GroupAnalysisSettings)
                .where(GroupAnalysisSettings.group_id == group.id)
                .with_for_update(skip_locked=True)
            )
            if route is None:
                continue
            open_run = await db.scalar(
                select(ShadowEvaluationRun.id).where(
                    ShadowEvaluationRun.group_id == group.id,
                    ShadowEvaluationRun.raw_message_id.is_(None),
                    ShadowEvaluationRun.state.in_(OPEN_STATES),
                    ShadowEvaluationRun.delete_at > now,
                )
            )
            if open_run is not None:
                continue
            planned = await _plan_next(db, group, route, settings, now)
            if planned is None:
                continue
            package, memory, revision, run_key, generation = planned
            existing = await db.scalar(
                select(ShadowEvaluationRun).where(
                    ShadowEvaluationRun.group_id == group.id,
                    ShadowEvaluationRun.raw_message_id.is_(None),
                    ShadowEvaluationRun.source_snapshot_sha256 == run_key,
                )
            )
            if existing is not None:
                # The very same package already ended without completing its targets: do not
                # loop on it. A failed run parks its targets as failed (visible, requeueable).
                await db.execute(
                    update(RawMessage)
                    .where(
                        RawMessage.group_id == group.id,
                        RawMessage.telegram_message_id.in_(package.target_ids),
                        RawMessage.revision == revision,
                        RawMessage.processing_state == "pending",
                    )
                    .values(
                        processing_state="failed" if existing.state == "failed" else "completed"
                    )
                )
                await db.commit()
                return "settled"
            db.add(
                ShadowEvaluationRun(
                    group_id=group.id,
                    batch_id=None,
                    job_id=None,
                    raw_message_id=None,
                    source_revision=revision,
                    analysis_generation=None,
                    source_snapshot_sha256=run_key,
                    engine_version=route.engine_version,
                    state="queued",
                    input_json=json.dumps(
                        package.analysis_input.model_dump(mode="json"),
                        ensure_ascii=False,
                        sort_keys=True,
                    ),
                    memory_json=json.dumps(memory, ensure_ascii=False, sort_keys=True),
                    metrics_json=json.dumps(
                        {
                            "package": {
                                "key": run_key,
                                "targets": list(package.target_ids),
                                "revision": revision,
                                "generation": generation,
                            }
                        },
                        sort_keys=True,
                    ),
                    attempts=0,
                    available_at=now,
                    prompt_tokens=0,
                    completion_tokens=0,
                    prompt_cache_hit_tokens=0,
                    reserved_usd=Decimal(0),
                    charged_usd=Decimal(0),
                    created_at=now,
                    delete_at=now + RUN_RETENTION,
                )
            )
            await db.commit()
            return "queued"
    return "idle"


async def _existing_items(db, group_id, now):
    """Trusted identities of the group's recent active cards (id + revision the model must copy).

    Without them the model cannot update or cancel an earlier card, and a later mention of the
    same task would become a second card. Read at run time so revisions are fresh.
    """
    rows = (
        await db.scalars(
            select(CanonicalAcademicItem)
            .where(
                CanonicalAcademicItem.group_id == group_id,
                CanonicalAcademicItem.state == "active",
                CanonicalAcademicItem.updated_at >= now - EXISTING_ITEMS_WINDOW,
            )
            .order_by(CanonicalAcademicItem.updated_at.desc(), CanonicalAcademicItem.id)
            .limit(EXISTING_ITEMS_LIMIT)
        )
    ).all()
    return [
        {
            "id": str(row.id),
            "revision": row.revision,
            "semantic_kind": row.semantic_kind,
            "subject": row.subject,
            "title": row.title[:160],
            "description": (row.description or "")[:300],
            "deadline_at": _utc(row.deadline_at).isoformat() if row.deadline_at else None,
            "deadline_basis": row.deadline_basis,
        }
        for row in rows
    ]


async def _subject_context(db, group):
    """The group's timetable in the shape the subject resolver needs (None without patterns)."""
    patterns = (
        await db.scalars(select(SchedulePattern).where(SchedulePattern.group_id == group.id))
    ).all()
    if not patterns:
        return None
    lessons = [
        Lesson(
            row.weekday,
            row.starts,
            row.ends,
            row.subject,
            None if row.week in (None, "all") else row.week,
        )
        for row in patterns
    ]
    return {
        "lessons": lessons,
        "names": sorted({row.subject for row in patterns}),
        "timezone": group.timezone,
        "covered_from": min(row.valid_from for row in patterns),
        "covered_to": max(row.valid_until for row in patterns),
        "first_week_anchor": group.first_week_anchor,
    }


def _targets(row):
    package = _metrics(row).get("package") or {}
    targets = package.get("targets")
    return [value for value in targets if isinstance(value, int)] if targets else []


async def _finish_targets(db, row, state):
    await db.execute(
        update(RawMessage)
        .where(
            RawMessage.group_id == row.group_id,
            RawMessage.telegram_message_id.in_(_targets(row)),
            RawMessage.revision == row.source_revision,
            RawMessage.processing_state == "pending",
        )
        .values(processing_state=state)
    )


async def process_package_next(engine, settings, provider=None, now=None):
    """Plan, run and apply one package of a ``new`` group; returns a short status string."""
    now = _utc(now or datetime.now(UTC))
    if not _gate(settings, now):
        return "disabled"
    queued = await enqueue_next_package(engine, settings, now)
    claimed = await _claim(engine, now, mode="new")
    if claimed is None:
        return "idle" if queued in {"idle", "settled"} else queued
    if isinstance(claimed, str):
        return claimed
    run_id, generation = claimed
    async with AsyncSession(engine) as db:
        row = await db.get(ShadowEvaluationRun, run_id)
        input_json, memory_json = row.input_json, row.memory_json
        existing_items = await _existing_items(db, row.group_id, now)
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
            existing_items=existing_items,
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
        failure = ShadowPassFailure("package_pipeline_error", True)
    async with AsyncSession(engine) as db:
        row = await db.scalar(
            select(ShadowEvaluationRun).where(ShadowEvaluationRun.id == run_id).with_for_update()
        )
        if row.state != "running" or row.attempts != generation:
            return "superseded"
        usage = result if result is not None else failure.usage
        actual = usage_cost_usd(usage) + (result.extra_charge_usd if result is not None else 0)
        row.prompt_tokens += usage.prompt_tokens
        row.completion_tokens += usage.completion_tokens
        row.prompt_cache_hit_tokens += usage.prompt_cache_hit_tokens
        row.reserved_usd = Decimal(0)
        row.lease_until = None
        group = await db.get(Group, row.group_id)
        if result is not None:
            row.charged_usd += actual
            row.context_result_json = result.context.model_dump_json()
            row.validation_result_json = result.validation.model_dump_json()
            row.provider_payload_hashes = json.dumps(result.provider_payload_hashes)
            carried = {k: v for k, v in _metrics(row).items() if k in ("package", "wave")}
            row.metrics_json = json.dumps(
                carried
                | {
                    "accepted_items": len(result.validation.accepted),
                    "rejected_items": len(result.validation.rejected),
                    "raw_proposal_count": result.validation.raw_proposal_count,
                    "adapter_actions": result.validation.adapter_actions,
                    "repair": result.repair,
                    "subject_catalog": catalog_summary(
                        AnalysisInput.model_validate_json(row.input_json).subject_catalog
                    ),
                },
                sort_keys=True,
            )
            row.latency_ms = result.latency_ms
            row.state = "completed"
            row.completed_at = now
            if result.validation.accepted:
                subjects = (
                    await db.scalars(
                        select(SchedulePattern.subject)
                        .where(SchedulePattern.group_id == group.id)
                        .distinct()
                    )
                ).all()
                promoted = await promote_run(
                    db,
                    group=group,
                    run=row,
                    validation=result.validation,
                    now=now,
                    include_expired=True,
                    apply=True,
                    actor="package-worker",
                    emit_notifications=settings.new_engine_notifications,
                    verified_subjects=tuple(subjects),
                    deadline_context=await build_deadline_context(db, group),
                    subject_context=await _subject_context(db, group),
                )
                if promoted != "runs_promoted":
                    # Evidence changed after the run: keep the validated proposals on the run for
                    # the owner to inspect/promote; nothing is silently dropped.
                    metrics = _metrics(row)
                    metrics["promotion_skipped"] = promoted
                    row.metrics_json = json.dumps(metrics, sort_keys=True)
            await _finish_targets(db, row, "completed")
            await incident(db, "ai_daily_group_budget_limit", now, recover=True)
            await db.commit()
            return "completed"
        # If the provider did not report usage, keep the full reservation as a
        # conservative charge unless it explicitly proved that no request ran.
        row.charged_usd += actual if failure.usage_complete else max(actual, SHADOW_RESERVATION_USD)
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
            await _finish_targets(db, row, "failed")
            await incident_once(db, "processing_exhausted", now, detail=failure.code)
            outcome = "failed"
        await db.commit()
        return outcome
