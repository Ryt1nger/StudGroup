"""Student-safe enrichment for already-published canonical learning cards.

The canonical projection link is the publication boundary. This module never reads shadow
evaluation results and never makes a canonical item public by itself.
"""

import json
from datetime import UTC

from sqlalchemy import select

from studgroup.materials import links
from studgroup.models import (
    AcademicDeadline,
    CanonicalAcademicItem,
    CanonicalItemEvidence,
    CanonicalItemRevision,
    CanonicalProjectionLink,
    Group,
    HeadmanDecision,
    Homework,
    MessageAttachment,
    RawMessage,
)
from studgroup.subject_identity import subject_uuid

CARD_KINDS = {
    "homework",
    "test",
    "control_point",
    "assessment",
    "credit",
    "exam",
    "other_academic_deadline",
}
ATTACHMENT_KINDS = {"photo", "document", "video", "audio", "voice", "animation"}
ATTACHMENT_TITLES = {
    "photo": "Фотография",
    "document": "Документ",
    "video": "Видео",
    "audio": "Аудиозапись",
    "voice": "Голосовое сообщение",
    "animation": "Анимация",
}


def utc(value):
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def telegram_url(group: Group, raw: RawMessage | None, now, source_revision: int | None = None):
    if raw is None or utc(raw.delete_at) <= now:
        return None, "retention_expired"
    if source_revision is not None and raw.revision != source_revision:
        return None, "unknown"
    chat_id = str(group.telegram_chat_id)
    if not chat_id.startswith("-100"):
        return None, "no_deep_link"
    return f"https://t.me/c/{chat_id[4:]}/{raw.telegram_message_id}", None


def source_detail(group, raw, now, source_revision=None):
    url, reason = telegram_url(group, raw, now, source_revision)
    imported = bool(raw and raw.imported)
    return {
        "id": raw.id if raw else None,
        "telegram_message_id": raw.telegram_message_id if raw else None,
        "kind": (
            "manual_entry"
            if raw is None
            else "imported_history"
            if imported
            else "telegram_group_message"
        ),
        "imported": imported,
        "availability": {
            "state": "available" if url else "unavailable",
            "reason": None if url else reason,
        },
        "message_date": utc(raw.message_date) if raw else None,
        "excerpt": raw.text[:1000] if raw and url else None,
        "action": {"type": "open_telegram_link" if url else "none", "url": url},
    }


async def published_projection(db, row: Homework):
    link = await db.scalar(
        select(CanonicalProjectionLink).where(
            CanonicalProjectionLink.entity_type == "homework",
            CanonicalProjectionLink.entity_id == row.id,
        )
    )
    if link is None:
        return None, None
    item = await db.get(CanonicalAcademicItem, link.item_id)
    if (
        item is None
        or item.group_id != row.group_id
        or item.destination != "task"
        or item.semantic_kind != "homework"
        or link.projected_revision > item.revision
    ):
        return None, None
    return link, item


async def projected_sources(db, group, row, now, link):
    if link is None:
        raw = await db.get(RawMessage, row.raw_message_id) if row.raw_message_id else None
        return [source_detail(group, raw, now)]
    evidence = (
        await db.execute(
            select(RawMessage, CanonicalItemEvidence.source_revision)
            .join(
                CanonicalItemEvidence,
                CanonicalItemEvidence.raw_message_id == RawMessage.id,
            )
            .where(
                CanonicalItemEvidence.item_id == link.item_id,
                CanonicalItemEvidence.item_revision == link.projected_revision,
                RawMessage.group_id == row.group_id,
            )
            .order_by(RawMessage.message_date, RawMessage.telegram_message_id)
        )
    ).all()
    unique = {}
    for raw, revision in evidence:
        unique.setdefault(raw.id, (raw, revision))
    if not unique:
        raw = await db.get(RawMessage, row.raw_message_id) if row.raw_message_id else None
        return [source_detail(group, raw, now)]
    return [source_detail(group, raw, now, revision) for raw, revision in unique.values()]


async def headman_resolved(db, key):
    """True once the headman or deputy has confirmed or edited the card (it is locked for the AI)."""
    decision = await db.get(HeadmanDecision, key)
    return decision is not None and decision.locked


