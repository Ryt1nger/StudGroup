"""Rebuild timetable-derived deadlines after the headman edited the schedule (owner, 18 Oct 2026).

Called in the same transaction as the schedule save. Only active cards that still show a
``schedule_inferred`` deadline are touched; locked, explicit/relative/confirmed and already
archived deadlines are never changed. No notification is requested.
"""

import json

from sqlalchemy import select

from studgroup.analysis.deadline_application import REASON_CONFIRM_DEADLINE, REASON_SET_DEADLINE
from studgroup.analysis.deadline_policy import headman_request
from studgroup.analysis.deadline_updates import rebuild_inferred_deadlines
from studgroup.analysis.entities import item_snapshot
from studgroup.analysis.projection import project_item
from studgroup.deadline_context import build_deadline_context
from studgroup.models import (
    CanonicalAcademicItem,
    CanonicalDeadlineHistory,
    CanonicalItemEvidence,
    CanonicalItemRevision,
    CanonicalProjectionLink,
    RawMessage,
)


def _utc(value):
    from datetime import UTC

    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


async def rebuild_after_schedule_change(db, group, now):
    items = (
        await db.scalars(
            select(CanonicalAcademicItem).where(
                CanonicalAcademicItem.group_id == group.id,
                CanonicalAcademicItem.state == "active",
                CanonicalAcademicItem.deadline_basis == "schedule_inferred",
                CanonicalAcademicItem.deadline_at.is_not(None),
            )
        )
    ).all()
    cards, by_id = [], {}
    for item in items:
        rows = (
            await db.execute(
                select(RawMessage)
                .join(CanonicalItemEvidence, CanonicalItemEvidence.raw_message_id == RawMessage.id)
                .where(CanonicalItemEvidence.item_id == item.id)
            )
        ).scalars()
        sources = {
            row.id: (row.text, _utc(row.message_date)) for row in rows if row.text
        }  # expired source text cannot be recomputed: such a card is left as it is
        if not sources:
            continue
        by_id[item.id] = item
        cards.append(
            {
                "id": item.id,
                "kind": item.semantic_kind,
                "subject": item.subject,
                "sources": list(sources.values()),
                "basis": item.deadline_basis,
                "at": _utc(item.deadline_at),
                "locked_fields": set(json.loads(item.locked_fields or "[]")),
            }
        )
    if not cards:
        return []
    context = await build_deadline_context(db, group)
    changes = rebuild_inferred_deadlines(
        cards,
        now=_utc(now),
        timezone=context.timezone,
        schedule=context.schedule,
        no_lesson_days=context.no_lesson_days,
    )
    for change in changes:
        item = by_id[change.card_id]
        item.deadline_at = change.new_at
        item.deadline_date_only = False
        item.deadline_basis = change.basis
        item.deadline_reason = change.reason
        reasons = [
            r
            for r in json.loads(item.attention_reasons)
            if r not in {REASON_CONFIRM_DEADLINE, REASON_SET_DEADLINE}
        ]
        request = headman_request(_decision(change), item.semantic_kind) or (
            "set_deadline" if change.new_at is None else None
        )
        if request == "set_deadline":
            reasons.append(REASON_SET_DEADLINE)
        elif request == "confirm_inferred_deadline":
            reasons.append(REASON_CONFIRM_DEADLINE)
        item.attention_reasons = json.dumps(reasons, ensure_ascii=False)
        item.revision += 1
        item.updated_at = now
        db.add(
            CanonicalDeadlineHistory(
                item_id=item.id,
                item_revision=item.revision,
                deadline_at=item.deadline_at,
                deadline_date_only=item.deadline_date_only,
                deadline_basis=item.deadline_basis,
                deadline_reason=item.deadline_reason,
                timezone_confirmed=context.timezone_confirmed,
                changed_at=now,
            )
        )
        db.add(
            CanonicalItemRevision(
                item_id=item.id,
                item_revision=item.revision,
                action="schedule_change",
                actor="schedule_change",
                snapshot_json=item_snapshot(item),
                created_at=now,
            )
        )
        await db.flush()
        if await db.get(CanonicalProjectionLink, item.id):
            await project_item(db, item, now, writer_mode="new", emit_notifications=False)
    return changes


def _decision(change):
    from studgroup.analysis.deadline_policy import DeadlineDecision

    return DeadlineDecision(change.new_at, False, change.basis, change.reason)
