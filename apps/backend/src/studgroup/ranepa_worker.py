"""Paced, single-browser university crawl; disabled unless explicitly enabled."""

import argparse
import asyncio
import json
import signal
from collections import Counter
from datetime import date, datetime, timedelta
from time import monotonic
from urllib.parse import urlsplit
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
from studgroup.ranepa_browser import (
    read_month,
    read_week,
    resolve_sdo_exits,
    wait_for_captcha_resolution,
)
from studgroup.ranepa_captcha import (
    CaptchaBudget,
    configured_solver,
    log_captcha_stage,
    solve_slider_captcha,
    warn_if_unconfigured,
)
from studgroup.ranepa_crawl_jobs import claim_next, enqueue_week, finish
from studgroup.ranepa_crawl_plan import CrawlCandidate
from studgroup.ranepa_credential_store import CredentialStoreUnavailable, configured_store
from studgroup.ranepa_login import normalize_sdo_login
from studgroup.ranepa_personal import remember_personal
from studgroup.ranepa_proxy import current_proxy_url, playwright_proxy_settings
from studgroup.ranepa_rotate import _read_original_halves, solve_rotating_halves

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
    engine,
    group_id,
    user_id,
    code,
    login,
    password,
    today,
    *,
    storage_state=None,
    persist_session=None,
    captcha_client=None,
    captcha_timeout_seconds=110,
    captcha_budget: CaptchaBudget | None = None,
    captcha_diagnostic_only: bool = False,
):
    try:
        async with asyncio.timeout(ACCOUNT_TIMEOUT_SECONDS):
            return await _collect(
                engine,
                group_id,
                user_id,
                code,
                login,
                password,
                today,
                storage_state=storage_state,
                persist_session=persist_session,
                captcha_client=captcha_client,
                captcha_timeout_seconds=captcha_timeout_seconds,
                captcha_budget=captcha_budget,
                captcha_diagnostic_only=captcha_diagnostic_only,
            )
    except TimeoutError:
        log_captcha_stage("crawl_timeout")
        return "source_unavailable"


async def prepare_week(
    engine,
    today: date,
    settings: Settings,
    *,
    pilot_group_id: UUID | None = None,
    pilot_user_id: UUID | None = None,
    bypass_cooldown: bool = False,
) -> int:
    if (pilot_group_id is None) != (pilot_user_id is None):
        raise ValueError("pilot_scope_requires_both_ids")
    if bypass_cooldown and (pilot_group_id is None or pilot_user_id is None):
        raise ValueError("cooldown_bypass_requires_targeted_pilot")
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
        count = await enqueue_week(db, monday, candidates, force_refresh=bypass_cooldown)
        await db.commit()
        return count


async def process_one(
    engine,
    settings: Settings,
    today: date,
    *,
    pilot_group_id: UUID | None = None,
    pilot_user_id: UUID | None = None,
    captcha_budget: CaptchaBudget | None = None,
) -> str:
    available_budget = captcha_budget or CaptchaBudget(settings.ranepa_captcha_max_tasks_per_run)
    async with AsyncSession(engine, expire_on_commit=False) as db:
        job = await claim_next(db, group_id=pilot_group_id, user_id=pilot_user_id)
        if job is None:
            await db.commit()
            if pilot_group_id is not None:
                log_captcha_stage("pilot_queue_idle")
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
            session_cipher = (
                cipher if key_version == "v1" and settings.ranepa_session_reuse_enabled else None
            )
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
                engine,
                group_id,
                user_id,
                code,
                login,
                password,
                today,
                storage_state=storage_state,
                persist_session=persist_session if session_cipher is not None else None,
                captcha_client=(
                    configured_solver(settings)
                    if available_budget.remaining > 0
                    and not settings.ranepa_captcha_diagnostic_only
                    else None
                ),
                captcha_timeout_seconds=settings.ranepa_captcha_timeout_seconds,
                captcha_budget=available_budget,
                captcha_diagnostic_only=(
                    settings.ranepa_captcha_diagnostic_only and pilot_group_id is not None
                ),
            )
    async with AsyncSession(engine) as db:
        await finish(db, job_id, group_id, user_id, failure_code=failure)
        await db.commit()
    if pilot_group_id is not None:
        # The value is chosen exclusively from known crawl failure categories.
        known_failures = {
            "login_failed",
            "captcha",
            "source_unavailable",
            "identity_unverified",
            "parse_failed",
            "internal",
        }
        category = (
            "succeeded"
            if failure is None
            else (failure if failure in known_failures else "unknown")
        )
        log_captcha_stage(f"pilot_finished_{category}")
    return "succeeded" if failure is None else "failed"


