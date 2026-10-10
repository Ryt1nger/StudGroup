"""Group-scoped homework reads and revision-protected personal completion."""

from datetime import UTC, date, datetime, time, timedelta
from typing import Annotated, Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Header, Query
from pydantic import BaseModel, Field
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from studgroup.academic_deadlines import items as deadline_items
from studgroup.api import ApiError, active_group, database, schedule_data
from studgroup.materials import links
from studgroup.models import (
    Group,
    Homework,
    Membership,
    PersonalCompletion,
    RawMessage,
    ScheduleException,
    SchedulePattern,
    WebSession,
)
from studgroup.pagination import decode_cursor, encode_cursor
from studgroup.schedule import next_lesson
from studgroup.security import token_hash

router = APIRouter(prefix="/v1")
Database = Annotated[AsyncSession, Depends(database)]
CurrentGroup = Annotated[Group, Depends(active_group)]


def utc(value):
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


async def user_id(db, authorization):
    session = await db.get(WebSession, token_hash(authorization[7:]))
    return session.user_id


async def representation(db, row, user, now):
    raw = await db.get(RawMessage, row.raw_message_id) if row.raw_message_id else None
    completed = await db.scalar(
        select(PersonalCompletion).where(
            PersonalCompletion.homework_id == row.id, PersonalCompletion.user_id == user
        )
    )
    available = raw is not None and utc(raw.delete_at) > now
    group = await db.get(Group, row.group_id)
    chat_id = str(group.telegram_chat_id)
    url = (
        f"https://t.me/c/{chat_id[4:]}/{raw.telegram_message_id}"
        if available and chat_id.startswith("-100")
        else None
    )
    availability = {
        "state": "available" if url else "unavailable",
        "reason": None if url else "retention_expired" if not available else "no_deep_link",
    }
    imported = bool(raw and raw.imported)
    membership = await db.scalar(
        select(Membership).where(
            Membership.user_id == user,
            Membership.group_id == row.group_id,
            Membership.status == "active",
        )
    )
    verification_state = row.verification_state
    if verification_state == "needs_clarification" and (
        membership is None or membership.role not in {"headman", "deputy"}
    ):
        verification_state = "from_group_message"
    source = {
        "kind": "manual_entry"
        if row.raw_message_id is None
        else "imported_history"
        if imported
        else "telegram_group_message",
        "imported": imported,
        "availability": availability,
    }
    return {
        "id": row.id,
        "materials": await links(db, row.group_id, [f"h:{row.id}"]),
        "type": "homework",
        "title": row.title,
        "subject": {"id": row.subject_id, "name": row.subject_name},
        "summary": row.description,
        "description": row.description,
        "deadline": {
            "state": "known" if row.deadline_at else "unknown",
            "at": utc(row.deadline_at) if row.deadline_at else None,
            "date_only": row.deadline_date_only,
        },
        "status": row.status,
        "urgency": row.urgency,
        "visibility": "group",
        "verification_state": verification_state,
        "revision": row.revision,
        "source": source,
        "source_detail": {
            **source,
            "message_date": utc(raw.message_date) if raw else None,
            "excerpt": raw.text[:1000] if available else None,
            "action": {"type": "open_telegram_link" if url else "none", "url": url},
        },
        "processing": {"state": "none", "retry_after_seconds": None},
        "cancelled_at": utc(row.cancelled_at) if row.cancelled_at else None,
        "significant_update": row.significant_updated_at is not None,
        "significant_updated_at": utc(row.significant_updated_at)
        if row.significant_updated_at
        else None,
        "my_state": {
            "completion": "completed" if completed and completed.completed_at else "pending",
            "completed_at": utc(completed.completed_at)
            if completed and completed.completed_at
            else None,
            "completed_revision": completed.completed_revision if completed else None,
        },
        "created_at": utc(row.created_at),
        "updated_at": utc(row.updated_at),
        "generated_at": now,
        "permissions": ["homework.read", "homework.completion.write"],
    }


