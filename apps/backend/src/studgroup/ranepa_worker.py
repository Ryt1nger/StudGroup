"""Paced, single-browser university crawl; disabled unless explicitly enabled."""

import argparse
import asyncio
import json
import os
import signal
from collections import Counter, defaultdict, deque
from datetime import UTC, date, datetime, timedelta
from time import monotonic
from urllib.parse import urlsplit
from uuid import UUID, uuid4
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
    RanepaCrawlJob,
    User,
    UserProfile,
)
from studgroup.process_runtime import ProcessGate, read_intent
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
from studgroup.ranepa_crawl_jobs import FAST_RETRY_DIAGNOSTICS, claim_next, enqueue_week, finish
from studgroup.ranepa_crawl_notifications import (
    CrawlRunStats,
    failed_account_line,
    notify_account_crawl_result,
    notify_batch_report,
    notify_captcha_detected,
    notify_successful_crawl,
    recover_account_crawl_notifications,
    successful_account_line,
)
from studgroup.ranepa_crawl_trace import CrawlTrace
from studgroup.ranepa_credential_store import CredentialStoreUnavailable, configured_store
from studgroup.ranepa_failure_diagnostics import diagnose_browser_state, diagnose_exception
from studgroup.ranepa_login import normalize_sdo_login
from studgroup.ranepa_manual_captcha import offer_manual_captcha
from studgroup.ranepa_materialization import materialize_verified_facts
from studgroup.ranepa_personal import enrich_personal_links, remember_personal
from studgroup.ranepa_proxy import current_proxy_url, playwright_proxy_settings
from studgroup.ranepa_rotate import _read_original_halves, solve_rotating_halves
from studgroup.ranepa_worker_health import heartbeat
from studgroup.urgent_sdo import active_run, eligible_candidates, finalize_urgent, prepare_urgent

ACCOUNT_TIMEOUT_SECONDS = 600
MAX_AUTOMATIC_CAPTCHA_ATTEMPTS = 3
# Optional Moodle discovery must not use up the entire account deadline.
# The six verified timetable weeks are persisted before link enrichment.
MAX_SDO_SCAN_TOTAL_SECONDS = 330
MAX_SDO_SCAN_WEEK_SECONDS = 70


def owner_student_name(student, profile) -> str:
    """Use entered name when Telegram only provides a placeholder."""
    display_name = (student.display_name or "").strip() if student else ""
    profile_name = " ".join(
        item.strip()
        for item in (getattr(profile, "last_name", None), getattr(profile, "first_name", None))
        if isinstance(item, str) and item.strip()
    )
    if display_name in {"", "-", "—", "–", "?"}:
        display_name = profile_name
    if not display_name and student and student.username:
        display_name = "@" + student.username
    return display_name or "Имя не указано"


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
        if trace is not None:
            trace.mark_diagnostic("timeout")
        # All six week snapshots were committed; a late optional Moodle
        # enrichment/session timeout must not invalidate the timetable.
        if stats is not None and stats.saved_weeks >= 6:
            if trace is not None:
                trace.record("schedule_verified")
            log_captcha_stage("crawl_optional_enrichment_timeout_after_six_weeks")
            return None
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
    bootstrap: bool = False,
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
        candidates = await eligible_candidates(
            db, settings, pilot_group_id=pilot_group_id, pilot_user_id=pilot_user_id
        )
        count = await enqueue_week(
            db, monday, candidates, force_refresh=bypass_cooldown or rescan_all, bootstrap=bootstrap
        )
        await db.commit()
        return count


async def process_one(engine, settings, today, *, require_scheduler=False, **kwargs):
    """Control state plus SQL transaction claim fence and durable browser lease."""
    if engine.dialect.name == "postgresql":
        intent = await read_intent(engine, "sdo", settings.ranepa_crawl_enabled)
        if not intent.enabled:
            return "paused"
        if require_scheduler and not (await read_intent(engine, "scheduler", True)).enabled:
            return "paused"
        if (kwargs.get("rescan_all") or kwargs.get("pilot_group_id")) and await active_run(engine):
            return "paused"
    return await _process_one(engine, settings, today, **kwargs)


