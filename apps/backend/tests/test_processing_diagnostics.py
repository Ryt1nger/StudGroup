import asyncio
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from test_processing import source

from studgroup.analysis.contracts import AnalysisInput, SourceMessage
from studgroup.main import Settings
from studgroup.models import (
    AIControl,
    Group,
    GroupAnalysisSettings,
    RawMessage,
    ShadowEvaluationRun,
)
from studgroup.processing_diagnostics import snapshot

pytest_plugins = ["test_schedule_api"]


def test_snapshot_reads_counters_without_claiming_work_or_reserving_budget(client):
    source(client, mid=31)
    source(client, mid=32)

    async def check():
        async with AsyncSession(client.app.state.engine) as db:
            imported = await db.scalar(
                select(RawMessage).where(RawMessage.telegram_message_id == 32)
            )
            imported.imported = True
            await db.commit()
            result = await snapshot(db, Settings(_env_file=None), datetime.now(UTC))
            group = result["groups"][0]
            assert group["raw"] == 2 and group["imported"] == 1 and group["live"] == 1
            assert group["bootstrap_scope"] == 2
            assert group["bot_added_at"] is not None
            assert group["pending_live"] == 1
            assert await db.get(AIControl, 1) is None
            assert "text" not in result

    asyncio.run(check())


def test_canary_preflight_sizes_one_expired_run_without_mutating_it(client):
    private_text = "Секретный текст задания"
    source(client, mid=41, text=private_text)
    now = datetime.now(UTC)

    async def check():
        async with AsyncSession(client.app.state.engine) as db:
            group = await db.scalar(select(Group).where(Group.telegram_chat_id == -1001))
            raw = await db.scalar(
                select(RawMessage).where(RawMessage.telegram_message_id == 41)
            )
            db.add(
                GroupAnalysisSettings(
                    group_id=group.id,
                    analysis_mode="shadow",
                    engine_version="cplus-shadow-1",
                    shadow_daily_budget_usd=Decimal("0.20"),
                    shadow_sample_percent=100,
                    updated_at=now - timedelta(hours=1),
                )
            )
            analysis_input = AnalysisInput(
                group_id=group.id,
                group_timezone=group.timezone,
                source_revision=raw.revision,
                target_message_ids=[41],
                messages=[
                    SourceMessage(
                        telegram_message_id=41,
                        revision=raw.revision,
                        message_date=raw.message_date.replace(tzinfo=UTC),
                        text=private_text,
                    )
                ],
            )
            run = ShadowEvaluationRun(
                group_id=group.id,
                batch_id=None,
                job_id=None,
                raw_message_id=raw.id,
                source_revision=raw.revision,
                analysis_generation=raw.analysis_generation,
                source_snapshot_sha256="0" * 64,
                engine_version="cplus-shadow-1",
                state="running",
                input_json=json.dumps(analysis_input.model_dump(mode="json")),
                memory_json="[]",
                attempts=1,
                available_at=now - timedelta(minutes=10),
                lease_until=now - timedelta(minutes=1),
                prompt_tokens=0,
                completion_tokens=0,
                prompt_cache_hit_tokens=0,
                reserved_usd=Decimal("0.04"),
                charged_usd=Decimal(0),
                created_at=now - timedelta(minutes=10),
                delete_at=now + timedelta(days=30),
            )
            db.add(run)
            await db.flush()
            run_id = run.id
            await db.commit()

            result = await snapshot(
                db,
                Settings(
                    _env_file=None,
                    ai_enabled=False,
                    ai_shadow_enabled=False,
                    ai_shadow_canary_only=True,
                ),
                now,
            )
            group_result = result["groups"][0]
            preflight = group_result["shadow_canary_preflight"]
            assert preflight["status"] == "ready_for_controlled_activation"
            assert preflight["blocking_codes"] == []
            assert preflight["recoverable_canary_count"] == 1
            assert preflight["active_shadow_lease_count"] == 0
            assert preflight["active_legacy_lease_count"] == 0
            assert preflight["envelope"]["message_count"] == 1
            assert preflight["envelope"]["context_request_bytes"] > 0
            assert Decimal(preflight["envelope"]["cost_upper_bound_usd"]) < Decimal(
                "0.04"
            )
            assert group_result["notifications"] == 0
            assert group_result["bot_outbox"] == 0
            assert group_result["canonical_items"] == 0
            assert private_text not in json.dumps(result, ensure_ascii=False)

            await db.refresh(run)
            assert run.id == run_id
            assert run.state == "running"
            assert run.attempts == 1
            assert run.reserved_usd == Decimal("0.04")

    asyncio.run(check())