async def find_card(db, group, homework_id):
    row = await db.scalar(
        select(Homework)
        .where(Homework.id == homework_id, Homework.group_id == group.id)
        .with_for_update()
    )
    if row is None:
        raise ApiError("not_found", "Задание не найдено", 404)
    if utc(row.delete_at) <= datetime.now(UTC):
        raise ApiError("gone", "Эти данные больше не хранятся", 410)
    if row.status not in {"published", "needs_clarification", "completed", "cancelled"}:
        raise ApiError("not_found", "Задание не найдено", 404)
    return row


def archived_condition(now, timezone):
    day = now.astimezone(ZoneInfo(timezone)).date()
    start = datetime.combine(day, time.min, ZoneInfo(timezone)).astimezone(UTC)
    return or_(
        Homework.status == "cancelled",
        and_(
            Homework.deadline_at.is_not(None),
            Homework.deadline_date_only.is_(True),
            Homework.deadline_at < start,
        ),
        and_(
            Homework.deadline_at.is_not(None),
            Homework.deadline_date_only.is_(False),
            Homework.deadline_at <= now,
        ),
        and_(
            or_(Homework.deadline_at.is_(None), Homework.verification_state == "inferred"),
            func.coalesce(
                Homework.source_message_at,
                select(RawMessage.message_date)
                .where(RawMessage.id == Homework.raw_message_id)
                .scalar_subquery(),
                Homework.created_at,
            )
            < now - timedelta(days=7),
        ),
    )


@router.get("/homework")
async def homework_list(
    group: CurrentGroup,
    db: Database,
    authorization: str = Header(),
    filter: Literal["all", "today", "week", "mine", "archive"] = "all",
    limit: int = Query(default=50, ge=1, le=100),
    cursor: str | None = Query(default=None, max_length=2048),
):
    generated = datetime.now(UTC)
    selected_at = generated
    after = None
    if cursor is not None:
        selected_at, created, last_id = decode_cursor(
            cursor, group.id, filter, generated, authorization
        )
        after = or_(
            Homework.created_at > created,
            and_(Homework.created_at == created, Homework.id > last_id),
        )
    user = await user_id(db, authorization)
    query = (
        select(Homework)
        .outerjoin(
            PersonalCompletion,
            and_(
                PersonalCompletion.homework_id == Homework.id,
                PersonalCompletion.user_id == user,
            ),
        )
        .where(
            Homework.group_id == group.id,
            Homework.status.in_(["published", "needs_clarification", "completed", "cancelled"]),
            Homework.delete_at > generated,
            Homework.created_at <= selected_at,
        )
    )
    archived = archived_condition(generated, group.timezone)
    # SQL comparisons to NULL must not make an unknown deadline disappear.
    active = and_(
        Homework.status != "cancelled",
        ~archived,
    )
    query = query.where(archived if filter == "archive" else active)
    if filter == "mine":
        query = query.where(
            PersonalCompletion.completed_at.is_not(None), Homework.status != "cancelled"
        )
    elif filter in {"today", "week"}:
        day = selected_at.astimezone(ZoneInfo(group.timezone)).date()
        start_day = day if filter == "today" else day - timedelta(days=day.weekday())
        start = datetime.combine(start_day, time.min, ZoneInfo(group.timezone)).astimezone(UTC)
        end = datetime.combine(
            start_day + timedelta(days=1 if filter == "today" else 7),
            time.min,
            ZoneInfo(group.timezone),
        ).astimezone(UTC)
        deadlines = and_(Homework.deadline_at >= start, Homework.deadline_at < end)
        query = query.where(
            PersonalCompletion.completed_at.is_(None), Homework.status != "cancelled", deadlines
        )
    if after is not None:
        query = query.where(after)
    rows = (
        await db.scalars(query.order_by(Homework.created_at, Homework.id).limit(limit + 1))
    ).all()
    items = []
    for row in rows[:limit]:
        card = await representation(db, row, user, generated)
        for detail_field in ("description", "source_detail", "generated_at", "permissions"):
            card.pop(detail_field)
        items.append(card)
    next_cursor = None
    if len(rows) > limit:
        last = rows[limit - 1]
        next_cursor = encode_cursor(
            group.id, filter, selected_at, utc(last.created_at), last.id, authorization
        )
    pending = await db.scalar(
        select(RawMessage.id)
        .where(RawMessage.group_id == group.id, RawMessage.processing_state == "pending")
        .limit(1)
    )
    return {
        "server_time": selected_at,
        "generated_at": generated,
        "group_timezone": group.timezone,
        "freshness": {"last_successful_sync_at": generated, "stale": False},
        "processing": {
            "state": "updating" if pending else "idle",
            "retry_after_seconds": 10 if pending else None,
        },
        "items": items,
        "next_cursor": next_cursor,
    }


