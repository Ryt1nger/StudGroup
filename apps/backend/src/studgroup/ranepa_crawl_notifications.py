"""Owner-only notification summaries; no credentials, URLs or CAPTCHA images.

Send through the existing DB-backed Telegram outbox. Never send directly from
a temporary GitHub runner. Counts describe verified/persisted source data,
not promises that every LMS course has a working online link.
"""

import os
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from studgroup.bot_admin import enqueue_once
from studgroup.ranepa_crawl_jobs import retry_at
from studgroup.ranepa_failure_diagnostics import DIAGNOSTIC_REASONS
from studgroup.models import RanepaManualCaptcha

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
    verified_sdo_accounts: int = 0
    timetable_only_accounts: int = 0
    failure_reasons: dict[str, int] = field(default_factory=dict)
    diagnostic_reasons: dict[str, int] = field(default_factory=dict)

    def add(self, other: "CrawlRunStats") -> None:
        self.successful_accounts += other.successful_accounts
        self.failed_accounts += other.failed_accounts
        self.saved_weeks += other.saved_weeks
        self.saved_lessons += other.saved_lessons
        self.saved_sdo_links += other.saved_sdo_links
        self.saved_sdo_courses += other.saved_sdo_courses
        self.verified_sdo_accounts += other.verified_sdo_accounts
        self.timetable_only_accounts += other.timetable_only_accounts
        for code, count in other.failure_reasons.items():
            self.failure_reasons[code] = self.failure_reasons.get(code, 0) + count
        for code, count in other.diagnostic_reasons.items():
            self.diagnostic_reasons[code] = self.diagnostic_reasons.get(code, 0) + count


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
    "login_failed": "Не удалось войти в личный кабинет",
    "captcha": "Не удалось пройти проверку CAPTCHA",
    "source_unavailable": "Не удалось открыть сайт; точная причина неизвестна",
    "identity_unverified": "Не удалось подтвердить учебную группу",
    "parse_failed": "Сайт открылся, но данные расписания не удалось проверить",
    "internal": "Внутренний сбой StudGroup",
}

_WEEK_FAILURE_DETAILS = {
    "month_incomplete": "Месячное расписание загрузилось не полностью",
    "week_unreadable": "Недельное расписание не удалось прочитать",
    "week_month_mismatch": "Недельное расписание не совпало с месячным",
}


def _retry_summary(code: str, attempt: int) -> str:
    if code == "login_failed":
        return "Нужна проверка входа или согласия. Автоповтор отключён."
    now = datetime.now(UTC)
    scheduled = retry_at(now, failure_code=code, attempts=attempt)
    if scheduled is None:
        return "Автоповтор не запланирован."
    minutes = max(1, round((scheduled - now).total_seconds() / 60))
    if minutes < 60:
        interval = f"{minutes} мин"
    elif minutes % 60:
        interval = f"{minutes // 60} ч {minutes % 60} мин"
    else:
        interval = f"{minutes // 60} ч"
    return f"Повтор автоматически через {interval}."


def _failure_summary(code: str, detail: str | None, diagnostic: str | None) -> str:
    if code == "parse_failed" and detail:
        return _WEEK_FAILURE_DETAILS[detail]
    if code in {"source_unavailable", "internal"} and diagnostic:
        return DIAGNOSTIC_REASONS[diagnostic]
    return _ACCOUNT_FAILURE_REASONS[code]


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
    failure_detail: str | None = None,
    diagnostic_code: str | None = None,
    telegram_group_name: str | None = None,
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
    if failure_detail is not None and failure_detail not in _WEEK_FAILURE_DETAILS:
        raise ValueError("invalid_crawl_failure_detail")
    if diagnostic_code is not None and diagnostic_code not in DIAGNOSTIC_REASONS:
        raise ValueError("invalid_crawl_diagnostic_code")
    if failure_code == "captcha":
        # The one-time owner WebApp notice already states the failed automatic
        # crawl, both groups and the manual action. Do not send its duplicate
        # final CAPTCHA error from a separate outbox item.
        async with AsyncSession(engine) as db:
            manual_notice = await db.scalar(
                select(RanepaManualCaptcha.id)
                .where(
                    RanepaManualCaptcha.job_id == job_id,
                    RanepaManualCaptcha.attempt == attempt,
                    RanepaManualCaptcha.owner_telegram_id == owner_id,
                )
                .limit(1)
            )
            if manual_notice is not None:
                return False
    group = _safe_label(group_code, fallback="группа не указана")
    telegram_group = _safe_label(telegram_group_name, fallback="не указана")
    name = _safe_label(student_name, fallback="студент")
    if failure_code is not None:
        # Account details and zero counters belong to the admin trace, not Telegram.
        message = "\n".join(
            [
                "❌ StudGroup · сбой СДО",
                f"{name} · {_safe_label(telegram_group_name or group_code, fallback='группа не указана')}",
                "Причина: " + _failure_summary(failure_code, failure_detail, diagnostic_code) + ".",
                _retry_summary(failure_code, attempt),
            ]
        )
    else:
        if stats.saved_sdo_links > 0 or stats.saved_sdo_courses > 0:
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
                f"Студент: {name}.",
                f"Группа ТГ: {telegram_group}. Группа РАНХиГС: {group}.",
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


