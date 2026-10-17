"""Atomic compatibility projections; disabled unless explicitly invoked in new mode."""

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, timedelta

from sqlalchemy import exists, select

from studgroup.analysis.card_editor import DESCRIPTION_ATTACHMENT_ONLY, DESCRIPTION_PENDING
from studgroup.analysis.linking import PENDING_INTEGRATION
from studgroup.models import (
    AcademicDeadline,
    CanonicalItemEvidence,
    CanonicalProjectionLink,
    Homework,
    Notification,
    RawMessage,
)
from studgroup.subject_identity import subject_uuid


@dataclass(frozen=True)
class ProjectionResult:
    action: str
    entity_type: str | None = None
    entity_id: uuid.UUID | None = None


def utc(value):
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def visible_title(item):
    if item.audience_scope == "subgroup" and item.audience_label:
        return f"[{item.audience_label}] {item.title}"[:255]
    return item.title


# A student does not see the "needs clarification" state, so a description that may lag behind the
# chat says so in its own text. The note states no fact; it points to the cited messages and
# disappears on its own when the AI integrates the clarification (the reasons are replaced).
LAGGING_NOTE = "В чате есть уточнение к этому заданию, которого пока нет в описании: смотрите исходные сообщения."
LAGGING_REASONS = {PENDING_INTEGRATION, "description_pending_ai"}


def visible_description(item):
    description = item.description or ""
    if description in {
        DESCRIPTION_PENDING,
        DESCRIPTION_ATTACHMENT_ONLY,
    } or not LAGGING_REASONS & set(json.loads(item.attention_reasons)):
        return description
    return f"{description[: 8000 - len(LAGGING_NOTE) - 2]}\n\n{LAGGING_NOTE}"


async def source_rows(db, item):
    rows = (
        await db.execute(
            select(RawMessage, CanonicalItemEvidence.fact_path)
            .join(
                CanonicalItemEvidence,
                CanonicalItemEvidence.raw_message_id == RawMessage.id,
            )
            .where(
                CanonicalItemEvidence.item_id == item.id,
                CanonicalItemEvidence.item_revision == item.revision,
            )
            .order_by(RawMessage.message_date, RawMessage.telegram_message_id)
        )
    ).all()
    if not rows:
        raise ValueError("projection_requires_current_evidence")
    unique = []
    for raw, _fact_path in rows:
        if raw not in unique:
            unique.append(raw)
    return unique


async def canonical_notification(db, item, entity_type, entity_id, now):
    key = f"canonical:{item.id}:{item.revision}:{entity_type}"
    notification_id = uuid.uuid5(uuid.NAMESPACE_URL, key)
    if await db.get(Notification, notification_id) is None:
        db.add(
            Notification(
                id=uuid.uuid5(uuid.NAMESPACE_URL, key),
                group_id=item.group_id,
                event_key=key,
                title=item.subject or visible_title(item),
                body=visible_title(item)[:1000],
                entity_type=entity_type,
                entity_id=entity_id,
                created_at=now,
                delete_at=now + timedelta(days=90),
            )
        )


