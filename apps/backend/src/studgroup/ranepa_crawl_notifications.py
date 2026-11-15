"""Owner-only notification summaries; no credentials, URLs or CAPTCHA images.

Send through the existing DB-backed Telegram outbox. Never send directly from
a temporary GitHub runner. Counts describe verified/persisted source data,
not promises that every LMS course has a working online link.
"""

import os
import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from studgroup.bot_admin import enqueue_once

_CAPTCHA_LOCATIONS = {
    "month": "месячное расписание",
    "week": "недельное расписание",
    "sdo": "переход к СДО",
}


@dataclass
class CrawlRunStats:
    successful_accounts: int = 0
    failed_accounts: int = 0
    saved_weeks: int = 0
    saved_lessons: int = 0
    saved_sdo_links: int = 0
    saved_sdo_courses: int = 0

    def add(self, other: "CrawlRunStats") -> None:
        self.successful_accounts += other.successful_accounts
        self.failed_accounts += other.failed_accounts
        self.saved_weeks += other.saved_weeks
        self.saved_lessons += other.saved_lessons
        self.saved_sdo_links += other.saved_sdo_links
        self.saved_sdo_courses += other.saved_sdo_courses


def _owner_id(value: int | None) -> bool:
    return type(value) is int and value > 0


def _run_key() -> str:
    run_id = os.getenv("GITHUB_RUN_ID", "")
    attempt = os.getenv("GITHUB_RUN_ATTEMPT", "1")
    if run_id.isdecimal() and attempt.isdecimal():
        return f"{run_id}:{attempt}"
    return str(uuid.uuid4())


async def notify_captcha_detected(
    engine,
    *,
    owner_id: int | None,
    job_id: uuid.UUID,
    attempt: int,
    location: str,
) -> bool:
    """Queue the first CAPTCHA sighting immediately, before paid solving.

    One sighting per account attempt. The normal failure outcome may later
    send a separate update if solving failed and the queue was paused.
    """
    if not _owner_id(owner_id):
        return False
    if location not in _CAPTCHA_LOCATIONS:
        raise ValueError("invalid_captcha_notice_location")
    text = (
        "⚠️ StudGroup · обнаружена CAPTCHA в СДО/ЛК РАНХиГС.\n"
        f"Этап: {_CAPTCHA_LOCATIONS[location]}.\n"
        "Автоматическая проверка продолжится в пределах разрешённого бюджета. "
        "Если решить CAPTCHA не удастся, очередь перейдёт к следующей учётке.\n"
        "Это уведомление не содержит самой CAPTCHA: её нельзя пройти по изображению "
        "в Telegram без той же активной браузерной сессии."
    )
    async with AsyncSession(engine) as db:
        queued = await enqueue_once(
            db,
            "sendMessage",
            {"chat_id": owner_id, "text": text, "link_preview_options": {"is_disabled": True}},
            f"ranepa-captcha-seen:{job_id}:{attempt}",
        )
        await db.commit()
        return queued


async def notify_successful_crawl(
    engine,
    *,
    owner_id: int | None,
    stats: CrawlRunStats,
    scope: str,
) -> bool:
    """One idempotent owner message per successful GitHub run or manual pilot."""
    if not _owner_id(owner_id) or stats.successful_accounts < 1:
        return False
    if scope not in {"batch", "pilot"}:
        raise ValueError("invalid_crawl_success_notice_scope")
    message = (
        "✅ StudGroup · обход СДО/ЛК завершён.\n"
        f"Успешно обработано учёток: {stats.successful_accounts}.\n"
        f"Не завершено учёток: {stats.failed_accounts}.\n"
        f"Сохранено недель расписания: {stats.saved_weeks}.\n"
        f"Подтверждено занятий: {stats.saved_lessons}.\n"
        f"Найдено ссылок на занятия в СДО: {stats.saved_sdo_links}.\n"
        f"Найдено страниц курсов СДО: {stats.saved_sdo_courses}.\n"
        "Ссылки указываются только там, где источник их действительно подтвердил. "
        "Отсутствие ссылки не означает, что занятие не существует."
    )
    async with AsyncSession(engine) as db:
        queued = await enqueue_once(
            db,
            "sendMessage",
            {"chat_id": owner_id, "text": message, "link_preview_options": {"is_disabled": True}},
            f"ranepa-crawl-success:{scope}:{_run_key()}",
        )
        await db.commit()
        return queued
