"""Unpublished event lifecycle. Model findings never override a manual or public event."""

import hashlib
import json
import uuid
from datetime import UTC, datetime

from sqlalchemy import delete, or_, select

from studgroup.analysis.image_contracts import ImageEvent
from studgroup.analysis.image_jobs import utc
from studgroup.api import ApiError
from studgroup.models import Group, GroupEvent, GroupEventRevision, GroupEventSource, RawMessage


def title_key(text):
    return " ".join(text.casefold().replace("ё", "е").split())


def fingerprint(event):
    # Same convention as candidates.EventRecord (title + when_text), extended only when
    # a normalized timestamp is supplied instead of original date words.
    when = event.when_text or (
        event.starts_at.isoformat()
        if event.starts_at
        else event.starts_on.isoformat()
        if event.starts_on
        else ""
    )
    return hashlib.sha256(f"{title_key(event.title)}|{title_key(when)}".encode()).hexdigest()


def snapshot(db, event, origin, now, actor=None):
    db.add(
        GroupEventRevision(
            event_id=event.id,
            revision=event.revision,
            actor_id=actor,
            snapshot_json=json.dumps(
                {
                    "payload": json.loads(event.payload_json),
                    "state": event.state,
                    "manual_lock": event.manual_lock,
                    "published_at": event.published_at.isoformat() if event.published_at else None,
                    "approved_by": str(event.approved_by) if event.approved_by else None,
                    "event_end_date": event.event_end_date.isoformat()
                    if event.event_end_date
                    else None,
                    "retained_until": event.delete_at.isoformat(),
                },
                ensure_ascii=False,
            ),
            origin=origin,
            created_at=now,
        )
    )


async def capture_event(db, group_id, raw, incoming, now, job=None):
    if raw.group_id != group_id or utc(raw.delete_at) <= now:
        raise ValueError("event_source_outside_group")
    # Same group lock for candidate and image findings; event uniqueness is also enforced by SQL.
    await db.scalar(select(Group).where(Group.id == group_id).with_for_update())
    if incoming.existing_event_id:
        event = await db.scalar(
            select(GroupEvent)
            .where(
                GroupEvent.id == incoming.existing_event_id,
                GroupEvent.group_id == group_id,
                GroupEvent.delete_at > now,
            )
            .with_for_update()
        )
        if event is None:
            return "unknown_event_identity"
    else:
        event = await db.scalar(
            select(GroupEvent)
            .where(
                GroupEvent.group_id == group_id,
                GroupEvent.fingerprint == fingerprint(incoming),
                GroupEvent.delete_at > now,
            )
            .with_for_update()
        )
        if event is None:
            event = await db.scalar(
                select(GroupEvent)
                .join(GroupEventSource, GroupEventSource.event_id == GroupEvent.id)
                .where(
                    GroupEvent.group_id == group_id,
                    GroupEvent.title_key == title_key(incoming.title),
                    GroupEventSource.raw_message_id == raw.id,
                    GroupEvent.delete_at > now,
                )
                .with_for_update()
                .limit(1)
            )
    new = event is None
    if new:
        event = GroupEvent(
            group_id=group_id,
            fingerprint=fingerprint(incoming),
            title_key=title_key(incoming.title),
            payload_json=incoming.model_dump_json(),
            state="draft",
            manual_lock=False,
            revision=1,
            created_at=now,
            updated_at=now,
            delete_at=min(raw.delete_at, job.delete_at) if job else raw.delete_at,
        )
        db.add(event)
        await db.flush()
    source_key = f"{raw.id}:{raw.revision}:{job.id if job else 'candidate'}:{fingerprint(incoming)}:{incoming.action}"
    source_id = uuid.uuid5(event.id, source_key)
    if await db.get(GroupEventSource, source_id):
        return "unchanged"
    held = not new and (
        event.manual_lock
        or (incoming.expected_revision is not None and incoming.expected_revision != event.revision)
    )
    if not held:
        old_sources = (
            await db.scalars(
                select(GroupEventSource).where(
                    GroupEventSource.event_id == event.id,
                    GroupEventSource.raw_message_id == raw.id,
                    GroupEventSource.source_revision != raw.revision,
                    GroupEventSource.apply_state == "applied",
                )
            )
        ).all()
        for old in old_sources:
            old.apply_state = "superseded"
    db.add(
        GroupEventSource(
            id=source_id,
            event_id=event.id,
            image_job_id=job.id if job else None,
            raw_message_id=raw.id,
            source_revision=raw.revision,
            incoming_json=incoming.model_dump_json(),
            apply_state="held" if held else "applied",
            created_at=now,
        )
    )
    if new:
        snapshot(db, event, "model", now)
    elif not held:
        previous = ImageEvent.model_validate_json(event.payload_json)
        values = incoming.model_dump()
        for field in (
            "description",
            "when_text",
            "starts_at",
            "ends_at",
            "starts_on",
            "ends_on",
            "place",
            "conditions",
            "audience_label",
        ):
            if values[field] in (None, ""):
                values[field] = getattr(previous, field)
        if values["audience"] == "unknown":
            values["audience"] = previous.audience
        updated = incoming.model_copy(update=values)
        if updated.model_dump_json() != event.payload_json or incoming.action == "cancel":
            event.payload_json = updated.model_dump_json()
            event.state = "cancelled" if incoming.action == "cancel" else event.state
            event.revision += 1
            event.updated_at = now
            snapshot(db, event, "model", now)
    if not event.manual_lock:
        await publish_verified_group_event(db, event, now)
    return "held" if held else "created" if new else "updated"


