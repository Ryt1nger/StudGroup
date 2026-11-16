"""Private, one-time human CAPTCHA handoff to the *live* Playwright page.

Only the bounded CAPTCHA artwork is stored in the production database. The
owner's signed Telegram WebApp initData is required for every read or answer.
The runner keeps its browser open and polls this same database; no cookies,
passwords, HTML or browser-control endpoint are exposed.
"""

import asyncio
import base64
import logging
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import monotonic
from urllib.parse import urlsplit

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from studgroup.api import ApiError
from studgroup.bot_admin import enqueue_once
from studgroup.models import RanepaManualCaptcha
from studgroup.ranepa_captcha import _challenge_clip, _slider_boxes
from studgroup.ranepa_rotate import (
    _angle_targets,
    _read_one_shot_range_state,
    _read_original_halves,
    _read_verified_handle_centers,
)
from studgroup.security import InvalidInitData, verify_init_data

router = APIRouter()
LOGGER = logging.getLogger(__name__)
CHALLENGE_TTL = timedelta(minutes=6)
POLL_SECONDS = 2
MAX_IMAGE_BYTES = 900_000
UI_FILE = Path(__file__).with_name("ranepa_manual_captcha_ui.html")
SCRIPT_FILE = Path(__file__).with_name("ranepa_manual_captcha_ui.js")


class OwnerCaptchaAccess(BaseModel):
    init_data: str = Field(min_length=1, max_length=16384)


class OwnerCaptchaAnswer(OwnerCaptchaAccess):
    angle_left: int = Field(ge=0, le=359)
    angle_right: int = Field(ge=0, le=359)


