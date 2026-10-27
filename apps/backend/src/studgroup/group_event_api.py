"""Additive social-event API. Review and student visibility have separate off-by-default gates."""

import json
from datetime import UTC, date, datetime, time
from typing import Annotated, Any, Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from studgroup.analysis.event_lifetime import approved_snapshot
from studgroup.analysis.group_events import get_event, retained_sources, review_update
from studgroup.analysis.image_contracts import ImageEvent
from studgroup.api import ApiError, active_membership
from studgroup.models import GroupEvent, GroupEventRevision, RawMessage
from studgroup.staff_api import CurrentStaff, Db
from studgroup.student_cards import telegram_url

router = APIRouter(prefix="/v1")


def gate(request, group_id, public=False):
    settings = request.app.state.settings
    flag = (
        settings.image_event_publication_enabled if public else settings.image_event_review_enabled
    )
    if not flag or str(group_id) not in settings.image_analysis_groups:
        raise ApiError("not_found", "Раздел недоступен", 404)


def view(event, source_availability=None):
    payload = json.loads(event.payload_json)
    # Model linking hints and internal identifiers are not public event properties.
    for field in ("action", "existing_event_id", "expected_revision"):
        payload.pop(field, None)
    return {
        "id": event.id,
        "revision": event.revision,
        "state": event.state,
        "retained_until": event.delete_at,
        "source_availability": source_availability,
        **payload,
    }


DATE_FIELDS = ("starts_at", "ends_at", "starts_on", "ends_on")


def calendar_bounds(payload, timezone):
    zone = ZoneInfo(timezone)
    start_day = (
        payload.starts_at.astimezone(zone).date() if payload.starts_at else payload.starts_on
    )
    end_day = payload.ends_at.astimezone(zone).date() if payload.ends_at else payload.ends_on
    return start_day, end_day or start_day


async def present(db, group, event, now, sources=None):
    sources = sources if sources is not None else await retained_sources(db, event, now)
    urls = []
    for source in sources:
        raw = await db.get(RawMessage, source.raw_message_id)
        url, _ = telegram_url(group, raw, now, source.source_revision)
        if url:
            urls.append(url)
    result = view(event, "available" if urls else "expired")
    history = (
        await db.scalars(
            select(GroupEventRevision.snapshot_json).where(
                GroupEventRevision.event_id == event.id,
                GroupEventRevision.revision < event.revision,
            )
        )
    ).all()
    current_dates = tuple(result.get(field) for field in DATE_FIELDS)
    result["rescheduled"] = any(
        record.get("published_at")
        and record.get("state") == "active"
        and tuple(record["payload"].get(field) for field in DATE_FIELDS) != current_dates
        for record in map(json.loads, history)
    )
    result["source_url"] = urls[0] if urls else None
    return result


@router.get("/staff/image-findings/events")
async def list_review_events(request: Request, context: CurrentStaff, db: Db):
    gate(request, context.group.id)
    rows = (
        await db.scalars(
            select(GroupEvent)
            .where(
                GroupEvent.group_id == context.group.id, GroupEvent.delete_at > datetime.now(UTC)
            )
            .order_by(GroupEvent.created_at.desc())
            .limit(100)
        )
    ).all()
    result = []
    for row in rows:
        sources = await retained_sources(db, row, datetime.now(UTC))
        result.append(
            await present(db, context.group, row, datetime.now(UTC), sources)
            | {
                "manual_lock": row.manual_lock,
                "findings": [
                    {
                        "id": source.id,
                        "state": source.apply_state,
                        "payload": json.loads(source.incoming_json),
                        "source_revision": source.source_revision,
                        "source_url": (await present_source_url(db, context.group, source)),
                    }
                    for source in sources
                ],
            }
        )
    return {"events": result}


async def present_source_url(db, group, source):
    raw = await db.get(RawMessage, source.raw_message_id)
    return telegram_url(group, raw, datetime.now(UTC), source.source_revision)[0]


class EventUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)
    payload: dict[str, Any]
    state: Literal["draft", "active", "cancelled"] = "draft"
    accept_source_ids: list[UUID] = Field(default_factory=list, max_length=100)


@router.put("/staff/image-findings/events/{ident}")
async def update_review_event(
    ident: UUID, body: EventUpdate, request: Request, context: CurrentStaff, db: Db
):
    from pydantic import ValidationError

    gate(request, context.group.id)
    if body.state == "active":
        gate(request, context.group.id, public=True)
    event = await get_event(db, context.group.id, ident, lock=True)
    try:
        await review_update(
            db,
            event,
            body.expected_revision,
            body.payload,
            body.state,
            context.user.id,
            datetime.now(UTC),
            body.accept_source_ids,
        )
    except ValidationError:
        raise ApiError("invalid_request", "Некорректные поля события", 422) from None
    await db.commit()
    return await present(db, context.group, event, datetime.now(UTC))


async def group_context(db: Db, authorization: str | None = Header(None)):
    return await active_membership(db, authorization)


GroupContext = Annotated[tuple, Depends(group_context)]


@router.get("/group-events")
async def list_group_events(
    request: Request,
    db: Db,
    context: GroupContext,
    start: date | None = None,
    end: date | None = None,
    status: Literal["active", "cancelled", "all"] = "active",
    upcoming: bool = False,
):
    group, _member = context
    gate(request, group.id, public=True)
    if (start is None) != (end is None) or (start and (end < start or (end - start).days > 365)):
        raise ApiError("invalid_period", "Укажите интервал не больше 366 дней", 422)
    now = datetime.now(UTC)
    zone = ZoneInfo(group.timezone)
    today = now.astimezone(zone).date()
    query = select(GroupEvent).where(
        GroupEvent.group_id == group.id,
        GroupEvent.state.in_(["active", "cancelled"] if status == "all" else [status]),
        GroupEvent.delete_at > now,
    )
    rows = (await db.scalars(query)).all()
    result = []
    for event in rows:
        incoming = ImageEvent.model_validate_json(event.payload_json)
        if incoming.audience != "group" or not approved_snapshot(event):
            continue
        first, last = calendar_bounds(incoming, group.timezone)
        if first is None or last is None:
            continue  # never guess a calendar cell for unknown dates
        if start and (first > end or last < start):
            continue
        if upcoming and ((incoming.ends_at and incoming.ends_at <= now) or last < today):
            continue
        timestamp = incoming.starts_at or datetime.combine(first, time.min, zone)
        result.append((timestamp, str(event.id), event))
    # Filter and order before the existing response-size cap.
    result.sort(key=lambda row: (row[0], row[1]))
    return {"events": [await present(db, group, row[2], now) for row in result[:200]]}


@router.get("/group-events/{ident}")
async def group_event(ident: UUID, request: Request, db: Db, context: GroupContext):
    group, _member = context
    gate(request, group.id, public=True)
    now = datetime.now(UTC)
    event = await get_event(db, group.id, ident, now)
    incoming = ImageEvent.model_validate_json(event.payload_json)
    if (
        event.state not in {"active", "cancelled"}
        or incoming.audience != "group"
        or not approved_snapshot(event)
    ):
        raise ApiError("not_found", "Событие недоступно", 404)
    return await present(db, group, event, now)
