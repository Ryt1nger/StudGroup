"""Opt-in real PostgreSQL tests: migrations must already be applied to the test database."""

import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool
from test_analysis_shadow import FakeProvider, HybridProvider, analysis_input
from test_processing import Provider

from studgroup.ai import ProviderFailure
from studgroup.analysis.routing import configure_group_mode
from studgroup.analysis.shadow import source_snapshot_sha256
from studgroup.analysis.shadow_worker import process_shadow_next
from studgroup.main import Settings, create_app
from studgroup.models import (
    AcademicDeadline,
    AIAttempt,
    AIJob,
    BotOutbox,
    CanonicalAcademicItem,
    Group,
    Homework,
    Notification,
    RawMessage,
    ShadowEvaluationRun,
)
from studgroup.processing import process_next
from studgroup.worker import sweep_once

pytestmark = pytest.mark.skipif(
    not os.environ.get("POSTGRES_TEST_URL"), reason="PostgreSQL not configured"
)


def test_postgres_concurrent_worker_claims_and_idempotency():
    async def run():
        engine = create_async_engine(os.environ["POSTGRES_TEST_URL"], poolclass=NullPool)
        group_id, raw_id = uuid.uuid4(), uuid.uuid4()
        now = datetime.now(UTC)
        async with AsyncSession(engine) as db:
            await db.execute(delete(Group).where(Group.name.like("Disposable %")))
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


