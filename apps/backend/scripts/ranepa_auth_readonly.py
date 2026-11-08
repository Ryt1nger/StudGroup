"""One-shot external read-only pilot; no HTML, cookies or credentials are printed or saved.

Run with: uv run --with playwright python scripts/ranepa_auth_readonly.py --group-code CODE
Credentials are entered interactively and stay only in process memory.
The optional DB check uses a disposable local SQLite file, never production.
"""

import argparse
import asyncio
import getpass
import json
import tempfile
from collections import Counter
from datetime import date, datetime, timedelta
from pathlib import Path
from time import perf_counter
from uuid import uuid4
from zoneinfo import ZoneInfo

import httpx
from playwright.async_api import async_playwright
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from studgroup.models import Base, Group, Membership, User
from studgroup.ranepa_browser import read_month, read_week, resolve_sdo_exits
from studgroup.ranepa_personal import personal_period, remember_personal
from studgroup.ranepa_schedule import group_code


async def direct_http_check(context):
    """GET the same LK page with in-memory session cookies and no browser renderer."""
    jar = httpx.Cookies()
    for cookie in await context.cookies("https://my.ranepa.ru/"):
        domain = cookie["domain"].lstrip(".")
        if domain in {"ranepa.ru", "my.ranepa.ru"}:
            jar.set(cookie["name"], cookie["value"], domain=domain, path=cookie["path"])
    try:
        async with httpx.AsyncClient(cookies=jar, timeout=20, follow_redirects=False) as client:
            response = await client.get("https://my.ranepa.ru/lk/student/schedule")
        html = response.text if len(response.content) <= 12_000_000 else ""
        return {
            "http_status": response.status_code,
            "rendered_week_grid": "schedule-week__row" in html,
            "rendered_month_grid": "schedule-month__cell--item" in html,
        }
    except httpx.HTTPError:
        return {"http_status": None, "rendered_week_grid": False, "rendered_month_grid": False}


async def local_db_check(snapshot):
    with tempfile.TemporaryDirectory(prefix="studgroup-ranepa-pilot-") as folder:
        engine = create_async_engine(f"sqlite+aiosqlite:///{Path(folder) / 'pilot.db'}")
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with AsyncSession(engine, expire_on_commit=False) as db:
                group = Group(
                    id=uuid4(),
                    telegram_chat_id=-990001,
                    name="Pilot",
                    academic_group_code=snapshot.group_code,
                    timezone="Europe/Moscow",
                )
                user = User(id=uuid4(), telegram_user_id=990001, display_name="Pilot")
                db.add_all([group, user])
                await db.flush()
                db.add(
                    Membership(group_id=group.id, user_id=user.id, role="student", status="active")
                )
                await db.flush()
                await remember_personal(
                    db, group.id, user.id, snapshot, group_identity_verified=True
                )
                await db.commit()
                cached = await personal_period(
                    db, group.id, user.id, snapshot.week_start, snapshot.week_end
                )
                return cached["complete"] and len(cached["lessons"]) == len(snapshot.indexed())
        finally:
            await engine.dispose()