def test_canary_preflight_recognizes_one_queued_prepared_run(client):
    private_text = "Приватный текст подготовленного пакета"
    source(client, mid=42, text=private_text)
    now = datetime.now(UTC)

    async def check():
        async with AsyncSession(client.app.state.engine) as db:
            group = await db.scalar(select(Group).where(Group.telegram_chat_id == -1001))
            raw = await db.scalar(
                select(RawMessage).where(RawMessage.telegram_message_id == 42)
            )
            db.add(
                GroupAnalysisSettings(
                    group_id=group.id,
                    analysis_mode="shadow",
                    engine_version="cplus-shadow-1",
                    shadow_daily_budget_usd=Decimal("0.20"),
                    shadow_sample_percent=100,
                    updated_at=now - timedelta(hours=1),
                )
            )
            analysis_input = AnalysisInput(
                group_id=group.id,
                group_timezone=group.timezone,
                source_revision=raw.revision,
                target_message_ids=[42],
                messages=[
                    SourceMessage(
                        telegram_message_id=42,
                        revision=raw.revision,
                        message_date=raw.message_date.replace(tzinfo=UTC),
                        text=private_text,
                    )
                ],
            )
            run = ShadowEvaluationRun(
                group_id=group.id,
                batch_id=None,
                job_id=None,
                raw_message_id=raw.id,
                source_revision=raw.revision,
                analysis_generation=raw.analysis_generation,
                source_snapshot_sha256="0" * 64,
                engine_version="cplus-shadow-1",
                state="queued",
                input_json=json.dumps(analysis_input.model_dump(mode="json")),
                memory_json="[]",
                metrics_json=json.dumps({"preparation_key": "canary-test"}),
                attempts=0,
                available_at=now,
                lease_until=None,
                prompt_tokens=0,
                completion_tokens=0,
                prompt_cache_hit_tokens=0,
                reserved_usd=Decimal(0),
                charged_usd=Decimal(0),
                created_at=now,
                delete_at=now + timedelta(days=30),
            )
            db.add(run)
            await db.commit()

            result = await snapshot(
                db,
                Settings(
                    _env_file=None,
                    ai_enabled=False,
                    ai_shadow_enabled=False,
                    ai_shadow_canary_only=True,
                ),
                now,
            )
            preflight = result["groups"][0]["shadow_canary_preflight"]
            assert preflight["status"] == "ready_for_controlled_reservation"
            assert preflight["blocking_codes"] == []
            assert preflight["recoverable_canary_count"] == 0
            assert preflight["prepared_canary_count"] == 1
            assert preflight["selected_state"] == "queued"
            assert preflight["active_shadow_lease_count"] == 0
            assert preflight["active_legacy_lease_count"] == 0
            assert preflight["envelope"]["message_count"] == 1
            assert Decimal(preflight["envelope"]["cost_upper_bound_usd"]) < Decimal(
                "0.04"
            )
            assert "reserve_prepared_canary" in preflight["launch_requirements"]
            assert private_text not in json.dumps(result, ensure_ascii=False)

            await db.refresh(run)
            assert run.state == "queued"
            assert run.attempts == 0
            assert run.lease_until is None
            assert run.reserved_usd == 0

    asyncio.run(check())
