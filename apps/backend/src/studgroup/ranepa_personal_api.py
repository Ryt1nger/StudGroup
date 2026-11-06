"""Student's own last successful university timetable, never another member's."""

from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession

from studgroup.api import ApiError, active_group, database
from studgroup.models import Group
from studgroup.profile import current
from studgroup.ranepa_personal import personal_with_group_fallback

router = APIRouter(prefix="/v1")


@router.get("/ranepa-schedule")
async def my_ranepa_schedule(
    start: date,
    end: date,
    group: Annotated[Group, Depends(active_group)],
    db: Annotated[AsyncSession, Depends(database)],
    authorization: str | None = Header(None),
):
    user, _ = await current(db, authorization)
    try:
        return await personal_with_group_fallback(db, group.id, user.id, start, end)
    except ValueError:
        raise ApiError("invalid_request", "Выберите период до 32 дней", 422) from None