async def project_item(db, item, now, *, writer_mode="shadow", emit_notifications=True):
    if writer_mode != "new":
        return ProjectionResult("shadow_noop")
    if item.audience_scope in {"personal", "unknown"}:
        return ProjectionResult("audience_blocked")
    sources = await source_rows(db, item)
    source = sources[0]
    from studgroup.headman_domain import ai_locked

    if item.manual_lock or await ai_locked(db, item.group_id, source.id):
        item.manual_lock = True
        return ProjectionResult("blocked_by_lock")
    link = await db.get(CanonicalProjectionLink, item.id, with_for_update=True)
    if link and link.projected_revision >= item.revision:
        return ProjectionResult("unchanged", link.entity_type, link.entity_id)
    expected_entity_type = "homework" if item.destination == "task" else "deadline"
    if link and link.entity_type != expected_entity_type:
        return ProjectionResult("type_change_blocked", link.entity_type, link.entity_id)
    title = visible_title(item)
    if item.destination == "task":
        row = await db.get(Homework, link.entity_id, with_for_update=True) if link else None
        if row is None and link is None:
            row = await db.scalar(
                select(Homework)
                .where(
                    Homework.raw_message_id == source.id,
                    ~exists(
                        select(CanonicalProjectionLink.item_id).where(
                            CanonicalProjectionLink.entity_type == "homework",
                            CanonicalProjectionLink.entity_id == Homework.id,
                        )
                    ),
                )
                .with_for_update()
            )
        created = row is None
        if row is None:
            row = Homework(
                group_id=item.group_id,
                raw_message_id=source.id,
                subject_id=subject_uuid(item.group_id, item.subject or "unknown"),
                created_at=now,
                revision=1,
            )
            db.add(row)
        else:
            row.revision += 1
        row.subject_name = item.subject or "Предмет не указан"
        row.title = title
        row.description = visible_description(item)
        row.deadline_at = item.deadline_at
        row.deadline_date_only = item.deadline_date_only
        row.status = "cancelled" if item.state == "cancelled" else "published"
        row.cancelled_at = now if item.state == "cancelled" else None
        row.urgency = "normal"
        row.verification_state = (
            "needs_clarification"
            if json.loads(item.attention_reasons)
            else "inferred"
            if item.deadline_basis == "schedule_inferred"
            else "from_group_message"
        )
        row.source_message_at = source.message_date
        row.updated_at = now
        row.significant_updated_at = now
        row.delete_at = max(utc(source.delete_at), utc(now) + timedelta(days=30))
        entity_type = "homework"
    else:
        row = await db.get(AcademicDeadline, link.entity_id, with_for_update=True) if link else None
        if row is None and link is None:
            existing = (
                await db.scalars(
                    select(AcademicDeadline)
                    .where(
                        AcademicDeadline.group_id == item.group_id,
                        AcademicDeadline.raw_message_id == source.id,
                        AcademicDeadline.delete_at > now,
                        ~exists(
                            select(CanonicalProjectionLink.item_id).where(
                                CanonicalProjectionLink.entity_type == "deadline",
                                CanonicalProjectionLink.entity_id == AcademicDeadline.id,
                            )
                        ),
                    )
                    .with_for_update()
                )
            ).all()
            row = existing[0] if len(existing) == 1 else None
        created = row is None
        if row is None:
            row = AcademicDeadline(
                group_id=item.group_id,
                raw_message_id=source.id,
                import_key=f"canonical:{item.id}",
                created_at=now,
                revision=1,
            )
            db.add(row)
        else:
            row.revision += 1
        row.kind = item.semantic_kind
        row.subject = item.subject or "Предмет не указан"
        row.title = title
        row.description = visible_description(item)
        row.deadline_at = item.deadline_at
        row.date_only = item.deadline_date_only
        row.window_start = None
        row.window_end = None
        row.date_hint = None
        row.needs_clarification = bool(json.loads(item.attention_reasons))
        row.source_message_ids = json.dumps([source.telegram_message_id for source in sources])
        row.cancelled_at = now if item.state == "cancelled" else None
        row.delete_at = max(utc(source.delete_at), utc(now) + timedelta(days=90))
        entity_type = "deadline"
    await db.flush()
    if link is None:
        link = CanonicalProjectionLink(
            item_id=item.id,
            entity_type=entity_type,
            entity_id=row.id,
            projected_revision=item.revision,
            created_at=now,
            updated_at=now,
        )
        db.add(link)
    else:
        link.entity_type = entity_type
        link.entity_id = row.id
        link.projected_revision = item.revision
        link.updated_at = now
    if emit_notifications:
        await canonical_notification(db, item, entity_type, row.id, now)
    return ProjectionResult("created" if created else "updated", entity_type, row.id)
