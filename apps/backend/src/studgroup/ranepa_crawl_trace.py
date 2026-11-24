"""Privacy-safe, bounded per-account chronology for diagnosing CAPTCHA triggers.

The trace uses a closed vocabulary: never accept URL, login, account data,
page HTML, image bytes, cookies or exception details. Time is elapsed since
the account claim, not a user event stream.
"""

import json
import time
from datetime import UTC, datetime

from studgroup.models import RanepaCrawlObservation
from studgroup.ranepa_failure_diagnostics import DIAGNOSTIC_REASONS

LOGIN_STAGE_LABELS = {
    "lk_open_requested": "Открытие страницы личного кабинета",
    "lk_page_loaded": "Страница ЛК загружена, проверка состояния",
    "lk_form_visible": "Обнаружена форма авторизации",
    "lk_login_filled": "Поле логина заполнено",
    "lk_password_filled": "Поле пароля заполнено",
    "lk_submit_requested": "Нажатие кнопки Войти",
    "lk_form_submitted": "Форма отправлена, ожидание ответа",
    "lk_response_recheck": "Повторная проверка ответа на отправленную форму",
    "lk_connection_recheck": "Повторная проверка соединения с ЛК",
    "lk_connection_checks_exhausted": "Соединение с ЛК не восстановилось за 3 проверки",
    "lk_form_still_visible": "После отправки форма входа осталась открытой",
    "lk_empty_after_submit": "После отправки страница не загрузилась",
    "lk_challenge_pending": "После отправки не завершилась проверка браузера",
    "lk_explicit_rejection": "ЛК показал явное сообщение об отказе входа",
    "lk_captcha_required": "После отправки обнаружена CAPTCHA",
    "lk_unexpected_redirect": "Переход на неожиданную страницу ЛК",
    "lk_rendered_after_submit": "После отправки форма входа исчезла",
    "lk_schedule_requested": "Переход к расписанию после входа",
    "lk_session_expired": "Сессия истекла при чтении расписания",
    "lk_group_verify_requested": "Проверка учебной группы после входа",
    "lk_group_verified": "Учебная группа подтверждена",
}

STEPS = frozenset(
    {
        "job_claimed",
        "credentials_ready",
        "session_available",
        "session_fresh",
        "browser_started",
        "stored_session_rejected",
        "month_requested",
        "month_verified",
        "week_requested",
        "week_verified",
        "week_retry",
        "week_unreadable",
        "week_month_mismatch",
        "sdo_exits_requested",
        "sdo_exits_finished",
        "sdo_exits_timed_out",
        "sdo_exits_skipped_budget",
        "sdo_common_links_reused",
        "lms_dashboard_requested",
        "lms_login_form",
        "lms_login_submitted",
        "lms_courses_loaded",
        "lms_course_requested",
        "captcha_month",
        "captcha_week",
        "captcha_sdo",
        "captcha_solver_started",
        "captcha_solver_submitted",
        "captcha_solver_failed",
        "month_recheck",
        "week_recheck",
        "schedule_verified",
    }
)
STEPS |= frozenset(LOGIN_STAGE_LABELS)
STEPS |= frozenset(f"diagnostic_{code}" for code in DIAGNOSTIC_REASONS)
MAX_EVENTS = 40
_SAFE_RESULTS = frozenset(
    {
        "succeeded",
        "login_failed",
        "captcha",
        "source_unavailable",
        "identity_unverified",
        "parse_failed",
        "internal",
    }
)


class CrawlTrace:
    """Single-account trace, never shared across worker jobs."""

    def __init__(self, *, clock=None):
        self._clock = clock or time.monotonic
        self._start = self._clock()
        self.events: list[dict] = []
        self.captcha_seen = False
        self.captcha_location: str | None = None
        self.preceding_step: str | None = None
        self.session_available = False
        # Predefined, privacy-safe reason for one incomplete weekly read.
        self.failure_detail: str | None = None
        self.diagnostic: str | None = None

    def record(self, step: str) -> None:
        if step not in STEPS:
            raise ValueError("unsafe_crawl_trace_step")
        if len(self.events) >= MAX_EVENTS:
            self.events.pop(0)
        milliseconds = max(0, int((self._clock() - self._start) * 1000))
        self.events.append({"step": step, "elapsed_ms": milliseconds})

    def mark_diagnostic(self, code: str) -> None:
        """Persist only a reviewed, non-sensitive failure category."""
        if code not in DIAGNOSTIC_REASONS:
            raise ValueError("unsafe_crawl_diagnostic")
        if self.diagnostic != code:
            self.diagnostic = code
            self.record("diagnostic_" + code)

    def mark_captcha(self, location: str) -> None:
        if location not in {"month", "week", "sdo"}:
            raise ValueError("unsafe_crawl_trace_captcha_location")
        if not self.captcha_seen:
            self.captcha_seen = True
            self.captcha_location = location
            self.preceding_step = self.events[-1]["step"] if self.events else None
        self.record("captcha_" + location)

    def last_notification_step(self) -> str | None:
        """Return the last owner-relevant stage from the closed trace vocabulary."""
        relevant = {
            "month_requested",
            "month_recheck",
            "week_requested",
            "week_recheck",
            "week_retry",
            "sdo_exits_requested",
            "sdo_exits_finished",
        }
        relevant.update(LOGIN_STAGE_LABELS)
        return next(
            (event["step"] for event in reversed(self.events) if event["step"] in relevant),
            None,
        )

    def observation(
        self,
        *,
        job_id,
        attempt: int,
        failure_code: str | None,
        stats=None,
        notification_pending: bool = False,
    ):
        result = "succeeded" if failure_code is None else failure_code
        if result not in _SAFE_RESULTS:
            result = "internal"
        return RanepaCrawlObservation(
            job_id=job_id,
            attempt=attempt,
            observed_at=datetime.now(UTC),
            result_code=result,
            captcha_seen=self.captcha_seen,
            captcha_location=self.captcha_location,
            preceding_step=self.preceding_step,
            session_available=self.session_available,
            elapsed_ms=max(0, int((self._clock() - self._start) * 1000)),
            events_json=json.dumps(self.events, separators=(",", ":")),
            failure_detail=self.failure_detail,
            diagnostic_code=self.diagnostic,
            last_step=self.last_notification_step(),
            saved_weeks=stats.saved_weeks if stats is not None else None,
            saved_lessons=stats.saved_lessons if stats is not None else None,
            saved_sdo_links=stats.saved_sdo_links if stats is not None else None,
            discovered_sdo_candidates=(
                stats.discovered_sdo_candidates if stats is not None else None
            ),
            saved_sdo_courses=stats.saved_sdo_courses if stats is not None else None,
            notification_state="pending" if notification_pending else None,
        )
