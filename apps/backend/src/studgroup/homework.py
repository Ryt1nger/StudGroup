"""Group-scoped homework reads and revision-protected personal completion."""

from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from studgroup.api import ApiError, active_group, database
from studgroup.models import Group, Homework, PersonalCompletion, RawMessage, WebSession
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
    source = {"kind": "telegram_group_message", "imported": False, "availability": availability}
    return {
        "id": row.id,
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
        "verification_state": row.verification_state,
        "revision": row.revision,
        "source": source,
        "source_detail": {
            **source,
            "message_date": utc(raw.message_date) if raw else None,
            "excerpt": raw.text[:1000] if available else None,
            "action": {"type": "open_telegram_link" if url else "none", "url": url},
        },
        "processing": {"state": "none", "retry_after_seconds": None},
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


@router.get("/today")
async def today(group: CurrentGroup, db: Database, authorization: str = Header()):
    now = datetime.now(UTC)
    current_date = now.astimezone(ZoneInfo(group.timezone)).date()
    user = await user_id(db, authorization)
    rows = (
        await db.scalars(
            select(Homework).where(
                Homework.group_id == group.id,
                Homework.status.in_(["published", "needs_clarification"]),
                Homework.delete_at > now,
            )
        )
    ).all()
    buckets = {"overdue": [], "due_today": [], "new_or_changed": [], "upcoming": []}
    candidates = []
    for row in rows:
        card = await representation(db, row, user, now)
        mark = card["my_state"]
        if mark["completion"] == "completed" and (
            not row.significant_updated_at
            or utc(row.significant_updated_at) <= mark["completed_at"]
        ):
            continue
        deadline = utc(row.deadline_at) if row.deadline_at else None
        due = deadline.astimezone(ZoneInfo(group.timezone)).date() if deadline else None
        if due and due < current_date:
            buckets["overdue"].append(card)
        elif due == current_date:
            buckets["due_today"].append(card)
        elif utc(row.created_at) >= now - timedelta(hours=24) or (
            row.significant_updated_at
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
    return {
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
