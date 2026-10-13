"""Owner-controlled window backfill: plan, enqueue, requeue, review and promote.

Everything here works on messages the bot actually stored (``bot_added_at`` and retention are
hard lower bounds; Telegram history is never fetched). All write operations are idempotent, resume
after interruption and default to dry-run. Promotion to student-visible cards goes only through
the existing canonical resolver/writer and respects manual locks, audience and evidence checks.
"""

import json
from collections import Counter
from datetime import UTC, timedelta
from decimal import Decimal

from sqlalchemy import func, select

from studgroup.analysis.contracts import AnalysisInput, ValidationResult
from studgroup.analysis.routing import get_route
from studgroup.analysis.writer import apply_validated_batch
from studgroup.models import (
    CanonicalAcademicItem,
    CanonicalItemEvidence,
    Group,
    RawMessage,
    ShadowEvaluationRun,
)

FALLBACK_RUN_COST_USD = Decimal("0.0025")
COST_SAFETY_FACTOR = Decimal("1.5")
EXPIRY_GRACE = timedelta(days=1)


def _utc(value):
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _window(group, since, now):
    """Never earlier than the moment the bot started storing the chat."""
    lower = _utc(since)
    if group.bot_added_at is not None:
        lower = max(lower, _utc(group.bot_added_at))
    return lower, _utc(now)


def _eligible_raw(group_id, lower, now):
    return (
        RawMessage.group_id == group_id,
        RawMessage.message_date >= lower,
        RawMessage.delete_at > now,
    )


