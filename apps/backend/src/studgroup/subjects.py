"""Subject overview derived from active-group published data; no separate subject store."""

from collections import defaultdict
from datetime import UTC, datetime
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from studgroup.academic_deadlines import archived as deadline_archived
from studgroup.api import active_group, database
from studgroup.homework import archived_condition
from studgroup.models import AcademicDeadline, Group, Homework, SchedulePattern
from studgroup.subject_identity import match_key, subject_uuid

router = APIRouter(prefix="/v1")


PLACEHOLDER_SUBJECTS = {"предмет не указан", "неизвестно"}


def is_placeholder(value):
    return match_key(value) in PLACEHOLDER_SUBJECTS


def subject_key(value):
    # Exactly the identity formula: names that differ by ё/е or by spacing stay separate.
    return value.casefold()


@router.get("/subjects")
async def subjects(
    group: Annotated[Group, Depends(active_group)],
    db: Annotated[AsyncSession, Depends(database)],
):
    now = datetime.now(UTC)
    local_day = now.astimezone(ZoneInfo(group.timezone)).date()
    names = {}
    ids = defaultdict(list)
    homework_count = defaultdict(int)
    deadline_count = defaultdict(int)

    patterns = (
        await db.scalars(
            select(SchedulePattern).where(
                SchedulePattern.group_id == group.id,
                SchedulePattern.valid_until >= local_day,
            )
        )
    ).all()
    for row in patterns:
        key = subject_key(row.subject)
        names.setdefault(key, row.subject)

    homeworks = (
        await db.scalars(
            select(Homework).where(
                Homework.group_id == group.id,
                Homework.status.in_(["published", "needs_clarification", "completed"]),
                Homework.delete_at > now,
                ~archived_condition(now, group.timezone),
            )
        )
    ).all()
    for row in homeworks:
        if is_placeholder(row.subject_name):
            continue
        key = subject_key(row.subject_name)
        names.setdefault(key, row.subject_name)
        ids[key].append(row.subject_id)
        homework_count[key] += 1

    deadlines = (
        await db.scalars(
            select(AcademicDeadline).where(
                AcademicDeadline.group_id == group.id,
                AcademicDeadline.delete_at > now,
            )
        )
    ).all()
    for row in deadlines:
        if deadline_archived(row, now, group.timezone):
            continue
        if is_placeholder(row.subject):
            continue
        key = subject_key(row.subject)
        names.setdefault(key, row.subject)
        deadline_count[key] += 1

    items = []
    for key, name in sorted(names.items(), key=lambda item: item[1].casefold()):
        stable_id = min(ids[key], key=str) if ids[key] else subject_uuid(group.id, name)
        items.append(
            {
                "id": stable_id,
                "name": name,
                "homework_count": homework_count[key],
                "deadline_count": deadline_count[key],
            }
        )
    return {"generated_at": now, "group_timezone": group.timezone, "items": items}