async def _process_one(
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
        trace = (
            CrawlTrace()
            if settings.ranepa_crawl_telemetry_enabled
            or settings.ranepa_crawl_notify_account_results_enabled
            else None
        )
        if trace is not None:
            trace.record("job_claimed")
        group = await db.get(Group, group_id)
        student = await db.get(User, user_id)
        profile = await db.get(UserProfile, user_id)
        student_name = owner_student_name(student, profile)

        async def notify_finalized_attempt(
            failure_code: str | None, *, detail: str | None = None
        ) -> None:
            if not settings.ranepa_crawl_notify_account_results_enabled:
                return
            # Send each completed account outcome immediately, including during
            # a scheduled batch or a long-running sample. The final aggregate
            # report is additional and must not delay owner notifications.
            try:
                queued = await notify_account_crawl_result(
                    engine,
                    owner_id=settings.bot_admin_user_id,
                    job_id=job_id,
                    attempt=attempt,
                    user_id=user_id,
                    group_code=group.academic_group_code if group is not None else None,
                    telegram_group_name=group.name if group is not None else None,
                    student_name=student_name,
                    failure_code=failure_code,
                    failure_detail=detail or (trace.failure_detail if trace is not None else None),
                    diagnostic_code=trace.diagnostic if trace is not None else None,
                    last_step=trace.last_notification_step() if trace is not None else None,
                    stats=account_stats,
                )
            except Exception:  # noqa: BLE001
                # Alerts must never change the committed account outcome.
                log_captcha_stage("owner_account_notice_unavailable")
            else:
                if queued:
                    log_captcha_stage("owner_account_notice_queued")

        # A missing profile/consent is NOT a refused password. Report the
        # specific safe preflight reason rather than guessing a login failure.
        preflight_detail = None
        active_member = await db.scalar(
            select(Membership.id).where(
                Membership.user_id == user_id,
                Membership.group_id == group_id,
                Membership.status == "active",
            )
        )
        if group is None or group.status != "active" or not group.academic_group_code:
            preflight_detail = "group_missing"
        elif active_member is None:
            preflight_detail = "membership_inactive"
        elif profile is None:
            preflight_detail = "profile_missing"
        elif not profile.sdu_credential_id:
            preflight_detail = "credentials_missing"
        elif not legal_ready(settings):
            preflight_detail = "legal_not_ready"
        elif not settings.ranepa_access_approved:
            preflight_detail = "access_not_approved"
        elif not await has_consent(db, user_id, "sdo", settings):
            preflight_detail = "consent_missing"
        if preflight_detail is not None:
            if trace is not None:
                trace.failure_detail = preflight_detail
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
                    trace.observation(
                        job_id=job_id,
                        attempt=attempt,
                        failure_code="login_failed",
                        stats=account_stats,
                        notification_pending=settings.ranepa_crawl_notify_account_results_enabled,
                    )
                )
            await db.commit()
            if finished and run_stats is not None:
                run_stats.failed_accounts += 1
                run_stats.failure_reasons["login_failed"] = (
                    run_stats.failure_reasons.get("login_failed", 0) + 1
                )
                run_stats.failed_accounts_detail.append(
                    failed_account_line(
                        student_name=student_name,
                        telegram_group_name=group.name if group else None,
                        group_code=group.academic_group_code if group else None,
                        user_id=user_id,
                        failure_code="login_failed",
                        failure_detail=preflight_detail,
                    )
                )
            if finished:
                await notify_finalized_attempt("login_failed", detail=preflight_detail)
            return "failed"
        code = group.academic_group_code
        credential_id = profile.sdu_credential_id
        await db.commit()
    notice_failure_detail = None
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
            notice_failure_detail = "credentials_unreadable"
            if trace is not None:
                trace.failure_detail = "credentials_unreadable"
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
                        user_id=user_id,
                        student_name=student_name,
                        telegram_group_name=group.name if group is not None else None,
                        group_code=code,
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
                        lambda page, attempts_used: offer_manual_captcha(
                            engine,
                            page=page,
                            owner_id=settings.bot_admin_user_id,
                            job_id=job_id,
                            attempt=attempt,
                            settings=settings,
                            auto_attempts=attempts_used,
                            student_name=student.display_name if student else None,
                            group_code=code,
                            telegram_group_name=group.name,
                        )
                    )
                    if settings.ranepa_manual_captcha_enabled
                    else None
                ),
            )
            if failure == "login_failed":
                notice_failure_detail = "login_rejected"
    async with AsyncSession(engine) as db:
        fast_retry = (
            trace is not None
            and not trace.captcha_seen
            and (
                failure == "parse_failed"
                or (failure == "source_unavailable" and trace.diagnostic in FAST_RETRY_DIAGNOSTICS)
            )
        )
        finished = await finish(
            db, job_id, group_id, user_id, failure_code=failure, fast_retry=fast_retry
        )
        if finished and trace is not None:
            db.add(
                trace.observation(
                    job_id=job_id,
                    attempt=attempt,
                    failure_code=failure,
                    stats=account_stats,
                    notification_pending=settings.ranepa_crawl_notify_account_results_enabled,
                )
            )
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
            if (
                failure == "source_unavailable"
                and trace is not None
                and trace.diagnostic is not None
            ):
                account_stats.diagnostic_reasons[trace.diagnostic] = 1
        if failure is None:
            account_stats.successful_accounts_detail.append(
                successful_account_line(
                    student_name=student_name,
                    telegram_group_name=group.name if group else None,
                    group_code=group.academic_group_code if group else None,
                    user_id=user_id,
                    stats=account_stats,
                )
            )
        else:
            account_stats.failed_accounts_detail.append(
                failed_account_line(
                    student_name=student_name,
                    telegram_group_name=group.name if group else None,
                    group_code=group.academic_group_code if group else None,
                    user_id=user_id,
                    failure_code=failure,
                    failure_detail=notice_failure_detail
                    or (trace.failure_detail if trace is not None else None),
                    diagnostic_code=trace.diagnostic if trace is not None else None,
                )
            )
        if run_stats is not None:
            run_stats.add(account_stats)
        await notify_finalized_attempt(failure, detail=notice_failure_detail)
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
                captcha_attempts_used = 0
                captcha_solver_unavailable = False
                captcha_seen = False
                captcha_schedule_recovered = False

                async def solve_once(challenge_page, location="sdo"):
                    nonlocal captcha_attempts_used, captcha_solver_unavailable, captcha_seen
                    # Month/week detections were already announced before
                    # this call. The callback here is for live SDO exits only.
                    captcha_seen = True
                    if location == "sdo":
                        if trace is not None and not trace.captcha_seen:
                            trace.mark_captcha("sdo")
                        if on_captcha is not None:
                            await on_captcha("sdo")
                    if captcha_attempts_used >= MAX_AUTOMATIC_CAPTCHA_ATTEMPTS:
                        log_captcha_stage("automatic_attempts_exhausted")
                        return False
                    if captcha_diagnostic_only and captcha_attempts_used:
                        log_captcha_stage("diagnostic_attempt_already_used")
                        return False
                    if urlsplit(getattr(challenge_page, "url", "")).hostname != "my.ranepa.ru":
                        captcha_solver_unavailable = True
                        log_captcha_stage("unsupported_origin")
                        return False
                    if captcha_client is None and not captcha_diagnostic_only:
                        captcha_solver_unavailable = True
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
                        captcha_solver_unavailable = True
                        log_captcha_stage("paid_budget_exhausted")
                        return False
                    if (
                        not captcha_diagnostic_only
                        and captcha_daily_limit > 0
                        and not await reserve_daily_paid_tasks(
                            engine, count=required_tasks, daily_limit=captcha_daily_limit
                        )
                    ):
                        captcha_solver_unavailable = True
                        log_captcha_stage("paid_daily_budget_exhausted")
                        return False
                    captcha_attempts_used += 1
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
                    while (
                        month.status == "captcha_required"
                        and captcha_attempts_used < MAX_AUTOMATIC_CAPTCHA_ATTEMPTS
                        and not captcha_solver_unavailable
                    ):
                        submitted = await solve_once(page, "month")
                        if submitted:
                            log_captcha_stage(await wait_for_captcha_resolution(page))
                        if captcha_diagnostic_only or captcha_solver_unavailable:
                            break
                        # Read the real site again before considering another
                        # paid request. Mouse motion is not proof of success.
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
                    if await manual_captcha(page, captcha_attempts_used):
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
                    if month.status == "login_failed":
                        if trace is not None:
                            trace.failure_detail = "login_rejected"
                        return "login_failed"
                    if trace is not None:
                        diagnostic = getattr(month, "diagnostic", None) or diagnose_browser_state(
                            month.status
                        )
                        if diagnostic is not None:
                            trace.mark_diagnostic(diagnostic)
                    if (
                        month.status.startswith("cabinet_")
                        or month.status == "empty_source_requires_review"
                    ):
                        if trace is not None:
                            trace.failure_detail = "month_incomplete"
                        return "parse_failed"
                    # The login CAPTCHA was observed, but subsequent page
                    # verification failed. This is NOT proof the site is down.
                    if captcha_seen:
                        if trace is not None:
                            trace.failure_detail = "captcha_unverified"
                            trace.mark_diagnostic("challenge_pending")
                        return "captcha"
                    return "source_unavailable"
                if trace is not None:
                    trace.record("month_verified")
                # One authenticated account, many weeks: do not re-parse the
                # same Moodle course for every occurrence of the same lesson.
                course_candidates_cache = {}
                observed_sdo_candidates = set()
                sdo_discovery_deadline = monotonic() + MAX_SDO_SCAN_TOTAL_SECONDS
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
                            while (
                                result.status == "captcha_required"
                                and captcha_attempts_used < MAX_AUTOMATIC_CAPTCHA_ATTEMPTS
                                and not captcha_solver_unavailable
                            ):
                                submitted = await solve_once(page, "week")
                                if submitted:
                                    log_captcha_stage(await wait_for_captcha_resolution(page))
                                if captcha_diagnostic_only or captcha_solver_unavailable:
                                    break
                                log_captcha_stage("schedule_recheck_after_captcha_week")
                                if trace is not None:
                                    trace.record("week_recheck")
                                result = await read_week(
                                    page,
                                    login,
                                    password,
                                    code,
                                    target_monday,
                                    allow_empty=allow_empty,
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
                            if await manual_captcha(page, captcha_attempts_used):
                                log_captcha_stage(await wait_for_captcha_resolution(page))
                                result = await read_week(
                                    page,
                                    login,
                                    password,
                                    code,
                                    target_monday,
                                    allow_empty=allow_empty,
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
                            if result.status == "login_failed":
                                if trace is not None:
                                    trace.failure_detail = "login_rejected"
                                return "login_failed"
                            if trace is not None:
                                diagnostic = getattr(
                                    result, "diagnostic", None
                                ) or diagnose_browser_state(result.status)
                                if diagnostic is not None:
                                    trace.mark_diagnostic(diagnostic)
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
                            if result.snapshot is None and (
                                result.status
                                in {
                                    "source_unavailable",
                                    "challenge_pending",
                                    "unexpected_redirect",
                                    "http_403",
                                    "http_429",
                                    "http_5xx",
                                }
                            ):
                                return "source_unavailable"
                            return "parse_failed"
                        if trace is not None:
                            trace.record("week_retry")
                        await page.wait_for_timeout(750)
                    if trace is not None:
                        trace.record("week_verified")
                    # Source and every recipient MUST commit atomically. A failed
                    # group projection cannot produce a false covered backup job.
                    # Optional SDO link enrichment remains a separate transaction.
                    async with AsyncSession(engine) as db:
                        try:
                            await remember_personal(
                                db,
                                group_id,
                                user_id,
                                result.snapshot,
                                group_identity_verified=True,
                                source_complete=True,
                            )
                            await materialize_verified_facts(
                                db, group_id, user_id, result.snapshot.week_start
                            )
                            await db.commit()
                        except Exception:  # noqa: BLE001
                            await db.rollback()
                            # No private exception text (may contain credentials).
                            log_captcha_stage("peer_materialization_failed")
                            return "internal"
                    if stats is not None:
                        stats.saved_weeks += 1
                        stats.saved_lessons += len(actual)
                    if monotonic() >= sdo_discovery_deadline:
                        # The week was committed. Continue other weeks without
                        # spending the remaining account budget on optional URLs.
                        if trace is not None:
                            trace.record("sdo_exits_skipped_budget")
                        continue
                    if trace is not None:
                        trace.record("sdo_exits_requested")
                    sdo_captcha_unresolved = False
                    try:
                        exit_kwargs = {"course_cache": course_candidates_cache}
                        if (
                            captcha_client is not None
                            and captcha_attempts_used < MAX_AUTOMATIC_CAPTCHA_ATTEMPTS
                        ) or manual_captcha is not None:

                            async def solve_sdo_with_owner_fallback(challenge_page):
                                while (
                                    captcha_attempts_used < MAX_AUTOMATIC_CAPTCHA_ATTEMPTS
                                    and not captcha_solver_unavailable
                                ):
                                    submitted = await solve_once(challenge_page)
                                    if submitted:
                                        state = await wait_for_captcha_resolution(challenge_page)
                                        if state != "challenge_still_visible_after_wait":
                                            return True
                                    if captcha_diagnostic_only or captcha_solver_unavailable:
                                        break
                                if manual_captcha is not None:
                                    log_captcha_stage("owner_fallback_offered_sdo")
                                    return await manual_captcha(
                                        challenge_page, captcha_attempts_used
                                    )
                                return False

                            exit_kwargs["solve_captcha"] = solve_sdo_with_owner_fallback
                        if on_captcha is not None:
                            exit_kwargs["on_captcha"] = on_captcha
                        async with asyncio.timeout(
                            min(
                                MAX_SDO_SCAN_WEEK_SECONDS,
                                max(0.01, sdo_discovery_deadline - monotonic()),
                            )
                        ):
                            resolution = await resolve_sdo_exits(
                                context, result, login, password, **exit_kwargs
                            )
                        links, link_states, courses = resolution
                        # All vetted candidates are retained privately even when
                        # the course contains multiple teachers or no safe match.
                        candidates = getattr(resolution, "candidates", {})
                        for lesson_key, choices in candidates.items():
                            course_url = courses.get(lesson_key)
                            if course_url:
                                observed_sdo_candidates.update(
                                    (course_url, item.url) for item in choices
                                )
                        sdo_captcha_unresolved = "captcha_required" in link_states.values()
                        if trace is not None:
                            if sdo_captcha_unresolved and not trace.captcha_seen:
                                trace.mark_captcha("sdo")
                            trace.record("sdo_exits_finished")
                        if result.snapshot.lessons and (links or link_states or courses):
                            async with AsyncSession(engine) as db:
                                await enrich_personal_links(
                                    db,
                                    group_id,
                                    user_id,
                                    result.snapshot.week_start,
                                    links=links,
                                    courses=courses,
                                    link_states=link_states,
                                    candidates=candidates,
                                )
                                await db.commit()
                        if stats is not None:
                            stats.saved_sdo_links += len(links)
                            stats.saved_sdo_courses += len(courses)
                            stats.discovered_sdo_candidates = len(observed_sdo_candidates)
                    except TimeoutError:
                        if trace is not None:
                            trace.record("sdo_exits_timed_out")
                        log_captcha_stage("sdo_enrichment_time_budget_exhausted")
                    except Exception:  # noqa: BLE001
                        # No enrichment or provider error can roll back the committed week.
                        # Never log exception details which can contain credentials.
                        log_captcha_stage("sdo_enrichment_failed")
                    # After optional link enrichment, refresh the projection
                    # without turning a link failure into timetable failure.
                    if result.snapshot.lessons:
                        try:
                            async with AsyncSession(engine) as db:
                                await materialize_verified_facts(
                                    db, group_id, user_id, result.snapshot.week_start
                                )
                                await db.commit()
                        except Exception:  # noqa: BLE001
                            log_captcha_stage("peer_link_projection_failed")
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
    except Exception as error:  # noqa: BLE001
        # Only closed diagnostic codes may reach DB/Telegram, never raw exceptions.
        diagnostic = diagnose_exception(error)
        if trace is not None:
            trace.mark_diagnostic(diagnostic)
        if diagnostic in {
            "timeout",
            "dns",
            "connection",
            "tls",
            "http_403",
            "http_429",
            "http_5xx",
        }:
            log_captcha_stage("crawl_source_connection_error")
            return "source_unavailable"
        log_captcha_stage("crawl_internal_error")
        return "internal"


async def sdo_health(engine, settings: Settings):
    if not legal_ready(settings) or not settings.ranepa_access_approved:
        return "approval_missing"
    try:
        Fernet(settings.profile_encryption_key.get_secret_value().encode())
        store = configured_store(settings)
        if store is None:
            return "credentials_unavailable"
        # A read of a random nonexistent identifier verifies the actual YDB
        # credential table without reading or modifying a student's record.
        await store.get(uuid4())
    except (ValueError, CredentialStoreUnavailable):
        return "credentials_unavailable"
    try:
        await ensure_owner_for_crawl(engine, settings)
    except Exception:  # noqa: BLE001 -- owner lookup must fail closed
        return "configuration_missing"
    try:
        async with async_playwright() as playwright:
            browser = await playwright.chromium.launch(headless=True)
            try:
                page = await browser.new_page()
                await page.goto("about:blank")
            finally:
                await browser.close()
    except Exception:  # noqa: BLE001 -- no exception text can leave this probe
        return "browser_unavailable"
    return None


def autonomous_captcha_budget(settings: Settings):
    return CaptchaBudget(
        min(settings.ranepa_captcha_max_tasks_per_run, settings.ranepa_captcha_daily_max_tasks)
    )


async def run_forever(settings: Settings):
    warn_if_unconfigured(settings)
    captcha_budget = autonomous_captcha_budget(settings)
    budget_day = datetime.now(UTC).date()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(signum, stop.set)
    engine = create_worker_engine(settings)
    gate = ProcessGate(
        engine, settings, "sdo", settings.ranepa_crawl_enabled, lambda: sdo_health(engine, settings)
    )
    try:
        next_prepare_at = 0.0
        next_notice_recovery_at = 0.0
        previous_generation = None
        while not stop.is_set():
            generation = None
            delay = 5
            try:
                generation = await gate.begin()
                if generation is None and gate.reported_status == "stopped":
                    heartbeat()
                if generation is not None:
                    heartbeat()
                    today = datetime.now(ZoneInfo("Europe/Moscow")).date()
                    utc_day = datetime.now(UTC).date()
                    if utc_day != budget_day:
                        captcha_budget = autonomous_captcha_budget(settings)
                        budget_day = utc_day
                    if (
                        settings.ranepa_crawl_notify_account_results_enabled
                        and monotonic() >= next_notice_recovery_at
                    ):
                        try:
                            await recover_account_crawl_notifications(
                                engine, owner_id=settings.bot_admin_user_id
                            )
                        except Exception:  # noqa: BLE001
                            log_captcha_stage("owner_account_notice_recovery_unavailable")
                        next_notice_recovery_at = monotonic() + 60
                    await prepare_urgent(engine, settings, today)
                    if generation != previous_generation or monotonic() >= next_prepare_at:
                        await prepare_week(engine, today, settings, bootstrap=True)
                        next_prepare_at = monotonic() + 60
                        previous_generation = generation
                    status = await process_one(
                        engine, settings, today, captcha_budget=captcha_budget
                    )
                    await finalize_urgent(engine, settings)
                    heartbeat()
                    if status == "paused":
                        await gate.failed(generation, "captcha_guard_paused")
                    else:
                        await gate.succeeded(generation)
                    delay = (
                        15
                        if status == "idle"
                        else 300
                        if status == "paused"
                        else settings.ranepa_crawl_pause_seconds
                    )
            except Exception:  # noqa: BLE001 -- durable queue and safe diagnostics
                log_captcha_stage("process_control_iteration_failed")
                if generation is not None:
                    try:
                        await gate.failed(generation)
                    except Exception:  # noqa: BLE001 -- database outage cannot kill the poller
                        log_captcha_stage("process_control_status_unavailable")
            try:
                await gate.sleep(generation, delay, stop)
            except Exception:  # noqa: BLE001 -- retry control DB; never claim when unavailable
                await asyncio.sleep(5)
    finally:
        await engine.dispose()
        for signum in (signal.SIGTERM, signal.SIGINT):
            loop.remove_signal_handler(signum)


def balanced_sample_users(queued: list[tuple[UUID, UUID]], *, limit: int = 15) -> tuple[UUID, ...]:
    """Choose at most 15 distinct due users, alternating across groups.

    The caller provides eligible queued (group_id, user_id) rows in durable
    priority order. This never changes job priorities or bypasses cooldowns.
    """
    if not 1 <= limit <= 15:
        raise ValueError("invalid_sdo_sample_limit")
    groups: dict[UUID, deque[UUID]] = defaultdict(deque)
    seen: set[UUID] = set()
    for group_id, user_id in queued:
        if user_id in seen:
            continue
        seen.add(user_id)
        groups[group_id].append(user_id)
    selected: list[UUID] = []
    while len(selected) < limit:
        made_progress = False
        for group_id in sorted(groups):
            if groups[group_id] and len(selected) < limit:
                selected.append(groups[group_id].popleft())
                made_progress = True
        if not made_progress:
            break
    return tuple(selected)


async def run_batch(
    settings: Settings,
    *,
    max_minutes: int = 24,
    rescan_all: bool = False,
    sample_accounts: int | None = None,
) -> int:
    """Process a bounded shift or one owner-approved full rescan.

    The full rescan targets a fixed snapshot of consented accounts, once each.
    Normal shifts keep the durable 24-hour per-account cooldown.
    """
    if not settings.ranepa_crawl_enabled:
        return 0
    if sample_accounts is not None and (rescan_all or not 1 <= sample_accounts <= 15):
        raise ValueError("invalid_sample_scope")
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
    sample_order: tuple[UUID, ...] = ()
    stop_reason = "time_limit"
    try:
        await ensure_owner_for_crawl(engine, settings)
        if settings.ranepa_crawl_notify_account_results_enabled:
            try:
                await recover_account_crawl_notifications(
                    engine, owner_id=settings.bot_admin_user_id
                )
            except Exception:  # noqa: BLE001
                log_captcha_stage("owner_account_notice_recovery_unavailable")
        today = datetime.now(ZoneInfo("Europe/Moscow")).date()
        await prepare_week(engine, today, settings, rescan_all=rescan_all)
        if sample_accounts is not None:
            monday = today - timedelta(days=today.weekday())
            async with AsyncSession(engine) as db:
                now = datetime.now(UTC)
                rows = (
                    await db.execute(
                        select(RanepaCrawlJob.group_id, RanepaCrawlJob.user_id)
                        .where(
                            RanepaCrawlJob.week_start == monday,
                            RanepaCrawlJob.state == "queued",
                            (
                                RanepaCrawlJob.next_attempt_at.is_(None)
                                | (RanepaCrawlJob.next_attempt_at <= now)
                            ),
                        )
                        .order_by(RanepaCrawlJob.priority, RanepaCrawlJob.id)
                    )
                ).all()
            sample_order = balanced_sample_users(rows, limit=sample_accounts)
            print(f"RANEPA_BALANCED_SAMPLE_PLANNED {len(sample_order)}", flush=True)
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
        if sample_accounts is not None:
            # Each selected user is eligible at claim time and is attempted at
            # most once, while allowing another group's turn immediately.
            for selected_id in sample_order:
                if monotonic() >= deadline:
                    break
                status = await process_one(
                    engine,
                    settings,
                    datetime.now(ZoneInfo("Europe/Moscow")).date(),
                    captcha_budget=captcha_budget,
                    run_stats=run_stats,
                    allowed_user_ids={selected_id},
                )
                if status == "paused":
                    stop_reason = "paused"
                    break
                if status == "idle":
                    continue
                completed += 1
                if monotonic() < deadline:
                    await asyncio.sleep(settings.ranepa_crawl_pause_seconds)
            else:
                stop_reason = "idle"
        else:
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
                    require_scheduler=not rescan_all,
                )
                if status in {"idle", "paused"}:
                    stop_reason = status
                    break
                completed += 1
                if monotonic() < deadline:
                    await asyncio.sleep(settings.ranepa_crawl_pause_seconds)
        remaining = (
            max(0, len(sample_order) - completed)
            if sample_accounts is not None
            else max(0, len(eligible_ids) - completed)
            if eligible_ids is not None
            else 0
        )
        if settings.ranepa_crawl_notify_success_enabled or (
            settings.ranepa_crawl_notify_account_results_enabled and run_stats.failed_accounts
        ):
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
                    "sample_cap": sample_accounts,
                    "sample_selected": len(sample_order) if sample_accounts is not None else None,
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
        if settings.ranepa_crawl_notify_account_results_enabled:
            try:
                await recover_account_crawl_notifications(
                    engine, owner_id=settings.bot_admin_user_id
                )
            except Exception:  # noqa: BLE001
                log_captcha_stage("owner_account_notice_recovery_unavailable")
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
    parser.add_argument("--sample-accounts", type=int, choices=range(1, 16))
    parser.add_argument("--pilot-group-id", type=UUID)
    parser.add_argument("--pilot-user-id", type=UUID)
    args = parser.parse_args()
    if (args.pilot_group_id is None) != (args.pilot_user_id is None):
        parser.error("both pilot IDs are required")
    if args.pilot_group_id is not None and args.batch_minutes is not None:
        parser.error("--batch-minutes cannot be used with a single-account pilot")
    if args.sample_accounts is not None and (
        args.force_rescan_all or args.pilot_group_id is not None or args.batch_minutes is None
    ):
        parser.error("a bounded SDO sample requires --batch-minutes and no pilot/full rescan")
    if args.force_rescan_all and (
        args.batch_minutes is None
        or args.pilot_group_id is not None
        or os.environ.get("RANEPA_FORCE_RESCAN_ALL_APPROVED") != "true"
    ):
        parser.error("full rescan requires explicit owner-approved release workflow")
    if (
        not args.force_rescan_all
        and args.sample_accounts is None
        and args.batch_minutes
        and args.batch_minutes > 24
    ):
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
                sample_accounts=args.sample_accounts,
            )
        )
    else:
        asyncio.run(run_forever(settings))


if __name__ == "__main__":
    main()