_REPORT_ERROR_NAMES = {
    "login_failed": "Ошибка входа или отсутствует разрешение",
    "captcha": "Не удалось пройти проверку CAPTCHA",
    "source_unavailable": "Сайт расписания недоступен",
    "identity_unverified": "Не подтверждена учебная группа",
    "parse_failed": "Данные источника не прошли проверку",
    "internal": "Внутренняя ошибка обработки",
}


async def notify_batch_report(
    engine,
    *,
    owner_id: int | None,
    stats: CrawlRunStats,
    full_rescan: bool = False,
    planned: int | None = None,
    remaining: int = 0,
    stop_reason: str = "idle",
) -> bool:
    """One private owner summary per GitHub run, including failure-only shifts.

    An empty scheduled scan is silent, while an explicitly requested full scan
    always reports even if the circuit breaker stops it before the first login.
    """
    if not _owner_id(owner_id):
        return False
    if stop_reason not in {"idle", "paused", "time_limit"}:
        raise ValueError("invalid_crawl_stop_reason")
    if remaining < 0 or planned is not None and planned < 0:
        raise ValueError("invalid_crawl_report_counts")
    processed = stats.successful_accounts + stats.failed_accounts
    if not full_rescan and not processed:
        return False
    if full_rescan and planned is None:
        raise ValueError("full_rescan_requires_planned_count")
    completed = full_rescan and remaining == 0 and processed >= planned
    if not full_rescan and stats.failed_accounts:
        causes = []
        for code, count in sorted(stats.diagnostic_reasons.items()):
            if code in DIAGNOSTIC_REASONS:
                causes.append(f"{DIAGNOSTIC_REASONS[code]} — {count}")
        for code, count in sorted(stats.failure_reasons.items()):
            if code != "source_unavailable" or not causes:
                causes.append(f"{_REPORT_ERROR_NAMES.get(code, 'Неизвестная ошибка')} — {count}")
        lines = [
            "⚠️ StudGroup · ошибки обхода СДО",
            f"Не удалось обработать: {stats.failed_accounts} из {processed} учёток.",
            "Причины: " + "; ".join(causes[:4]) + ".",
            "Повтор будет выполнен автоматически по очереди; детали — в админке.",
        ]
        async with AsyncSession(engine) as db:
            queued = await enqueue_once(
                db, "sendMessage",
                {"chat_id": owner_id, "text": "\n".join(lines),
                 "link_preview_options": {"is_disabled": True}},
                f"ranepa-crawl-report:{_run_key()}",
            )
            await db.commit()
            return queued
    if full_rescan:
        heading = (
            "✅ StudGroup · полный повторный обход СДО завершён"
            if completed
            else "⚠️ StudGroup · повторный обход СДО завершён частично"
        )
    else:
        heading = "📊 StudGroup · отчёт о плановом обходе СДО"
    lines = [heading]
    if planned is not None:
        lines.append(f"Запланировано учёток: {planned}.")
    lines.extend(
        [
            f"Обработано учёток: {processed}.",
            f"Успешных попыток: {stats.successful_accounts}.",
            f"Неудачных попыток: {stats.failed_accounts}.",
            f"С подтверждёнными данными СДО: {stats.verified_sdo_accounts}.",
            f"Только расписание, без подтверждённой СДО: {stats.timetable_only_accounts}.",
            f"Сохранено недель: {stats.saved_weeks}; занятий: {stats.saved_lessons}.",
            f"Подтверждённых ссылок СДО: {stats.saved_sdo_links}; страниц курсов: {stats.saved_sdo_courses}.",
        ]
    )
    if stats.failure_reasons:
        lines.append("Причины ошибок:")
        for code, count in sorted(stats.failure_reasons.items()):
            lines.append(f"• {_REPORT_ERROR_NAMES.get(code, 'Неизвестная ошибка')}: {count}.")
    if full_rescan:
        lines.append(f"Не завершено из запланированных: {remaining}.")
    if stop_reason == "paused":
        lines.append("Очередь остановлена защитой CAPTCHA; требуется проверка администратора.")
    elif stop_reason == "time_limit":
        lines.append(
            "Достигнут лимит времени запуска. Незавершённые учётки не считаются обработанными."
        )
    lines.append(
        "Для следующего обычного сканирования каждой завершённой учётки "
        "сохраняется интервал не менее 24 часов."
    )
    async with AsyncSession(engine) as db:
        queued = await enqueue_once(
            db,
            "sendMessage",
            {
                "chat_id": owner_id,
                "text": "\n".join(lines),
                "link_preview_options": {"is_disabled": True},
            },
            f"ranepa-crawl-report:{_run_key()}",
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
