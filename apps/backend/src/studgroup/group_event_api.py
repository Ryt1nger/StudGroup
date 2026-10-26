"""Additive social-event API. Review and student visibility have separate off-by-default gates."""

import json
from datetime import UTC, datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from studgroup.analysis.group_events import get_event, review_update, verified
from studgroup.analysis.image_contracts import ImageEvent
from studgroup.api import ApiError, active_membership
from studgroup.models import GroupEvent, GroupEventSource
from studgroup.staff_api import CurrentStaff, Db

router = APIRouter(prefix="/v1")


def gate(request, group_id, public=False):
    settings = request.app.state.settings
    flag = settings.image_event_publication_enabled if public else settings.image_event_review_enabled
    if not flag or str(group_id) not in settings.image_analysis_groups:
        raise ApiError("not_found", "Раздел недоступен", 404)


def view(event):
    payload = json.loads(event.payload_json)
    # Model linking hints and internal identifiers are not public event properties.
    for field in ("action", "existing_event_id", "expected_revision"):
        payload.pop(field, None)
    return {"id": event.id, "revision": event.revision, "state": event.state, **payload}


@router.get("/staff/image-findings/events")
async def list_review_events(request: Request, context: CurrentStaff, db: Db):
    gate(request, context.group.id)
    rows = (await db.scalars(select(GroupEvent).where(GroupEvent.group_id == context.group.id,
        GroupEvent.delete_at > datetime.now(UTC)).order_by(GroupEvent.created_at.desc()).limit(100))).all()
    result = []
    for row in rows:
        sources = (await db.scalars(select(GroupEventSource).where(GroupEventSource.event_id == row.id))).all()
        result.append(view(row) | {"manual_lock": row.manual_lock, "findings": [
            {"id": source.id, "state": source.apply_state, "payload": json.loads(source.incoming_json),
             "source_revision": source.source_revision} for source in sources]})
    return {"events": result}


class EventUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)
    payload: dict[str, Any]
    state: Literal["draft", "active", "cancelled"] = "draft"
    accept_source_ids: list[UUID] = Field(default_factory=list, max_length=100)


@router.put("/staff/image-findings/events/{ident}")
async def update_review_event(ident: UUID, body: EventUpdate, request: Request, context: CurrentStaff, db: Db):
    from pydantic import ValidationError

    gate(request, context.group.id)
    if body.state == "active":
        gate(request, context.group.id, public=True)
    event = await get_event(db, context.group.id, ident, lock=True)
    try:
        await review_update(db, event, body.expected_revision, body.payload, body.state,
            context.user.id, datetime.now(UTC), body.accept_source_ids)
    except ValidationError:
        raise ApiError("invalid_request", "Некорректные поля события", 422) from None
    await db.commit()
    return view(event)


async def group_context(db: Db, authorization: str | None = Header(None)):
    return await active_membership(db, authorization)


GroupContext = Annotated[tuple, Depends(group_context)]


@router.get("/group-events")
async def list_group_events(request: Request, db: Db, context: GroupContext):
    group, _member = context
    gate(request, group.id, public=True)
    now = datetime.now(UTC)
    rows = (await db.scalars(select(GroupEvent).where(GroupEvent.group_id == group.id,
        GroupEvent.state == "active", GroupEvent.delete_at > now).order_by(GroupEvent.created_at.desc()).limit(200))).all()
    result = []
    for event in rows:
        incoming = ImageEvent.model_validate_json(event.payload_json)
        if incoming.audience == "group" and incoming.starts_at and incoming.starts_at > now and await verified(db, event, now):
            result.append(view(event))
    return {"events": result}


@router.get("/group-events/{ident}")
async def group_event(ident: UUID, request: Request, db: Db, context: GroupContext):
    group, _member = context
    gate(request, group.id, public=True)
    now = datetime.now(UTC)
    event = await get_event(db, group.id, ident, now)
    incoming = ImageEvent.model_validate_json(event.payload_json)
    if (event.state != "active" or incoming.audience != "group" or incoming.starts_at is None
            or incoming.starts_at <= now or not await verified(db, event, now)):
        raise ApiError("not_found", "Событие недоступно", 404)
    return view(event)
