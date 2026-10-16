"""Opt-in PostgreSQL gate for private owner-only shadow export delivery."""

import asyncio
import gzip
import json
import os
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import SecretStr
from sqlalchemy import delete, func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from studgroup import bot_admin
from studgroup.analysis.contracts import AnalysisInput, ValidationResult
from studgroup.main import Settings
from studgroup.models import (
    AcademicDeadline,
    BotOutbox,
    CanonicalAcademicItem,
    Group,
    Homework,
    Notification,
    RawMessage,
    ShadowEvaluationRun,
)

pytestmark = pytest.mark.skipif(
    not os.environ.get("POSTGRES_TEST_URL"), reason="PostgreSQL not configured"
)
OWNER_ID = 9001
WAVE = "wave-a-spread-1"
PRIVATE_MARKER = "PRIVATE SYNTHETIC POSTGRES EXPORT MESSAGE"


async def seed_export(engine):
    now = datetime(2026, 10, 12, 12, tzinfo=UTC)
    group_id = uuid.uuid4()
    run_id = uuid.uuid4()
    raw_id = uuid.uuid4()
    outbox_id = uuid.uuid4()
    group = Group(
        id=group_id,
        telegram_chat_id=-1004400000000 - int(uuid.uuid4().int % 10**6),
        name=f"Disposable shadow export {group_id}",
        timezone="Europe/Moscow",
        pilot_authorized=True,
        bot_added_at=now - timedelta(days=1),
    )
    raw = RawMessage(
        id=raw_id,
        group_id=group_id,
        telegram_message_id=10,
        sender_id=1,
        text=PRIVATE_MARKER,
        message_date=now,
        version_date=now,
        revision=1,
        analysis_generation=1,
        processing_state="completed",
        delete_at=now + timedelta(days=30),
    )
    data = AnalysisInput(
        group_id=group_id,
        group_timezone="Europe/Moscow",
        source_revision=1,
        target_message_ids=[10],
        messages=[
            {
                "telegram_message_id": 10,
                "revision": 1,
                "message_date": now,
                "text": raw.text,
            }
        ],
    )
    validation = ValidationResult(
        source_revision=1,
        raw_proposal_count=1,
        accepted_ordinals=[0],
        accepted=[
            {
                "destination": "task",
                "semantic_kind": "homework",
                "subject": "Математика",
                "title": "Решить задачи",
                "description": "Решить задачи 1–3",
                "deadline": {},
                "audience": {},
                "confidence": 90,
                "evidence": [
                    {
                        "telegram_message_id": 10,
                        "revision": 1,
                        "fact_paths": ["subject", "title", "description"],
                    }
                ],
            }
        ],
        rejected=[],
        adapter_actions={},
        explicit_empty=False,
    )
    run = ShadowEvaluationRun(
        id=run_id,
        group_id=group_id,
        raw_message_id=raw_id,
        source_revision=1,
        analysis_generation=1,
        source_snapshot_sha256="a" * 64,
        engine_version="cplus-shadow-1",
        state="completed",
        input_json=data.model_dump_json(),
        context_result_json=json.dumps({"discussion_facts": []}),
        validation_result_json=validation.model_dump_json(),
        metrics_json=json.dumps({"wave": WAVE}),
        attempts=1,
        prompt_tokens=0,
        completion_tokens=0,
        prompt_cache_hit_tokens=0,
        reserved_usd=Decimal(0),
        charged_usd=Decimal(0),
        created_at=now,
        completed_at=now,
        delete_at=now + timedelta(days=30),
    )
    outbox = BotOutbox(
        id=outbox_id,
        method=bot_admin.SHADOW_EXPORT_METHOD,
        payload=json.dumps(
            {
                "owner_id": OWNER_ID,
                "chat_id": OWNER_ID,
                "group_id": str(group_id),
                "wave": WAVE,
            }
        ),
        available_at=now,
        dedup_key=f"shadow-export:{OWNER_ID}:{uuid.uuid4().hex}",
    )
    async with AsyncSession(engine) as db:
        db.add_all([group, raw, run, outbox])
        await db.commit()
    return group_id, run_id, raw_id, outbox_id


async def application_snapshot(engine, group_id, run_id, raw_id):
    async with AsyncSession(engine) as db:
        raw = await db.get(RawMessage, raw_id)
        run = await db.get(ShadowEvaluationRun, run_id)
        product_counts = {}
        for model in (Homework, AcademicDeadline, Notification, CanonicalAcademicItem):
            product_counts[model.__tablename__] = await db.scalar(
                select(func.count()).select_from(model).where(model.group_id == group_id)
            )
        return {
            "raw": (
                raw.text,
                raw.processing_state,
                raw.analysis_generation,
                raw.revision,
            ),
            "run": (
                run.state,
                run.input_json,
                run.context_result_json,
                run.validation_result_json,
                run.metrics_json,
            ),
            "products": product_counts,
        }