async def publish_verified_group_event(db, event, now):
    """Expose a verified group event without making up missing calendar data.

    This is the single-group product policy: model findings with current evidence and
    group audience are immediately visible.  A staff edit still sets ``manual_lock``
    and thereafter wins over new model findings.
    """
    from studgroup.analysis.event_lifetime import payload_hash, retention_end

    payload = ImageEvent.model_validate_json(event.payload_json)
    if payload.audience != "group" or not await verified(db, event, now):
        return False
    try:
        end_date, expires = retention_end(payload, (await db.get(Group, event.group_id)).timezone)
    except ApiError as exc:
        if exc.code not in {"event_date_required", "event_end_required"}:
            return False
        # Keep the source-retention expiry for an event whose date is only written
        # in prose.  It is visible in Events, but never projected onto the calendar.
        end_date, expires = None, event.delete_at
    if expires <= now:
        return False
    changed = (
        event.state != ("cancelled" if payload.action == "cancel" else "active")
        or event.event_end_date != end_date
        or event.delete_at != expires
        or event.approved_payload_sha256 != payload_hash(event.payload_json)
    )
    event.state = "cancelled" if payload.action == "cancel" else "active"
    event.event_end_date, event.delete_at = end_date, expires
    event.published_at = event.published_at or now
    event.approved_payload_sha256 = payload_hash(event.payload_json)
    if changed:
        event.revision += 1
        event.updated_at = now
        snapshot(db, event, "model_publication", now)
    return True


async def capture_image(db, job, reading, raw, now):
    if {"schedule", "schedule_change"}.intersection(
        reading.semantic_types
    ) and "event" not in reading.semantic_types:
        return  # a classroom move belongs to schedule review, never a parallel event record
    for incoming in reading.events:
        await capture_event(db, job.group_id, raw, incoming, now, job)


async def capture_candidates(db, group_id, validation, data, now):
    from studgroup.analysis.candidates import event_records

    for record in event_records(validation):
        if record.source_status != "verified" or not record.title:
            continue
        incoming = ImageEvent(
            title=record.title,
            description=record.description or "",
            when_text=record.when_text,
            audience=record.audience.scope,
            audience_label=record.audience.label,
        )
        for reference in record.evidence:
            raw = await db.scalar(
                select(RawMessage).where(
                    RawMessage.group_id == group_id,
                    RawMessage.telegram_message_id == reference.telegram_message_id,
                    RawMessage.revision == reference.revision,
                    RawMessage.delete_at > now,
                )
            )
            if raw:
                await capture_event(db, group_id, raw, incoming, now)


async def verified(db, event, now):
    sources = (
        await db.scalars(
            select(GroupEventSource).where(
                GroupEventSource.event_id == event.id,
                GroupEventSource.apply_state == "applied",
            )
        )
    ).all()
    if not sources:
        return False
    for source in sources:
        raw = await db.get(RawMessage, source.raw_message_id)
        if (
            raw is None
            or raw.group_id != event.group_id
            or raw.revision != source.source_revision
            or utc(raw.delete_at) <= now
        ):
            return False
        if ImageEvent.model_validate_json(source.incoming_json).audience != "group":
            return False  # personal/unknown invitations cannot be promoted to the whole group
        if source.image_job_id:
            from studgroup.analysis.image_schedule import current_source

            try:
                await current_source(db, source.image_job_id, event.group_id, now)
            except ApiError:
                return False
    return True


