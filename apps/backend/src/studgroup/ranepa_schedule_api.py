"""Staff-only source setup and strictly read-only RANEPA schedule preview."""

from datetime import date, datetime, timedelta
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from studgroup.api import ApiError
from studgroup.models import GroupScheduleSource
from studgroup.ranepa_probe import SourceAccessError, fetch_public_page
from studgroup.ranepa_public import extract_public_schedule
from studgroup.ranepa_schedule import group_code
from studgroup.ranepa_storage import configure_source
from studgroup.staff_api import CurrentStaff, Db, headman_only

router = APIRouter(prefix="/v1/staff/ranepa-schedule", tags=["ranepa-schedule"])


class SourceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    official_group_code: str = Field(min_length=4, max_length=120)


class PreviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    internal_group_uuid: UUID
    week_start: date


@router.get("")
async def source_status(context: CurrentStaff, db: Db):
    source = await db.get(GroupScheduleSource, context.group.id)
    if source is None:
        return {"configured": False, "sync_enabled": False}
    return {
        "configured": True,
        "group_code": source.official_group_code,
        "internal_group_uuid": source.upstream_group_uuid,
        "sync_enabled": source.sync_enabled,
        "last_checked_at": source.last_checked_at,
        "last_success_at": source.last_success_at,
        "last_error": source.last_error,
    }


@router.put("")
async def update_source(body: SourceConfig, context: CurrentStaff, db: Db):
    """Headman submits university group code; never enables syncing implicitly."""
    headman_only(context)
    try:
        code = group_code(body.official_group_code)
    except ValueError:
        raise ApiError("invalid_group_code", "Неверный код учебной группы", 422) from None
    source = await configure_source(db, context.group.id, code)
    await db.commit()
    return {
        "configured": True,
        "group_code": source.official_group_code,
        "sync_enabled": source.sync_enabled,
    }


@router.post("/preview")
async def preview_source(body: PreviewRequest, context: CurrentStaff, db: Db):
    """Always read-only. No live/snapshot DB writes and no AI calls."""
    source = await db.get(GroupScheduleSource, context.group.id)
    if source is None:
        raise ApiError("group_code_required", "Сначала укажите университетский код группы", 409)
    if body.week_start.weekday() != 0:
        raise ApiError("invalid_week", "Выберите понедельник недели", 422)

    today = datetime.now(ZoneInfo(context.group.timezone)).date()
    if body.week_start < today - timedelta(days=90) or body.week_start > today + timedelta(
        days=120
    ):
        raise ApiError("invalid_week", "Неделя вне допустимого диапазона", 422)
    try:
        proof, html = await fetch_public_page(body.internal_group_uuid, source.official_group_code)
        result = extract_public_schedule(
            html,
            source.official_group_code,
            body.week_start,
            body.week_start + timedelta(days=6),
        )
    except SourceAccessError as error:
        raise ApiError(
            "ranepa_source_unavailable",
            "Сайт не предоставил подтверждённое расписание: " + str(error),
            503,
        ) from None
    if not result.lessons:
        raise ApiError(
            "ranepa_parse_unverified",
            "Расписание не удалось достоверно разобрать; записи не изменены",
            409,
        )
    return {
        "status": "preview_only",
        "group_code": source.official_group_code,
        "internal_group_uuid": proof.internal_group_uuid,
        "source_sha256": proof.page_sha256,
        "week_start": result.week_start,
        "week_end": result.week_end,
        "complete": False,
        "lessons": [
            {
                "day": row.day,
                "starts": row.starts,
                "ends": row.ends,
                "subject": row.subject,
                "teacher": row.teacher,
                "room": row.room,
                "lesson_type": row.lesson_type,
                "subgroup_hint": row.subgroup_hint,
                "cancelled": row.cancelled,
            }
            for row in result.indexed().values()
        ],
    }