def _aware(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value


def _b64(png: bytes) -> str:
    if not 100 <= len(png) <= MAX_IMAGE_BYTES or not png.startswith(
        bytes.fromhex("89504e470d0a1a0a")
    ):
        raise ValueError("unsafe_captcha_image")
    return base64.b64encode(png).decode("ascii")


def _captcha_url(settings, challenge_id: uuid.UUID) -> str:
    configured = settings.ranepa_manual_captcha_base_url.rstrip("/")
    uri = urlsplit(configured)
    if (
        uri.scheme != "https"
        or not uri.hostname
        or uri.username
        or uri.password
        or uri.query
        or uri.fragment
        or uri.path not in {"", "/"}
    ):
        raise ValueError("unsafe_captcha_base_url")
    return f"{configured}/ranepa-captcha/{challenge_id}"


def _safe_owner_label(value: str | None, fallback: str) -> str:
    """Keep student metadata confined to one printable line in the owner's DM."""
    printable = "".join(ch if ch.isprintable() else " " for ch in (value or ""))
    return " ".join(printable.split())[:72] or fallback


def _manual_request_text(
    *, student_name: str | None, group_code: str | None, auto_attempts: int
) -> str:
    if type(auto_attempts) is not int or not 0 <= auto_attempts <= 3:
        raise ValueError("invalid_auto_captcha_attempts")
    student = _safe_owner_label(student_name, "студент")
    group = _safe_owner_label(group_code, "не указана")
    attempts_detail = (
        "Три автоматические попытки не подтвердили решение."
        if auto_attempts == 3
        else "Остальные попытки не выполнены из-за лимита, диагностики или недоступности сервиса."
    )
    return (
        "❌ StudGroup · автоматический прогон учётки не завершился из-за CAPTCHA\n"
        f"Учётка: {student}. Группа: {group}.\n"
        f"RuCaptcha: {auto_attempts} из 3 попыток. {attempts_detail}\n"
        "Чтобы продолжить этот прогон, необходимо пройти CAPTCHA вручную. "
        "Нажми кнопку «🧩 Пройти CAPTCHA» ниже.\n"
        "Ссылка действует до 6 минут; бот проверит ответ на том же сайте "
        "в той же открытой сессии. Успех не засчитывается до этой проверки."
    )


async def _authorize(request: Request, init_data: str, owner: int) -> None:
    token = request.app.state.settings.telegram_bot_token
    try:
        user = verify_init_data(init_data, token, now=int(datetime.now(UTC).timestamp()))
    except InvalidInitData:
        raise ApiError("unauthenticated", "Откройте кнопку из Telegram ещё раз", 401) from None
    if user.telegram_user_id != owner:
        raise ApiError("permission_denied", "Доступен только администратору", 403)


@router.get("/ranepa-captcha/app.js", include_in_schema=False)
async def manual_captcha_script():
    return FileResponse(
        SCRIPT_FILE,
        media_type="text/javascript",
        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
    )


@router.get("/ranepa-captcha/{challenge_id}", include_in_schema=False)
async def manual_captcha_page(challenge_id: uuid.UUID):
    return FileResponse(
        UI_FILE,
        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
    )


@router.post("/v1/ranepa-captcha/{challenge_id}/state")
async def manual_captcha_state(challenge_id: uuid.UUID, body: OwnerCaptchaAccess, request: Request):
    async with AsyncSession(request.app.state.engine) as db:
        row = await db.get(RanepaManualCaptcha, challenge_id)
        if row is None:
            raise ApiError("captcha_not_found", "Проверка не найдена", 404)
        await _authorize(request, body.init_data, row.owner_telegram_id)
        pending = row.state == "pending" and _aware(row.expires_at) > datetime.now(UTC)
        return {
            "status": "pending"
            if pending
            else "answered"
            if row.state == "answered"
            else "expired",
            "screenshot": "data:image/png;base64," + row.screenshot_base64
            if pending and row.screenshot_base64
            else None,
            "left": "data:image/png;base64," + row.left_image_base64
            if pending and row.left_image_base64
            else None,
            "right": "data:image/png;base64," + row.right_image_base64
            if pending and row.right_image_base64
            else None,
        }


@router.post("/v1/ranepa-captcha/{challenge_id}/answer")
async def manual_captcha_answer(
    challenge_id: uuid.UUID, body: OwnerCaptchaAnswer, request: Request
):
    async with AsyncSession(request.app.state.engine) as db:
        async with db.begin():
            row = await db.get(RanepaManualCaptcha, challenge_id, with_for_update=True)
            if row is None:
                raise ApiError("captcha_not_found", "Проверка не найдена", 404)
            await _authorize(request, body.init_data, row.owner_telegram_id)
            if row.state != "pending" or _aware(row.expires_at) <= datetime.now(UTC):
                raise ApiError("captcha_expired", "Проверка уже завершена или истекла", 409)
            row.angle_left = body.angle_left
            row.angle_right = body.angle_right
            row.answered_at = datetime.now(UTC)
            row.state = "answered"
        return {"accepted": True, "message": "Ответ передан. Проверяем его на сайте РАНХиГС."}


async def _capture_challenge(page) -> tuple[str, str, str, tuple[bytes, bytes]] | None:
    """Capture the whole puzzle region and original halves, never the whole account page."""
    if urlsplit(getattr(page, "url", "")).hostname != "my.ranepa.ru":
        return None
    boxes = await _slider_boxes(page)
    if boxes is None:
        return None
    halves = await _read_original_halves(page)
    if halves is None:
        return None
    clip = _challenge_clip(boxes)
    viewport = page.viewport_size or {}
    if (
        clip["x"] < 0
        or clip["y"] < 0
        or clip["width"] > 900
        or clip["height"] > 950
        or clip["width"] < 150
        or clip["height"] < 190
        or clip["x"] + clip["width"] > viewport.get("width", 0)
        or clip["y"] + clip["height"] > viewport.get("height", 0)
    ):
        return None
    try:
        screenshot = await page.screenshot(clip=clip, type="png", animations="disabled")
        return _b64(screenshot), _b64(halves[0]), _b64(halves[1]), halves
    except (ValueError, TypeError):
        return None


async def _apply_manual_rotation(page, angles: tuple[int, int], halves) -> bool:
    """One real thumb drag per half, after revalidating the unchanged live challenge."""
    from studgroup.ranepa_captcha import log_captcha_stage

    if urlsplit(getattr(page, "url", "")).hostname != "my.ranepa.ru":
        return False
    if await _read_original_halves(page) != halves:
        log_captcha_stage("owner_challenge_changed")
        return False
    boxes = await _slider_boxes(page)
    if boxes is None:
        return False
    centers = await _read_verified_handle_centers(page, boxes)
    if centers is None:
        return False
    targets = _angle_targets(angles, boxes, centers)
    if targets is None:
        return False
    for index, (x0, x1, y) in enumerate(targets):
        fresh = await _read_verified_handle_centers(page, boxes, only_index=index)
        if fresh is None or abs(fresh[0][0] - x0) > 2 or abs(fresh[0][1] - y) > 2:
            return False
        await page.mouse.move(x0, y)
        await page.mouse.down()
        try:
            await page.mouse.move(x1, y, steps=10)
            during = await _read_one_shot_range_state(page, index)
        finally:
            await page.mouse.up()
        if during is None:
            log_captcha_stage("owner_thumb_motion_unverified")
            return False
    log_captcha_stage("owner_drag_submitted_pending_server_check")
    return True


async def cleanup_expired_captcha_artwork(engine) -> None:
    """Discard captured puzzle PNGs after the deadline, including crashed runners."""
    from sqlalchemy import update

    now = datetime.now(UTC)
    async with AsyncSession(engine) as db:
        await db.execute(
            update(RanepaManualCaptcha)
            .where(
                RanepaManualCaptcha.expires_at < now,
                RanepaManualCaptcha.screenshot_base64.is_not(None),
            )
            .values(
                state="expired",
                screenshot_base64=None,
                left_image_base64=None,
                right_image_base64=None,
            )
        )
        await db.commit()


async def offer_manual_captcha(
    engine,
    *,
    page,
    owner_id: int | None,
    job_id: uuid.UUID,
    attempt: int,
    settings,
    auto_attempts: int = 0,
    student_name: str | None = None,
    group_code: str | None = None,
) -> bool:
    """Notify owner *after* automatic solving fails and await their answer."""
    from studgroup.ranepa_captcha import log_captcha_stage

    if not settings.ranepa_manual_captcha_enabled or type(owner_id) is not int or owner_id <= 0:
        return False
    answer = None
    try:
        result = await _capture_challenge(page)
        if result is None:
            # A paid drag may consume the original sliders. Reload once to
            # obtain a fresh challenge in the same browser context.
            await page.reload(wait_until="domcontentloaded", timeout=12_000)
            result = await _capture_challenge(page)
        if result is None:
            log_captcha_stage("owner_challenge_not_available")
            return False
        screenshot, left, right, halves = result
        item_id = uuid.uuid4()
        url = _captcha_url(settings, item_id)
        now = datetime.now(UTC)
        async with AsyncSession(engine) as db:
            db.add(
                RanepaManualCaptcha(
                    id=item_id,
                    job_id=job_id,
                    attempt=attempt,
                    owner_telegram_id=owner_id,
                    state="pending",
                    screenshot_base64=screenshot,
                    left_image_base64=left,
                    right_image_base64=right,
                    created_at=now,
                    expires_at=now + CHALLENGE_TTL,
                )
            )
            await enqueue_once(
                db,
                "sendMessage",
                {
                    "chat_id": owner_id,
                    "text": _manual_request_text(
                        student_name=student_name,
                        group_code=group_code,
                        auto_attempts=auto_attempts,
                    ),
                    "reply_markup": {
                        "inline_keyboard": [
                            [{"text": "🧩 Пройти CAPTCHA", "web_app": {"url": url}}]
                        ]
                    },
                    "link_preview_options": {"is_disabled": True},
                },
                f"ranepa-owner-captcha:{item_id}",
            )
            await db.commit()
        log_captcha_stage("owner_manual_challenge_queued")
        answer = None
        deadline = monotonic() + CHALLENGE_TTL.total_seconds() - 5
        while monotonic() < deadline:
            await asyncio.sleep(POLL_SECONDS)
            async with AsyncSession(engine) as db:
                row = await db.get(RanepaManualCaptcha, item_id)
                if row is not None and row.state == "answered":
                    answer = (row.angle_left, row.angle_right)
                    break
        if answer is None or not all(type(n) is int and 0 <= n <= 359 for n in answer):
            log_captcha_stage("owner_manual_timeout")
            return False
        return await _apply_manual_rotation(page, answer, halves)
    except Exception:  # noqa: BLE001
        # Neither browser internals nor Telegram credentials may reach logs.
        log_captcha_stage("owner_manual_unavailable")
        return False
    finally:
        if "item_id" in locals():
            try:
                async with AsyncSession(engine) as db:
                    row = await db.get(RanepaManualCaptcha, item_id)
                    if row is not None:
                        row.state = "consumed" if answer is not None else "expired"
                        row.screenshot_base64 = None
                        row.left_image_base64 = None
                        row.right_image_base64 = None
                        await db.commit()
            except Exception:  # noqa: BLE001
                LOGGER.warning("RANEPA_OWNER_CAPTCHA_CLEANUP_UNAVAILABLE")