def deadline_metadata(row, item, resolved=False):
    if resolved and (
        getattr(row, "deadline_at", None) is not None or getattr(row, "window_start", None)
    ):
        # A person decided: the earlier "needs clarification" of the model no longer applies.
        return "confirmed", "manual"
    if item is not None:
        mapping = {
            "explicit": ("from_source", "explicit_message"),
            "relative_resolved": ("from_source", "explicit_message"),
            "schedule_inferred": ("inferred", "schedule_inferred"),
            "ambiguous_range": ("needs_clarification", "ambiguous_message"),
            "unknown": ("unknown", "unknown"),
        }
        return mapping.get(item.deadline_basis, ("unknown", "unknown"))
    if row.deadline_at is None:
        return "unknown", "unknown"
    mapping = {
        "manual_confirmed": ("confirmed", "manual"),
        "inferred": ("inferred", "schedule_inferred"),
        "needs_clarification": ("needs_clarification", "ambiguous_message"),
        "imported": ("from_source", "imported"),
        "from_group_message": ("from_source", "explicit_message"),
    }
    return mapping.get(row.verification_state, ("unknown", "unknown"))


def confirmed_subject(row, item):
    if item is not None:
        return {"id": row.subject_id, "name": item.subject} if item.subject else None
    if row.subject_name.strip().casefold() in {"предмет не указан", "неизвестно"}:
        return None
    return {"id": row.subject_id, "name": row.subject_name}


def update_text(previous, current):
    changed = []
    if (previous.get("deadline_at"), previous.get("deadline_date_only")) != (
        current.get("deadline_at"),
        current.get("deadline_date_only"),
    ):
        changed.append("срок")
    if previous.get("description") != current.get("description"):
        changed.append("описание")
    if previous.get("title") != current.get("title"):
        changed.append("заголовок")
    if previous.get("subject") != current.get("subject"):
        changed.append("предмет")
    if not changed:
        return "Карточка задания обновлена"
    return f"Обновлено: {', '.join(changed)}"


async def projected_updates(db, link):
    if link is None or link.projected_revision <= 1:
        return []
    current = await db.scalar(
        select(CanonicalItemRevision).where(
            CanonicalItemRevision.item_id == link.item_id,
            CanonicalItemRevision.item_revision == link.projected_revision,
        )
    )
    if current is None or current.action == "create":
        return []
    if current.action == "cancel":
        return [
            {
                "id": current.id,
                "kind": "cancellation",
                "text": "Задание отменено",
                "created_at": utc(current.created_at),
            }
        ]
    previous = await db.scalar(
        select(CanonicalItemRevision)
        .where(
            CanonicalItemRevision.item_id == link.item_id,
            CanonicalItemRevision.item_revision < link.projected_revision,
        )
        .order_by(CanonicalItemRevision.item_revision.desc())
        .limit(1)
    )
    current_snapshot = json.loads(current.snapshot_json)
    previous_snapshot = json.loads(previous.snapshot_json) if previous else {}
    only_description = (
        previous
        and all(
            previous_snapshot.get(field) == current_snapshot.get(field)
            for field in ("deadline_at", "deadline_date_only", "title", "subject", "state")
        )
        and previous_snapshot.get("description") != current_snapshot.get("description")
    )
    return [
        {
            "id": current.id,
            "kind": "supplement" if only_description else "change",
            "text": (
                "Добавлены уточнения к заданию"
                if only_description
                else update_text(previous_snapshot, current_snapshot)
            ),
            "created_at": utc(current.created_at),
        }
    ]


async def projected_materials(db, group_id, entity_key, now, sources):
    result = [
        {"kind": "link", "file_name": None, "mime_type": None, "size_bytes": None, **item}
        for item in await links(db, group_id, [entity_key])
    ]
    source_urls = {
        source["id"]: source["action"]["url"]
        for source in sources
        if source["id"] is not None and source["action"]["type"] == "open_telegram_link"
    }
    if not source_urls:
        return result
    source_order = {source_id: index for index, source_id in enumerate(source_urls)}
    attachments = (
        await db.scalars(
            select(MessageAttachment).where(
                MessageAttachment.group_id == group_id,
                MessageAttachment.raw_message_id.in_(source_urls),
                MessageAttachment.active.is_(True),
                MessageAttachment.delete_at > now,
            )
        )
    ).all()
    attachments.sort(
        key=lambda attachment: (
            source_order[attachment.raw_message_id],
            attachment.created_at,
            str(attachment.id),
        )
    )
    for attachment in attachments:
        if attachment.kind not in ATTACHMENT_KINDS:
            continue
        url = source_urls.get(attachment.raw_message_id)
        if not url:
            continue
        result.append(
            {
                "id": attachment.id,
                "title": attachment.file_name or ATTACHMENT_TITLES[attachment.kind],
                "url": url,
                "kind": attachment.kind,
                "file_name": attachment.file_name,
                "mime_type": attachment.mime_type,
                "size_bytes": attachment.size_bytes,
            }
        )
    return result


