"""Paced, single-browser university crawl; disabled unless explicitly enabled."""

import argparse
import asyncio
import json
import signal
from collections import Counter
from datetime import date, datetime, timedelta
from time import monotonic
from uuid import UUID
from zoneinfo import ZoneInfo

from cryptography.fernet import Fernet, InvalidToken
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import async_playwright
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from studgroup.legal import has_consent, legal_ready
from studgroup.main import Settings, database_engine_options
from studgroup.models import Group, Membership, MemberSubgroup, UserProfile
from studgroup.ranepa_browser import read_month, read_week, resolve_sdo_exits
from studgroup.ranepa_crawl_jobs import claim_next, enqueue_week, finish
from studgroup.ranepa_crawl_plan import CrawlCandidate
from studgroup.ranepa_credential_store import CredentialStoreUnavailable, configured_store
from studgroup.ranepa_login import normalize_sdo_login
from studgroup.ranepa_personal import remember_personal
from studgroup.ranepa_proxy import current_proxy_url, playwright_proxy_settings

ACCOUNT_TIMEOUT_SECONDS = 600


def restored_storage_state(encrypted_state: str | None, cipher: Fernet | None) -> dict | None:
    """Unusable or unrecognised session tokens never prevent password-based login."""
    if not encrypted_state or cipher is None:
        return None
    try:
        state = json.loads(cipher.decrypt(encrypted_state.encode("utf-8")).decode("utf-8"))
    except (InvalidToken, ValueError, TypeError, UnicodeError):
        return None
    if not isinstance(state, dict):
        return None
    if not isinstance(state.get("cookies"), list) or not isinstance(state.get("origins"), list):
        return None
    return state


def create_worker_engine(settings: Settings):
    return create_async_engine(
        settings.database_url,
        pool_pre_ping=True,
        hide_parameters=True,
        **database_engine_options(settings),
    )


async def collect_bounded(
    engine, group_id, user_id, code, login, password, today, *, storage_state=None, persist_session=None
):
    try:
        async with asyncio.timeout(ACCOUNT_TIMEOUT_SECONDS):
            return await _collect(
                engine, group_id, user_id, code, login, password, today,
                storage_state=storage_state, persist_session=persist_session,
            )
    except TimeoutError:
        return "source_unavailable"


async def prepare_week(
    engine,
    today: date,
    settings: Settings,
    *,
    pilot_group_id: UUID | None = None,
    pilot_user_id: UUID | None = None,
) -> int:
    if (pilot_group_id is None) != (pilot_user_id is None):
        raise ValueError("pilot_scope_requires_both_ids")
    if not legal_ready(settings) or not settings.ranepa_access_approved:
        return 0
    monday = today - timedelta(days=today.weekday())
    async with AsyncSession(engine, expire_on_commit=False) as db:
        query = (
            select(Membership, Group)
            .join(Group, Group.id == Membership.group_id)
            .join(UserProfile, UserProfile.user_id == Membership.user_id)
            .where(
                Membership.status == "active",
                Group.status == "active",
                Group.academic_group_code.is_not(None),
            )
        )
        if pilot_group_id is not None:
            query = query.where(
                Membership.group_id == pilot_group_id,
                Membership.user_id == pilot_user_id,
            )
        members = (await db.execute(query)).all()
        candidates = []
        for member, group in members:
            profile = await db.get(UserProfile, member.user_id)
            if not profile or not profile.sdu_credential_id:
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


async def process_one(
    engine,
    settings: Settings,
    today: date,
    *,
    pilot_group_id: UUID | None = None,
    pilot_user_id: UUID | None = None,
) -> str:
    async with AsyncSession(engine, expire_on_commit=False) as db:
        job = await claim_next(db, group_id=pilot_group_id, user_id=pilot_user_id)
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
            or not profile.sdu_credential_id
            or not legal_ready(settings)
            or not settings.ranepa_access_approved
            or not await has_consent(db, user_id, "sdo", settings)
        ):
            await finish(db, job_id, group_id, user_id, failure_code="login_failed")
            await db.commit()
            return "failed"
        code = group.academic_group_code
        credential_id = profile.sdu_credential_id
        await db.commit()
    store = configured_store(settings)
    if store is None:
        failure = "internal"
        credential = None
    else:
        try:
            credential = await store.get(credential_id, include_session_state=True)
        except CredentialStoreUnavailable:
            credential = None
        failure = "internal" if credential is None else None
    secret = settings.profile_encryption_key.get_secret_value()
    if failure is not None or not secret:
        failure = "internal"
    else:
        try:
            cipher = Fernet(secret.encode())
            stored_login, encrypted_password, key_version, encrypted_session = credential
            login = normalize_sdo_login(stored_login)
            if not login:
                raise ValueError("empty_sdo_login")
            password = cipher.decrypt(encrypted_password.encode()).decode()
        except (ValueError, InvalidToken, UnicodeDecodeError):
            failure = "login_failed"
        else:
            # The project currently has exactly one configured Fernet key, labelled v1.
            # Other versions must not be decoded or overwritten with the wrong key.
            session_cipher = cipher if key_version == "v1" else None
            storage_state = restored_storage_state(encrypted_session, session_cipher)

            async def persist_session(state: dict):
                # Browser state can include both LK and Moodle bearer cookies.
                # Only Fernet ciphertext ever reaches the YDB Document API.
                if session_cipher is None:
                    return
                payload = json.dumps(state, ensure_ascii=False, separators=(",", ":"))
                encrypted_state = session_cipher.encrypt(payload.encode("utf-8")).decode("utf-8")
                try:
                    await store.update_session_state(
                        credential_id,
                        encrypted_state,
                        login=stored_login,
                        ciphertext=encrypted_password,
                        key_version=key_version,
                    )
                except CredentialStoreUnavailable:
                    # An updated/revoked account must not be recreated by a late crawl.
                    return

            failure = await collect_bounded(
                engine, group_id, user_id, code, login, password, today,
                storage_state=storage_state,
                persist_session=persist_session if session_cipher is not None else None,
            )
    async with AsyncSession(engine) as db:
        await finish(db, job_id, group_id, user_id, failure_code=failure)
        await db.commit()
    return "succeeded" if failure is None else "failed"


