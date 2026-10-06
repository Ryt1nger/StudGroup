"""One conservative Celery worker + SQL inbox sweeper; no private text in broker tasks."""

import asyncio
from datetime import UTC, datetime

import httpx
from celery import Celery
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from studgroup.main import Settings
from studgroup.models import OwnerIncident
from studgroup.processing import process_next

settings = Settings()
app = Celery("studgroup", broker=settings.redis_url)
app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    task_ignore_result=True,
    worker_prefetch_multiplier=1,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    broker_connection_retry_on_startup=True,
    task_soft_time_limit=100,
    task_time_limit=120,
    timezone="UTC",
    beat_schedule={"durable-inbox": {"task": "studgroup.sweep", "schedule": 5.0}},
)


async def flush_incidents(engine, config):
    if not config.owner_telegram_user_id or not config.telegram_bot_token:
        return
    async with AsyncSession(engine) as db:
        rows = (
            await db.scalars(
                select(OwnerIncident).where(OwnerIncident.notification_pending.is_(True))
            )
        ).all()
        for row in rows:
            text = (
                "StudGroup: обработка AI приостановлена. "
                if row.active
                else "StudGroup: обработка AI восстановлена. "
            ) + f"Код: {row.code}."
            try:
                async with httpx.AsyncClient(
                    timeout=10, trust_env=False, follow_redirects=False
                ) as client:
                    response = await client.post(
                        f"https://api.telegram.org/bot{config.telegram_bot_token}/sendMessage",
                        json={"chat_id": config.owner_telegram_user_id, "text": text},
                    )
                    success = response.status_code == 200 and response.json().get("ok") is True
            except (httpx.HTTPError, ValueError):
                success = False
            if success:
                row.notification_pending = False
                await db.commit()


async def sweep_once(config=None, provider=None):
    config = config or settings
    engine = create_async_engine(config.database_url, pool_pre_ping=True)
    try:
        status = await process_next(engine, config, provider)
        await flush_incidents(engine, config)
        return {"status": status, "checked_at": datetime.now(UTC).isoformat()}
    finally:
        await engine.dispose()


@app.task(name="studgroup.sweep")
def sweep():
    return asyncio.run(sweep_once())
