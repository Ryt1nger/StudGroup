"""Group-scoped graded events. Never use homework completion to classify an assessment."""

import json
import uuid
from datetime import UTC, datetime
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Header
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from studgroup import subgroups
from studgroup.api import ApiError, active_group, database, edit_permissions
from studgroup.models import AcademicDeadline, Group, WebSession
from studgroup.security import token_hash
from studgroup.student_cards import enrich_deadline

router = APIRouter(prefix="/v1")


def utc(value):
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def archived(row, now, timezone):
    if row.cancelled_at:
        return True
    if row.deadline_at:
        at = utc(row.deadline_at)
        return (
            at.astimezone(ZoneInfo(timezone)).date() < now.astimezone(ZoneInfo(timezone)).date()
            if row.date_only
            else at <= now
        )
    return bool(row.window_end and utc(row.window_end) <= now)


async def representation(db, group, row, now):
    enrichment = await enrich_deadline(db, group, row, now)
    return {
        "id": row.id,
        "kind": row.kind,
        "card_kind": enrichment["card_kind"],
        "subject": row.subject,
        "subgroup": await subgroups.label(db, row.subgroup_id),
        "confirmed_subject": enrichment["confirmed_subject"],
        "title": row.title,
        "description": row.description,
        "deadline_at": utc(row.deadline_at) if row.deadline_at else None,
        "date_only": row.date_only,
        "window_start": utc(row.window_start) if row.window_start else None,
        "window_end": utc(row.window_end) if row.window_end else None,
        "date_hint": row.date_hint,
        "needs_clarification": row.needs_clarification,
        "deadline_certainty": enrichment["deadline_certainty"],
        "deadline_origin": enrichment["deadline_origin"],
        "deadline_label": enrichment["deadline_label"],
        "source_message_ids": json.loads(row.source_message_ids),
        "sources": enrichment["sources"],
        "updates": enrichment["updates"],
        "materials": enrichment["materials"],
        "revision": row.revision,
        "cancelled_at": utc(row.cancelled_at) if row.cancelled_at else None,
    }


async def items(db, group, now, archive=False, subject=None, review=False, hidden=frozenset()):
    rows = (
        await db.scalars(
            select(AcademicDeadline).where(
                AcademicDeadline.group_id == group.id,
                AcademicDeadline.delete_at > now,
            )
        )
    ).all()

    def normalized(text):
        return " ".join(text.casefold().replace("ё", "е").split())

    selected = [
        row
        for row in rows
        if archived(row, now, group.timezone) == archive
        and row.subgroup_id not in hidden
        and (not review or row.needs_clarification)
        and (subject is None or normalized(row.subject) == normalized(subject))
    ]
    selected.sort(
        key=lambda row: (
            utc(row.deadline_at or row.window_start)
            if (row.deadline_at or row.window_start)
            else datetime.max.replace(tzinfo=UTC),
            str(row.id),
        )
    )
    result = []
    for row in selected:
        item = await representation(db, group, row, now)
        result.append(item)
    return result


async def review_count(db, group, now):
    """Active events an editor still has to settle (no representation work needed)."""
    rows = await db.scalars(
        select(AcademicDeadline).where(
            AcademicDeadline.group_id == group.id,
            AcademicDeadline.delete_at > now,
            AcademicDeadline.needs_clarification.is_(True),
        )
    )
    return sum(1 for row in rows if not archived(row, now, group.timezone))


@router.get("/deadlines")
async def list_deadlines(
    group: Annotated[Group, Depends(active_group)],
    db: Annotated[AsyncSession, Depends(database)],
    authorization: str = Header(),
    archive: bool = False,
    review: bool = False,
):
    now = datetime.now(UTC)
    session = await db.get(WebSession, token_hash(authorization[7:]))
    editor = bool(await edit_permissions(db, group.id, session.user_id))
    if review and not editor:
        raise ApiError("permission_denied", "Очередь проверки доступна старосте и помощнику", 403)
    if review and archive:
        raise ApiError("invalid_request", "Проверка относится только к актуальным событиям", 422)
    response = {
        "generated_at": now,
        "group_timezone": group.timezone,
        "items": await items(
            db,
            group,
            now,
            archive,
            review=review,
            hidden=await subgroups.hidden(db, group.id, session.user_id),
        ),
    }
    if editor:
        # Lets the client show the review tab only while there is something to review.
        response["review_count"] = await review_count(db, group, now)
    return response


@router.get("/deadlines/{deadline_id}")
async def get_deadline(
    deadline_id: uuid.UUID,
    group: Annotated[Group, Depends(active_group)],
    db: Annotated[AsyncSession, Depends(database)],
    authorization: str = Header(),
):
    now = datetime.now(UTC)
    row = await db.scalar(
        select(AcademicDeadline).where(
            AcademicDeadline.id == deadline_id,
            AcademicDeadline.group_id == group.id,
            AcademicDeadline.delete_at > now,
        )
    )
    session = await db.get(WebSession, token_hash(authorization[7:]))
    if row is None or row.subgroup_id in await subgroups.hidden(db, group.id, session.user_id):
        raise ApiError("not_found", "Событие не найдено", 404)
    item = await representation(db, group, row, now)
    return {
        "generated_at": now,
        "group_timezone": group.timezone,
        "item": item,
        "permissions": await edit_permissions(db, group.id, session.user_id),
    }