def test_postgres_telegram_worker_router_shadow_e2e_is_read_only():
    """Real DB + HTTP ingestion + Celery task body + router + both AI engines."""

    class E2EProvider(HybridProvider):
        fail_next_shadow = False

        async def complete(self, request):
            if self.fail_next_shadow:
                self.fail_next_shadow = False
                raise ProviderFailure("provider_unreachable", True)
            return await super().complete(request)

    async def seed(url, chat_id):
        engine = create_async_engine(url, poolclass=NullPool)
        group_id = uuid.uuid4()
        try:
            async with AsyncSession(engine) as db:
                await db.execute(delete(Group).where(Group.name.like("Disposable %")))
                group = Group(
                    id=group_id,
                    telegram_chat_id=chat_id,
                    name="Disposable shadow E2E",
                    timezone="Europe/Moscow",
                    pilot_authorized=True,
                    bot_added_at=datetime.now(UTC) - timedelta(minutes=1),
                )
                db.add(group)
                await db.flush()
                await configure_group_mode(
                    db,
                    group_id,
                    "shadow",
                    datetime.now(UTC),
                    shadow_daily_budget_usd=Decimal("0.20"),
                    shadow_sample_percent=100,
                )
                await db.commit()
            return group_id
        finally:
            await engine.dispose()

    async def snapshot(url, group_id):
        engine = create_async_engine(url, poolclass=NullPool)
        try:
            async with AsyncSession(engine) as db:
                homework = (
                    await db.execute(
                        select(
                            Homework.id,
                            Homework.title,
                            Homework.description,
                            Homework.deadline_at,
                            Homework.revision,
                            Homework.verification_state,
                        ).where(Homework.group_id == group_id)
                    )
                ).all()
                counts = {}
                for model in (AcademicDeadline, Notification, CanonicalAcademicItem):
                    counts[model.__tablename__] = await db.scalar(
                        select(func.count()).select_from(model).where(model.group_id == group_id)
                    )
                counts["bot_outbox"] = await db.scalar(select(func.count()).select_from(BotOutbox))
                return homework, counts
        finally:
            await engine.dispose()

    async def shadow_row(url, group_id):
        engine = create_async_engine(url, poolclass=NullPool)
        try:
            async with AsyncSession(engine) as db:
                return await db.scalar(
                    select(ShadowEvaluationRun)
                    .where(ShadowEvaluationRun.group_id == group_id)
                    .order_by(ShadowEvaluationRun.created_at.desc())
                )
        finally:
            await engine.dispose()

    url = os.environ["POSTGRES_TEST_URL"]
    chat_id = -int(uuid.uuid4().int % 10**12) - 20000
    config = Settings(
        database_url=url,
        telegram_webhook_secret="shadow-e2e-secret",
        ai_enabled=True,
        legacy_engine_enabled=True,
        ai_total_budget_usd=100,
        ai_daily_group_budget_usd=1,
    )
    app = create_app(config)
    provider = E2EProvider()
    with TestClient(app) as client:
        group_id = asyncio.run(seed(url, chat_id))
        now = int(datetime.now(UTC).timestamp())
        update_base = int(uuid.uuid4().int % 1_000_000_000) + 10_000

        def deliver(update_id, text, *, edited=False):
            message = {
                "message_id": 3338,
                "date": now,
                "chat": {"id": chat_id, "type": "supergroup"},
                "text": text,
                "from": {"id": 42},
            }
            if edited:
                message["edit_date"] = now + 1
            return {"update_id": update_id, "edited_message" if edited else "message": message}

        headers = {"X-Telegram-Bot-Api-Secret-Token": "shadow-e2e-secret"}
        payload = deliver(update_base, "ДЗ по математике: решить задачи 1–3")
        assert client.post("/v1/telegram/webhook", json=payload, headers=headers).status_code == 200
        # Telegram retries are acknowledged idempotently.
        assert client.post("/v1/telegram/webhook", json=payload, headers=headers).status_code == 200
        statuses = [asyncio.run(sweep_once(config, provider))["status"] for _ in range(3)]
        assert statuses == ["structure_mapped", "shadow_completed", "completed"]
        stable = asyncio.run(snapshot(url, group_id))
        row = asyncio.run(shadow_row(url, group_id))
        assert row.state == "completed" and row.validation_result_json
        assert asyncio.run(sweep_once(config, provider))["status"] in {
            "backfill_completed",
            "idle",
        }
        assert asyncio.run(snapshot(url, group_id)) == stable

        # A Telegram edit gets its own immutable shadow run. A failed context pass
        # is persisted and retried without touching the legacy product projection.
        edited = deliver(
            update_base + 1,
            "ДЗ по математике, уточнение: решить задачи 1–4",
            edited=True,
        )
        assert client.post("/v1/telegram/webhook", json=edited, headers=headers).status_code == 200
        for _attempt in range(6):
            status = asyncio.run(sweep_once(config, provider))["status"]
            if status == "completed":
                break
            assert status in {"structure_mapped", "backfill_completed", "idle"}
        else:
            pytest.fail("edited Telegram source was not processed")
        after_legacy_edit = asyncio.run(snapshot(url, group_id))
        provider.fail_next_shadow = True
        assert asyncio.run(sweep_once(config, provider))["status"] == "shadow_retry"
        assert asyncio.run(snapshot(url, group_id)) == after_legacy_edit

        async def release_retry():
            engine = create_async_engine(url, poolclass=NullPool)
            try:
                async with AsyncSession(engine) as db:
                    retry = await db.scalar(
                        select(ShadowEvaluationRun)
                        .where(
                            ShadowEvaluationRun.group_id == group_id,
                            ShadowEvaluationRun.state == "retry",
                        )
                        .with_for_update()
                    )
                    retry.available_at = datetime.now(UTC) - timedelta(seconds=1)
                    await db.commit()
            finally:
                await engine.dispose()

        asyncio.run(release_retry())
        assert asyncio.run(sweep_once(config, provider))["status"] == "shadow_completed"
        assert asyncio.run(snapshot(url, group_id)) == after_legacy_edit

        async def verify():
            engine = create_async_engine(url, poolclass=NullPool)
            try:
                async with AsyncSession(engine) as db:
                    rows = (
                        await db.scalars(
                            select(ShadowEvaluationRun)
                            .where(ShadowEvaluationRun.group_id == group_id)
                            .order_by(ShadowEvaluationRun.created_at)
                        )
                    ).all()
                    assert len(rows) == 2
                    assert [row.source_revision for row in rows] == [1, 2]
                    assert rows[1].attempts == 2
                    assert all(row.reserved_usd == 0 for row in rows)
                    group = await db.get(Group, group_id)
                    group.pilot_authorized = False
                    await db.commit()
            finally:
                await engine.dispose()

        asyncio.run(verify())


