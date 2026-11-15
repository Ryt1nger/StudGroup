"""Paced, single-browser university crawl; disabled unless explicitly enabled."""

import argparse
import asyncio
import json
import os
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
from studgroup.models import (
    BotPanel,
    Group,
    Membership,
    MemberSubgroup,
    RanepaCrawlJob,
    User,
    UserProfile,
)
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
from studgroup.ranepa_crawl_guard import (
    load_guard,
    record_crawl_outcome,
    reserve_daily_paid_tasks,
)
from studgroup.ranepa_crawl_jobs import claim_next, enqueue_week, finish
from studgroup.ranepa_crawl_notifications import (
    CrawlRunStats,
    notify_account_crawl_result,
    notify_batch_report,
    notify_captcha_detected,
    notify_successful_crawl,
)
from studgroup.ranepa_crawl_plan import CrawlCandidate
from studgroup.ranepa_crawl_trace import CrawlTrace
from studgroup.ranepa_credential_store import CredentialStoreUnavailable, configured_store
from studgroup.ranepa_login import normalize_sdo_login
from studgroup.ranepa_manual_captcha import offer_manual_captcha
from studgroup.ranepa_personal import remember_personal
from studgroup.ranepa_proxy import current_proxy_url, playwright_proxy_settings
from studgroup.ranepa_rotate import _read_original_halves, solve_rotating_halves

ACCOUNT_TIMEOUT_SECONDS = 600


