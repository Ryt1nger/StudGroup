"""Application factory; dependency readiness never exposes credentials."""

import asyncio
from contextlib import asynccontextmanager, suppress
from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import create_async_engine

from studgroup.academic_deadlines import router as deadlines_router
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
    redis_url: str = "redis://localhost:6379/0"
    webapp_origin: str = "http://localhost:5173"
    telegram_bot_token: str = ""
    telegram_webhook_secret: str = ""
    # Fernet key (urlsafe base64, 32 bytes) that protects study-platform passwords; empty disables them.
    profile_encryption_key: SecretStr = SecretStr("")
    deepseek_api_key: SecretStr = SecretStr("")
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-flash"
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
    processing_mode: Literal["external", "embedded"] = "external"
    ai_loop_pause_seconds: float = Field(default=4, ge=0, le=60)
    owner_telegram_user_id: int | None = None
    owner_update_notifications_enabled: bool = False
    owner_update_notifications_since: datetime | None = None
    bot_admin_user_id: int | None = None
    support_contact: str = ""
    bot_username: str = ""  # without @; builds t.me invitation links, else code text is shown

    @field_validator("database_url", mode="before")
    @classmethod
    def postgres_driver(cls, value):
        for prefix in ("postgres://", "postgresql://"):
            if isinstance(value, str) and value.startswith(prefix):
                return "postgresql+asyncpg://" + value[len(prefix) :]
        return value

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


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.engine = create_async_engine(settings.database_url, pool_pre_ping=True)
        from studgroup.reviewed_publication import startup as apply_reviewed_payload

        try:
            await apply_reviewed_payload(app.state.engine, settings)
        except Exception as error:  # noqa: BLE001 -- startup must not expose private SQL payloads
            print("REVIEWED_PUBLICATION_FAILED " + type(error).__name__, flush=True)
            raise RuntimeError("reviewed_publication_failed") from None
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
        if settings.owner_update_notifications_enabled:
            from studgroup.processing_diagnostics import startup

            await startup(app.state.engine, settings)
        processor = None
        if settings.processing_mode == "embedded":
            from studgroup.runtime import processing_loop

            processor = asyncio.create_task(processing_loop(app.state.engine, settings))
        app.state.processor = processor
        delivery = None
        if settings.bot_admin_user_id is not None or settings.owner_update_notifications_enabled:
            from studgroup.bot_admin import delivery_loop

            delivery = asyncio.create_task(delivery_loop(app.state.engine, settings))
        try:
            yield
        finally:
            if delivery is not None:
                delivery.cancel()
                with suppress(asyncio.CancelledError):
                    await delivery
            if processor is not None:
                processor.cancel()
                with suppress(asyncio.CancelledError):
                    await processor
            await app.state.engine.dispose()
            await app.state.redis.aclose()

    app = FastAPI(title="StudGroup", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    configure_file_links(settings.telegram_webhook_secret)
    app.include_router(router)
    app.include_router(ingestion_router)
    app.include_router(homework_router)
    app.include_router(card_edit_router)
    app.include_router(files_router)
    app.include_router(subjects_router)
    app.include_router(deadlines_router)
    app.include_router(notifications_router)
    app.include_router(profile_router)
    app.include_router(subgroup_router)
    app.include_router(staff_router)

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

        if settings.processing_mode == "embedded":
            database = await check(postgres)
            redis = None  # SQL inbox is authoritative; no Redis is used in this pilot mode.
            processor_ok = app.state.processor is not None and not app.state.processor.done()
            ok = database and processor_ok
        else:
            database, redis = await asyncio.gather(check(postgres), check(app.state.redis.ping))
            ok = database and redis
        return JSONResponse(
            {
                "status": "ready" if ok else "not_ready",
                "checks": {"postgres": database, "redis": redis},
            },
            status_code=200 if ok else 503,
        )

    return app


app = create_app()
