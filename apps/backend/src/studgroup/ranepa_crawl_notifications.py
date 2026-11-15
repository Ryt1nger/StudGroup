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


_ACCOUNT_FAILURE_REASONS = {
    "login_failed": "вход не выполнен или разрешённые реквизиты недоступны",
    "captcha": "CAPTCHA не пройдена",
    "source_unavailable": "источник расписания недоступен",
    "identity_unverified": "не удалось подтвердить принадлежность расписания",
    "parse_failed": "данные источника не прошли проверку",
    "internal": "внутренняя ошибка обработки",
}


def _safe_label(value: str | None, *, fallback: str) -> str:
    """One printable, bounded line; reject hidden formatting or control characters."""
    cleaned = "".join(ch if ch.isprintable() else " " for ch in (value or ""))
    return " ".join(cleaned.split())[:72] or fallback


async def notify_account_crawl_result(
    engine,
    *,
    owner_id: int | None,
    job_id: uuid.UUID,
    attempt: int,
    user_id: uuid.UUID,
    group_code: str | None,
    student_name: str | None,
    failure_code: str | None,
    stats: CrawlRunStats,
) -> bool:
    """One private message per finalized account attempt, even when extraction fails.

    A green *SDO extraction* result requires persisted source-backed links or
    course pages. Verified timetable-only reads are a separate warning, never
    presented as confirmed SDO extraction.
    """
    if not _owner_id(owner_id):
        return False
    if failure_code is not None and failure_code not in _ACCOUNT_FAILURE_REASONS:
        raise ValueError("invalid_crawl_failure_code")
    if attempt < 1:
        raise ValueError("invalid_crawl_attempt")
    group = _safe_label(group_code, fallback="группа не указана")
    name = _safe_label(student_name, fallback="студент")
    if failure_code is not None:
        heading = "❌ StudGroup · ошибка обработки учётки СДО"
        outcome = "Причина: " + _ACCOUNT_FAILURE_REASONS[failure_code] + f" ({failure_code})."
    elif stats.saved_sdo_links > 0 or stats.saved_sdo_courses > 0:
        heading = "✅ StudGroup · данные СДО извлечены"
        outcome = "Подтверждённые ссылки или страницы курсов успешно сохранены."
    elif stats.saved_weeks > 0:
        heading = "⚠️ StudGroup · данные СДО не извлечены"
        outcome = (
            "Расписание сохранено, но подтверждённых ссылок или страниц курсов СДО не найдено. "
            "Это не обязательно ошибка входа — требуется проверка доступности материалов."
        )
    else:
        heading = "⚠️ StudGroup · данные СДО не подтверждены"
        outcome = "Подтверждённые данные расписания или СДО не получены."
    message = "\n".join(
        [
            heading,
            f"Студент: {name}. Группа: {group}.",
            f"Учётка: {str(user_id)[:8]}. Попытка: {attempt}.",
            outcome,
            f"Сохранено недель: {stats.saved_weeks}; занятий: {stats.saved_lessons}.",
            f"Ссылок СДО: {stats.saved_sdo_links}; страниц курсов: {stats.saved_sdo_courses}.",
        ]
    )
    async with AsyncSession(engine) as db:
        queued = await enqueue_once(
            db,
            "sendMessage",
            {"chat_id": owner_id, "text": message, "link_preview_options": {"is_disabled": True}},
            f"ranepa-crawl-account:{job_id}:{attempt}",
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
