"""Local Mac pilot: use a private Playwright browser profile to inspect RANEPA timetable.

No password or session contents are passed to StudGroup or GitHub.
First run with --setup, log in yourself in the new window, then rerun without it.
This is deliberately read-only and never mutates the StudGroup calendar.
"""

import argparse
import asyncio
import json
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import async_playwright

from studgroup.ranepa_discovery import exact_code_match
from studgroup.ranepa_probe import SourceAccessError
from studgroup.ranepa_public import extract_public_schedule
from studgroup.ranepa_schedule import group_code, normalized
from studgroup.ranepa_ui import page_status, private_profile, source_diagnostics

URLS = {
    "cabinet": "https://my.ranepa.ru/lk/student/schedule",
    "public": "https://my.ranepa.ru/schedule/",
    "moodle": "https://lms.ranepa.ru/my/",
}
HOSTS = {"my.ranepa.ru", "lms.ranepa.ru"}
DEFAULT_PROFILE = Path.home() / "Library" / "Application Support" / "StudGroup" / "RANEPA Browser"


async def select_group(page, code: str) -> str:
    """Use normal visible group picker; never select a near-matching cohort."""
    try:
        tab = page.get_by_text("Академ. группы", exact=False).first
        if await tab.is_visible():
            await tab.click(timeout=2500)
    except PlaywrightError:
        return "group_picker_not_available"
    for selector in ('input[placeholder*="групп" i]', 'input[role="combobox"]'):
        inputs = page.locator(selector)
        for index in range(min(await inputs.count(), 5)):
            control = inputs.nth(index)
            try:
                if not await control.is_visible():
                    continue
                await control.fill(code, timeout=2500)
                await page.wait_for_timeout(1200)
                options = page.get_by_role("option")
                for index2 in range(min(await options.count(), 25)):
                    option = options.nth(index2)
                    if exact_code_match(await option.inner_text(), code):
                        await option.click(timeout=2500)
                        await page.wait_for_timeout(1500)
                        return "group_selected"
                return "group_not_found_in_picker"
            except PlaywrightError:
                continue
    return "group_picker_not_found"


async def pilot(
    *,
    mode: str,
    code: str,
    monday: date,
    profile: Path,
    setup: bool,
    headed: bool,
) -> dict:
    if monday.weekday() != 0:
        raise ValueError("week_start_must_be_monday")
    profile = private_profile(profile)
    async with async_playwright() as playwright:
        context = await playwright.chromium.launch_persistent_context(
            str(profile),
            headless=False if setup else not headed,
            locale="ru-RU",
            timezone_id="Europe/Moscow",
        )
        try:
            page = context.pages[0] if context.pages else await context.new_page()
            try:
                await page.goto(URLS[mode], wait_until="domcontentloaded", timeout=30000)
                await page.wait_for_timeout(1500)
            except PlaywrightError:
                return {"status": "source_unreachable", "mode": mode}
            if urlparse(page.url).hostname not in HOSTS:
                return {"status": "unexpected_redirect", "mode": mode}
            if setup:
                print("В открытом браузере самостоятельно войдите в университетский кабинет.")
                print("Когда откроется нужная страница, вернитесь в Терминал.")
                await asyncio.to_thread(input, "Нажмите Enter после входа: ")
                body = await page.locator("body").inner_text(timeout=10000)
                state = page_status(
                    page.url,
                    body,
                    password_input=(await page.locator('input[type="password"]').count()) > 0,
                )
                return {"status": "session_ready_for_test" if state == "rendered" else state}
            if mode == "public":
                await select_group(page, code)
            body = await page.locator("body").inner_text(timeout=10000)
            state = page_status(
                page.url,
                body,
                password_input=(await page.locator('input[type="password"]').count()) > 0,
            )
            if state != "rendered":
                return {"status": state, "mode": mode}
            stats = source_diagnostics(
                body,
                table_count=await page.locator("table").count(),
                row_count=await page.locator("tr").count(),
            )
            if mode == "moodle":
                return {"status": "moodle_available_not_group_timetable", **stats}
            if normalized(code) not in normalized(body):
                return {"status": "group_identity_unverified", "mode": mode, **stats}
            try:
                snapshot = extract_public_schedule(
                    await page.content(),
                    code,
                    monday,
                    monday + timedelta(days=6),
                )
            except SourceAccessError as error:
                return {"status": "parser_unverified", "reason": str(error), **stats}
            lessons = list(snapshot.indexed().values())
            if not lessons:
                return {"status": "parser_unverified", "mode": mode, **stats}
            return {
                "status": "preview_only",
                "complete": False,
                "week_start": monday.isoformat(),
                "group_code": code,
                "lesson_count": len(lessons),
                "lessons": [
                    {
                        "day": row.day.isoformat(),
                        "time": row.starts.isoformat(timespec="minutes"),
                        "subject": row.subject,
                        "teacher": row.teacher,
                        "room": row.room,
                    }
                    for row in lessons
                ],
            }
        finally:
            await context.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Local read-only RANEPA timetable pilot")
    parser.add_argument("--setup", action="store_true", help="one-off human sign-in")
    parser.add_argument("--headed", action="store_true", help="show browser during collection")
    parser.add_argument("--mode", choices=sorted(URLS), default="cabinet")
    parser.add_argument("--group-code", default="Э-ОБ-385-04-26-02")
    parser.add_argument("--week-start", type=date.fromisoformat)
    parser.add_argument("--profile", type=Path, default=DEFAULT_PROFILE)
    args = parser.parse_args()
    code = group_code(args.group_code)
    day = datetime.now(ZoneInfo("Europe/Moscow")).date()
    monday = args.week_start or day - timedelta(days=day.weekday())
    result = asyncio.run(
        pilot(
            mode=args.mode,
            code=code,
            monday=monday,
            profile=args.profile,
            setup=args.setup,
            headed=args.headed,
        )
    )
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