def test_postgres_shadow_budget_reservation_is_atomic_under_concurrency():
    async def run():
        engine = create_async_engine(os.environ["POSTGRES_TEST_URL"], poolclass=NullPool)
        group_id = uuid.uuid4()
        now = datetime.now(UTC)
        data = analysis_input(group_id)
        async with AsyncSession(engine) as db:
            db.add(
                Group(
                    id=group_id,
                    telegram_chat_id=-int(uuid.uuid4().int % 10**12) - 30000,
                    name="Disposable atomic shadow budget",
                    timezone="Europe/Moscow",
                    pilot_authorized=True,
                )
            )
            await db.flush()
            await configure_group_mode(
                db,
                group_id,
                "shadow",
                now,
                shadow_daily_budget_usd=Decimal("0.04"),
                shadow_sample_percent=100,
            )
            for _ in range(2):
                db.add(
                    ShadowEvaluationRun(
                        group_id=group_id,
                        batch_id=None,
                        job_id=None,
                        source_revision=1,
                        source_snapshot_sha256=source_snapshot_sha256(data),
                        engine_version=data.engine_version,
                        state="queued",
                        input_json=data.model_dump_json(),
                        memory_json="[]",
                        attempts=0,
                        available_at=now,
                        prompt_tokens=0,
                        completion_tokens=0,
                        prompt_cache_hit_tokens=0,
                        reserved_usd=0,
                        charged_usd=0,
                        created_at=now,
                        delete_at=now + timedelta(days=30),
                    )
                )
            await db.commit()

        class SlowProvider(FakeProvider):
            async def complete(self, request):
                await asyncio.sleep(0.05)
                return await super().complete(request)

        provider = SlowProvider()
        settings = Settings(ai_enabled=True)
        outcomes = await asyncio.gather(
            process_shadow_next(engine, settings, provider, now),
            process_shadow_next(engine, settings, provider, now),
        )
        assert sorted(outcomes) == ["completed", "idle"]
        assert await process_shadow_next(engine, settings, provider, now) == "budget_deferred"
        assert len(provider.requests) == 2
        async with AsyncSession(engine) as db:
            rows = (
                await db.scalars(
                    select(ShadowEvaluationRun).where(ShadowEvaluationRun.group_id == group_id)
                )
            ).all()
            assert sorted(row.state for row in rows) == ["budget_deferred", "completed"]
            assert all(row.reserved_usd == 0 for row in rows)
            group = await db.get(Group, group_id)
            group.pilot_authorized = False
            await db.commit()
        await engine.dispose()

    asyncio.run(run())


def test_postgres_missing_budget_row_is_created_once_under_concurrency():
    """Two workers meeting an absent ai_control row must not both insert id=1."""
    from studgroup.models import AIControl
    from studgroup.processing import ensure_ai_control

    async def run():
        engine = create_async_engine(os.environ["POSTGRES_TEST_URL"], poolclass=NullPool)

        async def worker():
            async with AsyncSession(engine) as db:
                control_id = (await ensure_ai_control(db)).id
                await asyncio.sleep(0.05)  # hold the row lock so the other worker must wait
                await db.commit()
                return control_id

        try:
            for _ in range(5):
                async with AsyncSession(engine) as db:
                    await db.execute(delete(AIControl))
                    await db.commit()
                assert await asyncio.gather(worker(), worker(), worker()) == [1, 1, 1]
                async with AsyncSession(engine) as db:
                    assert await db.scalar(select(func.count()).select_from(AIControl)) == 1
        finally:
            await engine.dispose()

    asyncio.run(run())
