"""Opt-in real PostgreSQL gate for the package worker (new engine): locks and idempotency."""

import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool
from test_package_worker import PackageProvider

from studgroup.analysis.package_worker import process_package_next
from studgroup.analysis.pilot import transition_mode
from studgroup.main import Settings
from studgroup.models import (
    CanonicalAcademicItem,
    Group,
    Homework,
    RawMessage,
    ShadowEvaluationRun,
)

pytestmark = pytest.mark.skipif(
    not os.environ.get("POSTGRES_TEST_URL"), reason="PostgreSQL not configured"
)


class SlowPackageProvider(PackageProvider):
    async def complete(self, request):
        await asyncio.sleep(0.05)
        return await super().complete(request)


async def prepare(engine, *, name):
    group_id = uuid.uuid4()
    now = datetime.now(UTC)
    base = now - timedelta(hours=3)
    async with AsyncSession(engine) as db:
        await db.execute(delete(Group).where(Group.name.like("Disposable package%")))
        db.add(
            Group(
                id=group_id,
                telegram_chat_id=-int(uuid.uuid4().int % 10**12) - 30000,
                name=name,
                timezone="Europe/Moscow",
                pilot_authorized=True,
                bot_added_at=base,
            )
        )
        await db.flush()
        for index, text in enumerate(
            ["По математике решить задачи 1–3", "кто-нибудь сделал?", "напоминаю: задачи 1–3"]
        ):
            stamp = base + timedelta(minutes=index)
            db.add(
                RawMessage(
                    group_id=group_id,
                    telegram_message_id=31 + index,
                    text=text,
                    revision=1,
                    message_date=stamp,
                    version_date=stamp,
                    live_received_at=stamp,
                    delete_at=now + timedelta(days=30),
                )
            )
        await db.commit()
    async with AsyncSession(engine) as db:
        for target in ("shadow", "new"):
            await transition_mode(
                db,
                group_id=group_id,
                target_mode=target,
                actor="postgres-test",
                reason="package worker gate",
                now=now,
                gate_approved=target == "new",
                shadow_daily_budget_usd=Decimal("0.50"),
            )
        await db.commit()
    return group_id, now


def test_postgres_concurrent_package_workers_run_and_publish_once():
    async def run():
        engine = create_async_engine(os.environ["POSTGRES_TEST_URL"], poolclass=NullPool)
        group_id, now = await prepare(engine, name="Disposable package worker concurrency")
        settings = Settings(_env_file=None, new_engine_enabled=True)
        provider = SlowPackageProvider()
        outcomes = await asyncio.gather(
            process_package_next(engine, settings, provider, now),
            process_package_next(engine, settings, provider, now),
        )
        # One worker owns the package; the other either finds nothing or only queued it.
        assert "completed" in outcomes
        assert sorted(outcomes) in (["completed", "idle"], ["completed", "queued"])
        assert await process_package_next(engine, settings, provider, now) == "idle"
        # Two passes for the whole three-message package, once.
        assert len(provider.requests) == 2
        async with AsyncSession(engine) as db:
            runs = (
                await db.scalars(
                    select(ShadowEvaluationRun).where(ShadowEvaluationRun.group_id == group_id)
                )
            ).all()
            assert [row.state for row in runs] == ["completed"]
            assert runs[0].raw_message_id is None and runs[0].reserved_usd == 0
            states = (
                await db.scalars(
                    select(RawMessage.processing_state).where(RawMessage.group_id == group_id)
                )
            ).all()
            assert set(states) == {"completed"}
            for model in (Homework, CanonicalAcademicItem):
                assert (
                    await db.scalar(
                        select(func.count()).select_from(model).where(model.group_id == group_id)
                    )
                    == 1
                )
            group = await db.get(Group, group_id)
            group.pilot_authorized = False
            await db.commit()
        await engine.dispose()

    asyncio.run(run())


def test_postgres_a_lost_lease_is_recovered_without_publishing_twice():
    async def run():
        engine = create_async_engine(os.environ["POSTGRES_TEST_URL"], poolclass=NullPool)
        group_id, now = await prepare(engine, name="Disposable package worker lease")
        settings = Settings(_env_file=None, new_engine_enabled=True)

        class Crash(PackageProvider):
            async def complete(self, request):
                raise asyncio.CancelledError  # the worker process dies mid-package

        with pytest.raises(asyncio.CancelledError):
            await process_package_next(engine, settings, Crash(), now)
        async with AsyncSession(engine) as db:
            row = await db.scalar(
                select(ShadowEvaluationRun).where(ShadowEvaluationRun.group_id == group_id)
            )
            assert row.state == "running" and row.reserved_usd > 0
        provider = PackageProvider()
        later = now + timedelta(minutes=10)  # lease (3 min) has expired
        assert await process_package_next(engine, settings, provider, later) == "completed"
        assert await process_package_next(engine, settings, provider, later) == "idle"
        async with AsyncSession(engine) as db:
            assert (
                await db.scalar(
                    select(func.count()).select_from(Homework).where(Homework.group_id == group_id)
                )
                == 1
            )
            group = await db.get(Group, group_id)
            group.pilot_authorized = False
            await db.commit()
        await engine.dispose()

    asyncio.run(run())
