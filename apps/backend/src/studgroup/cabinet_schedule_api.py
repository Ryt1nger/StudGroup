"""Staff-only timetable screenshots; first safe timetable may bootstrap automatically."""

import hashlib
import io
import json
import os
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import Response
from PIL import Image, UnidentifiedImageError
from sqlalchemy import delete, select

from studgroup.analysis.image_schedule import ScheduleConfirmation
from studgroup.analysis.photo_reading import MAX_IMAGE_BYTES, sniff_mime
from studgroup.api import ApiError
from studgroup.headman_domain import StError
from studgroup.models import (
    CabinetScheduleUpload,
    Group,
    GroupAnalysisSettings,
    Subgroup,
    SubjectSplit,
)
from studgroup.staff_api import CurrentStaff, Db

router = APIRouter(prefix="/v1/staff/schedule-upload")
RETENTION = timedelta(days=7)
MAX_PIXELS = 20_000_000


def unlimited_retry_for(row, context):
    """Personal, authenticated exception for explicitly initiated retries only."""
    try:
        configured_id = int(os.environ.get("CABINET_UNLIMITED_RETRY_TELEGRAM_ID", "0"))
    except ValueError:
        return False
    return (
        configured_id > 0
        and context.user.telegram_user_id == configured_id
        and row.created_by == context.user.id
    )


def metadata(row, context):
    return {
        "id": row.id,
        "revision": row.revision,
        "state": row.state,
        "schedule_version": row.schedule_version,
        "week_start": row.week_start,
        "duration": row.duration,
        "rows": json.loads(row.rows_json),
        "source_url": None,
        "confirmation": json.loads(row.confirmation_json) if row.confirmation_json else None,
        "progress_percent": row.progress_percent,
        "progress_stage": row.progress_stage,
        "can_retry": row.state == "failed"
        and (row.retry_count < 1 or unlimited_retry_for(row, context)),
    }


async def prune(db, group_id, now):
    await db.execute(
        delete(CabinetScheduleUpload).where(
            CabinetScheduleUpload.group_id == group_id,
            CabinetScheduleUpload.delete_at <= now,
        )
    )


def check_image(data: bytes):
    if not 0 < len(data) <= MAX_IMAGE_BYTES:
        raise ApiError("image_too_large", "Скриншот должен быть не больше 8 МБ", 422)
    mime = sniff_mime(data)
    if mime not in {"image/png", "image/jpeg", "image/webp"}:
        raise ApiError("invalid_image", "Нужен PNG, JPEG или WEBP", 422)
    try:
        with Image.open(io.BytesIO(data)) as picture:
            width, height = picture.size
            if width < 100 or height < 100 or width * height > MAX_PIXELS:
                raise ApiError("invalid_image", "Неподходящий размер изображения", 422)
            picture.verify()
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        raise ApiError(
            "invalid_image", "Файл повреждён или не является изображением", 422
        ) from None
    return mime


async def eligible(db, group, now, settings=None):
    route = await db.get(GroupAnalysisSettings, group.id)
    if group.status != "active" or not group.pilot_authorized or route is None:
        raise ApiError("analysis_unavailable", "Для этой группы анализ не настроен", 409)
    # Manual staff uploads are isolated from automatic chat-analysis routing.
    # A legacy-mode group may run explicitly funded screenshot recognition without
    # waking background AI processing or changing its normal publication mode.
    if route.paid_outcome_hold:
        raise ApiError("analysis_unavailable", "Анализ временно недоступен", 409)
    # No per-group paid activation in the owner-funded pilot; provider requests
    # still pass through the durable reservation and accounting layer.
    if settings is not None and settings.owner_funded_pilot:
        return route
    if route.release_budget_usd is None:
        raise ApiError(
            "analysis_budget_closed",
            "Платный анализ не настроен. Требуется ранее подтверждённый лимит расходов.",
            409,
        )
    from studgroup.analysis.release_budget import utc

    if (
        route.release_budget_since is None
        or route.release_budget_until is None
        or not utc(route.release_budget_since) <= now < utc(route.release_budget_until)
    ):
        raise ApiError("analysis_budget_closed", "Лимит платного теста завершён", 409)
    # Refuse an obviously unfunded request before returning a queued job.
    # Full provider accounting still performs the authoritative reservation.
    from studgroup.analysis.release_budget import spent

    if await spent(db, group.id) >= route.release_budget_usd:
        raise ApiError(
            "analysis_budget_closed",
            "Бюджет распознавания исчерпан. Новые запросы не запускаются.",
            409,
        )
    return route


@router.get("")
async def list_uploads(context: CurrentStaff, db: Db):
    now = datetime.now(UTC)
    await prune(db, context.group.id, now)
    # One-time cleanup of pre-reset cabinet uploads: delete historical images,
    # checkpoints and attempts, not the actual published timetable. The fixed
    # cutoff keeps every future upload (including retryable failures) intact.
    cutoff = datetime(2026, 11, 3, 15, 0, tzinfo=UTC)
    await db.execute(
        delete(CabinetScheduleUpload).where(
            CabinetScheduleUpload.group_id == context.group.id,
            CabinetScheduleUpload.created_at < cutoff,
        )
    )
    rows = (
        await db.scalars(
            select(CabinetScheduleUpload)
            .where(
                CabinetScheduleUpload.group_id == context.group.id,
                CabinetScheduleUpload.delete_at > now,
            )
            .order_by(CabinetScheduleUpload.created_at.desc())
            .limit(30)
        )
    ).all()
    subgroups = (
        await db.execute(
            select(Subgroup, SubjectSplit)
            .join(SubjectSplit, SubjectSplit.id == Subgroup.split_id)
            .where(SubjectSplit.group_id == context.group.id)
            .order_by(SubjectSplit.subject_name, Subgroup.position)
        )
    ).all()
    await db.commit()
    return {
        "current_schedule_version": context.group.schedule_version,
        "subgroups": [
            {
                "id": subgroup.id,
                "subject_id": split.subject_id,
                "subject": split.subject_name,
                "name": subgroup.name,
            }
            for subgroup, split in subgroups
        ],
        "suggestions": [metadata(row, context) for row in rows],
    }


