"""Classify university timetable pages observed by a normal browser."""

import re
from pathlib import Path
from urllib.parse import urlparse

from studgroup.ranepa_schedule import normalized

CAPTCHA_MARKERS = ("captcha", "капча", "recaptcha", "проверка, что вы не робот")
LOGIN_PATHS = ("/login", "/auth", "/signin")
TIME_RE = re.compile(r"\b\d{1,2}:\d{2}\s*[-–—]\s*\d{1,2}:\d{2}\b")


def page_status(url: str, body: str, *, password_input: bool = False) -> str:
    text = normalized(body)
    if any(marker in text for marker in CAPTCHA_MARKERS):
        return "captcha_required"
    path = urlparse(url).path.casefold()
    if any(part in path for part in LOGIN_PATHS) or password_input:
        return "login_required"
    if not text.strip():
        return "empty_page"
    return "rendered"


def source_diagnostics(body: str, *, table_count: int, row_count: int) -> dict:
    text = normalized(body)
    return {
        "table_count": table_count,
        "row_count": row_count,
        "lesson_time_candidates": len(TIME_RE.findall(text)),
        "has_schedule_label": "расписание" in text or "schedule" in text,
    }


def private_profile(path: Path) -> Path:
    """Keep the browser session outside the repository with private permissions."""
    expanded = path.expanduser()
    if expanded.is_symlink():
        raise ValueError("profile_symlink_not_allowed")
    target = expanded.resolve()
    target.mkdir(parents=True, exist_ok=True, mode=0o700)
    target.chmod(0o700)
    return target
