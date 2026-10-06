"""Application factory; dependency readiness never exposes credentials."""

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import create_async_engine

from studgroup.academic_deadlines import router as deadlines_router
from studgroup.api import ApiError, router
from studgroup.homework import router as homework_router
from studgroup.ingestion import router as ingestion_router


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")
    database_url: str = "postgresql+asyncpg://studgroup:studgroup@localhost:5432/studgroup"
    redis_url: str = "redis://localhost:6379/0"
    webapp_origin: str = "http://localhost:5173"
    telegram_bot_token: str = ""
    telegram_webhook_secret: str = ""
    deepseek_api_key: SecretStr = SecretStr("")
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-flash"


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.engine = create_async_engine(settings.database_url, pool_pre_ping=True)
        app.state.redis = Redis.from_url(settings.redis_url, socket_connect_timeout=2)
        try:
            yield
        finally:
            await app.state.engine.dispose()
            await app.state.redis.aclose()

    app = FastAPI(title="StudGroup", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.include_router(router)
    app.include_router(ingestion_router)
    app.include_router(homework_router)
    app.include_router(deadlines_router)

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
        allow_methods=["GET", "POST", "PUT"],
        allow_headers=["Authorization", "Content-Type"],
    )

    @app.get("/health", include_in_schema=False)
    async def health():
        return {"status": "ok"}

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