@router.get("/homework/{homework_id}")
async def detail(
    homework_id: UUID, group: CurrentGroup, db: Database, authorization: str = Header()
):
    row = await find_card(db, group, homework_id)
    return await representation(db, row, await user_id(db, authorization), datetime.now(UTC))


class CompletionBody(BaseModel):
    completed: bool
    expected_revision: int = Field(ge=1)


@router.put("/homework/{homework_id}/completion")
async def completion(
    homework_id: UUID,
    body: CompletionBody,
    group: CurrentGroup,
    db: Database,
    authorization: str = Header(),
):
    row = await find_card(db, group, homework_id)
    if row.revision != body.expected_revision:
        raise ApiError("revision_conflict", "Данные задания изменились. Обновите карточку", 409)
    user = await user_id(db, authorization)
    mark = await db.scalar(
        select(PersonalCompletion).where(
            PersonalCompletion.homework_id == row.id, PersonalCompletion.user_id == user
        )
    )
    if mark is None:
        mark = PersonalCompletion(homework_id=row.id, user_id=user)
        db.add(mark)
    if body.completed:
        if mark.completed_at is None or mark.completed_revision != row.revision:
            mark.completed_at = datetime.now(UTC)
        mark.completed_revision = row.revision
    else:
        mark.completed_at = None
        mark.completed_revision = None
    await db.flush()
    result = await representation(db, row, user, datetime.now(UTC))
    await db.commit()
    return result


@router.get("/lessons/{lesson_id}/subject")
async def lesson_subject(
    lesson_id: str, group: CurrentGroup, db: Database, authorization: str = Header()
):
    try:
        pattern_id, day_text = lesson_id.split(":", 1)
        pattern_id = UUID(pattern_id)
        lesson_day = date.fromisoformat(day_text)
    except (ValueError, TypeError):
        raise ApiError("not_found", "Пара не найдена", 404) from None
    pattern = await db.get(SchedulePattern, pattern_id)
    if pattern is None or pattern.group_id != group.id:
        raise ApiError("not_found", "Пара не найдена", 404)
    exception = await db.scalar(
        select(ScheduleException).where(
            ScheduleException.group_id == group.id,
            ScheduleException.pattern_id == pattern.id,
            ScheduleException.occurrence_date == lesson_day,
        )
    )
    if exception:
        lesson_day = utc(exception.starts_at).astimezone(ZoneInfo(group.timezone)).date()
    calendar = await schedule_data(lesson_day, lesson_day, group, db)
    lesson = next((item for item in calendar["lessons"] if item["id"] == lesson_id), None)
    if lesson is None:
        raise ApiError("not_found", "Пара не найдена", 404)
    now = datetime.now(UTC)
    starts = datetime.fromisoformat(lesson["starts_at"])
    user = await user_id(db, authorization)
    rows = (
        await db.scalars(
            select(Homework).where(
                Homework.group_id == group.id,
                Homework.status.in_(["published", "needs_clarification"]),
                Homework.delete_at > now,
                ~archived_condition(now, group.timezone),
            )
        )
    ).all()

    def normalize(name):
        return " ".join(name.casefold().replace("ё", "е").split())

    matching = []
    for row in rows:
        if normalize(row.subject_name) != normalize(lesson["subject"]):
            continue
        raw = await db.get(RawMessage, row.raw_message_id) if row.raw_message_id else None
        if utc(raw.message_date if raw else row.created_at) > starts:
            continue
        if (
            row.deadline_at
            and utc(row.deadline_at).astimezone(ZoneInfo(group.timezone)).date() > lesson_day
        ):
            continue
        matching.append(row)
    matching.sort(
        key=lambda row: (
            utc(row.deadline_at) if row.deadline_at else datetime.max.replace(tzinfo=UTC),
            str(row.id),
        )
    )
    return {
        "generated_at": now,
        "group_timezone": group.timezone,
        "lesson": lesson,
        "homework": [await representation(db, row, user, now) for row in matching[:100]],
        "homework_total": len(matching),
        "events_state": "ready",
        "events": await deadline_items(db, group, now, subject=lesson["subject"]),
        "materials_state": "ready",
        "materials": await links(
            db,
            group.id,
            [f"h:{r.id}" for r in matching]
            + [
                f"e:{r['id']}"
                for r in await deadline_items(db, group, now, subject=lesson["subject"])
            ],
        ),
    }


