"""Owner-only preview of how shadow proposals would look as student cards.

The preview runs the real card editor, resolver and projection (the same code a future
publication uses) inside ONE savepoint that is always rolled back: no canonical item, homework,
deadline, notification or outbox row is ever written. Message texts appear only in the owner's
private bot chat; nothing here is exposed through an HTTP API or logs.
"""

import json
import re
from collections import Counter
from zoneinfo import ZoneInfo

from sqlalchemy import select

from studgroup.analysis.backfill import (
    _expired,
    _links,
    _window,
    completed_runs,
    with_supplement_updates,
)
from studgroup.analysis.card_editor import edit_card
from studgroup.analysis.card_materials import derive_materials
from studgroup.analysis.contracts import AnalysisInput, ValidationResult
from studgroup.analysis.linking import reconcile_validation
from studgroup.analysis.writer import apply_validated_batch
from studgroup.models import (
    AcademicDeadline,
    CanonicalAcademicItem,
    CanonicalItemEvidence,
    CanonicalItemRevision,
    CanonicalProjectionLink,
    Group,
    Homework,
    RawMessage,
)

KIND_LABELS = {
    "homework": "Домашнее задание",
    "test": "Тест",
    "control_point": "Контрольная точка",
    "assessment": "Оценочная работа",
    "credit": "Зачёт",
    "exam": "Экзамен",
    "other_academic_deadline": "Учебный дедлайн",
}
SERIOUS_STATUS = "held"
MAX_RUNS = 120


RELATIVE_PHRASE = re.compile(
    r"(?:к|на|до)\s+следующ\w+\s+(?:пар\w*|занят\w*|недел\w*|урок\w*)", re.IGNORECASE
)


def reliability(deadline_at, date_only, basis, reasons, tz, texts=()):
    if deadline_at is None:
        relative = next((m.group(0) for t in texts if (m := RELATIVE_PHRASE.search(t or ""))), None)
        if relative:
            when, trust = f"срок не указан (в тексте: «{relative}»)", "относительный, дата не подтверждена"
        else:
            when, trust = "срок не указан", "неизвестен"
    else:
        deadline_at = deadline_at.astimezone(tz)
        when = f"{deadline_at:%d.%m.%Y}" + ("" if date_only else f" {deadline_at:%H:%M}")
        trust = {
            "explicit": "назван в сообщении",
            "schedule_inferred": "вычислен по расписанию, нужна проверка",
        }.get(basis, basis)
    if reasons:
        trust += "; есть вопросы к проверке"
    return when, trust


async def _item_state(db, group_id, raw_ids):
    rows = (
        await db.execute(
            select(CanonicalAcademicItem.id, CanonicalAcademicItem.revision)
            .join(CanonicalItemEvidence, CanonicalItemEvidence.item_id == CanonicalAcademicItem.id)
            .where(
                CanonicalAcademicItem.group_id == group_id,
                CanonicalItemEvidence.raw_message_id.in_(raw_ids),
            )
            .distinct()
        )
    ).all()
    return {item_id: revision for item_id, revision in rows}


async def build_preview(db, *, group_id, since, now, limit=60):
    """Entries oldest first; never commits anything."""
    group = await db.get(Group, group_id)
    lower, now = _window(group, since, now)
    entries, snapshots, by_item, review_marks = [], {}, {}, []
    outcomes = Counter()
    considered = 0
    async with db.begin_nested() as savepoint:
        try:
            for run, raw in await completed_runs(db, group_id, lower):
                if considered >= min(limit, MAX_RUNS):
                    break
                validation = ValidationResult.model_validate_json(run.validation_result_json)
                data = AnalysisInput.model_validate_json(run.input_json)
                # Idempotent: results produced before the reconciliation step get it here.
                validation = reconcile_validation(validation, data.messages)
                for message in data.messages:
                    snapshots[message.telegram_message_id] = message
                for rejected in validation.rejected:
                    # Every rejected proposal is surfaced: a cancellation or a malformed change
                    # must not vanish silently from the owner's review.
                    review_marks.append(
                        {
                            "target_message_id": raw.telegram_message_id,
                            "reason": rejected.reason_code,
                        }
                    )
                if not validation.accepted:
                    continue
                considered += 1
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
                validation = await with_supplement_updates(
                    db, group_id, validation, data, raw_by_id
                )
                links, blocked = await _links(db, group_id, validation, raw_by_id)
                for ordinal, proposal in zip(
                    validation.accepted_ordinals, validation.accepted, strict=True
                ):
                    card = edit_card(proposal, data.messages)
                    entry = {
                        "target_message_id": raw.telegram_message_id,
                        "ordinal": ordinal,
                        "proposal": proposal,
                        "card": card,
                        "expired": _expired(proposal, now),
                        "status": None,
                        "item_id": None,
                        "cited": sorted({ref.telegram_message_id for ref in proposal.evidence}),
                    }
                    entries.append(entry)
                    if ordinal in blocked:
                        entry["status"] = "blocked_ambiguous_existing_item"
                        continue
                    linked = links.get(ordinal)
                    effective = proposal
                    if linked is not None:
                        effective = proposal.model_copy(
                            update={
                                "action": "update",
                                "existing_item_id": linked.id,
                                "expected_item_revision": linked.revision,
                            }
                        )
                    raw_ids = [raw_by_id[i].id for i in entry["cited"] if i in raw_by_id]
                    before = await _item_state(db, group_id, raw_ids)
                    result = await apply_validated_batch(
                        db,
                        group_id=group_id,
                        validation=validation.model_copy(
                            update={
                                "accepted": [effective],
                                "accepted_ordinals": [ordinal],
                                "rejected": [],
                            }
                        ),
                        messages=data.messages,
                        now=now,
                        emit_notifications=False,
                    )
                    status = result.outcomes[0].application_status
                    entry["status"] = status
                    outcomes[status] += 1
                    after = await _item_state(db, group_id, raw_ids)
                    touched = [i for i, rev in after.items() if before.get(i) != rev]
                    if touched:
                        entry["item_id"] = touched[0]
                    elif status in {"unchanged", "updated"} and effective.existing_item_id:
                        entry["item_id"] = effective.existing_item_id
                    if entry["item_id"] is not None:
                        by_item.setdefault(entry["item_id"], []).append(entry)
            final = await _collect_items(db, by_item, snapshots)
        finally:
            await savepoint.rollback()
    cards = _assemble(entries, final, snapshots, group.telegram_chat_id, ZoneInfo(group.timezone))
    return {"cards": cards, "outcomes": dict(outcomes), "review_marks": review_marks}


