"""Opt-in real PostgreSQL tests: migrations must already be applied to the test database."""

import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool
from test_processing import Provider

from studgroup.main import Settings
from studgroup.models import AIAttempt, AIJob, Group, Homework, RawMessage
from studgroup.processing import process_next

pytestmark = pytest.mark.skipif(
    not os.environ.get("POSTGRES_TEST_URL"), reason="PostgreSQL not configured"
)


def test_postgres_concurrent_worker_claims_and_idempotency():
    async def run():
        engine = create_async_engine(os.environ["POSTGRES_TEST_URL"], poolclass=NullPool)
        group_id, raw_id = uuid.uuid4(), uuid.uuid4()
        now = datetime.now(UTC)
        async with AsyncSession(engine) as db:
            db.add(
                Group(
                    id=group_id,
                    telegram_chat_id=-int(uuid.uuid4().int % 10**12) - 10000,
                    name="Disposable pipeline integration",
                    timezone="Europe/Moscow",
                    pilot_authorized=True,
                )
            )
            await db.flush()
            db.add(
                RawMessage(
                    id=raw_id,
                    group_id=group_id,
                    telegram_message_id=1,
                    text="ДЗ: задачи",
                    revision=1,
                    message_date=now,
                    version_date=now,
                    delete_at=now + timedelta(days=30),
                )
            )
            await db.commit()
        provider = Provider()
        settings = Settings(ai_enabled=True, ai_total_budget_usd=100, ai_daily_group_budget_usd=1)
        outcomes = await asyncio.gather(
            process_next(engine, settings, provider), process_next(engine, settings, provider)
        )
        assert sorted(outcomes) == ["completed", "idle"]
        assert provider.calls == 1
        async with AsyncSession(engine) as db:
            assert (
                await db.scalar(
                    select(func.count(Homework.id)).where(Homework.group_id == group_id)
                )
                == 1
            )
            assert (
                await db.scalar(select(func.count(AIJob.id)).where(AIJob.group_id == group_id)) == 1
            )
            assert (
                await db.scalar(
                    select(func.count(AIAttempt.id)).where(AIAttempt.group_id == group_id)
                )
                == 1
            )
            group = await db.get(Group, group_id)
            group.pilot_authorized = False  # retained test evidence, never process again
            await db.commit()
        await engine.dispose()

    asyncio.run(run())