@router.get("/today")
async def today(
    group: CurrentGroup,
    db: Database,
    authorization: str = Header(),
    day: Literal["today", "tomorrow"] = "today",
):
    now = datetime.now(UTC)
    current_date = now.astimezone(ZoneInfo(group.timezone)).date()
    selected_date = current_date + timedelta(days=day == "tomorrow")
    user = await user_id(db, authorization)
    rows = (
        await db.scalars(
            select(Homework).where(
                Homework.group_id == group.id,
                Homework.status.in_(["published", "needs_clarification"]),
                Homework.delete_at > now,
                ~archived_condition(now, group.timezone),
            )
        )
    ).all()
    buckets = {"due_today": [], "new_or_changed": [], "upcoming": []}
    candidates = []
    for row in rows:
        card = await representation(db, row, user, now)
        mark = card["my_state"]
        if mark["completion"] == "completed" and row.deadline_at is None:
            continue
        deadline = utc(row.deadline_at) if row.deadline_at else None
        due = deadline.astimezone(ZoneInfo(group.timezone)).date() if deadline else None
        if day == "tomorrow" and due != selected_date:
            continue
        if due == selected_date:
            buckets["due_today"].append(card)
        elif (
            row.revision > 1
            and row.significant_updated_at
            and utc(row.significant_updated_at) > utc(row.created_at)
            and utc(row.significant_updated_at) >= now - timedelta(hours=24)
        ):
            buckets["new_or_changed"].append(card)
        elif deadline and deadline <= now + timedelta(days=7):
            candidates.append(card)
    buckets["upcoming"] = sorted(candidates, key=lambda card: card["deadline"]["at"])[:3]
    for cards in buckets.values():
        cards.sort(
            key=lambda card: (
                card["deadline"]["at"] or datetime.max.replace(tzinfo=UTC),
                str(card["id"]),
            )
        )
    sections = [{"kind": kind, "items": cards} for kind, cards in buckets.items() if cards]
    pending = await db.scalar(
        select(RawMessage.id)
        .where(RawMessage.group_id == group.id, RawMessage.processing_state == "pending")
        .limit(1)
    )
    calendar = await schedule_data(
        selected_date,
        selected_date if day == "tomorrow" else current_date + timedelta(days=31),
        group,
        db,
    )
    coverage = await db.scalar(
        select(SchedulePattern.id)
        .where(
            SchedulePattern.group_id == group.id,
            SchedulePattern.valid_from <= selected_date,
            SchedulePattern.valid_until >= selected_date,
        )
        .limit(1)
    )
    selected_lessons = [
        lesson
        for lesson in calendar["lessons"]
        if datetime.fromisoformat(lesson["starts_at"]).astimezone(ZoneInfo(group.timezone)).date()
        == selected_date
        and lesson["status"] == "scheduled"
    ]
    if coverage is None or calendar["week_state"] != "ready":
        day_lessons_state = "unknown"
    elif not selected_lessons:
        day_lessons_state = "empty"
    elif all(datetime.fromisoformat(lesson["ends_at"]) <= now for lesson in selected_lessons):
        day_lessons_state = "finished"
    else:
        day_lessons_state = "scheduled"
    return {
        "day_lessons_state": day_lessons_state,
        "next_lesson": next_lesson(calendar["lessons"], now),
        "generated_at": now,
        "server_time": now,
        "group_timezone": group.timezone,
        "freshness": {"last_successful_sync_at": now, "stale": False},
        "processing": {
            "state": "updating" if pending else "idle",
            "retry_after_seconds": 10 if pending else None,
        },
        "empty": not sections,
        "sections": sections,
    }