async def _collect_items(db, by_item, snapshots):
    final = {}
    for item_id in by_item:
        item = await db.get(CanonicalAcademicItem, item_id)
        link = await db.get(CanonicalProjectionLink, item_id)
        projected = None
        if link is not None:
            model = Homework if link.entity_type == "homework" else AcademicDeadline
            projected = await db.get(model, link.entity_id)
        revisions = (
            await db.scalars(
                select(CanonicalItemRevision)
                .where(CanonicalItemRevision.item_id == item_id)
                .order_by(CanonicalItemRevision.item_revision)
            )
        ).all()
        evidence = (
            await db.execute(
                select(RawMessage.telegram_message_id, CanonicalItemEvidence.item_revision)
                .join(RawMessage, RawMessage.id == CanonicalItemEvidence.raw_message_id)
                .where(CanonicalItemEvidence.item_id == item_id)
            )
        ).all()
        final[item_id] = {
            "item": item,
            "projected": projected,
            "projection_type": link.entity_type if link else None,
            "revisions": [(r.item_revision, r.action) for r in revisions],
            "evidence": sorted({(mid, rev) for mid, rev in evidence}),
            "snapshot": (
                {
                    "title": item.title,
                    "description": item.description,
                    "subject": item.subject,
                    "kind": item.semantic_kind,
                    "deadline_at": item.deadline_at,
                    "deadline_date_only": item.deadline_date_only,
                    "deadline_basis": item.deadline_basis,
                    "audience_scope": item.audience_scope,
                    "audience_label": item.audience_label,
                    "reasons": json.loads(item.attention_reasons),
                    "state": item.state,
                    "revision": item.revision,
                }
            ),
        }
    return final


def _source_rows(ids, snapshots, chat_id, tz):
    from studgroup.analysis.review_text import telegram_link

    rows = []
    for message_id in ids:
        message = snapshots.get(message_id)
        if message is None:
            continue
        rows.append(
            {
                "telegram_message_id": message_id,
                "message_date": message.message_date.astimezone(tz),
                "text": message.text,
                "author_role": message.author_role,
                "reply_to": message.reply_to_message_id,
                "link": telegram_link(chat_id, message_id),
            }
        )
    return rows


def _assemble(entries, final, snapshots, chat_id, tz):
    cards, emitted = [], set()
    all_messages = sorted(snapshots.values(), key=lambda m: (m.message_date, m.telegram_message_id))
    for entry in entries:
        proposal, card = entry["proposal"], entry["card"]
        item_id = entry["item_id"]
        if item_id is not None:
            if item_id in emitted:
                continue
            emitted.add(item_id)
            data = final[item_id]
            snap = data["snapshot"]
            cited_ids = sorted({mid for mid, _ in data["evidence"]})
            status = (
                "отменена"
                if snap["state"] == "cancelled"
                else "будет показана"
                if snap["audience_scope"] not in {"personal", "unknown"}
                else "удержана"
            )
            body = {
                "title": snap["title"],
                "description": snap["description"],
                "subject": snap["subject"],
                "kind": snap["kind"],
                "deadline_at": snap["deadline_at"],
                "deadline_date_only": snap["deadline_date_only"],
                "deadline_basis": snap["deadline_basis"],
                "audience": {"scope": snap["audience_scope"], "label": snap["audience_label"]},
                "reasons": snap["reasons"],
                "projection": data["projection_type"],
                "revisions": data["revisions"],
                "needs_attention": bool(snap["reasons"]),
            }
        else:
            cited_ids = entry["cited"]
            status = (
                "удержана (нужна проверка старосты)"
                if card.visibility == "held"
                else "не применена: " + str(entry["status"])
            )
            body = {
                "title": card.title,
                "description": card.description,
                "subject": card.subject,
                "kind": proposal.semantic_kind,
                "deadline_at": proposal.deadline.at,
                "deadline_date_only": proposal.deadline.date_only,
                "deadline_basis": proposal.deadline.basis,
                "audience": proposal.audience.model_dump(),
                "reasons": card.attention_reasons,
                "projection": None,
                "revisions": [],
                "needs_attention": True,
            }
        cited = [snapshots[i] for i in cited_ids if i in snapshots]
        confirmed, candidates = derive_materials(cited, all_messages, chat_id)
        when, trust = reliability(
            body["deadline_at"],
            body["deadline_date_only"],
            body["deadline_basis"],
            body["reasons"],
            tz,
            [snapshots[i].text for i in cited_ids if i in snapshots],
        )
        cards.append(
            {
                **body,
                "type": KIND_LABELS[body["kind"]],
                "status": status,
                "when": when,
                "trust": trust,
                "expired": entry["expired"],
                "materials": confirmed,
                "candidates": candidates,
                "sources": _source_rows(cited_ids, snapshots, chat_id, tz),
                "target_message_id": entry["target_message_id"],
            }
        )
    return cards
