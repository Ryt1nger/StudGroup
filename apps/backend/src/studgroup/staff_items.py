"""Headman/deputy decisions on canonical card deadlines and field locks (owner, 18-19 Oct 2026).

A manual decision outranks unconfirmed AI: setting or confirming a deadline locks the ``deadline``
field, so neither the AI nor a timetable rebuild changes it afterwards. Every decision is a new
item revision with history; no notification is requested (spec section 8 is out of scope).
"""

import json
from datetime import datetime, time
from zoneinfo import ZoneInfo

from sqlalchemy import select

from studgroup.analysis.deadline_application import REASON_CONFIRM_DEADLINE, REASON_SET_DEADLINE
from studgroup.analysis.deadline_policy import staff_note
from studgroup.analysis.deadline_updates import LOCKABLE_FIELDS
from studgroup.deadline_rebuild import commit_item_revision
from studgroup.models import (
    CanonicalAcademicItem,
    CanonicalItemEvidence,
    CanonicalProjectionLink,
    RawMessage,
)
from studgroup.student_cards import (
    deadline_decision,
    deadline_label,
    locked_fields,
    source_detail,
    utc,
)

DEADLINE_REASONS = frozenset({REASON_SET_DEADLINE, REASON_CONFIRM_DEADLINE})
QUEUE_LIMIT = 200


class StaffItemError(Exception):
    def __init__(self, code, message):
        self.code, self.message = code, message


def attention_reasons(item):
    try:
        return list(json.loads(item.attention_reasons or "[]"))
    except ValueError:
        return []


async def find_item(db, group, item_id):
    item = await db.scalar(
        select(CanonicalAcademicItem)
        .where(CanonicalAcademicItem.id == item_id, CanonicalAcademicItem.group_id == group.id)
        .with_for_update()
    )
    if item is None:
        raise StaffItemError("not_found", "Карточка не найдена")
    return item


async def retire_canonical(db, entity_type, entity_id, now):
    """A card removed by the headman leaves the «needs a deadline» queue and is never re-opened.

    Only the state changes: no new revision is written, so nothing is re-projected to students.
    """
    link = await db.scalar(
        select(CanonicalProjectionLink).where(
            CanonicalProjectionLink.entity_type == entity_type,
            CanonicalProjectionLink.entity_id == entity_id,
        )
    )
    item = await db.get(CanonicalAcademicItem, link.item_id) if link else None
    if item is not None and item.state == "active":
        item.state = "cancelled"
        item.attention_reasons = "[]"
        item.updated_at = now


async def staff_view(db, group, item, now):
    rows = (
        await db.execute(
            select(RawMessage, CanonicalItemEvidence.source_revision)
            .join(CanonicalItemEvidence, CanonicalItemEvidence.raw_message_id == RawMessage.id)
            .where(
                CanonicalItemEvidence.item_id == item.id,
                CanonicalItemEvidence.item_revision == item.revision,
                RawMessage.group_id == group.id,
            )
            .order_by(RawMessage.message_date, RawMessage.telegram_message_id)
        )
    ).all()
    unique = {}
    for raw, revision in rows:
        unique.setdefault(raw.id, (raw, revision))
    locked = locked_fields(item)
    return {
        "id": item.id,
        "revision": item.revision,
        "state": item.state,
        "card_kind": item.semantic_kind,
        "destination": item.destination,
        "subject": item.subject,
        "title": item.title,
        "description": item.description,
        "deadline": {
            "at": utc(item.deadline_at) if item.deadline_at else None,
            "date_only": item.deadline_date_only,
            "basis": item.deadline_basis,
            "reason": item.deadline_reason,
            "timezone_confirmed": item.timezone_confirmed,
            "label": deadline_label(None, item),
        },
        "staff_note": None if "deadline" in locked else staff_note(deadline_decision(item)),
        "attention_reasons": attention_reasons(item),
        "locked_fields": sorted(locked),
        "sources": [source_detail(group, raw, now, rev) for raw, rev in unique.values()],
        "created_at": utc(item.created_at),
        "updated_at": utc(item.updated_at),
    }


async def deadline_queue(db, group):
    """Active cards waiting for the headman to set or confirm a deadline, oldest first."""
    items = (
        await db.scalars(
            select(CanonicalAcademicItem)
            .where(
                CanonicalAcademicItem.group_id == group.id,
                CanonicalAcademicItem.state == "active",
                CanonicalAcademicItem.attention_reasons != "[]",
            )
            .order_by(CanonicalAcademicItem.created_at, CanonicalAcademicItem.id)
        )
    ).all()
    return [item for item in items if DEADLINE_REASONS & set(attention_reasons(item))][:QUEUE_LIMIT]


def _check(item, revision):
    if item.revision != revision:
        raise StaffItemError("revision_conflict", "Карточка изменилась. Обновите её и повторите")
    if item.state != "active":
        raise StaffItemError("action_rejected", "Карточка уже не активна")


def _actor(user, member):
    # canonical_item_revisions.actor is 24 chars: role plus Telegram id fits, a UUID does not.
    return f"{member.role}:{user.telegram_user_id}"


def _resolve_deadline_request(item):
    reasons = [r for r in attention_reasons(item) if r not in DEADLINE_REASONS]
    item.attention_reasons = json.dumps(reasons, ensure_ascii=False)
    item.locked_fields = json.dumps(sorted(locked_fields(item) | {"deadline"}))


async def set_deadline(db, group, user, member, item, revision, at, date_only, now):
    _check(item, revision)
    if date_only:
        zone = ZoneInfo(group.timezone)
        at = datetime.combine(at.astimezone(zone).date(), time.min, zone)
    item.deadline_at, item.deadline_date_only = at, date_only
    # The headman is the source of this date: it counts as stated, never as inferred.
    item.deadline_basis, item.deadline_reason = "explicit", "headman_set"
    _resolve_deadline_request(item)
    await commit_item_revision(
        db,
        item,
        now,
        action="headman_set_deadline",
        actor=_actor(user, member),
        timezone_confirmed=item.timezone_confirmed,
    )


async def confirm_deadline(db, group, user, member, item, revision, now):
    _check(item, revision)
    if item.deadline_at is None:
        raise StaffItemError("action_rejected", "У карточки нет срока. Установите его")
    item.deadline_reason = "headman_confirmed"
    _resolve_deadline_request(item)
    await commit_item_revision(
        db,
        item,
        now,
        action="headman_confirm_deadline",
        actor=_actor(user, member),
        timezone_confirmed=item.timezone_confirmed,
    )


async def set_locks(db, group, user, member, item, revision, fields, now):
    _check(item, revision)
    unknown = set(fields) - LOCKABLE_FIELDS
    if unknown:
        raise StaffItemError("invalid_request", "Это поле нельзя зафиксировать")
    if set(fields) == locked_fields(item):
        return
    item.locked_fields = json.dumps(sorted(set(fields)))
    await commit_item_revision(db, item, now, action="headman_locks", actor=_actor(user, member))
