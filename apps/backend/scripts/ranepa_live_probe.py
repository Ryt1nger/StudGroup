"""One-shot, read-only Playwright experiment for group-code discovery at RANEPA.

No credentials, cookies or source HTML are printed. Nothing is written to StudGroup.
Requires: uv run --with playwright python -m playwright install chromium
Run: uv run --with playwright python scripts/ranepa_live_probe.py --group-code CODE
"""

import argparse
import asyncio
import json
from datetime import datetime, timedelta
from urllib.parse import parse_qs, urlparse
from uuid import UUID
from zoneinfo import ZoneInfo

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import async_playwright

from studgroup.ranepa_discovery import DiscoveryError, find_candidates
from studgroup.ranepa_probe import SCHEDULE_URL, SourceAccessError
from studgroup.ranepa_public import extract_public_schedule
from studgroup.ranepa_schedule import group_code, normalized

QUERY_SELECTORS = (
    'input[placeholder*="групп" i]',
    'input[aria-label*="групп" i]',
    'input[role="combobox"]',
    'input[type="search"]',
    'input[autocomplete="off"]',
)
CAPTCHA_MARKERS = ("captcha", "капча", "проверка, что вы не робот")


def group_uuid_in_url(url: str) -> UUID | None:
    if urlparse(url).hostname != "my.ranepa.ru":
        return None
    values = parse_qs(urlparse(url).query).get("f", [])
    if len(values) != 1:
        return None
    try:
        return UUID(values[0])
    except ValueError:
        return None


async def live_discovery(code: str) -> dict:
    """Use normal site UI; on a CAPTCHA return a diagnostic, never an invented UUID."""
    matched = set()
    pending = []
    ui_errors = 0

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            page = await browser.new_page(locale="ru-RU")

            async def inspect_response(response):
                if urlparse(response.url).hostname != "my.ranepa.ru":
                    return
                if "json" not in response.headers.get("content-type", ""):
                    return
                if int(response.headers.get("content-length", 0)) > 2_000_000:
                    return
                try:
                    data = await response.json()
                    matched.update(find_candidates(data, code))
                except (ValueError, DiscoveryError, RuntimeError):
                    return

            page.on(
                "response",
                lambda response: pending.append(asyncio.create_task(inspect_response(response))),
            )
            try:
                await page.goto(SCHEDULE_URL, wait_until="domcontentloaded", timeout=30000)
                await page.wait_for_timeout(3000)
            except PlaywrightError:
                return {"status": "unreachable"}

            title = normalized(await page.title())
            if any(mark in title for mark in CAPTCHA_MARKERS):
                return {"status": "captcha"}

            # Trigger the site's native group selector and its normal autocomplete.
            try:
                group_tab = page.get_by_text("Академ. группы", exact=False).first
                if await group_tab.is_visible():
                    await group_tab.click(timeout=3000)
            except PlaywrightError:
                ui_errors += 1

            searched = False
            for selector in QUERY_SELECTORS:
                controls = page.locator(selector)
                for index in range(min(await controls.count(), 8)):
                    control = controls.nth(index)
                    try:
                        if await control.is_visible():
                            await control.fill(code, timeout=2500)
                            await page.wait_for_timeout(2500)
                            searched = True
                            break
                    except PlaywrightError:
                        ui_errors += 1
                        continue
                if searched:
                    break

            if pending:
                await asyncio.gather(*pending, return_exceptions=True)

            # Also check visible DOM controls if the university embeds catalog
            # records in HTML instead of an XHR JSON response.
            elements = await page.locator(
                'a[href*="f="], option, [role="option"], [data-id], [data-uuid]'
            ).evaluate_all(
                """nodes => nodes.slice(0, 10000).map(el => ({
                    label: (el.innerText || el.textContent || "").trim(),
                    href: el.getAttribute("href") || "",
                    uuid: el.getAttribute("data-uuid") || el.getAttribute("data-id") || ""
                }))"""
            )
            matched.update(find_candidates(elements, code))

            # Select the exact catalog suggestion, then inspect the URL.
            for candidate in sorted(matched, key=lambda item: str(item.upstream_uuid)):
                try:
                    option = page.get_by_text(candidate.label, exact=True).first
                    if await option.is_visible():
                        await option.click(timeout=2500)
                        await page.wait_for_timeout(1500)
                        selected_uuid = group_uuid_in_url(page.url)
                        if selected_uuid is not None:
                            matched.add(candidate)
                        break
                except PlaywrightError:
                    ui_errors += 1
                    continue

            ids = {candidate.upstream_uuid for candidate in matched}
            if not ids:
                body = normalized((await page.locator("body").inner_text())[:10000])
                if any(mark in body for mark in CAPTCHA_MARKERS):
                    return {"status": "captcha"}
                return {
                    "status": "no_exact_group",
                    "search_input_found": searched,
                    "ui_errors": ui_errors,
                }
            if len(ids) != 1:
                return {"status": "ambiguous_group", "matching_uuid_count": len(ids)}

            uuid = next(iter(ids))
            url = f"{SCHEDULE_URL}?by=group&f={uuid}&type=schedule"
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                await page.wait_for_timeout(2000)
                html = await page.content()
                start = datetime.now(ZoneInfo("Europe/Moscow")).date()
                start -= timedelta(days=start.weekday())
                week = extract_public_schedule(html, code, start, start + timedelta(days=6))
            except (SourceAccessError, PlaywrightError) as error:
                # Never mark a UUID as verified before we can see its group.
                kind = str(error) if isinstance(error, SourceAccessError) else "page_unavailable"
                return {"status": "candidate_not_verified", "reason": kind}

            if not week.lessons:
                return {"status": "source_unparsed", "group_uuid": str(uuid)}
            return {
                "status": "verified_preview",
                "group_code": code,
                "group_uuid": str(uuid),
                "week_start": start.isoformat(),
                "lesson_count": len(week.indexed()),
                "examples": [
                    {"day": item.day.isoformat(), "subject": item.subject, "teacher": item.teacher}
                    for item in list(week.indexed().values())[:5]
                ],
            }
        finally:
            await browser.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--group-code", required=True)
    args = parser.parse_args()
    code = group_code(args.group_code)
    print(json.dumps(asyncio.run(live_discovery(code)), ensure_ascii=False))


if __name__ == "__main__":
    main()