async def _collect(
    engine,
    group_id,
    user_id,
    code,
    login,
    password,
    today,
    *,
    storage_state=None,
    persist_session=None,
    captcha_client=None,
    captcha_timeout_seconds=110,
    captcha_budget: CaptchaBudget | None = None,
    captcha_diagnostic_only: bool = False,
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
                captcha_attempted = False

                async def solve_once(challenge_page):
                    nonlocal captcha_attempted
                    if captcha_attempted:
                        log_captcha_stage("attempt_already_used")
                        return False
                    if urlsplit(getattr(challenge_page, "url", "")).hostname != "my.ranepa.ru":
                        log_captcha_stage("unsupported_origin")
                        return False
                    if captcha_client is None and not captcha_diagnostic_only:
                        log_captcha_stage("provider_not_configured")
                        return False
                    if (
                        not captcha_diagnostic_only
                        and captcha_budget is not None
                        and not captcha_budget.claim()
                    ):
                        log_captcha_stage("paid_budget_exhausted")
                        return False
                    captcha_attempted = True
                    log_captcha_stage(
                        "diagnostic_started" if captcha_diagnostic_only else "paid_attempt_started"
                    )
                    # Free mode keeps the read-only native-thumb diagnostic.
                    # Paid mode uses one multi-image RotateCaptcha task, NEVER
                    # the old point-click Coordinates interpretation.
                    if captcha_diagnostic_only:
                        submitted = await solve_slider_captcha(
                            challenge_page, None, timeout_seconds=captcha_timeout_seconds
                        )
                        # Also verify the two original assets for the upcoming
                        # rotate pilot without sending them or touching a thumb.
                        rotate_assets = await _read_original_halves(challenge_page)
                        log_captcha_stage(
                            "diagnostic_rotate_halves_available"
                            if rotate_assets is not None
                            else "diagnostic_rotate_halves_unavailable"
                        )
                    else:
                        submitted = await solve_rotating_halves(
                            challenge_page,
                            captcha_client,
                            timeout_seconds=captcha_timeout_seconds,
                        )
                    if submitted:
                        log_captcha_stage("motion_submitted_verification_pending")
                    elif not captcha_diagnostic_only:
                        log_captcha_stage("motion_not_submitted")
                    return submitted

                month = await read_month(page, login, password, code, today.year, today.month)
                if month.status == "captcha_required":
                    log_captcha_stage("challenge_detected_month")
                    if await solve_once(page):
                        # Wait for a possible asynchronous redirect without
                        # retrying the CAPTCHA or declaring the gate solved.
                        log_captcha_stage(await wait_for_captcha_resolution(page))
                        month = await read_month(
                            page, login, password, code, today.year, today.month
                        )
                        if month.status == "captcha_required":
                            log_captcha_stage("challenge_still_present_month")
                        elif month.status == "preview_only":
                            log_captcha_stage("month_verified_after_challenge")
                        else:
                            log_captcha_stage("month_not_verified_after_challenge")
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
                    if result.status == "captcha_required":
                        log_captcha_stage("challenge_detected_week")
                        if await solve_once(page):
                            log_captcha_stage(await wait_for_captcha_resolution(page))
                            result = await read_week(
                                page, login, password, code, week_start, allow_empty=not expected
                            )
                            if result.status == "captcha_required":
                                log_captcha_stage("challenge_still_present_week")
                            elif result.snapshot is not None:
                                log_captcha_stage("week_received_after_challenge")
                            else:
                                log_captcha_stage("week_not_verified_after_challenge")
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
                    if captcha_client is not None and not captcha_attempted:
                        links, link_states, courses = await resolve_sdo_exits(
                            context, result, login, password, solve_captcha=solve_once
                        )
                    else:
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
                        # A cache failure must not invalidate verified timetable data.
                        # Playwright/YDB error messages may contain secrets; do not log them.
                        return None
                return None
            finally:
                await browser.close()
    except Exception:  # noqa: BLE001
        # Never include provider exception text, HTML, URLs or credentials in SQL/logs.
        log_captcha_stage("crawl_internal_error")
        return "internal"


async def run_forever(settings: Settings):
    if not settings.ranepa_crawl_enabled:
        return
    warn_if_unconfigured(settings)
    captcha_budget = CaptchaBudget(settings.ranepa_captcha_max_tasks_per_run)
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
            status = await process_one(engine, settings, today, captcha_budget=captcha_budget)
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
    warn_if_unconfigured(settings)
    engine = create_worker_engine(settings)
    captcha_budget = CaptchaBudget(settings.ranepa_captcha_max_tasks_per_run)
    completed = 0
    try:
        await prepare_week(engine, datetime.now(ZoneInfo("Europe/Moscow")).date(), settings)
        deadline = monotonic() + max_minutes * 60
        while monotonic() < deadline:
            today = datetime.now(ZoneInfo("Europe/Moscow")).date()
            status = await process_one(engine, settings, today, captcha_budget=captcha_budget)
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
    warn_if_unconfigured(settings)
    engine = create_worker_engine(settings)
    captcha_budget = CaptchaBudget(settings.ranepa_captcha_max_tasks_per_run)
    try:
        today = datetime.now(ZoneInfo("Europe/Moscow")).date()
        await prepare_week(
            engine,
            today,
            settings,
            pilot_group_id=group_id,
            pilot_user_id=user_id,
            bypass_cooldown=True,
        )
        return await process_one(
            engine,
            settings,
            today,
            pilot_group_id=group_id,
            pilot_user_id=user_id,
            captcha_budget=captcha_budget,
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