async def _collect(
    engine, group_id, user_id, code, login, password, today, *, storage_state=None, persist_session=None
):
    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            try:
                proxy_url = current_proxy_url()
                proxy_config = playwright_proxy_settings(proxy_url) if proxy_url else None
                context_options = {
                    "proxy": proxy_config,
                    "locale": "ru-RU",
                    "timezone_id": "Europe/Moscow",
                }
                restored_state = storage_state
                try:
                    context = await browser.new_context(
                        storage_state=restored_state, **context_options
                    )
                except (PlaywrightError, ValueError, TypeError):
                    if restored_state is None:
                        raise
                    # Structurally valid JSON may still be rejected by Playwright.
                    restored_state = None
                    context = await browser.new_context(storage_state=None, **context_options)
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
                if persist_session is not None:
                    try:
                        fresh_state = await context.storage_state()
                        if fresh_state != restored_state:
                            await persist_session(fresh_state)
                    except Exception:  # noqa: BLE001
                        # Failed cache updates never fail a verified timetable crawl.
                        # Playwright/YDB errors may contain secrets; do not log them.
                        pass
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
    engine = create_worker_engine(settings)
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
    if not legal_ready(settings) or not settings.ranepa_access_approved:
        raise RuntimeError("Approved legal settings and university access are required")
    if not settings.profile_encryption_key.get_secret_value():
        raise RuntimeError("PROFILE_ENCRYPTION_KEY is required")
    if configured_store(settings) is None:
        raise RuntimeError("YDB credential store is required")
    if not settings.database_url.startswith("postgresql+asyncpg://"):
        raise RuntimeError("PostgreSQL DATABASE_URL is required")
    engine = create_worker_engine(settings)
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


async def run_single_pilot(settings: Settings, *, group_id: UUID, user_id: UUID) -> str:
    """Process exactly one consented account without touching other queued jobs."""
    if not settings.ranepa_crawl_enabled:
        raise RuntimeError("RANEPA_CRAWL_ENABLED is required for manual pilot")
    if not legal_ready(settings) or not settings.ranepa_access_approved:
        raise RuntimeError("Approved legal settings and university access are required")
    if not settings.profile_encryption_key.get_secret_value():
        raise RuntimeError("PROFILE_ENCRYPTION_KEY is required")
    if configured_store(settings) is None:
        raise RuntimeError("YDB credential store is required")
    if not settings.database_url.startswith("postgresql+asyncpg://"):
        raise RuntimeError("PostgreSQL DATABASE_URL is required")
    engine = create_worker_engine(settings)
    try:
        today = datetime.now(ZoneInfo("Europe/Moscow")).date()
        await prepare_week(engine, today, settings, pilot_group_id=group_id, pilot_user_id=user_id)
        return await process_one(
            engine, settings, today, pilot_group_id=group_id, pilot_user_id=user_id
        )
    finally:
        await engine.dispose()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-minutes", type=int, choices=range(1, 25))
    parser.add_argument("--pilot-group-id", type=UUID)
    parser.add_argument("--pilot-user-id", type=UUID)
    args = parser.parse_args()
    if (args.pilot_group_id is None) != (args.pilot_user_id is None):
        parser.error("both pilot IDs are required")
    if args.pilot_group_id is not None and args.batch_minutes is not None:
        parser.error("--batch-minutes cannot be used with a single-account pilot")
    settings = Settings()
    if args.pilot_group_id is not None:
        result = asyncio.run(
            run_single_pilot(settings, group_id=args.pilot_group_id, user_id=args.pilot_user_id)
        )
        print("RANEPA_PILOT_RESULT " + result, flush=True)
        if result != "succeeded":
            raise SystemExit(1)
    elif args.batch_minutes:
        asyncio.run(run_batch(settings, max_minutes=args.batch_minutes))
    else:
        asyncio.run(run_forever(settings))


if __name__ == "__main__":
    main()
