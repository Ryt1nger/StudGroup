"""Read-only browser navigation test for RANEPA personal cabinet and LMS.

Does not use APIs, log in, transfer cookies, solve CAPTCHA, or write StudGroup data.
Run: uv run --with playwright python scripts/ranepa_browser_ui_probe.py
"""

import argparse
import asyncio
import json
from datetime import date, datetime, timedelta
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import async_playwright

from studgroup.ranepa_probe import SourceAccessError
from studgroup.ranepa_proxy import current_proxy_url, playwright_proxy_settings
from studgroup.ranepa_public import extract_public_schedule
from studgroup.ranepa_schedule import group_code, normalized
from studgroup.ranepa_ui import page_status, source_diagnostics

SOURCES = {
    "cabinet": "https://my.ranepa.ru/lk/student/schedule",
    "public": "https://my.ranepa.ru/schedule/",
    "moodle": "https://lms.ranepa.ru/my/",
}
ALLOWED_HOSTS = {"my.ranepa.ru", "lms.ranepa.ru"}


async def inspect(mode: str, code: str, week_start: date) -> dict:
    """Follow the browser UI but fail closed on identity, login and challenge pages."""
    url = SOURCES[mode]
    async with async_playwright() as playwright:
        proxy_url = current_proxy_url()
        options = {"proxy": playwright_proxy_settings(proxy_url)} if proxy_url else {}
        browser = await playwright.chromium.launch(headless=True, **options)
        try:
            page = await browser.new_page(locale="ru-RU", timezone_id="Europe/Moscow")
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=25000)
                await page.wait_for_timeout(1300)
            except PlaywrightError:
                return {"mode": mode, "status": "network_unavailable"}
            if urlparse(page.url).hostname not in ALLOWED_HOSTS:
                return {"mode": mode, "status": "unexpected_redirect"}
            try:
                text = await page.locator("body").inner_text(timeout=5000)
                password_input = await page.locator('input[type="password"]').count() > 0
            except PlaywrightError:
                return {"mode": mode, "status": "page_unreadable"}
            status = page_status(page.url, text, password_input=password_input)
            if status != "rendered":
                return {"mode": mode, "status": status}
            stats = source_diagnostics(
                text,
                table_count=await page.locator("table").count(),
                row_count=await page.locator("tr").count(),
            )
            if mode == "moodle":
                return {
                    "mode": mode,
                    "status": "moodle_calendar_is_not_group_timetable",
                    **stats,
                }
            if not stats["has_schedule_label"] and not stats["lesson_time_candidates"]:
                return {"mode": mode, "status": "no_timetable_in_rendered_shell", **stats}
            if normalized(code) not in normalized(text):
                return {"mode": mode, "status": "group_identity_unverified", **stats}
            try:
                snapshot = extract_public_schedule(
                    await page.content(), code, week_start, week_start + timedelta(days=6)
                )
            except SourceAccessError:
                return {"mode": mode, "status": "unrecognized_schedule_layout", **stats}
            lessons = list(snapshot.indexed().values())
            if not lessons:
                return {"mode": mode, "status": "unrecognized_schedule_layout", **stats}
            return {
                "mode": mode,
                "status": "preview_only",
                "complete": False,
                "group_code": code,
                "week_start": week_start.isoformat(),
                "lesson_count": len(lessons),
                "lessons": [
                    {
                        "day": row.day.isoformat(),
                        "starts": row.starts.isoformat(timespec="minutes"),
                        "ends": row.ends.isoformat(timespec="minutes"),
                        "subject": row.subject,
                        "teacher": row.teacher,
                        "room": row.room,
                    }
                    for row in lessons
                ],
            }
        finally:
            await browser.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=SOURCES, default="cabinet")
    parser.add_argument("--group-code", required=True)
    parser.add_argument("--week-start", type=date.fromisoformat)
    args = parser.parse_args()
    code = group_code(args.group_code)
    today = datetime.now(ZoneInfo("Europe/Moscow")).date()
    week_start = args.week_start or today - timedelta(days=today.weekday())
    if week_start.weekday() != 0:
        parser.error("week_start must be Monday")
    result = asyncio.run(inspect(args.mode, code, week_start))
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