async def plan(db, *, group_id, since, now):
    group = await db.get(Group, group_id)
    if group is None:
        raise ValueError("backfill_group_not_found")
    lower, now = _window(group, since, now)
    route = await get_route(db, group_id)
    eligible = int(
        await db.scalar(
            select(func.count(RawMessage.id)).where(*_eligible_raw(group_id, lower, now))
        )
        or 0
    )
    states = Counter(
        {
            state: int(count)
            for state, count in (
                await db.execute(
                    select(ShadowEvaluationRun.state, func.count(ShadowEvaluationRun.id))
                    .join(RawMessage, RawMessage.id == ShadowEvaluationRun.raw_message_id)
                    .where(
                        ShadowEvaluationRun.group_id == group_id,
                        RawMessage.message_date >= lower,
                    )
                    .group_by(ShadowEvaluationRun.state)
                )
            ).all()
        }
    )
    covered = int(
        await db.scalar(
            select(func.count(func.distinct(ShadowEvaluationRun.raw_message_id)))
            .join(RawMessage, RawMessage.id == ShadowEvaluationRun.raw_message_id)
            .where(ShadowEvaluationRun.group_id == group_id, RawMessage.message_date >= lower)
        )
        or 0
    )
    average = await db.scalar(
        select(func.avg(ShadowEvaluationRun.charged_usd)).where(
            ShadowEvaluationRun.group_id == group_id,
            ShadowEvaluationRun.state == "completed",
            ShadowEvaluationRun.charged_usd > 0,
        )
    )
    per_run = Decimal(str(average)) if average is not None else FALLBACK_RUN_COST_USD
    to_run = max(eligible - covered, 0) + states.get("failed", 0)
    return {
        "window_start": lower,
        "mode": route.mode.value,
        "shadow_daily_budget_usd": route.shadow_daily_budget_usd,
        "eligible_messages": eligible,
        "messages_without_run": max(eligible - covered, 0),
        "runs_by_state": dict(states),
        "measured_cost_per_run_usd": per_run,
        "estimated_cost_usd": (per_run * to_run * COST_SAFETY_FACTOR).quantize(Decimal("0.0001")),
        "hard_cap_usd": Decimal("0.04") * to_run,
        "days_at_daily_budget": (
            None
            if not route.shadow_daily_budget_usd
            else int(-(-(per_run * to_run * COST_SAFETY_FACTOR) // route.shadow_daily_budget_usd))
        ),
    }


async def enqueue_window(db, *, group_id, since, now, limit, apply=False):
    """Create missing shadow runs oldest-first. Idempotent: existing runs are never touched."""
    from studgroup.analysis.shadow_worker import enqueue_shadow
    from studgroup.processing import context_for

    group = await db.get(Group, group_id)
    if group is None:
        raise ValueError("backfill_group_not_found")
    lower, now = _window(group, since, now)
    route = await get_route(db, group_id)
    if not route.shadow_runs:
        raise ValueError("backfill_requires_shadow_mode")
    covered = select(ShadowEvaluationRun.id).where(
        ShadowEvaluationRun.raw_message_id == RawMessage.id,
        ShadowEvaluationRun.source_revision == RawMessage.revision,
        ShadowEvaluationRun.analysis_generation == RawMessage.analysis_generation,
    )
    rows = (
        await db.scalars(
            select(RawMessage)
            .where(*_eligible_raw(group_id, lower, now), ~covered.exists())
            .order_by(RawMessage.message_date, RawMessage.telegram_message_id)
            .limit(limit)
        )
    ).all()
    created = 0
    for raw in rows:
        if not apply:
            created += 1
            continue
        context, _schedule = await context_for(db, raw, group, now, semantic_neighborhood=True)
        row = await enqueue_shadow(db, group=group, job=None, raw=raw, context=context, now=now)
        created += row is not None and row.state == "queued"
    return {"would_enqueue" if not apply else "enqueued": created, "limit": limit}


async def requeue_failed(db, *, group_id, since, now, limit, apply=False, include_rejected=True):
    """Re-run runs lost to a pipeline defect (failed, or completed with only rejections).

    The stored input snapshot is reused, so the new run sees exactly the same sources.
    """
    group = await db.get(Group, group_id)
    lower, now = _window(group, since, now)
    rows = (
        await db.scalars(
            select(ShadowEvaluationRun)
            .join(RawMessage, RawMessage.id == ShadowEvaluationRun.raw_message_id)
            .where(
                ShadowEvaluationRun.group_id == group_id,
                RawMessage.message_date >= lower,
                ShadowEvaluationRun.state.in_(["failed", "completed"]),
            )
            .order_by(RawMessage.message_date, ShadowEvaluationRun.created_at)
        )
    ).all()
    picked = []
    for row in rows:
        if row.state == "failed" or include_rejected and _only_rejections(row):
            picked.append(row)
        if len(picked) >= limit:
            break
    if apply:
        for row in picked:
            row.state = "queued"
            row.attempts = 0
            row.available_at = now
            row.lease_until = None
            row.last_error = None
            row.context_result_json = None
            row.validation_result_json = None
            row.provider_payload_hashes = None
            row.metrics_json = None
            row.completed_at = None
            row.reserved_usd = Decimal(0)
    return {"would_requeue" if not apply else "requeued": len(picked)}


def _only_rejections(row):
    try:
        validation = json.loads(row.validation_result_json or "")
    except ValueError:
        return False
    return bool(validation.get("rejected")) and not validation.get("accepted")


def _promoted(run):
    try:
        return "promotion" in json.loads(run.metrics_json or "{}")
    except ValueError:
        return False


async def completed_runs(db, group_id, lower):
    return (
        await db.execute(
            select(ShadowEvaluationRun, RawMessage)
            .join(RawMessage, RawMessage.id == ShadowEvaluationRun.raw_message_id)
            .where(
                ShadowEvaluationRun.group_id == group_id,
                ShadowEvaluationRun.state == "completed",
                ShadowEvaluationRun.validation_result_json.is_not(None),
                RawMessage.message_date >= lower,
            )
            .order_by(RawMessage.message_date, ShadowEvaluationRun.created_at)
        )
    ).all()


async def review(db, *, group_id, since, now, limit=50):
    """Accepted proposals with their cited sources, for owner inspection (contains chat text)."""
    from studgroup.analysis.card_editor import edit_card

    group = await db.get(Group, group_id)
    lower, now = _window(group, since, now)
    out = []
    for run, raw in await completed_runs(db, group_id, lower):
        validation = ValidationResult.model_validate_json(run.validation_result_json)
        if not validation.accepted:
            continue
        data = AnalysisInput.model_validate_json(run.input_json)
        by_id = {row.telegram_message_id: row for row in data.messages}
        for ordinal, proposal in zip(validation.accepted_ordinals, validation.accepted,
                                     strict=True):
            card = edit_card(proposal, data.messages)
            out.append(
                {
                    "run_id": str(run.id),
                    "ordinal": ordinal,
                    "target_message_id": raw.telegram_message_id,
                    "promoted": _promoted(run),
                    "visibility": card.visibility,
                    "attention_reasons": card.attention_reasons,
                    "action": proposal.action,
                    "kind": proposal.semantic_kind,
                    "subject": proposal.subject,
                    "title": card.title,
                    "description": card.description,
                    "deadline_at": proposal.deadline.at,
                    "deadline_date_only": proposal.deadline.date_only,
                    "deadline_basis": proposal.deadline.basis,
                    "audience": proposal.audience.model_dump(),
                    "sources": [
                        {
                            "telegram_message_id": ref.telegram_message_id,
                            "message_date": by_id[ref.telegram_message_id].message_date,
                            "text": by_id[ref.telegram_message_id].text,
                            "fact_paths": ref.fact_paths,
                        }
                        for ref in proposal.evidence
                    ],
                }
            )
            if len(out) >= limit:
                return out
    return out


def _expired(proposal, now):
    deadline = proposal.deadline
    if deadline.at is None or proposal.action == "cancel":
        return False
    end = _utc(deadline.at) + (EXPIRY_GRACE if deadline.date_only else timedelta(0))
    return end < now


def _same_subject(left, right):
    if not left or not right:
        return True
    return " ".join(left.casefold().split()) == " ".join(right.casefold().split())


async def _links(db, group_id, validation, raw_by_telegram_id):
    """Evidence-based identity for repeated discussion of one item across runs.

    A create whose cited messages already back exactly one active item of the same
    destination, kind and subject becomes an update of it. Ambiguity blocks the proposal
    instead of guessing.
    """
    links, blocked = {}, set()
    claimed = set()
    for ordinal, proposal in zip(validation.accepted_ordinals, validation.accepted, strict=True):
        if proposal.action != "create":
            continue
        raw_ids = [
            raw_by_telegram_id[ref.telegram_message_id].id
            for ref in proposal.evidence
            if ref.telegram_message_id in raw_by_telegram_id
        ]
        if not raw_ids:
            continue
        items = (
            await db.scalars(
                select(CanonicalAcademicItem)
                .join(CanonicalItemEvidence, CanonicalItemEvidence.item_id == CanonicalAcademicItem.id)
                .where(
                    CanonicalAcademicItem.group_id == group_id,
                    CanonicalAcademicItem.state == "active",
                    CanonicalAcademicItem.destination == proposal.destination,
                    CanonicalAcademicItem.semantic_kind == proposal.semantic_kind,
                    CanonicalItemEvidence.raw_message_id.in_(raw_ids),
                )
                .distinct()
            )
        ).all()
        items = [item for item in items if _same_subject(item.subject, proposal.subject)]
        if not items:
            continue
        if len(items) > 1 or items[0].id in claimed:
            blocked.add(ordinal)
            continue
        claimed.add(items[0].id)
        links[ordinal] = items[0]
    return links, blocked


async def promote(
    db,
    *,
    group_id,
    since,
    now,
    limit,
    apply=False,
    include_expired=False,
    actor="owner",
    emit_notifications=False,
):
    """Apply accepted shadow proposals to canonical items/projections, oldest run first.

    Dry-run executes the real resolver/projection inside a rolled-back savepoint, so the
    report shows genuine outcomes. Each run is atomic and marked as promoted exactly once.
    """
    group = await db.get(Group, group_id)
    lower, now = _window(group, since, now)
    totals = Counter()
    outcomes = Counter()
    for run, _raw in await completed_runs(db, group_id, lower):
        validation = ValidationResult.model_validate_json(run.validation_result_json)
        if not validation.accepted:
            totals["runs_without_cards"] += 1
            continue
        if _promoted(run):
            totals["runs_already_promoted"] += 1
            continue
        if totals["runs_considered"] >= limit:
            break
        totals["runs_considered"] += 1
        data = AnalysisInput.model_validate_json(run.input_json)
        try:
            async with db.begin_nested() as savepoint:
                raw_rows = (
                    await db.scalars(
                        select(RawMessage).where(
                            RawMessage.group_id == group_id,
                            RawMessage.telegram_message_id.in_(
                                {row.telegram_message_id for row in data.messages}
                            ),
                        )
                    )
                ).all()
                raw_by_id = {row.telegram_message_id: row for row in raw_rows}
                links, blocked = await _links(db, group_id, validation, raw_by_id)
                kept, kept_ordinals = [], []
                for ordinal, proposal in zip(
                    validation.accepted_ordinals, validation.accepted, strict=True
                ):
                    if ordinal in blocked:
                        outcomes["blocked_ambiguous_existing_item"] += 1
                        continue
                    if not include_expired and _expired(proposal, now):
                        outcomes["skipped_expired"] += 1
                        continue
                    item = links.get(ordinal)
                    if item is not None:
                        proposal = proposal.model_copy(
                            update={
                                "action": "update",
                                "existing_item_id": item.id,
                                "expected_item_revision": item.revision,
                            }
                        )
                        outcomes["linked_to_existing_item"] += 1
                    kept.append(proposal)
                    kept_ordinals.append(ordinal)
                result = await apply_validated_batch(
                    db,
                    group_id=group_id,
                    validation=validation.model_copy(
                        update={
                            "accepted": kept,
                            "accepted_ordinals": kept_ordinals,
                            "rejected": [],
                        }
                    ),
                    messages=data.messages,
                    now=now,
                    emit_notifications=emit_notifications,
                )
                for outcome in result.outcomes:
                    outcomes[outcome.application_status] += 1
                if apply:
                    metrics = json.loads(run.metrics_json or "{}")
                    metrics["promotion"] = {
                        "at": now.isoformat(),
                        "actor": actor[:64],
                        "statuses": dict(Counter(o.application_status for o in result.outcomes)),
                    }
                    run.metrics_json = json.dumps(metrics, sort_keys=True)
                else:
                    await savepoint.rollback()
            totals["runs_promoted" if apply else "runs_would_promote"] += 1
        except ValueError as error:
            # Stale or missing evidence (a source was edited/deleted after the run).
            totals["runs_skipped_" + str(error)[:40]] += 1
    return {"totals": dict(totals), "outcomes": dict(sorted(outcomes.items())), "applied": apply}
