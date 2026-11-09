"""Application factory; dependency readiness never exposes credentials."""

import asyncio
import ssl
from contextlib import asynccontextmanager, suppress
from datetime import datetime
from time import perf_counter
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import AwareDatetime, Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import create_async_engine

from studgroup.academic_deadlines import router as deadlines_router
from studgroup.admin_bot_monitor import router as admin_bot_monitor_router
from studgroup.admin_dashboard import router as admin_dashboard_router
from studgroup.admin_insights import router as admin_insights_router
from studgroup.admin_telemetry import RequestTelemetry
from studgroup.admin_visuals import router as admin_visuals_router
from studgroup.api import ApiError, router
from studgroup.card_edit import router as card_edit_router
from studgroup.homework import router as homework_router
from studgroup.ingestion import router as ingestion_router
from studgroup.notifications import router as notifications_router
from studgroup.profile import router as profile_router
from studgroup.staff_api import router as staff_router
from studgroup.subgroup_api import router as subgroup_router
from studgroup.subjects import router as subjects_router
from studgroup.telegram_files import configure as configure_file_links
from studgroup.telegram_files import router as files_router


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    database_url: str = "postgresql+asyncpg://studgroup:studgroup@localhost:5432/studgroup"
    # Refuse to start when the configured production database is a different project.
    database_project_ref: str | None = None
    # Optional for the Supabase Session Pooler; unset keeps Render PostgreSQL behavior.
    database_ca_file: str | None = None
    database_pool_size: int | None = Field(default=None, ge=1, le=10)
    database_max_overflow: int | None = Field(default=None, ge=0, le=10)
    database_pool_timeout: int | None = Field(default=None, ge=1, le=60)
    # Stage-5 verification gate. When enabled, no startup writes or background loops run.
    cutover_read_only: bool = False
    redis_url: str = "redis://localhost:6379/0"
    webapp_origin: str = "http://localhost:5173"
    telegram_bot_token: str = ""
    telegram_webhook_secret: str = ""
    # Fernet key (urlsafe base64, 32 bytes) that protects study-platform passwords; empty disables them.
    profile_encryption_key: SecretStr = SecretStr("")
    ranepa_crawl_enabled: bool = False
    ranepa_access_approved: bool = False
    ranepa_schedule_view_enabled: bool = False
    ranepa_crawl_pause_seconds: int = Field(default=20, ge=5, le=300)
    legal_operator_name: str = ""
    legal_operator_address: str = ""
    legal_operator_contact: str = ""
    legal_data_region: str = ""
    legal_documents_approved: bool = False
    legal_onboarding_required: bool = False
    deepseek_api_key: SecretStr = SecretStr("")
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-flash"
    ai_release_budget_required_groups: list[str] = Field(default_factory=list)
    # Live paid operation after pilots: no expiring release grant or amount cap.
    # Only explicitly enrolled groups; all requests still use accounting and safety holds.
    ai_production_groups: list[str] = Field(default_factory=list)
    # Explicitly authorized recurring policy, independent of the one-off release grants.
    # An empty allowlist/receipt or expired approval is NEVER a spending permission.
    ai_recurring_budget_groups: list[str] = Field(default_factory=list)
    ai_recurring_approved_until: AwareDatetime | None = None
    ai_recurring_approval_receipt: str = ""
    # All amounts in USD per authorized group, with UTC-day/month accounting.
    ai_recurring_daily_budget_usd: float = Field(default=0.20, gt=0, allow_inf_nan=False)
    ai_recurring_monthly_budget_usd: float = Field(default=6.0, gt=0, allow_inf_nan=False)
    ai_recurring_text_daily_budget_usd: float = Field(default=0.10, gt=0, allow_inf_nan=False)
    ai_recurring_text_monthly_budget_usd: float = Field(default=3.0, gt=0, allow_inf_nan=False)
    ai_recurring_image_daily_budget_usd: float = Field(default=0.10, gt=0, allow_inf_nan=False)
    ai_recurring_image_monthly_budget_usd: float = Field(default=3.0, gt=0, allow_inf_nan=False)
    ai_daily_group_budget_usd: float = 0.05
    ai_hourly_group_budget_usd: float = 0.25
    ai_total_budget_usd: float = 1.70
    ai_circuit_failure_threshold: int = 3
    ai_circuit_cooldown_seconds: int = 300
    ai_max_recoverable_attempts_per_stage: int = 8
    ai_enabled: bool = False
    # Hard lock: the legacy per-message engine never writes cards unless this is set explicitly
    # (LEGACY_ENGINE_ENABLED=true). AI_ENABLED alone no longer wakes it up.
    legacy_engine_enabled: bool = False
    # New (package) engine: live writer for groups whose analysis mode is ``new``. Off by default;
    # the daily spend cap is the group's ``shadow_daily_budget_usd`` setting (shared with shadow).
    new_engine_enabled: bool = False
    new_engine_enabled_until: datetime | None = None
    # Student notifications for cards the new engine creates; keep off while backfilling history.
    new_engine_notifications: bool = False
    # Vision pass over photos of homework before a package is analysed (costs one image call each).
    # Internal pilot only: never inferred from PHOTO_READING_ENABLED.
    image_diagnostics_enabled: bool = False
    image_event_review_enabled: bool = False
    image_event_publication_enabled: bool = False
    image_schedule_review_enabled: bool = False
    image_analysis_since: AwareDatetime | None = None
    image_analysis_enabled: bool = False
    image_model_revision: str = Field(default="", max_length=128)
    image_analysis_groups: list[str] = Field(default_factory=list)
    image_analysis_mode: Literal["shadow", "live"] = "shadow"
    image_pass_reservation_usd: float = Field(default=0.02, gt=0, allow_inf_nan=False)
    image_total_budget_usd: float = Field(default=0.12, ge=0, allow_inf_nan=False)
    image_package_budget_usd: float = Field(default=0.20, ge=0, allow_inf_nan=False)
    image_hourly_budget_usd: float = Field(default=0.25, ge=0, allow_inf_nan=False)
    photo_reading_enabled: bool = False
    # With photo reading on: stored messages since this moment whose photos were never read are
    # analysed again once (e.g. PHOTO_BACKFILL_SINCE=2026-10-17T00:00:00+03:00). Idempotent.
    photo_backfill_since: datetime | None = None
    # Rolling one-hour spend cap of the new engine, on top of the group's daily cap.
    new_engine_hourly_budget_usd: float = Field(default=0.50, ge=0)
    ai_enabled_until: datetime | None = None
    ai_shadow_enabled: bool = False
    ai_shadow_enabled_until: datetime | None = None
    ai_shadow_canary_only: bool = False
    ai_shadow_prepare_once_key: str | None = None
    ai_shadow_prepare_message_id: int | None = None
    ai_shadow_backfill_key: str | None = None
    ai_shadow_find_candidates: bool = False
    ai_shadow_wave_preflight: bool = False
    ai_shadow_wave_results: bool = False
    ai_shadow_backfill_spread: bool = False
    ai_shadow_backfill_limit: int | None = None
    ai_import_chat_id: int | None = None
    ai_schedule_enabled: bool = False
    ai_live_two_pass: bool = False
    ai_store_raw_provider_responses: bool = False
    ai_technical_repair_enabled: bool = False
    # Paid structural repair of rejected C+ proposals; off until separately approved.
    ai_output_repair_enabled: bool = False
    ai_schedule_timezone: str = "Europe/Moscow"
    # The production Render web service has no Celery worker. Manual screenshot
    # drafts need their own durable embedded consumer, irrespective of text AI mode.
    cabinet_schedule_background_enabled: bool = True
    cabinet_review_admin_push_enabled: bool = False
    cabinet_review_admin_push_telegram_id: int | None = None
    processing_mode: Literal["external", "embedded"] = "external"
    ai_loop_pause_seconds: float = Field(default=4, ge=0, le=60)
    owner_telegram_user_id: int | None = None
    owner_update_notifications_enabled: bool = False
    owner_update_notifications_since: datetime | None = None
    bot_admin_user_id: int | None = None
    admin_page_url: str = ""
    support_contact: str = ""
    bot_username: str = ""  # without @; builds t.me invitation links, else code text is shown

    @field_validator("database_url", mode="before")
    @classmethod
    def postgres_driver(cls, value):
        for prefix in ("postgres://", "postgresql://"):
            if isinstance(value, str) and value.startswith(prefix):
                return "postgresql+asyncpg://" + value[len(prefix) :]
        return value

    @model_validator(mode="after")
    def expected_database_project(self):
        if not self.database_project_ref:
            return self
        url = make_url(self.database_url)
        ref = self.database_project_ref
        if url.host != f"db.{ref}.supabase.co" and not (url.username or "").endswith(f".{ref}"):
            raise ValueError("DATABASE_URL does not match DATABASE_PROJECT_REF")
        return self

    @field_validator(
        "ai_enabled_until",
        "ai_shadow_enabled_until",
        "new_engine_enabled_until",
        "photo_backfill_since",
        mode="before",
    )
    @classmethod
    def empty_cutoff(cls, value):
        return None if value == "" else value

    @field_validator("ai_schedule_timezone")
    @classmethod
    def valid_schedule_timezone(cls, value):
        ZoneInfo(value)
        return value

    @field_validator(
        "ai_daily_group_budget_usd",
        "ai_hourly_group_budget_usd",
        "ai_total_budget_usd",
        "ai_circuit_failure_threshold",
        "ai_circuit_cooldown_seconds",
        "ai_max_recoverable_attempts_per_stage",
    )
    @classmethod
    def positive_ai_guardrail(cls, value):
        if value <= 0:
            raise ValueError("AI guardrails must be positive")
        return value

    @field_validator(
        "ai_enabled_until",
        "ai_shadow_enabled_until",
        "new_engine_enabled_until",
        "owner_update_notifications_since",
        "photo_backfill_since",
    )
    @classmethod
    def deadline_requires_timezone(cls, value):
        if value is not None and value.utcoffset() is None:
            raise ValueError("AI deadline requires an explicit timezone")
        return value

    @field_validator(
        "owner_telegram_user_id", "ai_import_chat_id", "bot_admin_user_id", mode="before"
    )
    @classmethod
    def empty_owner(cls, value):
        return None if value == "" else value

    @field_validator("ai_shadow_prepare_message_id", mode="before")
    @classmethod
    def valid_shadow_preparation_message(cls, value):
        if value in (None, ""):
            return None
        number = int(value)
        if number <= 0:
            raise ValueError("invalid shadow preparation message id")
        return number

    @field_validator("ai_shadow_backfill_limit", mode="before")
    @classmethod
    def valid_backfill_limit(cls, value):
        if value in (None, ""):
            return None
        number = int(value)
        if not 1 <= number <= 200:
            raise ValueError("invalid shadow backfill limit")
        return number

    @field_validator("ai_shadow_prepare_once_key", "ai_shadow_backfill_key", mode="before")
    @classmethod
    def valid_shadow_preparation_key(cls, value):
        if value in (None, ""):
            return None
        allowed = "abcdefghijklmnopqrstuvwxyz0123456789-"
        if (
            not isinstance(value, str)
            or not 1 <= len(value) <= 64
            or any(character not in allowed for character in value)
        ):
            raise ValueError("invalid shadow preparation key")
        return value