@router.post("", status_code=201)
async def upload_schedule(
    image: Annotated[UploadFile, File()],
    week_start: Annotated[date, Form()],
    duration: Annotated[Literal["one_week", "recurring"], Form()],
    context: CurrentStaff,
    db: Db,
    request: Request,
):
    if week_start.weekday() != 0:
        raise ApiError("invalid_week", "Выберите понедельник нужной недели", 422)
    now = datetime.now(UTC)
    if week_start + timedelta(days=6) < now.date() or week_start > now.date() + timedelta(days=90):
        raise ApiError("invalid_week", "Выберите актуальную неделю в пределах трёх месяцев", 422)
    data = await image.read(MAX_IMAGE_BYTES + 1)
    mime = check_image(data)
    sha = hashlib.sha256(data).hexdigest()
    await eligible(db, context.group, now, request.app.state.settings)
    # Serialize concurrent identical submissions by group. Commit BEFORE DeepSeek HTTP.
    group = await db.scalar(select(Group).where(Group.id == context.group.id).with_for_update())
    await prune(db, context.group.id, now)
    previous = await db.scalar(
        select(CabinetScheduleUpload)
        .where(
            CabinetScheduleUpload.group_id == group.id,
            CabinetScheduleUpload.image_sha256 == sha,
            CabinetScheduleUpload.week_start == week_start,
            CabinetScheduleUpload.duration == duration,
            CabinetScheduleUpload.delete_at > now,
        )
        .order_by(CabinetScheduleUpload.created_at.desc())
        .limit(1)
    )
    if previous is not None:
        if previous.state in {"queued", "processing", "pending", "confirmed"}:
            await db.commit()
            return metadata(previous, context)
        await db.commit()
        raise ApiError(
            "already_processing",
            "Этот скриншот уже был обработан с ошибкой. Повторите из списка задач.",
            409,
        )
    item = CabinetScheduleUpload(
        id=uuid.uuid4(),
        group_id=group.id,
        created_by=context.user.id,
        schedule_version=group.schedule_version,
        week_start=week_start,
        duration=duration,
        rows_json="[]",
        image_bytes=data,
        image_mime=mime,
        image_sha256=sha,
        state="queued",
        progress_percent=5,
        progress_stage="queued_a",
        retry_count=0,
        revision=1,
        created_at=now,
        delete_at=now + RETENTION,
    )
    db.add(item)
    payload = metadata(item, context)
    await db.commit()
    # AI runs from the durable server-side queue, not from this HTTP request.
    return payload


@router.post("/{ident}/retry", status_code=202)
async def retry_upload(ident: uuid.UUID, context: CurrentStaff, db: Db, request: Request):
    """Explicit retry only; never silently repeat an unknown paid attempt."""
    now = datetime.now(UTC)
    row = await db.scalar(
        select(CabinetScheduleUpload)
        .where(
            CabinetScheduleUpload.id == ident,
            CabinetScheduleUpload.group_id == context.group.id,
            CabinetScheduleUpload.delete_at > now,
        )
        .with_for_update()
    )
    if row is None:
        raise ApiError("not_found", "Черновик недоступен", 404)
    if row.state != "failed" or (row.retry_count >= 1 and not unlimited_retry_for(row, context)):
        raise ApiError("retry_unavailable", "Повторная обработка недоступна", 409)
    await eligible(db, context.group, now, request.app.state.settings)
    row.retry_count += 1
    row.progress_percent = 50 if row.first_pass_json else 5
    row.progress_stage = "queued_b" if row.first_pass_json else "queued_a"
    row.state = "queued"
    row.claimed_at = None
    payload = metadata(row, context)
    await db.commit()
    return payload


@router.get("/{ident}/image")
async def view_image(ident: uuid.UUID, context: CurrentStaff, db: Db):
    now = datetime.now(UTC)
    row = await db.scalar(
        select(CabinetScheduleUpload).where(
            CabinetScheduleUpload.id == ident,
            CabinetScheduleUpload.group_id == context.group.id,
            CabinetScheduleUpload.delete_at > now,
        )
    )
    if row is None:
        raise ApiError("not_found", "Скриншот недоступен", 404)
    return Response(
        content=row.image_bytes,
        media_type=row.image_mime,
        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
    )


@router.post("/{ident}/confirm")
async def confirm_upload(
    ident: uuid.UUID,
    body: ScheduleConfirmation,
    context: CurrentStaff,
    db: Db,
):
    from studgroup.analysis.image_schedule import confirm

    try:
        version = await confirm(
            db, context.group.id, context.user, context.member, ident, body, cabinet=True
        )
    except ApiError as error:
        # Only the finite code is logged; no private timetable or image content.
        print(f"CABINET_SCHEDULE_CONFIRM_REJECTED code={error.code}", flush=True)
        raise
    except StError as error:
        print("CABINET_SCHEDULE_CONFIRM_REJECTED code=action_rejected", flush=True)
        raise ApiError("action_rejected", str(error), 409) from None
    await db.commit()
    return {"schedule_version": version}