async def ensure_owner_for_crawl(engine, settings: Settings) -> None:
    """Fail closed unless account alerts/guard have one verified admin recipient.

    The bot can create its "admin" panel only after authenticating the configured
    owner. A private single-owner lookup avoids committing a Telegram user ID
    into the public GitHub workflow. No identifier is printed to logs.
    """
    needs_owner = (
        settings.ranepa_captcha_guard_enabled
        or settings.ranepa_crawl_notify_success_enabled
        or settings.ranepa_crawl_notify_captcha_seen_enabled
        or settings.ranepa_crawl_notify_account_results_enabled
    )
    if not needs_owner:
        return
    configured = settings.bot_admin_user_id
    if type(configured) is int and configured > 0:
        return
    if configured is not None:
        raise RuntimeError("ranepa_owner_id_invalid")

    async with AsyncSession(engine) as db:
        owners = (
            await db.scalars(
                select(BotPanel.chat_id)
                .join(User, User.telegram_user_id == BotPanel.chat_id)
                .where(BotPanel.mode == "admin", BotPanel.chat_id > 0)
                .distinct()
            )
        ).all()
    if len(owners) != 1 or type(owners[0]) is not int:
        raise RuntimeError("ranepa_unique_owner_recipient_required")
    settings.bot_admin_user_id = owners[0]


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
    captcha_daily_limit: int = 0,
    trace: CrawlTrace | None = None,
    stats: CrawlRunStats | None = None,
    on_captcha=None,
    manual_captcha=None,
):
    try:
        async with asyncio.timeout(900 if manual_captcha is not None else ACCOUNT_TIMEOUT_SECONDS):
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
                captcha_daily_limit=captcha_daily_limit,
                trace=trace,
                stats=stats,
                on_captcha=on_captcha,
                manual_captcha=manual_captcha,
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
    rescan_all: bool = False,
) -> int:
    if (pilot_group_id is None) != (pilot_user_id is None):
        raise ValueError("pilot_scope_requires_both_ids")
    if bypass_cooldown and (pilot_group_id is None or pilot_user_id is None):
        raise ValueError("cooldown_bypass_requires_targeted_pilot")
    if rescan_all and (bypass_cooldown or pilot_group_id is not None):
        raise ValueError("full_rescan_cannot_be_a_pilot")
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
        count = await enqueue_week(
            db, monday, candidates, force_refresh=bypass_cooldown or rescan_all
        )
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
    run_stats: CrawlRunStats | None = None,
    rescan_all: bool = False,
    allowed_user_ids: set[UUID] | None = None,
    processed_user_ids: set[UUID] | None = None,
) -> str:
    account_stats = CrawlRunStats()
    available_budget = captcha_budget or CaptchaBudget(settings.ranepa_captcha_max_tasks_per_run)
    async with AsyncSession(engine, expire_on_commit=False) as db:
        # The durable CAPTCHA breaker never changes fair queue priorities.
        # Owner-authorized targeted pilots may run while scheduled jobs are paused.
        if settings.ranepa_captcha_guard_enabled:
            guard = await load_guard(db)
            if guard.paused and (pilot_group_id is None or not guard.recovery_requested):
                await db.commit()
                log_captcha_stage("crawl_paused_repeated_captcha")
                return "paused"
        job = await claim_next(
            db,
            group_id=pilot_group_id,
            user_id=pilot_user_id,
            rescan_all=rescan_all,
            allowed_user_ids=allowed_user_ids,
            processed_user_ids=processed_user_ids,
        )
        if job is None:
            await db.commit()
            if pilot_group_id is not None:
                log_captcha_stage("pilot_queue_idle")
            return "idle"
        job_id, group_id, user_id, attempt = (job.id, job.group_id, job.user_id, job.attempts)
        if processed_user_ids is not None:
            # One owner-authorized account per rescan, even with two memberships.
            processed_user_ids.add(user_id)
        trace = CrawlTrace() if settings.ranepa_crawl_telemetry_enabled else None
        if trace is not None:
            trace.record("job_claimed")
        group = await db.get(Group, group_id)
        student = await db.get(User, user_id)
        profile = await db.get(UserProfile, user_id)

        async def notify_finalized_attempt(failure_code: str | None) -> None:
            if not settings.ranepa_crawl_notify_account_results_enabled:
                return
            try:
                queued = await notify_account_crawl_result(
                    engine,
                    owner_id=settings.bot_admin_user_id,
                    job_id=job_id,
                    attempt=attempt,
                    user_id=user_id,
                    group_code=group.academic_group_code if group is not None else None,
                    student_name=student.display_name if student is not None else None,
                    failure_code=failure_code,
                    failure_detail=trace.failure_detail if trace is not None else None,
                    stats=account_stats,
                )
            except Exception:  # noqa: BLE001
                # Alerts must never change the committed account outcome.
                log_captcha_stage("owner_account_notice_unavailable")
            else:
                if queued:
                    log_captcha_stage("owner_account_notice_queued")

        if (
            group is None
            or profile is None
            or not group.academic_group_code
            or not profile.sdu_credential_id
            or not legal_ready(settings)
            or not settings.ranepa_access_approved
            or not await has_consent(db, user_id, "sdo", settings)
        ):
            finished = await finish(db, job_id, group_id, user_id, failure_code="login_failed")
            if finished and settings.ranepa_captcha_guard_enabled:
                await record_crawl_outcome(
                    db,
                    owner_id=settings.bot_admin_user_id,
                    job_id=job_id,
                    group_id=group_id,
                    user_id=user_id,
                    attempt=attempt,
                    failure_code="login_failed",
                    manual_pilot=pilot_group_id is not None,
                )
            if finished and trace is not None:
                db.add(
                    trace.observation(job_id=job_id, attempt=attempt, failure_code="login_failed")
                )
            await db.commit()
            if finished and run_stats is not None:
                run_stats.failed_accounts += 1
                run_stats.failure_reasons["login_failed"] = (
                    run_stats.failure_reasons.get("login_failed", 0) + 1
                )
            if finished:
                await notify_finalized_attempt("login_failed")
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
            if trace is not None:
                trace.record("credentials_ready")
                trace.session_available = storage_state is not None
                trace.record("session_available" if trace.session_available else "session_fresh")

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

            async def on_captcha(location: str) -> None:
                if not settings.ranepa_crawl_notify_captcha_seen_enabled:
                    return
                try:
                    queued = await notify_captcha_detected(
                        engine,
                        owner_id=settings.bot_admin_user_id,
                        job_id=job_id,
                        attempt=attempt,
                        location=location,
                    )
                except Exception:  # noqa: BLE001
                    # A failed Telegram outbox write cannot obscure a genuine
                    # CAPTCHA or change the authorized solver/queue behavior.
                    log_captcha_stage("owner_captcha_seen_notice_unavailable")
                    return
                if queued:
                    log_captcha_stage("owner_captcha_seen_notice_queued")

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
                    and (pilot_group_id is not None or settings.ranepa_captcha_daily_max_tasks > 0)
                    else None
                ),
                captcha_timeout_seconds=settings.ranepa_captcha_timeout_seconds,
                captcha_budget=available_budget,
                captcha_diagnostic_only=(
                    settings.ranepa_captcha_diagnostic_only and pilot_group_id is not None
                ),
                # Owner-targeted retries also reserve the shared daily paid
                # budget. Previously pilot tasks were not accounted for.
                captcha_daily_limit=settings.ranepa_captcha_daily_max_tasks,
                trace=trace,
                stats=account_stats,
                on_captcha=on_captcha
                if settings.ranepa_crawl_notify_captcha_seen_enabled
                and not settings.ranepa_manual_captcha_enabled
                else None,
                manual_captcha=(
                    (
                        lambda page: offer_manual_captcha(
                            engine,
                            page=page,
                            owner_id=settings.bot_admin_user_id,
                            job_id=job_id,
                            attempt=attempt,
                            settings=settings,
                        )
                    )
                    if settings.ranepa_manual_captcha_enabled
                    else None
                ),
            )
    async with AsyncSession(engine) as db:
        finished = await finish(db, job_id, group_id, user_id, failure_code=failure)
        if finished and trace is not None:
            db.add(trace.observation(job_id=job_id, attempt=attempt, failure_code=failure))
        if finished and settings.ranepa_captcha_guard_enabled:
            paused, consecutive = await record_crawl_outcome(
                db,
                owner_id=settings.bot_admin_user_id,
                job_id=job_id,
                group_id=group_id,
                user_id=user_id,
                attempt=attempt,
                failure_code=failure,
                manual_pilot=pilot_group_id is not None,
            )
            if paused:
                log_captcha_stage("crawl_paused_repeated_captcha")
            elif consecutive == 0 and failure is None:
                log_captcha_stage("crawl_captcha_streak_cleared")
        await db.commit()
    if finished:
        if failure is None:
            account_stats.successful_accounts = 1
            if account_stats.saved_sdo_links or account_stats.saved_sdo_courses:
                account_stats.verified_sdo_accounts = 1
            elif account_stats.saved_weeks:
                account_stats.timetable_only_accounts = 1
        else:
            account_stats.failed_accounts = 1
            account_stats.failure_reasons[failure] = 1
        if run_stats is not None:
            run_stats.add(account_stats)
        await notify_finalized_attempt(failure)
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
    captcha_daily_limit: int = 0,
    trace: CrawlTrace | None = None,
    stats: CrawlRunStats | None = None,
    on_captcha=None,
    manual_captcha=None,
):
    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            if trace is not None:
                trace.record("browser_started")
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
                    if trace is not None:
                        trace.record("stored_session_rejected")
                    context = await browser.new_context(storage_state=None, **context_options)
                page = await context.new_page()
                captcha_attempted = False
                captcha_seen = False
                captcha_schedule_recovered = False

                async def solve_once(challenge_page, location="sdo"):
                    nonlocal captcha_attempted, captcha_seen
                    # Month/week detections were already announced before
                    # this call. The callback here is for live SDO exits only.
                    captcha_seen = True
                    if location == "sdo":
                        if trace is not None and not trace.captcha_seen:
                            trace.mark_captcha("sdo")
                        if on_captcha is not None:
                            await on_captcha("sdo")
                    if captcha_attempted:
                        log_captcha_stage("attempt_already_used")
                        return False
                    if urlsplit(getattr(challenge_page, "url", "")).hostname != "my.ranepa.ru":
                        log_captcha_stage("unsupported_origin")
                        return False
                    if captcha_client is None and not captcha_diagnostic_only:
                        log_captcha_stage("provider_not_configured")
                        return False
                    # Two v2 RotateTask jobs must be covered by the paid
                    # budget before the first request. No partial reservation
                    # and no billing in diagnostic-only mode.
                    required_tasks = getattr(captcha_client, "max_tasks", 1)
                    if (
                        not captcha_diagnostic_only
                        and captcha_budget is not None
                        and not captcha_budget.claim_many(required_tasks)
                    ):
                        log_captcha_stage("paid_budget_exhausted")
                        return False
                    if (
                        not captcha_diagnostic_only
                        and captcha_daily_limit > 0
                        and not await reserve_daily_paid_tasks(
                            engine, count=required_tasks, daily_limit=captcha_daily_limit
                        )
                    ):
                        log_captcha_stage("paid_daily_budget_exhausted")
                        return False
                    captcha_attempted = True
                    if trace is not None:
                        trace.record("captcha_solver_started")
                    log_captcha_stage(
                        "diagnostic_started" if captcha_diagnostic_only else "paid_attempt_started"
                    )
                    # Free mode keeps the read-only native-thumb diagnostic.
                    # Paid v2 pilot solves one half per task with a shared
                    # visual instruction. Never interpret click coordinates
                    # as drag distances or move sliders before both answers.
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
                        if trace is not None:
                            trace.record("captcha_solver_submitted")
                        log_captcha_stage("motion_submitted_verification_pending")
                    elif not captcha_diagnostic_only:
                        if trace is not None:
                            trace.record("captcha_solver_failed")
                        log_captcha_stage("motion_not_submitted")
                    return submitted

                if trace is not None:
                    trace.record("month_requested")
                month = await read_month(page, login, password, code, today.year, today.month)
                if month.status == "captcha_required":
                    captcha_seen = True
                    if trace is not None:
                        trace.mark_captcha("month")
                    log_captcha_stage("challenge_detected_month")
                    if on_captcha is not None:
                        await on_captcha("month")
                    if await solve_once(page, "month"):
                        # Wait for a possible asynchronous redirect without
                        # retrying the CAPTCHA or declaring the gate solved.
                        log_captcha_stage(await wait_for_captcha_resolution(page))
                        log_captcha_stage("schedule_recheck_after_captcha_month")
                        if trace is not None:
                            trace.record("month_recheck")
                        month = await read_month(
                            page, login, password, code, today.year, today.month
                        )
                        if month.status == "captcha_required":
                            log_captcha_stage("challenge_still_present_month")
                        elif month.status == "preview_only":
                            captcha_schedule_recovered = True
                            log_captcha_stage("month_verified_after_challenge")
                        else:
                            log_captcha_stage("month_not_verified_after_challenge")
                if month.status == "captcha_required" and manual_captcha is not None:
                    log_captcha_stage("owner_fallback_offered_month")
                    if await manual_captcha(page):
                        log_captcha_stage(await wait_for_captcha_resolution(page))
                        month = await read_month(
                            page, login, password, code, today.year, today.month
                        )
                        if month.status == "preview_only":
                            captcha_schedule_recovered = True
                            log_captcha_stage("month_verified_after_owner_solution")
                if month.status != "preview_only":
                    if month.status == "captcha_required":
                        return "captcha"
                    if month.status == "identity_unverified":
                        return "identity_unverified"
                    return "source_unavailable"
                if trace is not None:
                    trace.record("month_verified")
                first = date(today.year, today.month, 1)
                monday = first - timedelta(days=first.weekday())
                for offset in range(6):
                    week_start = monday + timedelta(weeks=offset)
                    expected = [
                        card
                        for card in month.cards
                        if week_start <= card.day <= week_start + timedelta(days=6)
                    ]
                    if trace is not None:
                        trace.record("week_requested")
                    async def read_and_resolve_week(
                        target_monday=week_start, allow_empty=not expected
                    ):
                        nonlocal captcha_seen, captcha_schedule_recovered
                        result = await read_week(
                            page, login, password, code, target_monday, allow_empty=allow_empty
                        )
                        if result.status == "captcha_required":
                            captcha_seen = True
                            if trace is not None:
                                trace.mark_captcha("week")
                            log_captcha_stage("challenge_detected_week")
                            if on_captcha is not None:
                                await on_captcha("week")
                            if await solve_once(page, "week"):
                                log_captcha_stage(await wait_for_captcha_resolution(page))
                                log_captcha_stage("schedule_recheck_after_captcha_week")
                                if trace is not None:
                                    trace.record("week_recheck")
                                result = await read_week(
                                    page, login, password, code, target_monday, allow_empty=allow_empty
                                )
                                if result.status == "captcha_required":
                                    log_captcha_stage("challenge_still_present_week")
                                elif result.snapshot is not None:
                                    captcha_schedule_recovered = True
                                    log_captcha_stage("week_received_after_challenge")
                                else:
                                    log_captcha_stage("week_not_verified_after_challenge")
                        if result.status == "captcha_required" and manual_captcha is not None:
                            log_captcha_stage("owner_fallback_offered_week")
                            if await manual_captcha(page):
                                log_captcha_stage(await wait_for_captcha_resolution(page))
                                result = await read_week(
                                    page, login, password, code, target_monday, allow_empty=allow_empty
                                )
                                if result.snapshot is not None:
                                    captcha_schedule_recovered = True
                                    log_captcha_stage("week_verified_after_owner_solution")
                        return result

                    # Revisit a single week once if the view is transiently
                    # incomplete or the month/week cards disagree. A second
                    # unverified read fails closed; no source data is invented.
                    for week_read_attempt in range(2):
                        result = await read_and_resolve_week()
                        if result.snapshot is None:
                            if result.status == "captcha_required":
                                return "captcha"
                            if result.status == "identity_unverified":
                                return "identity_unverified"
                            if trace is not None:
                                trace.failure_detail = "week_unreadable"
                                trace.record("week_unreadable")
                        else:
                            actual = [
                                row
                                for row in result.snapshot.indexed().values()
                                if row.day.month == today.month
                            ]
                            if Counter((r.day, r.starts, r.subject) for r in actual) == Counter(
                                (r.day, r.starts, r.subject) for r in expected
                            ):
                                break
                            if trace is not None:
                                trace.failure_detail = "week_month_mismatch"
                                trace.record("week_month_mismatch")
                        if week_read_attempt == 1:
                            return "parse_failed"
                        if trace is not None:
                            trace.record("week_retry")
                        await page.wait_for_timeout(750)
                    if trace is not None:
                        trace.record("week_verified")
                        trace.record("sdo_exits_requested")
                    exit_kwargs = {}
                    if (
                        captcha_client is not None and not captcha_attempted
                    ) or manual_captcha is not None:

                        async def solve_sdo_with_owner_fallback(challenge_page):
                            submitted = await solve_once(challenge_page)
                            if submitted:
                                state = await wait_for_captcha_resolution(challenge_page)
                                if state != "challenge_still_visible_after_wait":
                                    return True
                            if manual_captcha is not None:
                                log_captcha_stage("owner_fallback_offered_sdo")
                                return await manual_captcha(challenge_page)
                            return False

                        exit_kwargs["solve_captcha"] = solve_sdo_with_owner_fallback
                    if on_captcha is not None:
                        exit_kwargs["on_captcha"] = on_captcha
                    links, link_states, courses = await resolve_sdo_exits(
                        context, result, login, password, **exit_kwargs
                    )
                    sdo_captcha_unresolved = "captcha_required" in link_states.values()
                    if trace is not None:
                        if sdo_captcha_unresolved and not trace.captcha_seen:
                            trace.mark_captcha("sdo")
                        trace.record("sdo_exits_finished")
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
                    if stats is not None:
                        stats.saved_weeks += 1
                        stats.saved_lessons += len(actual)
                        stats.saved_sdo_links += len(links)
                        stats.saved_sdo_courses += len(courses)
                    if sdo_captcha_unresolved:
                        log_captcha_stage("challenge_still_present_sdo")
                        return "captcha"
                if persist_session is not None:
                    try:
                        fresh_state = await context.storage_state()
                        if fresh_state != restored_state:
                            await persist_session(fresh_state)
                    except Exception:  # noqa: BLE001
                        # A cache failure must not invalidate verified timetable data.
                        # Playwright/YDB error messages may contain secrets; do not log them.
                        return None
                # A green crawl result alone does not prove that a CAPTCHA was
                # presented or solved. Emit one explicit, privacy-safe outcome
                # for every successful collection, even when no challenge occurs.
                if trace is not None:
                    trace.record("schedule_verified")
                if captcha_schedule_recovered:
                    log_captcha_stage("captcha_seen_schedule_recovered")
                elif captcha_seen:
                    log_captcha_stage("captcha_seen_schedule_verified_not_solution_proof")
                else:
                    log_captcha_stage("captcha_not_seen_schedule_verified")
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
        await ensure_owner_for_crawl(engine, settings)
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
                    timeout=300
                    if status in {"idle", "paused"}
                    else settings.ranepa_crawl_pause_seconds,
                )
            except TimeoutError:
                pass
    finally:
        await engine.dispose()
        for signum in (signal.SIGTERM, signal.SIGINT):
            loop.remove_signal_handler(signum)