async def enrich_homework(db, group, row, now):
    link, item = await published_projection(db, row)
    sources = await projected_sources(db, group, row, now, link)
    certainty, origin = deadline_metadata(row, item, await headman_resolved(db, f"h:{row.id}"))
    return {
        "card_kind": (
            item.semantic_kind if item and item.semantic_kind in CARD_KINDS else "homework"
        ),
        "confirmed_subject": confirmed_subject(row, item),
        "deadline_certainty": certainty,
        "deadline_origin": origin,
        "sources": sources,
        "materials": await projected_materials(db, row.group_id, f"h:{row.id}", now, sources),
        "updates": await projected_updates(db, link),
    }


async def enrich_deadline(db, group, row: AcademicDeadline, now):
    link = await db.scalar(
        select(CanonicalProjectionLink).where(
            CanonicalProjectionLink.entity_type == "deadline",
            CanonicalProjectionLink.entity_id == row.id,
        )
    )
    item = await db.get(CanonicalAcademicItem, link.item_id) if link else None
    if (
        item is None
        or item.group_id != row.group_id
        or item.destination != "deadline"
        or item.semantic_kind == "homework"
        or link.projected_revision > item.revision
    ):
        link = item = None
    if link:
        evidence = (
            await db.execute(
                select(RawMessage, CanonicalItemEvidence.source_revision)
                .join(
                    CanonicalItemEvidence,
                    CanonicalItemEvidence.raw_message_id == RawMessage.id,
                )
                .where(
                    CanonicalItemEvidence.item_id == link.item_id,
                    CanonicalItemEvidence.item_revision == link.projected_revision,
                    RawMessage.group_id == row.group_id,
                )
                .order_by(RawMessage.message_date, RawMessage.telegram_message_id)
            )
        ).all()
        unique = {}
        for raw, revision in evidence:
            unique.setdefault(raw.id, (raw, revision))
        sources = [source_detail(group, raw, now, revision) for raw, revision in unique.values()]
    else:
        message_ids = json.loads(row.source_message_ids)
        raws = (
            await db.scalars(
                select(RawMessage)
                .where(
                    RawMessage.group_id == row.group_id,
                    RawMessage.telegram_message_id.in_(message_ids),
                )
                .order_by(RawMessage.message_date, RawMessage.telegram_message_id)
            )
        ).all()
        sources = [source_detail(group, raw, now) for raw in raws]
    if not sources:
        raw = await db.get(RawMessage, row.raw_message_id) if row.raw_message_id else None
        sources = [source_detail(group, raw, now)]
    if item:
        certainty, origin = deadline_metadata(row, item, await headman_resolved(db, f"e:{row.id}"))
        kind = item.semantic_kind if item.semantic_kind in CARD_KINDS else row.kind
        subject = (
            {
                "id": subject_uuid(group.id, item.subject),
                "name": item.subject,
            }
            if item.subject
            else None
        )
    else:
        resolved = await headman_resolved(db, f"e:{row.id}")
        certainty, origin = deadline_metadata(row, None, resolved) if resolved else (None, None)
        if certainty is None:
            certainty = (
                "needs_clarification"
                if row.needs_clarification
                else ("from_source" if row.deadline_at else "unknown")
            )
            origin = (
                "ambiguous_message"
                if row.needs_clarification
                else ("explicit_message" if row.deadline_at else "unknown")
            )
        kind = row.kind if row.kind in CARD_KINDS else "other_academic_deadline"
        subject = (
            None
            if row.subject.strip().casefold() in {"предмет не указан", "неизвестно"}
            else {
                "id": subject_uuid(group.id, row.subject),
                "name": row.subject,
            }
        )
    return {
        "card_kind": kind,
        "confirmed_subject": subject,
        "deadline_certainty": certainty,
        "deadline_origin": origin,
        "sources": sources,
        "materials": await projected_materials(db, row.group_id, f"e:{row.id}", now, sources),
        "updates": await projected_updates(db, link),
    }
