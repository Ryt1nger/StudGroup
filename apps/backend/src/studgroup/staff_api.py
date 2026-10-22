"""Headman/deputy cabinet API: group timetable, days without lessons, settings, card deadlines.

Thin HTTP layer over the same domain code the headman bot uses (``headman_schedule``, revision
guards, audit) plus ``schedule_changed`` and ``staff_items``. The membership role is re-read and
locked on every request; the client's permission list is only a hint for the UI.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Annotated, Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, Header
from pydantic import AwareDatetime, BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from studgroup import headman_schedule, staff_items
from studgroup.api import ApiError, active_membership, database, schedule_data
from studgroup.headman_domain import StError
from studgroup.models import (
    Group,
    GroupNoLessonDay,
    Membership,
    ScheduleException,
    SchedulePattern,
    User,
)
from studgroup.schedule_audit import schedule_changed

router = APIRouter(prefix="/v1/staff")
METHOD = "cabinet"


@dataclass
class Staff:
    group: Group
    user: User
    member: Membership


async def staff(
    db: Annotated[AsyncSession, Depends(database)], authorization: str | None = Header(None)
):
    group, member = await active_membership(db, authorization)
    # Serialize with role revocation, as the bot does: re-read the locked membership row.
    member = await db.scalar(
        select(Membership)
        .where(Membership.id == member.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if member.status != "active" or member.role not in {"headman", "deputy"}:
        raise ApiError("permission_denied", "Доступно старосте и заместителю", 403)
    return Staff(group, await db.get(User, member.user_id), member)


CurrentStaff = Annotated[Staff, Depends(staff)]
Db = Annotated[AsyncSession, Depends(database)]


def headman_only(context):
    if context.member.role != "headman":
        raise ApiError("permission_denied", "Это может изменить только староста", 403)


def rejected(error):
    return ApiError("action_rejected", str(error), 409)


def item_error(error):
    status = {
        "not_found": 404,
        "revision_conflict": 409,
        "action_rejected": 409,
        "invalid_request": 422,
    }[error.code]
    return ApiError(error.code, error.message, status)


def schedule_result(group):
    return {"schedule_version": group.schedule_version}


# --- timetable -------------------------------------------------------------------------------


@router.get("/schedule")
async def staff_schedule(start: date, end: date, context: CurrentStaff, db: Db):
    """The student calendar plus what the editor needs: revisions, week kind, days off."""
    group = context.group
    data = await schedule_data(start, end, group, db)
    patterns = {
        str(row.id): row
        for row in await db.scalars(
            select(SchedulePattern).where(SchedulePattern.group_id == group.id)
        )
    }
    overrides = {
        (str(row.pattern_id), row.occurrence_date): row.revision
        for row in await db.scalars(
            select(ScheduleException).where(ScheduleException.group_id == group.id)
        )
    }
    edits = []
    for lesson in data["lessons"]:
        pattern_id, day = lesson["id"].split(":")
        pattern = patterns.get(pattern_id)
        if pattern is None:
            continue
        edits.append(
            {
                "lesson_id": lesson["id"],
                "pattern_revision": pattern.revision,
                "override_revision": overrides.get((pattern_id, date.fromisoformat(day)), 0),
                "week": pattern.week,
                "single_day": pattern.valid_from == pattern.valid_until,
            }
        )
    return {
        "schedule": data,
        "lesson_edits": edits,
        "no_lesson_days": [no_lesson_day(row) for row in await days_off(db, group, start, end)],
        "schedule_version": group.schedule_version,
    }


class LessonCreate(BaseModel):
    subject: str = Field(min_length=1, max_length=255)
    starts_at: AwareDatetime
    ends_at: AwareDatetime
    location: str | None = Field(None, max_length=512)


@router.post("/schedule/lessons", status_code=201)
async def create_lesson(body: LessonCreate, context: CurrentStaff, db: Db):
    try:
        await headman_schedule.add(
            db,
            context.group,
            context.user,
            context.member,
            body.subject.strip(),
            body.starts_at,
            body.ends_at,
            body.location,
            method=METHOD,
        )
    except StError as error:
        raise rejected(error) from None
    await db.commit()
    return schedule_result(context.group)


class LessonChange(BaseModel):
    scope: Literal["once", "recurring"]
    pattern_revision: int
    override_revision: int
    starts_at: AwareDatetime | None = None
    ends_at: AwareDatetime | None = None
    location: str | None = Field(None, max_length=512)
    online_url: str | None = Field(None, max_length=2048)
    cancelled: bool | None = None


@router.patch("/schedule/lessons/{lesson_id}")
async def change_lesson(lesson_id: str, body: LessonChange, context: CurrentStaff, db: Db):
    patch = {
        field: getattr(body, field)
        for field in ("starts_at", "ends_at", "location", "online_url", "cancelled")
        if field in body.model_fields_set
    }
    if ("starts_at" in patch) != ("ends_at" in patch):
        raise ApiError("invalid_request", "Укажите и начало, и конец пары", 422)
    if None in (patch.get("starts_at", 0), patch.get("ends_at", 0), patch.get("cancelled", 0)):
        raise ApiError("invalid_request", "Время и отмена пары не могут быть пустыми", 422)
    if not patch:
        raise ApiError("invalid_request", "Нет изменений", 422)
    try:
        await headman_schedule.change(
            db,
            context.group,
            context.user,
            context.member,
            lesson_id,
            body.pattern_revision,
            body.override_revision,
            body.scope,
            patch,
            method=METHOD,
        )
    except StError as error:
        raise rejected(error) from None
    await db.commit()
    return schedule_result(context.group)


# --- days without lessons --------------------------------------------------------------------


async def days_off(db, group, start=None, end=None):
    query = select(GroupNoLessonDay).where(GroupNoLessonDay.group_id == group.id)
    if start:
        query = query.where(GroupNoLessonDay.day >= start)
    if end:
        query = query.where(GroupNoLessonDay.day <= end)
    return (await db.scalars(query.order_by(GroupNoLessonDay.day))).all()


def no_lesson_day(row):
    return {"day": row.day, "reason": row.reason}


@router.get("/no-lesson-days")
async def list_no_lesson_days(
    context: CurrentStaff, db: Db, start: date | None = None, end: date | None = None
):
    return {"items": [no_lesson_day(row) for row in await days_off(db, context.group, start, end)]}


class NoLessonDayCreate(BaseModel):
    day: date
    reason: str | None = Field(None, max_length=255)


@router.post("/no-lesson-days", status_code=201)
async def add_no_lesson_day(body: NoLessonDayCreate, context: CurrentStaff, db: Db):
    group = context.group
    existing = await db.scalar(
        select(GroupNoLessonDay).where(
            GroupNoLessonDay.group_id == group.id, GroupNoLessonDay.day == body.day
        )
    )
    if existing:
        raise ApiError("action_rejected", "Этот день уже отмечен как день без пар", 409)
    db.add(
        GroupNoLessonDay(
            group_id=group.id,
            day=body.day,
            reason=(body.reason or "").strip() or None,
            created_at=datetime.now(UTC),
        )
    )
    await db.flush()
    await schedule_changed(
        db,
        group,
        method=METHOD,
        operation="no_lesson_day_add",
        user=context.user,
        member=context.member,
    )
    await db.commit()
    return schedule_result(group)


@router.delete("/no-lesson-days/{day}")
async def remove_no_lesson_day(day: date, context: CurrentStaff, db: Db):
    group = context.group
    row = await db.scalar(
        select(GroupNoLessonDay)
        .where(GroupNoLessonDay.group_id == group.id, GroupNoLessonDay.day == day)
        .with_for_update()
    )
    if row is None:
        raise ApiError("not_found", "Такого дня без пар нет", 404)
    await db.delete(row)
    await db.flush()
    await schedule_changed(
        db,
        group,
        method=METHOD,
        operation="no_lesson_day_remove",
        user=context.user,
        member=context.member,
    )
    await db.commit()
    return schedule_result(group)


# --- group settings --------------------------------------------------------------------------


class GroupSettingsChange(BaseModel):
    schedule_version: int
    timezone: str | None = Field(None, min_length=1, max_length=64)
    first_week_anchor: date | None = None


@router.patch("/group/settings")
async def change_group_settings(body: GroupSettingsChange, context: CurrentStaff, db: Db):
    headman_only(context)
    group = await db.scalar(
        select(Group)
        .where(Group.id == context.group.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if group.schedule_version != body.schedule_version:
        raise ApiError("revision_conflict", "Расписание изменилось. Откройте настройки заново", 409)
    changes = body.model_fields_set & {"timezone", "first_week_anchor"}
    if not changes or None in (getattr(body, field) for field in changes):
        raise ApiError("invalid_request", "Укажите часовой пояс или первую неделю", 422)
    if "timezone" in changes:
        try:
            ZoneInfo(body.timezone)
        except (ZoneInfoNotFoundError, ValueError):
            raise ApiError("invalid_request", "Неизвестный часовой пояс", 422) from None
        group.timezone = body.timezone
    if "first_week_anchor" in changes:
        group.first_week_anchor = body.first_week_anchor
    await db.flush()
    await schedule_changed(
        db,
        group,
        method=METHOD,
        operation="settings",
        user=context.user,
        member=context.member,
    )
    await db.commit()
    return {
        "schedule_version": group.schedule_version,
        "timezone": group.timezone,
        "first_week_anchor": group.first_week_anchor,
    }


# --- card deadlines and field locks ----------------------------------------------------------


@router.get("/deadline-queue")
async def deadline_queue(context: CurrentStaff, db: Db):
    now = datetime.now(UTC)
    items = await staff_items.deadline_queue(db, context.group)
    return {"items": [await staff_items.staff_view(db, context.group, i, now) for i in items]}


async def load_item(db, context, item_id):
    try:
        return await staff_items.find_item(db, context.group, item_id)
    except staff_items.StaffItemError as error:
        raise item_error(error) from None


@router.get("/items/{item_id}")
async def staff_item(item_id: UUID, context: CurrentStaff, db: Db):
    item = await load_item(db, context, item_id)
    return await staff_items.staff_view(db, context.group, item, datetime.now(UTC))


class DeadlineAction(BaseModel):
    action: Literal["set", "confirm"]
    revision: int
    deadline_at: AwareDatetime | None = None
    date_only: bool = False


@router.post("/items/{item_id}/deadline")
async def item_deadline(item_id: UUID, body: DeadlineAction, context: CurrentStaff, db: Db):
    item = await load_item(db, context, item_id)
    now = datetime.now(UTC)
    args = (db, context.group, context.user, context.member, item, body.revision)
    try:
        if body.action == "set":
            if body.deadline_at is None:
                raise ApiError("invalid_request", "Укажите срок", 422)
            await staff_items.set_deadline(*args, body.deadline_at, body.date_only, now)
        else:
            await staff_items.confirm_deadline(*args, now)
    except staff_items.StaffItemError as error:
        raise item_error(error) from None
    await db.commit()
    return await staff_items.staff_view(db, context.group, item, now)


class FieldLocks(BaseModel):
    revision: int
    locked_fields: list[Literal["deadline", "subject", "kind", "title", "description"]]


@router.put("/items/{item_id}/locks")
async def item_locks(item_id: UUID, body: FieldLocks, context: CurrentStaff, db: Db):
    item = await load_item(db, context, item_id)
    now = datetime.now(UTC)
    try:
        await staff_items.set_locks(
            db,
            context.group,
            context.user,
            context.member,
            item,
            body.revision,
            body.locked_fields,
            now,
        )
    except staff_items.StaffItemError as error:
        raise item_error(error) from None
    await db.commit()
    return await staff_items.staff_view(db, context.group, item, now)
