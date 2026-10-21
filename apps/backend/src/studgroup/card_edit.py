"""Headman/deputy editing of published cards from the Mini App.

A thin, authoritative HTTP layer over ``headman_domain.mutate``: the same revision guard, audit trail,
undo data, student notification and AI lock as an edit made through the Telegram bot. The client
never decides who may edit: every request re-checks an active ``headman``/``deputy`` membership in
the card's group on the server.
"""

import uuid
from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from studgroup import headman_domain as domain
from studgroup.academic_deadlines import representation as deadline_representation
from studgroup.api import EDIT_PERMISSIONS, ApiError, active_group, database
from studgroup.homework import find_card
from studgroup.homework import representation as homework_representation
from studgroup.models import AcademicDeadline, Group, User, WebSession
from studgroup.security import token_hash
from studgroup.subject_identity import subject_uuid

router = APIRouter(prefix="/v1")
Database = Annotated[AsyncSession, Depends(database)]
CurrentGroup = Annotated[Group, Depends(active_group)]

DEADLINE_KINDS = (
    "control_point",
    "assessment",
    "test",
    "credit",
    "exam",
    "other_academic_deadline",
)


class DeadlineValue(BaseModel):
    model_config = ConfigDict(extra="forbid")
    at: datetime | None = None
    date_only: bool = False

    @model_validator(mode="after")
    def consistent(self):
        if self.at is not None and self.at.utcoffset() is None:
            raise ValueError("deadline_requires_offset")
        if self.at is None and self.date_only:
            raise ValueError("unknown_deadline_cannot_be_date_only")
        if self.at is not None:
            self.at = self.at.astimezone(UTC)  # stored and compared in UTC everywhere
        return self


def clean(value, *, required, limit):
    text = " ".join(value.split()) if limit <= 255 else value.strip()
    if required and not text:
        raise ValueError("must_not_be_empty")
    if len(text) > limit:
        raise ValueError("too_long")
    return text


class EditHomeworkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)
    title: str | None = None
    description: str | None = None
    subject_name: str | None = None
    deadline: DeadlineValue | None = None

    @field_validator("title", "subject_name")
    @classmethod
    def short_text(cls, value):
        return None if value is None else clean(value, required=True, limit=255)

    @field_validator("description")
    @classmethod
    def long_text(cls, value):
        return None if value is None else clean(value, required=False, limit=8000)


class EditDeadlineRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)
    kind: Literal[DEADLINE_KINDS] | None = None
    title: str | None = None
    description: str | None = None
    subject: str | None = None
    deadline: DeadlineValue | None = None
    date_hint: str | None = None

    @field_validator("title", "subject")
    @classmethod
    def short_text(cls, value):
        return None if value is None else clean(value, required=True, limit=255)

    @field_validator("description")
    @classmethod
    def long_text(cls, value):
        return None if value is None else clean(value, required=False, limit=8000)

    @field_validator("date_hint")
    @classmethod
    def hint(cls, value):
        return None if value is None else (clean(value, required=False, limit=1000) or None)


async def editor(db, group, authorization):
    """The caller as (user, membership); only an active headman/deputy of THIS group passes."""
    session = await db.get(WebSession, token_hash(authorization[7:]))
    user = await db.get(User, session.user_id)
    try:
        _user, member, _group = await domain.access(db, user.telegram_user_id, group.id)
    except domain.StError as error:
        raise ApiError("permission_denied", str(error), 403) from None
    return user, member


def provided(body):
    return body.model_fields_set - {"expected_revision"}


async def apply(db, group, user, member, key, row, expected, values, only_description):
    if row.revision != expected:
        raise ApiError("revision_conflict", "Данные карточки изменились. Обновите карточку", 409)
    try:
        await domain.mutate(
            db,
            group,
            user,
            member,
            key,
            expected,
            "description" if only_description else "edit",
            values,
        )
    except domain.StError as error:
        raise ApiError("permission_denied", str(error), 403) from None


@router.patch("/homework/{homework_id}")
async def edit_homework(
    homework_id: uuid.UUID,
    body: EditHomeworkRequest,
    group: CurrentGroup,
    db: Database,
    authorization: str = Header(),
):
    user, member = await editor(db, group, authorization)
    row = await find_card(db, group, homework_id)
    fields = provided(body)
    if not fields:
        raise ApiError("invalid_request", "Нет изменений", 422)
    values = {}
    if "title" in fields:
        if body.title is None:
            raise ApiError("invalid_request", "Название не может быть пустым", 422)
        values["title"] = body.title
    if "description" in fields:
        values["description"] = body.description or None
    if "subject_name" in fields:
        if body.subject_name is None:
            raise ApiError("invalid_request", "Предмет не может быть пустым", 422)
        values["subject_name"] = body.subject_name
        values["subject_id"] = subject_uuid(group.id, body.subject_name)
    if "deadline" in fields:
        deadline = body.deadline or DeadlineValue()
        at, date_only = deadline.at, deadline.date_only
        if date_only:
            # a date without time means the lesson of the subject on that day (as in the bot)
            from studgroup.homework_timing import lesson_deadline

            at, date_only = await lesson_deadline(
                db, group, values.get("subject_name", row.subject_name), at, True, ""
            )
        values["deadline_at"], values["deadline_date_only"] = at, date_only
    await apply(
        db,
        group,
        user,
        member,
        f"h:{row.id}",
        row,
        body.expected_revision,
        values,
        fields == {"description"},
    )
    now = datetime.now(UTC)
    result = await homework_representation(db, row, user.id, now)
    await db.commit()
    return result


@router.patch("/deadlines/{deadline_id}")
async def edit_deadline(
    deadline_id: uuid.UUID,
    body: EditDeadlineRequest,
    group: CurrentGroup,
    db: Database,
    authorization: str = Header(),
):
    user, member = await editor(db, group, authorization)
    now = datetime.now(UTC)
    row = await db.scalar(
        select(AcademicDeadline)
        .where(
            AcademicDeadline.id == deadline_id,
            AcademicDeadline.group_id == group.id,
            AcademicDeadline.delete_at > now,
        )
        .with_for_update()
    )
    if row is None:
        raise ApiError("not_found", "Событие не найдено", 404)
    fields = provided(body)
    if not fields:
        raise ApiError("invalid_request", "Нет изменений", 422)
    values = {}
    for name in ("kind", "title", "subject"):
        if name in fields:
            if getattr(body, name) is None:
                raise ApiError("invalid_request", "Поле не может быть пустым", 422)
            values[name] = getattr(body, name)
    if "description" in fields:
        values["description"] = body.description or ""
    if "date_hint" in fields:
        values["date_hint"] = body.date_hint
    if "deadline" in fields:
        deadline = body.deadline or DeadlineValue()
        values["deadline_at"], values["date_only"] = deadline.at, deadline.date_only
        # an explicit date replaces any earlier period of the event
        values["window_start"] = values["window_end"] = None
    await apply(
        db,
        group,
        user,
        member,
        f"e:{row.id}",
        row,
        body.expected_revision,
        values,
        fields == {"description"},
    )
    item = await deadline_representation(db, group, row, now)
    await db.commit()
    return {
        "generated_at": now,
        "group_timezone": group.timezone,
        "item": item,
        "permissions": list(EDIT_PERMISSIONS),
    }
