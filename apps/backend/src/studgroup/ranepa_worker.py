"""Paced, single-browser university crawl; disabled unless explicitly enabled."""

import asyncio
from collections import Counter
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from cryptography.fernet import Fernet, InvalidToken
from playwright.async_api import async_playwright
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from studgroup.main import Settings
from studgroup.models import Group, Membership, MemberSubgroup, RanepaAccount
from studgroup.ranepa_browser import read_month, read_week, resolve_sdo_exits
from studgroup.ranepa_crawl_jobs import claim_next, enqueue_week, finish
from studgroup.ranepa_crawl_plan import CrawlCandidate
from studgroup.ranepa_personal import remember_personal


async def prepare_week(engine, today: date) -> int:
    monday = today - timedelta(days=today.weekday())
    async with AsyncSession(engine, expire_on_commit=False) as db:
        members = (
            await db.execute(
                select(Membership, Group)
                .join(Group, Group.id == Membership.group_id)
                .join(RanepaAccount, RanepaAccount.user_id == Membership.user_id)
                .where(
                    Membership.status == "active",
                    Group.status == "active",
                    Group.academic_group_code.is_not(None),
                )
            )
        ).all()
        candidates = []
        for member, group in members:
            subgroups = (
                await db.scalars(
                    select(MemberSubgroup.subgroup_id).where(
                        MemberSubgroup.membership_id == member.id
                    )
                )
            ).all()
            candidates.append(CrawlCandidate(group.id, member.user_id, frozenset(subgroups)))
        count = await enqueue_week(db, monday, candidates)
        await db.commit()
        return count


async def process_one(engine, settings: Settings, today: date) -> str:
    async with AsyncSession(engine, expire_on_commit=False) as db:
        job = await claim_next(db)
        if job is None:
            await db.commit()
            return "idle"
        job_id, group_id, user_id = job.id, job.group_id, job.user_id
        group = await db.get(Group, group_id)
        account = await db.get(RanepaAccount, user_id)
        if group is None or account is None or not group.academic_group_code:
            await finish(db, job_id, group_id, user_id, failure_code="login_failed")
            await db.commit()
            return "failed"
        code = group.academic_group_code
        encrypted_login, encrypted_password = account.login_encrypted, account.password_encrypted
        await db.commit()
    secret = settings.profile_encryption_key.get_secret_value()
    if not secret:
        failure = "internal"
    else:
        try:
            cipher = Fernet(secret.encode())
            login = cipher.decrypt(encrypted_login.encode()).decode()
            password = cipher.decrypt(encrypted_password.encode()).decode()
        except (ValueError, InvalidToken, UnicodeDecodeError):
            failure = "login_failed"
        else:
            failure = await _collect(engine, group_id, user_id, code, login, password, today)
    async with AsyncSession(engine) as db:
        await finish(db, job_id, group_id, user_id, failure_code=failure)
        await db.commit()
    return "succeeded" if failure is None else "failed"


async def _collect(engine, group_id, user_id, code, login, password, today):
    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            try:
                context = await browser.new_context(locale="ru-RU", timezone_id="Europe/Moscow")
                page = await context.new_page()
                month = await read_month(page, login, password, code, today.year, today.month)
                if month.status != "preview_only":
                    if month.status == "captcha_required":
                        return "captcha"
                    if month.status == "identity_unverified":
                        return "identity_unverified"
                    return "source_unavailable"
                first = date(today.year, today.month, 1)
                monday = first - timedelta(days=first.weekday())
                for offset in range(6):
                    week_start = monday + timedelta(weeks=offset)
                    expected = [
                        card
                        for card in month.cards
                        if week_start <= card.day <= week_start + timedelta(days=6)
                    ]
                    result = await read_week(
                        page, login, password, code, week_start, allow_empty=not expected
                    )
                    if result.snapshot is None:
                        if result.status == "captcha_required":
                            return "captcha"
                        if result.status == "identity_unverified":
                            return "identity_unverified"
                        return "parse_failed"
                    actual = [
                        row
                        for row in result.snapshot.indexed().values()
                        if row.day.month == today.month
                    ]
                    if Counter((r.day, r.starts, r.subject) for r in actual) != Counter(
                        (r.day, r.starts, r.subject) for r in expected
                    ):
                        return "parse_failed"
                    links, link_states = await resolve_sdo_exits(context, result, login, password)
                    async with AsyncSession(engine) as db:
                        await remember_personal(
                            db,
                            group_id,
                            user_id,
                            result.snapshot,
                            links=links,
                            link_states=link_states,
                            group_identity_verified=True,
                        )
                        await db.commit()  # Each validated week is visible immediately.
                return None
            finally:
                await browser.close()
    except Exception:  # noqa: BLE001
        # Never include provider exception text, HTML, URLs or credentials in SQL/logs.
        return "internal"


async def run_forever(settings: Settings):
    if not settings.ranepa_crawl_enabled:
        return
    engine = create_async_engine(settings.database_url)
    try:
        while True:
            today = datetime.now(ZoneInfo("Europe/Moscow")).date()
            await prepare_week(engine, today)
            status = await process_one(engine, settings, today)
            await asyncio.sleep(settings.ranepa_crawl_pause_seconds if status != "idle" else 300)
    finally:
        await engine.dispose()


def main():
    asyncio.run(run_forever(Settings()))


if __name__ == "__main__":
    main()