async def inspect(
    login: str,
    password: str,
    code: str,
    monday: date,
    headed: bool,
    month: bool,
    sdo: bool,
    local_db: bool,
    http_check: bool = False,
    show_matches: bool = False,
) -> dict:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=not headed)
        try:
            context = await browser.new_context(locale="ru-RU", timezone_id="Europe/Moscow")
            page = await context.new_page()
            if month:
                selected = await read_month(page, login, password, code, monday.year, monday.month)
                if selected.status != "preview_only":
                    return {"status": selected.status, "stage": "month"}
                starts = date(monday.year, monday.month, 1)
                first_monday = starts - timedelta(days=starts.weekday())
                weekly = []
                weeks_complete = 0
                sdo_marked = 0
                sdo_matched = 0
                sdo_courses = 0
                sdo_states = Counter()
                matched_rows = []
                for offset in range(6):
                    week = first_monday + timedelta(weeks=offset)
                    if not any(
                        week <= card.day <= week + timedelta(days=6) for card in selected.cards
                    ):
                        continue
                    result = await read_week(page, login, password, code, week)
                    if result.snapshot is None:
                        return {
                            "status": result.status,
                            "stage": "week",
                            "weeks_complete": weeks_complete,
                        }
                    weeks_complete += 1
                    if sdo:
                        linked, states, courses = await resolve_sdo_exits(
                            context, result, login, password
                        )
                        sdo_marked += sum(row.room == "СДО" for row in result.snapshot.lessons)
                        sdo_matched += len(linked)
                        sdo_courses += len(courses)
                        sdo_states.update(states.values())
                        if show_matches:
                            matched_rows.extend(
                                {"day": key[0], "subject": key[3], "url": url}
                                for key, url in linked.items()
                            )
                    weekly.extend(
                        row
                        for row in result.snapshot.indexed().values()
                        if row.day.month == monday.month
                    )
                from_month = Counter((row.day, row.starts, row.subject) for row in selected.cards)
                from_weeks = Counter((row.day, row.starts, row.subject) for row in weekly)
                return {
                    "status": "preview_only" if from_month == from_weeks else "month_week_mismatch",
                    "month_card_count": len(selected.cards),
                    "weekly_card_count": len(weekly),
                    "month_week_match": from_month == from_weeks,
                    "sdo_marked_count": sdo_marked if sdo else None,
                    "sdo_matched_count": sdo_matched if sdo else None,
                    "sdo_course_count": sdo_courses if sdo else None,
                    "sdo_states": dict(sorted(sdo_states.items())) if sdo else None,
                    **({"matched_links": matched_rows} if show_matches else {}),
                    "group_identity_verified": True,
                    "complete": False,
                    **({"direct_http": await direct_http_check(context)} if http_check else {}),
                }
            result = await read_week(page, login, password, code, monday)
            if result.snapshot is None:
                return {"status": result.status}
            rows = result.snapshot.indexed().values()
            linked, link_states, courses = (
                await resolve_sdo_exits(context, result, login, password) if sdo else ({}, {}, {})
            )
            local_ok = await local_db_check(result.snapshot) if local_db else None
            return {
                "status": result.status,
                "week_start": monday.isoformat(),
                "lesson_count": len(rows),
                "days_with_lessons": len({row.day for row in rows}),
                "sdo_marked_count": sum(row.room == "СДО" for row in rows),
                "subject_count": len({row.subject.casefold() for row in rows}),
                "sdo_matched_count": len(linked) if sdo else None,
                "sdo_course_count": len(courses) if sdo else None,
                "sdo_states": sorted(link_states.values()) if sdo else None,
                **(
                    {
                        "matched_links": [
                            {"day": key[0], "subject": key[3], "url": url}
                            for key, url in linked.items()
                        ]
                    }
                    if show_matches
                    else {}
                ),
                "isolated_db_round_trip": local_ok,
                "group_identity_verified": True,
                "complete": False,
                **({"direct_http": await direct_http_check(context)} if http_check else {}),
            }
        finally:
            await browser.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--group-code", required=True)
    parser.add_argument("--week-start", type=date.fromisoformat)
    parser.add_argument("--headed", action="store_true")
    parser.add_argument(
        "--month", action="store_true", help="cross-check month grid against every week"
    )
    parser.add_argument(
        "--sdo", action="store_true", help="follow SDO course pages without saving links"
    )
    parser.add_argument(
        "--show-matches", action="store_true", help="print matched course URL and lesson subject"
    )
    parser.add_argument(
        "--local-db-check", action="store_true", help="test real rows in a disposable local DB"
    )
    parser.add_argument(
        "--http-check", action="store_true", help="try authenticated direct HTTP without a renderer"
    )
    args = parser.parse_args()
    if args.show_matches and not args.sdo:
        parser.error("--show-matches requires --sdo")
    today = datetime.now(ZoneInfo("Europe/Moscow")).date()
    monday = args.week_start or today - timedelta(days=today.weekday())
    login = getpass.getpass("Логин РАНХиГС: ")
    password = getpass.getpass("Пароль РАНХиГС: ")
    started = perf_counter()
    result = asyncio.run(
        inspect(
            login,
            password,
            group_code(args.group_code),
            monday,
            args.headed,
            args.month,
            args.sdo,
            args.local_db_check,
            args.http_check,
            args.show_matches,
        )
    )
    result["elapsed_seconds"] = round(perf_counter() - started, 1)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