def database_engine_options(settings: Settings) -> dict:
    options = {}
    if settings.database_ca_file:
        if not settings.database_url.startswith("postgresql+asyncpg://"):
            raise ValueError("database CA requires PostgreSQL with asyncpg")
        tls = ssl.create_default_context(cafile=settings.database_ca_file)
        tls.check_hostname = True
        tls.verify_mode = ssl.CERT_REQUIRED
        options["connect_args"] = {"ssl": tls, "timeout": 10}
    for setting, option in (
        ("database_pool_size", "pool_size"),
        ("database_max_overflow", "max_overflow"),
        ("database_pool_timeout", "pool_timeout"),
    ):
        if (value := getattr(settings, setting)) is not None:
            options[option] = value
    return options


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.engine = create_async_engine(
            settings.database_url, pool_pre_ping=True, **database_engine_options(settings)
        )
        if settings.cutover_read_only:
            app.state.redis = None
            app.state.processor = None
            app.state.cabinet_processor = None
            try:
                yield
            finally:
                await app.state.engine.dispose()
            return
        from studgroup.reviewed_publication import startup as apply_reviewed_payload

        try:
            await apply_reviewed_payload(app.state.engine, settings)
        except Exception as error:  # noqa: BLE001 -- startup must not expose private SQL payloads
            print("REVIEWED_PUBLICATION_FAILED " + type(error).__name__, flush=True)
            raise RuntimeError("reviewed_publication_failed") from None
        # One-off, owner-approved BI 1.2 manual screenshot pilot: immutable $0.05
        # ledger window. Missing environment approval makes this a no-op.
        try:
            from studgroup.cabinet_vision_budget_bootstrap import startup as fund_cabinet_vision

            await fund_cabinet_vision(app.state.engine)
        except Exception as error:  # noqa: BLE001 -- never expose funding parameters in logs
            print("BI12_MANUAL_VISION_BUDGET_UNAVAILABLE " + type(error).__name__, flush=True)
        # Explicit, idempotent owner-approved USD 1 manual-vision top-up.
        # No top-up happens without the Render approval marker.
        try:
            from studgroup.cabinet_vision_budget_topup import startup as apply_bi12_topup

            await apply_bi12_topup(app.state.engine)
        except Exception as error:  # noqa: BLE001 -- never log private funding metadata
            print("BI12_MANUAL_TOPUP_FAILED " + type(error).__name__, flush=True)
        app.state.redis = Redis.from_url(settings.redis_url, socket_connect_timeout=2)
        from studgroup.online_lessons import backfill as backfill_online_links

        try:
            async with asyncio.timeout(15):
                await backfill_online_links(app.state.engine)
        except Exception as error:  # noqa: BLE001 -- optional backfill must not log URLs or block service readiness
            print("ONLINE_LESSON_LINK_BACKFILL_UNAVAILABLE " + type(error).__name__, flush=True)
        if settings.ai_shadow_prepare_once_key:
            from studgroup.analysis.shadow_worker import prepare_shadow_canary

            preparation = await prepare_shadow_canary(app.state.engine, settings)
            print(f"SHADOW_CANARY_PREPARATION {preparation}", flush=True)
        if settings.ai_shadow_find_candidates:
            import json as _json

            from sqlalchemy.ext.asyncio import AsyncSession

            from studgroup.analysis.canary_finder import find_candidates

            try:
                async with AsyncSession(app.state.engine) as session:
                    found = await find_candidates(session)
                    await session.rollback()
                print("SHADOW_CANARY_CANDIDATES " + _json.dumps(found, sort_keys=True), flush=True)
            except Exception as error:  # noqa: BLE001 -- read-only diagnostic, never blocks startup
                print("SHADOW_CANARY_CANDIDATES_FAILED " + type(error).__name__, flush=True)
        if settings.ai_shadow_backfill_key and settings.ai_shadow_backfill_limit:
            from studgroup.analysis.shadow_worker import prepare_backfill_wave

            wave = await prepare_backfill_wave(app.state.engine, settings)
            print(f"SHADOW_BACKFILL_WAVE {wave}", flush=True)
        if settings.ai_shadow_wave_results:
            import json as _json_results

            from sqlalchemy.ext.asyncio import AsyncSession as _ResultsSession

            from studgroup.analysis.wave_results import wave_results

            try:
                async with _ResultsSession(app.state.engine) as session:
                    summary = await wave_results(session)
                    await session.rollback()
                print(
                    "SHADOW_WAVE_RESULTS "
                    + _json_results.dumps(summary, sort_keys=True, default=str),
                    flush=True,
                )
            except Exception as error:  # noqa: BLE001 -- read-only diagnostic, never blocks startup
                print("SHADOW_WAVE_RESULTS_FAILED " + type(error).__name__, flush=True)
        if settings.ai_shadow_wave_preflight:
            import json as _json_wave

            from sqlalchemy.ext.asyncio import AsyncSession as _WaveSession

            from studgroup.analysis.wave_preflight import wave_preflight

            try:
                async with _WaveSession(app.state.engine) as session:
                    report = await wave_preflight(
                        session, settings, wave_size=settings.ai_shadow_backfill_limit or 25
                    )
                    await session.rollback()
                print(
                    "SHADOW_WAVE_PREFLIGHT " + _json_wave.dumps(report, sort_keys=True),
                    flush=True,
                )
            except Exception as error:  # noqa: BLE001 -- read-only diagnostic, never blocks startup
                print("SHADOW_WAVE_PREFLIGHT_FAILED " + type(error).__name__, flush=True)
        # Read-only owner diagnostics are optional: a slow Supabase query must not
        # delay serving the first Mini App request after a Render cold start.
        diagnostics_task = None
        if settings.owner_update_notifications_enabled:
            from studgroup.processing_diagnostics import startup

            diagnostics_task = asyncio.create_task(startup(app.state.engine, settings))
        app.state.diagnostics_task = diagnostics_task
        processor = None
        if settings.processing_mode == "embedded":
            from studgroup.runtime import processing_loop

            processor = asyncio.create_task(processing_loop(app.state.engine, settings))
        app.state.processor = processor
        cabinet_processor = None
        if settings.cabinet_schedule_background_enabled:
            from studgroup.cabinet_schedule_worker import processing_loop as cabinet_loop

            cabinet_processor = asyncio.create_task(cabinet_loop(app.state.engine, settings))
        app.state.cabinet_processor = cabinet_processor
        delivery = None
        if settings.bot_admin_user_id is not None or settings.owner_update_notifications_enabled:
            from studgroup.bot_admin import delivery_loop

            delivery = asyncio.create_task(delivery_loop(app.state.engine, settings))
        try:
            yield
        finally:
            if diagnostics_task is not None:
                diagnostics_task.cancel()
                with suppress(asyncio.CancelledError):
                    await diagnostics_task
            if delivery is not None:
                delivery.cancel()
                with suppress(asyncio.CancelledError):
                    await delivery
            if cabinet_processor is not None:
                cabinet_processor.cancel()
                with suppress(asyncio.CancelledError):
                    await cabinet_processor
            if processor is not None:
                processor.cancel()
                with suppress(asyncio.CancelledError):
                    await processor
            await app.state.engine.dispose()
            await app.state.redis.aclose()

    app = FastAPI(title="StudGroup", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.owner_http_telemetry = RequestTelemetry()
    configure_file_links(settings.telegram_webhook_secret)

    # Bounded diagnostic for the real Mini App cold path. Never log auth data,
    # request bodies, tokens, identifiers or query strings: only static routes.
    @app.middleware("http")
    async def academic_request_timing(request, call_next):
        path = request.url.path
        measured_routes = {
            "/v1/session/bootstrap",
            "/v1/today",
            "/v1/schedule",
            "/v1/homework",
            "/v1/deadlines",
            "/v1/profile",
        }
        if path not in measured_routes:
            return await call_next(request)
        start = perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            return response
        finally:
            duration_ms = round((perf_counter() - start) * 1000)
            print(f"ACADEMIC_API_TIMING route={path} status={status} ms={duration_ms}", flush=True)

    @app.middleware("http")
    async def cutover_write_gate(request, call_next):
        if settings.cutover_read_only and request.method not in {"GET", "HEAD", "OPTIONS"}:
            return JSONResponse(
                {
                    "error": {
                        "code": "maintenance",
                        "message": "Запись временно недоступна",
                        "retryable": True,
                    }
                },
                status_code=503,
                headers={"Retry-After": "60"},
            )
        return await call_next(request)

    # Real HTTP request performance for the operator's console. Kept only in
    # bounded process memory; exclude probes and never record URLs or user data.
    @app.middleware("http")
    async def owner_api_telemetry(request, call_next):
        if not request.url.path.startswith("/v1/"):
            return await call_next(request)
        start = perf_counter()
        status = 500
        try:
            response = await call_next(request)
            status = response.status_code
            return response
        finally:
            app.state.owner_http_telemetry.record(status, (perf_counter() - start) * 1000)

    app.include_router(router)
    app.include_router(admin_dashboard_router)
    app.include_router(admin_insights_router)
    app.include_router(admin_bot_monitor_router)
    app.include_router(admin_visuals_router)
    app.include_router(ingestion_router)
    app.include_router(homework_router)
    app.include_router(card_edit_router)
    app.include_router(files_router)
    app.include_router(subjects_router)
    app.include_router(deadlines_router)
    app.include_router(notifications_router)
    app.include_router(profile_router)
    from studgroup.legal import router as legal_router
    from studgroup.ranepa_personal_api import router as ranepa_personal_router
    from studgroup.ranepa_schedule_api import router as ranepa_schedule_router

    app.include_router(ranepa_personal_router)
    app.include_router(ranepa_schedule_router)
    app.include_router(legal_router)
    from studgroup.onboarding import router as onboarding_router

    app.include_router(onboarding_router)
    app.include_router(subgroup_router)
    app.include_router(staff_router)
    from studgroup.image_review_api import router as image_review_router

    app.include_router(image_review_router)
    from studgroup.cabinet_schedule_api import router as cabinet_schedule_router

    app.include_router(cabinet_schedule_router)
    from studgroup.group_event_api import router as group_event_router

    app.include_router(group_event_router)

    @app.exception_handler(ApiError)
    async def api_error(request, error):
        from uuid import uuid4

        return JSONResponse(
            {
                "error": {
                    "code": error.code,
                    "message": error.message,
                    "correlation_id": str(uuid4()),
                    "retryable": error.status == 503,
                }
            },
            status_code=error.status,
        )

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request, error):
        # Never echo request bodies (Telegram initData or other credentials).
        return await api_error(request, ApiError("invalid_request", "Некорректный запрос", 422))

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.webapp_origin],
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
        allow_headers=["Authorization", "Content-Type"],
    )

    @app.get("/health", include_in_schema=False)
    async def health():
        from studgroup.processing import HOSTED_AI_EMERGENCY_STOP

        ai_processing = "enabled" if settings.ai_enabled else "disabled"
        if HOSTED_AI_EMERGENCY_STOP:
            ai_processing = "emergency_stopped"
        elif settings.ai_enabled_until is not None and settings.ai_enabled_until <= datetime.now(
            settings.ai_enabled_until.tzinfo
        ):
            ai_processing = "scheduled_off"
        return {
            "status": "ok",
            "ai_processing": ai_processing,
        }

    @app.get("/ready", include_in_schema=False)
    async def ready():
        async def postgres():
            async with app.state.engine.connect() as connection:
                await connection.execute(text("SELECT 1"))

        async def check(operation):
            try:
                async with asyncio.timeout(3):
                    await operation()
                return True
            except (OSError, TimeoutError, RedisError, SQLAlchemyError):
                return False

        if settings.cutover_read_only:
            database = await check(postgres)
            redis = None
            ok = database
            status = "read_only" if ok else "not_ready"
        elif settings.processing_mode == "embedded":
            database = await check(postgres)
            redis = None  # SQL inbox is authoritative; no Redis is used in this pilot mode.
            processor_ok = app.state.processor is not None and not app.state.processor.done()
            ok = database and processor_ok
            status = "ready" if ok else "not_ready"
        else:
            database, redis = await asyncio.gather(check(postgres), check(app.state.redis.ping))
            ok = database and redis
            status = "ready" if ok else "not_ready"
        return JSONResponse(
            {
                "status": status,
                "checks": {"postgres": database, "redis": redis},
            },
            status_code=200 if ok else 503,
        )

    return app


app = create_app()
