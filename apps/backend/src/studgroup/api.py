"""Database-backed session bootstrap and group-scoped schedule read API."""

from datetime import UTC, date, datetime, timedelta
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from studgroup.models import Group, Membership, SchedulePattern, User, WebSession
from studgroup.schedule import LessonPattern, expand, week_kind
from studgroup.security import InvalidInitData, issue_token, token_hash, verify_init_data

router = APIRouter(prefix="/v1")


class ApiError(Exception):
    def __init__(self, code, message, status=401):
        self.code, self.message, self.status = code, message, status


async def database(request: Request):
    async with AsyncSession(request.app.state.engine, expire_on_commit=False) as session:
        yield session


class BootstrapBody(BaseModel):
    init_data: str


@router.post("/session/bootstrap")
async def bootstrap(
    body: BootstrapBody, request: Request, db: Annotated[AsyncSession, Depends(database)]
):
    settings = request.app.state.settings
    if not settings.telegram_bot_token:
        raise ApiError("service_unavailable", "Вход временно недоступен", 503)
    now = datetime.now(UTC)
    try:
        identity = verify_init_data(
            body.init_data, settings.telegram_bot_token, now=int(now.timestamp())
        )
    except InvalidInitData as error:
        raise ApiError(error.code, "Откройте приложение заново из Telegram") from None
    query = insert(User).values(
        id=uuid4(),
        telegram_user_id=identity.telegram_user_id,
        display_name=identity.display_name,
        username=identity.username,
    )
    query = query.on_conflict_do_update(
        index_elements=[User.telegram_user_id],
        set_={"display_name": identity.display_name, "username": identity.username},
    ).returning(User)
    user = (await db.execute(query)).scalar_one()
    member = await db.scalar(
        select(Membership)
        .where(Membership.user_id == user.id, Membership.status == "active")
        .order_by(Membership.id)
        .limit(1)
    )
    group = await db.get(Group, member.group_id) if member else None
    if group is not None and (not group.pilot_authorized or group.status != "active"):
        member = None
        group = None
    token, digest = issue_token()
    expires = now + timedelta(hours=12)
    db.add(
        WebSession(
            token_hash=digest,
            user_id=user.id,
            membership_id=member.id if member else None,
            expires_at=expires,
        )
    )
    await db.commit()
    permissions = (
        ["today.read", "homework.read", "homework.completion.write", "schedule.read"]
        if member
        else []
    )
    return {
        "access_token": token,
        "token_type": "Bearer",
        "expires_at": expires,
        "session": {
            "user": {
                "id": user.id,
                "telegram_user_id": user.telegram_user_id,
                "display_name": user.display_name,
                "username": user.username,
            },
            "access_state": "active" if member else "no_active_group",
            "membership": {
                "id": member.id,
                "role": member.role,
                "status": member.status,
                "can_recheck": False,
            }
            if member
            else None,
            "group": {
                "id": group.id,
                "name": group.name,
                "timezone": group.timezone,
                "status": group.status,
                "pilot_authorized": group.pilot_authorized,
            }
            if group
            else None,
            "permissions": permissions,
            "server_time": now,
        },
    }


async def active_group(
    db: Annotated[AsyncSession, Depends(database)], authorization: str | None = Header(None)
):
    if not authorization or not authorization.startswith("Bearer "):
        raise ApiError("unauthenticated", "Откройте приложение из Telegram")
    credential = await db.get(WebSession, token_hash(authorization[7:]))
    expiry = credential.expires_at if credential else None
    if expiry is not None and expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=UTC)
    if credential is None or expiry <= datetime.now(UTC):
        raise ApiError("session_expired", "Откройте приложение заново из Telegram")
    member = (
        await db.get(Membership, credential.membership_id) if credential.membership_id else None
    )
    if member is None:
        raise ApiError("no_active_group", "Нет активной группы", 403)
    if member.user_id != credential.user_id or member.status != "active":
        raise ApiError("membership_suspended", "Доступ к группе приостановлен", 403)
    group = await db.get(Group, member.group_id)
    if group is None:
        raise ApiError("no_active_group", "Нет активной группы", 403)
    if not group.pilot_authorized or group.status != "active":
        raise ApiError("permission_denied", "Доступ к группе недоступен", 403)
    return group


@router.get("/schedule")
async def schedule(
    start: date,
    end: date,
    group: Annotated[Group, Depends(active_group)],
    db: Annotated[AsyncSession, Depends(database)],
):
    if end < start or (end - start).days > 31:
        raise ApiError("invalid_request", "Выберите период до 32 дней", 422)
    patterns = (
        await db.scalars(
            select(SchedulePattern).where(
                SchedulePattern.group_id == group.id,
                SchedulePattern.valid_from <= end,
                SchedulePattern.valid_until >= start,
            )
        )
    ).all()
    lessons = []
    unresolved = False
    for row in patterns:
        if row.week != "all" and group.first_week_anchor is None:
            unresolved = True
            continue
        lessons.extend(
            expand(
                LessonPattern(
                    str(row.id),
                    row.subject,
                    row.weekday,
                    row.starts,
                    row.ends,
                    row.valid_from,
                    row.valid_until,
                    row.week,
                    row.teacher,
                    row.location,
                ),
                start,
                end,
                group.timezone,
                group.first_week_anchor,
            )
        )
    lessons.sort(key=lambda lesson: (lesson["starts_at"], lesson["id"]))
    return {
        "generated_at": datetime.now(UTC),
        "group_timezone": group.timezone,
        "start": start,
        "end": end,
        "week_state": "needs_clarification" if unresolved else "ready",
        "first_week_anchor": group.first_week_anchor,
        "selected_week": week_kind(start, group.first_week_anchor)
        if group.first_week_anchor
        else None,
        "lessons": lessons,
    }