async def get_event(db, group_id, ident, now=None, lock=False):
    query = select(GroupEvent).where(
        GroupEvent.id == ident,
        GroupEvent.group_id == group_id,
        GroupEvent.delete_at > (now or datetime.now(UTC)),
    )
    event = await db.scalar(query.with_for_update() if lock else query)
    if event is None:
        raise ApiError("not_found", "Событие недоступно", 404)
    return event


async def review_update(
    db, event, expected_revision, payload, state, actor, now, accept_source_ids=()
):
    if event.revision != expected_revision:
        raise ApiError("revision_conflict", "Событие уже изменилось", 409)
    incoming = ImageEvent.model_validate_json(json.dumps(payload, ensure_ascii=False))
    sources = (
        await db.scalars(
            select(GroupEventSource)
            .where(
                GroupEventSource.event_id == event.id,
            )
            .with_for_update()
        )
    ).all()
    available = {source.id: source for source in sources}
    if set(accept_source_ids) - set(available):
        raise ApiError("invalid_source", "Источник не принадлежит событию", 409)
    for ident in accept_source_ids:
        source = available[ident]
        source.apply_state = "applied"
        for old in sources:
            if old.id != source.id and old.raw_message_id == source.raw_message_id:
                old.apply_state = "superseded"
    await db.flush()
    from studgroup.analysis.event_lifetime import payload_hash, retention_end

    group = await db.get(Group, event.group_id)
    if state == "active":
        if incoming.audience != "group":
            raise ApiError("event_not_publishable", "Нужна подтверждённая аудитория группы", 409)
        end_date, expires = retention_end(incoming, group.timezone)
        if expires <= now:
            raise ApiError("event_expired", "Срок жизни мероприятия уже истёк", 409)
        # First publication needs current evidence. After editor approval the structured
        # record has its own lifetime, even when the private sources have already expired.
        if event.published_at is None and not await verified(db, event, now):
            raise ApiError("stale_source", "Источники изменились или недоступны", 409)
        event.event_end_date, event.delete_at = end_date, expires
        event.published_at = event.published_at or now
        event.approved_by = actor
        event.approved_payload_sha256 = payload_hash(incoming.model_dump_json())
    elif event.published_at and state == "cancelled":
        # Cancellation retains the last approved end-date expiry, without extending sources.
        event.approved_payload_sha256 = payload_hash(incoming.model_dump_json())
    else:
        event.approved_payload_sha256 = None
    event.payload_json, event.state = incoming.model_dump_json(), state
    event.title_key = title_key(incoming.title)
    event.manual_lock, event.updated_at = True, now
    event.revision += 1
    snapshot(db, event, "manual", now, actor)


async def retained_sources(db, event, now):
    from studgroup.models import ImageAnalysisJob

    return (
        await db.scalars(
            select(GroupEventSource)
            .join(RawMessage, RawMessage.id == GroupEventSource.raw_message_id)
            .outerjoin(ImageAnalysisJob, ImageAnalysisJob.id == GroupEventSource.image_job_id)
            .where(
                GroupEventSource.event_id == event.id,
                RawMessage.group_id == event.group_id,
                RawMessage.delete_at > now,
                or_(GroupEventSource.image_job_id.is_(None), ImageAnalysisJob.delete_at > now),
            )
        )
    ).all()


async def purge_events(db, now):
    from studgroup.models import ImageAnalysisJob

    # Private findings expire with their original source, regardless of event lifetime.
    await db.execute(
        delete(GroupEventSource).where(
            or_(
                GroupEventSource.raw_message_id.in_(
                    select(RawMessage.id).where(RawMessage.delete_at <= now)
                ),
                GroupEventSource.image_job_id.in_(
                    select(ImageAnalysisJob.id).where(ImageAnalysisJob.delete_at <= now)
                ),
            )
        )
    )
    result = await db.execute(delete(GroupEvent).where(GroupEvent.delete_at <= now))
    return result.rowcount
