"""Staff-only, opt-in timetable screenshot reading; no Telegram ingestion or auto-publishing."""

import hashlib
import io
import json
import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import Response
from PIL import Image, UnidentifiedImageError
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from studgroup.ai import ProviderFailure
from studgroup.analysis.image_contracts import ScheduleInterpretation
from studgroup.analysis.image_reading import read_pass
from studgroup.analysis.image_schedule import ScheduleConfirmation
from studgroup.analysis.photo_reading import MAX_IMAGE_BYTES, sniff_mime
from studgroup.analysis.schedule_scope import suggest_review_rows
from studgroup.api import ApiError
from studgroup.headman_domain import StError
from studgroup.models import (
    CabinetScheduleUpload,
    Group,
    GroupAnalysisSettings,
    SchedulePattern,
    Subgroup,
    SubjectSplit,
)
from studgroup.staff_api import CurrentStaff, Db

router = APIRouter(prefix="/v1/staff/schedule-upload")
RETENTION = timedelta(days=7)
MAX_PIXELS = 20_000_000


def metadata(row):
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


async def eligible(db, group, now):
    route = await db.get(GroupAnalysisSettings, group.id)
    if group.status != "active" or not group.pilot_authorized or route is None:
        raise ApiError("analysis_unavailable", "Для этой группы анализ не настроен", 409)
    if route.analysis_mode not in {"new", "shadow"} or route.paid_outcome_hold:
        raise ApiError("analysis_unavailable", "Анализ временно недоступен", 409)
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
    return route


@router.get("")
async def list_uploads(context: CurrentStaff, db: Db):
    now = datetime.now(UTC)
    await prune(db, context.group.id, now)
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
        "suggestions": [metadata(row) for row in rows],
    }


@router.post("", status_code=201)
async def upload_schedule(
    image: Annotated[UploadFile, File()],
    week_start: Annotated[date, Form()],
    duration: Annotated[Literal["one_week", "recurring"], Form()],
    request: Request,
    context: CurrentStaff,
    db: Db,
):
    if week_start.weekday() != 0:
        raise ApiError("invalid_week", "Выберите понедельник нужной недели", 422)
    now = datetime.now(UTC)
    if week_start + timedelta(days=6) < now.date() or week_start > now.date() + timedelta(days=90):
        raise ApiError("invalid_week", "Выберите актуальную неделю в пределах трёх месяцев", 422)
    data = await image.read(MAX_IMAGE_BYTES + 1)
    mime = check_image(data)
    sha = hashlib.sha256(data).hexdigest()
    await eligible(db, context.group, now)
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
        if previous.state in {"pending", "confirmed"}:
            await db.commit()
            return metadata(previous)
        await db.commit()
        raise ApiError(
            "already_processing",
            "Это изображение уже обрабатывается или попытка завершилась ошибкой. Проверьте черновики.",
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
        state="processing",
        revision=1,
        created_at=now,
        delete_at=now + RETENTION,
    )
    db.add(item)
    await db.commit()
    try:
        from studgroup.analysis.release_budget import provider_for

        provider = provider_for(
            request.app.state.engine,
            request.app.state.settings,
            group.id,
            kind="image",
            attempt_key=f"cabinet-schedule:{item.id}",
        )
        first, _ = await read_pass(provider, data, mime, 1, context={})
        if not first.complete:
            raise ApiError("image_unclear", "Не удалось полностью прочитать скриншот", 422)
        hint = f"Староста явно выбрал расписание с {week_start.isoformat()} " + (
            "только на эту неделю."
            if duration == "one_week"
            else "на постоянной основе для этой чётности."
        )
        second, _ = await read_pass(
            provider,
            data,
            mime,
            2,
            first=first,
            context={
                "messages": [{"telegram_message_id": 0, "text": hint}],
                "image_source_message_id": 0,
                "group_timezone": group.timezone,
            },
        )
        if (
            not second.complete
            or not second.schedule
            or not {"schedule", "schedule_change"}.intersection(second.semantic_types)
        ):
            raise ApiError("not_a_schedule", "DeepSeek не смог надёжно распознать расписание", 422)
        # The editor's explicit date/scope selection is authoritative; DeepSeek reads the cells.
        scope = ScheduleInterpretation(
            coverage=second.schedule_interpretation.coverage
            if second.schedule_interpretation is not None
            else "unknown",
            duration=duration,
            week_start=week_start,
            explanation="Период подтверждён старостой при загрузке",
            source_message_ids=[],
        )
        second = second.model_copy(update={"schedule_interpretation": scope})
        async with AsyncSession(request.app.state.engine) as lookup:
            horizon = await lookup.scalar(
                select(func.max(SchedulePattern.valid_until)).where(
                    SchedulePattern.group_id == group.id,
                    SchedulePattern.cancelled.is_(False),
                )
            )
        rows = suggest_review_rows(second, group.first_week_anchor, horizon)
        if not rows:
            raise ApiError("not_a_schedule", "На скриншоте не найдены пары", 422)
        async with AsyncSession(request.app.state.engine, expire_on_commit=False) as save:
            saved = await save.get(CabinetScheduleUpload, item.id, with_for_update=True)
            if saved is None or saved.state != "processing":
                raise ApiError("upload_changed", "Черновик загрузки изменился", 409)
            saved.rows_json = json.dumps(rows, ensure_ascii=False)
            saved.state = "pending"
            await save.commit()
            return metadata(saved)
    except (ApiError, ProviderFailure) as error:
        async with AsyncSession(request.app.state.engine, expire_on_commit=False) as save:
            saved = await save.get(CabinetScheduleUpload, item.id, with_for_update=True)
            if saved and saved.state == "processing":
                saved.state = "failed"
                await save.commit()
        if isinstance(error, ApiError):
            raise
        raise ApiError(
            "analysis_unavailable", "DeepSeek недоступен или лимит исчерпан", 503
        ) from None
    except Exception:  # noqa: BLE001 - paid-vision boundary; redact provider details
        async with AsyncSession(request.app.state.engine, expire_on_commit=False) as save:
            saved = await save.get(CabinetScheduleUpload, item.id, with_for_update=True)
            if saved and saved.state == "processing":
                saved.state = "failed"
                await save.commit()
        raise ApiError("analysis_failed", "Ошибка обработки. Расписание не изменено", 503) from None


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
    except StError as error:
        raise ApiError("action_rejected", str(error), 409) from None
    await db.commit()
    return {"schedule_version": version}
