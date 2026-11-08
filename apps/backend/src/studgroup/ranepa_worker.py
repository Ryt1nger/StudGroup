"""Paced, single-browser university crawl; disabled unless explicitly enabled."""

import argparse
import asyncio
import signal
from collections import Counter
from datetime import date, datetime, timedelta
from time import monotonic
from zoneinfo import ZoneInfo

from cryptography.fernet import Fernet, InvalidToken
from playwright.async_api import async_playwright
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from studgroup.legal import has_consent, legal_ready
from studgroup.main import Settings
from studgroup.models import Group, Membership, MemberSubgroup, UserProfile
from studgroup.ranepa_browser import read_month, read_week, resolve_sdo_exits
from studgroup.ranepa_crawl_jobs import claim_next, enqueue_week, finish
from studgroup.ranepa_crawl_plan import CrawlCandidate
from studgroup.ranepa_personal import remember_personal


async def prepare_week(engine, today: date, settings: Settings) -> int:
    if not legal_ready(settings):
        return 0
    monday = today - timedelta(days=today.weekday())
    async with AsyncSession(engine, expire_on_commit=False) as db:
        members = (
            await db.execute(
                select(Membership, Group)
                .join(Group, Group.id == Membership.group_id)
                .join(UserProfile, UserProfile.user_id == Membership.user_id)
                .where(
                    Membership.status == "active",
                    Group.status == "active",
                    Group.academic_group_code.is_not(None),
                )
            )
        ).all()
        candidates = []
        for member, group in members:
            profile = await db.get(UserProfile, member.user_id)
            if not profile or not profile.sdu_login or not profile.sdu_password_encrypted:
                continue
            if not await has_consent(db, member.user_id, "sdo", settings):
                continue
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
        profile = await db.get(UserProfile, user_id)
        if (
            group is None
            or profile is None
            or not group.academic_group_code
            or not profile.sdu_login
            or not profile.sdu_password_encrypted
            or not legal_ready(settings)
            or not await has_consent(db, user_id, "sdo", settings)
        ):
            await finish(db, job_id, group_id, user_id, failure_code="login_failed")
            await db.commit()
            return "failed"
        code = group.academic_group_code
        login = profile.sdu_login
        encrypted_password = profile.sdu_password_encrypted
        await db.commit()
    secret = settings.profile_encryption_key.get_secret_value()
    if not secret:
        failure = "internal"
    else:
        try:
            cipher = Fernet(secret.encode())
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
                    links, link_states, courses = await resolve_sdo_exits(
                        context, result, login, password
                    )
                    async with AsyncSession(engine) as db:
                        await remember_personal(
                            db,
                            group_id,
                            user_id,
                            result.snapshot,
                            links=links,
                            courses=courses,
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
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(signum, stop.set)
    engine = create_async_engine(settings.database_url)
    try:
        next_prepare_at = 0.0
        while not stop.is_set():
            today = datetime.now(ZoneInfo("Europe/Moscow")).date()
            if monotonic() >= next_prepare_at:
                await prepare_week(engine, today, settings)
                # Pick up new accounts during the week without rescanning every
                # member before each browser job.
                next_prepare_at = monotonic() + 3600
            status = await process_one(engine, settings, today)
            try:
                await asyncio.wait_for(
                    stop.wait(),
                    timeout=settings.ranepa_crawl_pause_seconds if status != "idle" else 300,
                )
            except TimeoutError:
                pass
    finally:
        await engine.dispose()
        for signum in (signal.SIGTERM, signal.SIGINT):
            loop.remove_signal_handler(signum)


async def run_batch(settings: Settings, *, max_minutes: int = 24) -> int:
    """Run a bounded hosted-runner shift, finishing the current account before exit."""
    if not settings.ranepa_crawl_enabled:
        return 0
    if not legal_ready(settings):
        raise RuntimeError("Approved legal settings and consent are required")
    if not settings.profile_encryption_key.get_secret_value():
        raise RuntimeError("PROFILE_ENCRYPTION_KEY is required")
    if not settings.database_url.startswith("postgresql+asyncpg://"):
        raise RuntimeError("PostgreSQL DATABASE_URL is required")
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    completed = 0
    try:
        await prepare_week(engine, datetime.now(ZoneInfo("Europe/Moscow")).date(), settings)
        deadline = monotonic() + max_minutes * 60
        while monotonic() < deadline:
            today = datetime.now(ZoneInfo("Europe/Moscow")).date()
            status = await process_one(engine, settings, today)
            if status == "idle":
                break
            completed += 1
            if monotonic() < deadline:
                await asyncio.sleep(settings.ranepa_crawl_pause_seconds)
        return completed
    finally:
        await engine.dispose()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-minutes", type=int, choices=range(1, 25))
    args = parser.parse_args()
    settings = Settings()
    if args.batch_minutes:
        asyncio.run(run_batch(settings, max_minutes=args.batch_minutes))
    else:
        asyncio.run(run_forever(settings))


if __name__ == "__main__":
    main()
