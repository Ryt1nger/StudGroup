"""Group-scoped graded events. Never use homework completion to classify an assessment."""

import json
import uuid
from datetime import UTC, datetime
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from studgroup.api import ApiError, active_group, database
from studgroup.materials import links
from studgroup.models import AcademicDeadline, Group

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


def representation(row):
    return {
        "id": row.id,
        "kind": row.kind,
        "subject": row.subject,
        "title": row.title,
        "description": row.description,
        "deadline_at": utc(row.deadline_at) if row.deadline_at else None,
        "date_only": row.date_only,
        "window_start": utc(row.window_start) if row.window_start else None,
        "window_end": utc(row.window_end) if row.window_end else None,
        "date_hint": row.date_hint,
        "needs_clarification": row.needs_clarification,
        "source_message_ids": json.loads(row.source_message_ids),
        "revision": row.revision,
        "cancelled_at": utc(row.cancelled_at) if row.cancelled_at else None,
    }


async def items(db, group, now, archive=False, subject=None):
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
        item = representation(row)
        item["materials"] = await links(db, group.id, [f"e:{row.id}"])
        result.append(item)
    return result


@router.get("/deadlines")
async def list_deadlines(
    group: Annotated[Group, Depends(active_group)],
    db: Annotated[AsyncSession, Depends(database)],
    archive: bool = False,
):
    now = datetime.now(UTC)
    return {
        "generated_at": now,
        "group_timezone": group.timezone,
        "items": await items(db, group, now, archive),
    }


@router.get("/deadlines/{deadline_id}")
async def get_deadline(
    deadline_id: uuid.UUID,
    group: Annotated[Group, Depends(active_group)],
    db: Annotated[AsyncSession, Depends(database)],
):
    now = datetime.now(UTC)
    row = await db.scalar(
        select(AcademicDeadline).where(
            AcademicDeadline.id == deadline_id,
            AcademicDeadline.group_id == group.id,
            AcademicDeadline.delete_at > now,
        )
    )
    if row is None:
        raise ApiError("not_found", "Событие не найдено", 404)
    item = representation(row)
    item["materials"] = await links(db, group.id, [f"e:{row.id}"])
    return {"generated_at": now, "group_timezone": group.timezone, "item": item}