async def run_batch(
    settings: Settings,
    *,
    max_minutes: int = 24,
    rescan_all: bool = False,
) -> int:
    """Process a bounded shift or one owner-approved full rescan.

    The full rescan targets a fixed snapshot of consented accounts, once each.
    Normal shifts keep the durable 24-hour per-account cooldown.
    """
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
    run_stats = CrawlRunStats()
    processed_ids: set[UUID] = set()
    eligible_ids: set[UUID] | None = None
    stop_reason = "time_limit"
    try:
        await ensure_owner_for_crawl(engine, settings)
        today = datetime.now(ZoneInfo("Europe/Moscow")).date()
        await prepare_week(engine, today, settings, rescan_all=rescan_all)
        if rescan_all:
            monday = today - timedelta(days=today.weekday())
            async with AsyncSession(engine) as db:
                eligible_ids = set(
                    (
                        await db.scalars(
                            select(RanepaCrawlJob.user_id).where(
                                RanepaCrawlJob.week_start == monday,
                                RanepaCrawlJob.state == "queued",
                            )
                        )
                    ).all()
                )
            print(f"RANEPA_OWNER_RESCAN_PLANNED {len(eligible_ids)}", flush=True)
        deadline = monotonic() + max_minutes * 60
        while monotonic() < deadline:
            today = datetime.now(ZoneInfo("Europe/Moscow")).date()
            status = await process_one(
                engine,
                settings,
                today,
                captcha_budget=captcha_budget,
                run_stats=run_stats,
                rescan_all=rescan_all,
                allowed_user_ids=eligible_ids,
                processed_user_ids=processed_ids if rescan_all else None,
            )
            if status in {"idle", "paused"}:
                stop_reason = status
                break
            completed += 1
            if monotonic() < deadline:
                await asyncio.sleep(settings.ranepa_crawl_pause_seconds)
        remaining = max(0, len(eligible_ids) - completed) if eligible_ids is not None else 0
        if settings.ranepa_crawl_notify_success_enabled:
            try:
                queued = await notify_batch_report(
                    engine,
                    owner_id=settings.bot_admin_user_id,
                    stats=run_stats,
                    full_rescan=rescan_all,
                    planned=len(eligible_ids) if eligible_ids is not None else None,
                    remaining=remaining,
                    stop_reason=stop_reason,
                )
            except Exception:  # noqa: BLE001
                log_captcha_stage("owner_batch_report_unavailable")
            else:
                if queued:
                    log_captcha_stage("owner_batch_report_queued")
        print(
            "RANEPA_OWNER_RESCAN_RESULT "
            + json.dumps(
                {
                    "rescan_all": rescan_all,
                    "processed": completed,
                    "succeeded": run_stats.successful_accounts,
                    "failed": run_stats.failed_accounts,
                    "verified_sdo": run_stats.verified_sdo_accounts,
                    "remaining": remaining,
                    "stop_reason": stop_reason,
                }
            ),
            flush=True,
        )
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
    run_stats = CrawlRunStats()
    try:
        await ensure_owner_for_crawl(engine, settings)
        today = datetime.now(ZoneInfo("Europe/Moscow")).date()
        await prepare_week(
            engine,
            today,
            settings,
            pilot_group_id=group_id,
            pilot_user_id=user_id,
            bypass_cooldown=True,
        )
        result = await process_one(
            engine,
            settings,
            today,
            pilot_group_id=group_id,
            pilot_user_id=user_id,
            captcha_budget=captcha_budget,
            run_stats=run_stats,
        )
        if settings.ranepa_crawl_notify_success_enabled and run_stats.successful_accounts:
            try:
                queued = await notify_successful_crawl(
                    engine,
                    owner_id=settings.bot_admin_user_id,
                    stats=run_stats,
                    scope="pilot",
                )
            except Exception:  # noqa: BLE001
                log_captcha_stage("owner_success_notice_unavailable")
            else:
                if queued:
                    log_captcha_stage("owner_success_notice_queued")
        return result
    finally:
        await engine.dispose()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-minutes", type=int, choices=range(1, 181))
    parser.add_argument("--force-rescan-all", action="store_true")
    parser.add_argument("--pilot-group-id", type=UUID)
    parser.add_argument("--pilot-user-id", type=UUID)
    args = parser.parse_args()
    if (args.pilot_group_id is None) != (args.pilot_user_id is None):
        parser.error("both pilot IDs are required")
    if args.pilot_group_id is not None and args.batch_minutes is not None:
        parser.error("--batch-minutes cannot be used with a single-account pilot")
    if args.force_rescan_all and (
        args.batch_minutes is None
        or args.pilot_group_id is not None
        or os.environ.get("RANEPA_FORCE_RESCAN_ALL_APPROVED") != "true"
    ):
        parser.error("full rescan requires explicit owner-approved release workflow")
    if not args.force_rescan_all and args.batch_minutes and args.batch_minutes > 24:
        parser.error("scheduled batches cannot exceed 24 minutes")
    # An absent Actions variable is represented as an empty environment string,
    # which is not a valid optional integer for Pydantic Settings.
    if os.environ.get("BOT_ADMIN_USER_ID") == "":
        os.environ.pop("BOT_ADMIN_USER_ID")
    settings = Settings()
    if args.pilot_group_id is not None:
        result = asyncio.run(
            run_single_pilot(settings, group_id=args.pilot_group_id, user_id=args.pilot_user_id)
        )
        print("RANEPA_PILOT_RESULT " + result, flush=True)
        if result != "succeeded":
            raise SystemExit(1)
    elif args.batch_minutes:
        asyncio.run(
            run_batch(
                settings,
                max_minutes=args.batch_minutes,
                rescan_all=args.force_rescan_all,
            )
        )
    else:
        asyncio.run(run_forever(settings))


if __name__ == "__main__":
    main()
