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

from sqlalchemy import func, select, update

from studgroup.analysis.contracts import AnalysisInput, ValidationResult
from studgroup.analysis.routing import get_route
from studgroup.analysis.stored import effective_validation
from studgroup.analysis.subject_catalog import load_current_catalog, with_catalog
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


def select_spread(rows, limit):
    """Deterministic spread over the whole window: every day, then every topic, no keywords.

    Slots go to days in proportion to their volume (at least one per day when ``limit`` allows;
    otherwise evenly spaced days). Inside a day the slots rotate over topics, busiest first, and
    each pick is the message farthest in time from those already chosen that day, so neighbouring
    messages (and their overlapping context) are avoided where the data allows it.
    """
    rows = sorted(rows, key=lambda row: (row.message_date, row.telegram_message_id))
    if limit <= 0 or not rows:
        return []
    if len(rows) <= limit:
        return rows
    days = {}
    for row in rows:
        days.setdefault(_utc(row.message_date).date(), []).append(row)
    ordered = sorted(days)
    if limit < len(ordered):
        chosen = [ordered[index * len(ordered) // limit] for index in range(limit)]
        quotas = {day: 1 for day in chosen}
    else:
        quotas = {day: 1 for day in ordered}
        spare = limit - len(ordered)
        total = len(rows)
        shares = {day: len(days[day]) * spare / total for day in ordered}
        for day in ordered:
            quotas[day] += int(shares[day])
        left = limit - sum(quotas.values())
        for day in sorted(ordered, key=lambda d: (-(shares[d] - int(shares[d])), d))[:left]:
            quotas[day] += 1
        for day in ordered:  # a day cannot give more than it holds; hand the rest on
            quotas[day] = min(quotas[day], len(days[day]))
        short = limit - sum(quotas.values())
        for day in sorted(ordered, key=lambda d: (-len(days[d]), d)):
            while short and quotas[day] < len(days[day]):
                quotas[day] += 1
                short -= 1
    picked = []
    for day in sorted(quotas):
        picked.extend(_spread_day(days[day], quotas[day]))
    return sorted(picked, key=lambda row: (row.message_date, row.telegram_message_id))


def _spread_day(rows, quota):
    position = {id(row): index for index, row in enumerate(rows)}
    topics = {}
    for row in rows:
        topics.setdefault(row.message_thread_id, []).append(row)
    queue = sorted(topics, key=lambda topic: (-len(topics[topic]), -1 if topic is None else topic))
    chosen = []
    while len(chosen) < quota:
        progressed = False
        for topic in queue:
            if len(chosen) >= quota:
                break
            remaining = [row for row in topics[topic] if row not in chosen]
            if not remaining:
                continue
            if not chosen:
                pick = remaining[len(remaining) // 2]
            else:
                pick = max(
                    remaining,
                    key=lambda row: (
                        min(abs(position[id(row)] - position[id(other)]) for other in chosen),
                        -position[id(row)],
                    ),
                )
            chosen.append(pick)
            progressed = True
        if not progressed:
            break
    return chosen


async def enqueue_window(
    db, *, group_id, since, now, limit, apply=False, marker=None, spread=False
):
    """Create missing shadow runs. Idempotent: existing runs are never touched.

    Oldest-first by default; ``spread`` picks the same number of messages across the whole window.
    """
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
            .limit(None if spread else limit)
        )
    ).all()
    if spread:
        rows = select_spread(rows, limit)
    created = 0
    for raw in rows:
        if not apply:
            created += 1
            continue
        context, _schedule = await context_for(db, raw, group, now, semantic_neighborhood=True)
        row = await enqueue_shadow(db, group=group, job=None, raw=raw, context=context, now=now)
        if row is not None and marker is not None:
            row.metrics_json = json.dumps(marker, sort_keys=True)
        created += row is not None and row.state == "queued"
    return {"would_enqueue" if not apply else "enqueued": created, "limit": limit}


REQUEUE_VERSION = "cplus-hybrid-1"
MAX_HISTORY = 5


def _metrics(row):
    try:
        value = json.loads(row.metrics_json or "{}")
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def _requeue_candidate(row, include_rejected, force):
    metrics = _metrics(row)
    if not force and metrics.get("requeue_version") == REQUEUE_VERSION:
        return False  # already re-run on this code: never loop paid calls
    if row.state == "failed":
        return True
    # Completed by the pre-adapter code with only rejections (no adapter metrics recorded).
    return include_rejected and "adapter_actions" not in metrics and _only_rejections(row)


async def requeue_failed(
    db, *, group_id, since, now, limit, apply=False, include_rejected=True, force=False
):
    """Re-run runs lost to the old pipeline defect, keeping every earlier result.

    The superseded outcome (state, error, context/validation JSON, hashes, metrics, cost so
    far) is appended to ``metrics.history`` before the row is reset; attempts, tokens and
    charges stay cumulative, so retry caps, leases and the daily budget remain truthful. A
    row is re-run at most once per ``REQUEUE_VERSION`` unless ``force`` is given. The stored
    input snapshot and a canary preparation marker are preserved.
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
            .with_for_update(skip_locked=True)
        )
    ).all()
    picked = [row for row in rows if _requeue_candidate(row, include_rejected, force)][:limit]
    if apply:
        # A requeue is an explicit re-analysis: it uses the group's subjects as they are now (the
        # messages stay frozen). The superseded outcome keeps its own catalog fingerprint in
        # ``history.metrics``. Run identity is unchanged, so no second run or card appears.
        catalog = await load_current_catalog(db, group_id) if picked else None
        for row in picked:
            if row.input_json:
                refreshed = with_catalog(row.input_json, catalog)
                if refreshed is not None:
                    row.input_json = refreshed
            metrics = _metrics(row)
            history = list(metrics.get("history", []))
            history.append(
                {
                    "superseded_at": now.isoformat(),
                    "state": row.state,
                    "last_error": row.last_error,
                    "attempts": row.attempts,
                    "context_result_json": row.context_result_json,
                    "validation_result_json": row.validation_result_json,
                    "provider_payload_hashes": row.provider_payload_hashes,
                    "metrics": {k: v for k, v in metrics.items() if k != "history"},
                    "prompt_tokens": row.prompt_tokens,
                    "completion_tokens": row.completion_tokens,
                    "charged_usd": str(row.charged_usd),
                    "completed_at": row.completed_at.isoformat() if row.completed_at else None,
                }
            )
            kept = {k: metrics[k] for k in ("preparation_key",) if k in metrics}
            row.metrics_json = json.dumps(
                kept
                | {
                    "history": history[-MAX_HISTORY:],
                    "attempt_base": row.attempts,
                    "requeue_version": REQUEUE_VERSION,
                },
                sort_keys=True,
            )
            row.state = "queued"
            row.available_at = now
            row.lease_until = None
            row.last_error = None
            row.context_result_json = None
            row.validation_result_json = None
            row.provider_payload_hashes = None
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
        validation = effective_validation(validation, data.messages, group.timezone)
        by_id = {row.telegram_message_id: row for row in data.messages}
        for ordinal, proposal in zip(
            validation.accepted_ordinals, validation.accepted, strict=True
        ):
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


async def with_supplement_updates(db, group_id, validation, data, raw_by_id):
    """A card that only clarifies an existing item (provable reply) becomes an update of it.

    Runs after the cross-run state is visible (earlier runs applied first), so a supplement from
    its own later run never becomes a second card. Only the evidence changes (the clarification
    joins as a cited source marked ``supplement_reply``); title, description, deadline, audience and
    subject stay the item's own. The item is flagged ``supplement_pending_integration`` only when
    the clarification says something the card does not already say.
    """
    from studgroup.analysis.card_editor import supplement_adds_information
    from studgroup.analysis.contracts import Audience, DeadlineValue, EvidenceRef
    from studgroup.analysis.linking import (
        PENDING_INTEGRATION,
        STRONG,
        SUPPLEMENT_PATH,
        _has_text,
        _text_supplement_ok,
        link_candidates,
    )

    by_id = {row.telegram_message_id: row for row in data.messages}
    strong = {}
    for link in link_candidates(data.messages):
        if link.strength == STRONG and link.relation == "reply":
            strong.setdefault(link.member, []).append(link)
    replaced = {}
    for ordinal, proposal in zip(validation.accepted_ordinals, validation.accepted, strict=True):
        if proposal.action != "create" or proposal.deadline.at is not None:
            continue
        anchors, proven = set(), True
        for ref in proposal.evidence:
            member = by_id[ref.telegram_message_id]
            good = [
                link
                for link in strong.get(member.telegram_message_id, [])
                if _has_text(by_id[link.anchor])
                and _text_supplement_ok(by_id[link.anchor], member, link)
            ]
            if not _has_text(member) or not good:
                proven = False
                break
            anchors.update(link.anchor for link in good)
        if not proven or len(anchors) != 1:
            continue
        anchor_id = next(iter(anchors))
        anchor_raw = raw_by_id.get(anchor_id)
        if anchor_raw is None or anchor_id in {r.telegram_message_id for r in proposal.evidence}:
            continue
        items = (
            await db.scalars(
                select(CanonicalAcademicItem)
                .join(
                    CanonicalItemEvidence, CanonicalItemEvidence.item_id == CanonicalAcademicItem.id
                )
                .where(
                    CanonicalAcademicItem.group_id == group_id,
                    CanonicalAcademicItem.state == "active",
                    CanonicalAcademicItem.destination == proposal.destination,
                    CanonicalAcademicItem.semantic_kind == proposal.semantic_kind,
                    CanonicalAcademicItem.audience_scope == proposal.audience.scope,
                    CanonicalItemEvidence.raw_message_id == anchor_raw.id,
                )
                .distinct()
            )
        ).all()
        items = [item for item in items if _same_subject(item.subject, proposal.subject)]
        if len(items) != 1:
            continue
        item = items[0]
        # The supplement joins the item as a cited source only; its raw text is not pasted into
        # the description. The item is flagged until the AI integrates the clarification.
        reasons = json.loads(item.attention_reasons)
        known = f"{item.title}\n{item.description}"
        if PENDING_INTEGRATION not in reasons and any(
            supplement_adds_information(known, by_id[ref.telegram_message_id].text or "")
            for ref in proposal.evidence
        ):
            reasons.append(PENDING_INTEGRATION)
        anchor = by_id[anchor_id]
        replaced[ordinal] = proposal.model_copy(
            update={
                "action": "update",
                "existing_item_id": item.id,
                "expected_item_revision": item.revision,
                "title": item.title,
                "description": item.description,
                "subject": item.subject,
                "deadline": DeadlineValue(
                    at=item.deadline_at,
                    date_only=item.deadline_date_only,
                    basis=item.deadline_basis,
                ),
                "audience": Audience(scope=item.audience_scope, label=item.audience_label),
                "attention_reasons": reasons,
                "evidence": [
                    EvidenceRef(
                        telegram_message_id=anchor_id,
                        revision=anchor.revision,
                        fact_paths=["description"],
                    ),
                    *[
                        EvidenceRef(
                            telegram_message_id=ref.telegram_message_id,
                            revision=ref.revision,
                            fact_paths=sorted({*ref.fact_paths, SUPPLEMENT_PATH}),
                        )
                        for ref in proposal.evidence
                    ],
                ],
            }
        )
    if not replaced:
        return validation
    return validation.model_copy(
        update={
            "accepted": [
                replaced.get(ordinal, proposal)
                for ordinal, proposal in zip(
                    validation.accepted_ordinals, validation.accepted, strict=True
                )
            ]
        }
    )


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
                .join(
                    CanonicalItemEvidence, CanonicalItemEvidence.item_id == CanonicalAcademicItem.id
                )
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


async def promote_run(
    db,
    *,
    group,
    run,
    validation,
    now,
    include_expired=False,
    apply=False,
    actor="owner",
    emit_notifications=False,
    outcomes=None,
    verified_subjects=(),
    deadline_context=None,
):
    """Apply one completed run's accepted proposals; returns the totals key for the run.

    Atomic per run (savepoint). ``verified_subjects`` and ``deadline_context`` are forwarded to the
    writer so the live path gets the same timetable-backed subject check and intelligent deadline
    policy; the owner CLI keeps its previous behaviour when they are not given.
    """
    outcomes = Counter() if outcomes is None else outcomes
    group_id = group.id
    data = AnalysisInput.model_validate_json(run.input_json)
    validation = effective_validation(validation, data.messages, group.timezone)
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
            validation = await with_supplement_updates(db, group_id, validation, data, raw_by_id)
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
                verified_subjects=verified_subjects,
                emit_notifications=emit_notifications,
                deadline_context=deadline_context,
            )
            for outcome in result.outcomes:
                outcomes[outcome.application_status] += 1
            if apply:
                metrics = json.loads(run.metrics_json or "{}")
                metrics["promotion"] = {
                    "at": now.isoformat(),
                    "actor": actor[:64],
                    "statuses": dict(Counter(o.application_status for o in result.outcomes)),
                    # why each proposal ended the way it did (codes only, no text), so a held or
                    # stale proposal is never unexplained
                    "reasons": dict(
                        Counter(f"{o.application_status}:{o.reason_code}" for o in result.outcomes)
                    ),
                }
                run.metrics_json = json.dumps(metrics, sort_keys=True)
            else:
                await savepoint.rollback()
    except ValueError as error:
        # Stale or missing evidence (a source was edited/deleted after the run).
        return "runs_skipped_" + str(error)[:40]
    return "runs_promoted" if apply else "runs_would_promote"


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
        key = await promote_run(
            db,
            group=group,
            run=run,
            validation=validation,
            now=now,
            include_expired=include_expired,
            apply=apply,
            actor=actor,
            emit_notifications=emit_notifications,
            outcomes=outcomes,
        )
        totals[key] += 1
    return {"totals": dict(totals), "outcomes": dict(sorted(outcomes.items())), "applied": apply}


async def reopen_window(db, *, group_id, since, now, apply=False):
    """Queue the already-stored live messages of a ``new`` group for a fresh package analysis.

    Only the processing state and generation of RawMessage rows change (``pending`` again, so the
    next package gets a new run key); no card, canonical
    item or notification is touched or deleted. Imported history and rows already expired by
    retention are never reopened. Dry-run unless ``apply``.
    """
    group = await db.get(Group, group_id)
    if group is None:
        raise ValueError("analysis_group_not_found")
    if (await get_route(db, group_id)).mode.value != "new":
        raise ValueError("reopen_requires_new_mode")
    lower, now = _window(group, since, now)
    scope = (
        *_eligible_raw(group_id, lower, now),
        RawMessage.imported.is_(False),
        RawMessage.processing_state.in_(["completed", "failed", "needs_context"]),
    )
    rows = (
        await db.execute(
            select(RawMessage.processing_state, func.count())
            .where(*scope)
            .group_by(RawMessage.processing_state)
        )
    ).all()
    counts = {state: number for state, number in rows}
    if apply and counts:
        await db.execute(
            update(RawMessage)
            .where(*scope)
            .values(
                processing_state="pending",
                analysis_generation=RawMessage.analysis_generation + 1,
            )
        )
    return {"would_reopen": counts, "reopened": apply and sum(counts.values()) or 0}