async def cleanup(engine, group_id, outbox_id):
    async with AsyncSession(engine) as db:
        await db.execute(delete(BotOutbox).where(BotOutbox.id == outbox_id))
        await db.execute(delete(Group).where(Group.id == group_id))
        await db.commit()


def settings(url):
    return Settings(
        database_url=url,
        telegram_bot_token="9001:test-token",
        bot_admin_user_id=OWNER_ID,
        deepseek_api_key=SecretStr("must-not-be-used"),
    )


def test_postgres_export_reads_stored_wave_delivers_to_owner_and_rolls_back(
    monkeypatch, caplog, tmp_path
):
    url = os.environ["POSTGRES_TEST_URL"]

    async def scenario():
        engine = create_async_engine(url, poolclass=NullPool)
        group_id, run_id, raw_id, outbox_id = await seed_export(engine)
        try:
            before = await application_snapshot(engine, group_id, run_id, raw_id)
            files_before = {path.relative_to(tmp_path) for path in tmp_path.rglob("*")}
            documents = []

            async def document_request(_settings, **kwargs):
                documents.append(kwargs)
                return {"ok": True, "result": {"message_id": 1}}

            async def forbidden_provider(*args, **kwargs):
                raise AssertionError("DeepSeek must not be called by stored export")

            from studgroup.ai import DeepSeekProvider

            monkeypatch.setattr(bot_admin, "telegram_document_request", document_request)
            monkeypatch.setattr(DeepSeekProvider, "_complete", forbidden_provider, raising=True)
            async with AsyncSession(engine, expire_on_commit=False) as db:
                row = await db.get(BotOutbox, outbox_id)
                await bot_admin.deliver_shadow_export(db, row, settings(url))

            assert len(documents) == 1
            assert documents[0]["chat_id"] == OWNER_ID
            exported = json.loads(gzip.decompress(documents[0]["document"]))
            assert exported["wave"] == WAVE
            assert exported["counts"]["runs"] == 1
            assert exported["counts"]["preview_cards"] == 1
            assert exported["runs"][0]["run_id"] == str(run_id)
            assert PRIVATE_MARKER in json.dumps(exported)
            assert await application_snapshot(engine, group_id, run_id, raw_id) == before
            assert {path.relative_to(tmp_path) for path in tmp_path.rglob("*")} == files_before
            assert PRIVATE_MARKER not in caplog.text
            async with AsyncSession(engine) as db:
                row = await db.get(BotOutbox, outbox_id)
                assert row.state == "sent"
                assert PRIVATE_MARKER not in row.payload
        finally:
            await cleanup(engine, group_id, outbox_id)
            await engine.dispose()

    asyncio.run(scenario())


def test_postgres_export_database_failure_never_sends_partial_document(monkeypatch, caplog):
    url = os.environ["POSTGRES_TEST_URL"]

    async def scenario():
        engine = create_async_engine(url, poolclass=NullPool)
        group_id, run_id, raw_id, outbox_id = await seed_export(engine)
        try:
            before = await application_snapshot(engine, group_id, run_id, raw_id)
            documents = []
            notices = []

            async def fail_export(*args, **kwargs):
                raise OperationalError("SELECT shadow_evaluation_runs", {}, Exception("failed"))

            async def document_request(_settings, **kwargs):
                documents.append(kwargs)
                return {"ok": True}

            async def message_request(_settings, method, payload):
                notices.append((method, payload))
                return {"ok": True}

            from studgroup import export_shadow_wave

            monkeypatch.setattr(export_shadow_wave, "export_wave", fail_export)
            monkeypatch.setattr(bot_admin, "telegram_document_request", document_request)
            monkeypatch.setattr(bot_admin, "telegram_request", message_request)
            async with AsyncSession(engine, expire_on_commit=False) as db:
                row = await db.get(BotOutbox, outbox_id)
                await bot_admin.deliver_shadow_export(db, row, settings(url))

            assert documents == []
            assert notices == [
                (
                    "sendMessage",
                    {
                        "chat_id": OWNER_ID,
                        "text": (
                            "Экспорт не отправлен: сохранённые данные не удалось "
                            "безопасно подготовить."
                        ),
                    },
                )
            ]
            assert await application_snapshot(engine, group_id, run_id, raw_id) == before
            assert PRIVATE_MARKER not in caplog.text
            async with AsyncSession(engine) as db:
                row = await db.get(BotOutbox, outbox_id)
                assert row.state == "failed"
                assert PRIVATE_MARKER not in row.payload
        finally:
            await cleanup(engine, group_id, outbox_id)
            await engine.dispose()

    asyncio.run(scenario())
