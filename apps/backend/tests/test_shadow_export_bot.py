import asyncio
import gzip
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from test_bot_admin import private, session
from test_ingestion import send

from studgroup.analysis.contracts import AnalysisInput, ValidationResult
from studgroup.models import BotOutbox, Group, RawMessage, ShadowEvaluationRun

pytest_plugins = ["test_schedule_api"]

PRIVATE_MARKER = "PRIVATE SYNTHETIC MESSAGE"


def seed_wave(client, *, group_chat_id=-1001, wave="wave-a-spread-1", description=None):
    now = datetime(2026, 10, 12, 12, tzinfo=UTC)

    async def seed():
        async with AsyncSession(client.app.state.engine, expire_on_commit=False) as db:
            group = await db.scalar(select(Group).where(Group.telegram_chat_id == group_chat_id))
            group.bot_added_at = now - timedelta(days=1)
            raw = RawMessage(
                id=uuid4(),
                group_id=group.id,
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
                group_id=group.id,
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
                        "description": description or "Решить задачи 1–3",
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
            db.add_all(
                [
                    raw,
                    ShadowEvaluationRun(
                        id=uuid4(),
                        group_id=group.id,
                        raw_message_id=raw.id,
                        source_revision=1,
                        analysis_generation=1,
                        source_snapshot_sha256="a" * 64,
                        engine_version="cplus-shadow-1",
                        state="completed",
                        input_json=data.model_dump_json(),
                        context_result_json=json.dumps({"discussion_facts": []}),
                        validation_result_json=validation.model_dump_json(),
                        metrics_json=json.dumps({"wave": wave}),
                        attempts=1,
                        prompt_tokens=0,
                        completion_tokens=0,
                        prompt_cache_hit_tokens=0,
                        reserved_usd=Decimal(0),
                        charged_usd=Decimal(0),
                        created_at=now,
                        completed_at=now,
                        delete_at=now + timedelta(days=30),
                    ),
                ]
            )
            await db.commit()
            return group.id

    return asyncio.run(seed())


def export_rows(client):
    async def read():
        async with AsyncSession(client.app.state.engine, expire_on_commit=False) as db:
            return list(
                (
                    await db.scalars(
                        select(BotOutbox).where(BotOutbox.method == "sendShadowExport")
                    )
                ).all()
            )

    return asyncio.run(read())


def confirmation(client, *, update=100):
    private(client, "/shadowexport", update=update)
    current, messages = session(client)
    callback = messages[-1]["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
    return current, callback


def confirm(client, callback, *, update=101):
    private(client, "", update=update, callback=callback)


def mark_nonexport_outbox_sent(client):
    async def mark():
        async with AsyncSession(client.app.state.engine) as db:
            rows = (
                await db.scalars(select(BotOutbox).where(BotOutbox.method != "sendShadowExport"))
            ).all()
            for row in rows:
                row.state = "sent"
            await db.commit()

    asyncio.run(mark())


def application_snapshot(client):
    async def read():
        async with AsyncSession(client.app.state.engine) as db:
            raw = await db.scalar(select(RawMessage))
            run = await db.scalar(select(ShadowEvaluationRun))
            return {
                "raw_count": await db.scalar(select(func.count(RawMessage.id))),
                "run_count": await db.scalar(select(func.count(ShadowEvaluationRun.id))),
                "raw": (raw.text, raw.processing_state, raw.analysis_generation),
                "run": (
                    run.state,
                    run.input_json,
                    run.context_result_json,
                    run.validation_result_json,
                    run.metrics_json,
                ),
            }

    return asyncio.run(read())


def test_owner_confirmation_delivers_private_in_memory_export_without_ai_or_mutation(
    client, monkeypatch, caplog, tmp_path
):
    from studgroup import bot_admin
    from studgroup.ai import DeepSeekProvider

    group_id = seed_wave(client)
    client.app.state.settings.telegram_bot_token = "9001:test-token"
    client.app.state.settings.deepseek_api_key = SecretStr("must-not-be-used")
    before = application_snapshot(client)
    _admin_session, callback = confirmation(client)
    confirm(client, callback)
    queued = export_rows(client)
    assert len(queued) == 1
    assert json.loads(queued[0].payload) == {
        "owner_id": 9001,
        "chat_id": 9001,
        "group_id": str(group_id),
        "wave": "wave-a-spread-1",
    }
    assert PRIVATE_MARKER not in queued[0].payload
    mark_nonexport_outbox_sent(client)
    files_before_delivery = {path.relative_to(tmp_path) for path in tmp_path.rglob("*")}

    async def forbidden_provider(*args, **kwargs):
        raise AssertionError("DeepSeek must not be called by stored export")

    sent = []

    async def document_request(_settings, **kwargs):
        sent.append(kwargs)
        return {"ok": True, "result": {"message_id": 1}}

    monkeypatch.setattr(DeepSeekProvider, "_complete", forbidden_provider, raising=True)
    monkeypatch.setattr(bot_admin, "telegram_document_request", document_request)
    asyncio.run(bot_admin.deliver(client.app.state.engine, client.app.state.settings))

    assert len(sent) == 1
    assert sent[0]["chat_id"] == client.app.state.settings.bot_admin_user_id == 9001
    assert sent[0]["filename"].endswith(".json.gz")
    exported = json.loads(gzip.decompress(sent[0]["document"]))
    assert exported["wave"] == "wave-a-spread-1"
    assert exported["counts"]["runs"] == 1
    assert PRIVATE_MARKER in json.dumps(exported)
    assert export_rows(client)[0].state == "sent"
    assert application_snapshot(client) == before
    assert PRIVATE_MARKER not in caplog.text
    assert {path.relative_to(tmp_path) for path in tmp_path.rglob("*")} == files_before_delivery


def test_shadow_export_rejects_nonowner_and_group_chat(client):
    seed_wave(client)
    private(client, "/shadowexport", uid=2)
    assert export_rows(client) == []

    payload = {
        "update_id": 200,
        "message": {
            "message_id": 99,
            "date": int(datetime.now(UTC).timestamp()),
            "chat": {"id": -1001, "type": "supergroup"},
            "from": {"id": 9001},
            "text": "/shadowexport",
        },
    }
    assert send(client, payload).status_code == 200
    assert export_rows(client) == []


def test_shadow_export_rejects_invalid_expired_and_used_nonce(client):
    seed_wave(client)
    current, callback = confirmation(client)
    confirm(client, f"a:{'0' * len(current.nonce)}:shadow_export", update=101)
    assert export_rows(client) == []

    async def expire():
        async with AsyncSession(client.app.state.engine) as db:
            row = await db.get(type(current), 9001)
            row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
            await db.commit()

    asyncio.run(expire())
    confirm(client, callback, update=102)
    assert export_rows(client) == []

    _current, fresh = confirmation(client, update=103)
    confirm(client, fresh, update=104)
    confirm(client, fresh, update=105)
    assert len(export_rows(client)) == 1


def test_shadow_export_refuses_parallel_request_and_wrong_group(client):
    wave_group_id = seed_wave(client)
    current, callback = confirmation(client)

    async def swap_group():
        async with AsyncSession(client.app.state.engine) as db:
            other = await db.scalar(select(Group).where(Group.id != wave_group_id))
            row = await db.get(type(current), 9001)
            row.group_id = other.id
            await db.commit()

    asyncio.run(swap_group())
    confirm(client, callback)
    assert export_rows(client) == []

    _current, valid = confirmation(client, update=102)
    confirm(client, valid, update=103)
    assert len(export_rows(client)) == 1
    private(client, "/shadowexport", update=104)
    assert len(export_rows(client)) == 1
    assert "уже формируется" in session(client)[1][-1]["text"]


def test_shadow_export_enqueue_is_atomic_for_concurrent_confirmation(client):
    from studgroup.bot_admin import enqueue_once

    async def race():
        payload = {
            "owner_id": 9001,
            "chat_id": 9001,
            "group_id": str(uuid4()),
            "wave": "wave-a-spread-1",
        }

        async def attempt():
            async with AsyncSession(client.app.state.engine) as db:
                inserted = await enqueue_once(
                    db, "sendShadowExport", payload, "shadow-export:9001:same-confirmation"
                )
                await db.commit()
                return inserted

        return await asyncio.gather(attempt(), attempt())

    assert sorted(asyncio.run(race())) == [False, True]
    assert len(export_rows(client)) == 1


def test_shadow_export_reports_missing_wave_without_document(client):
    private(client, "/shadowexport")
    assert export_rows(client) == []
    assert "не найдена" in session(client)[1][-1]["text"]


def test_shadow_export_rejects_wave_spanning_multiple_groups(client):
    seed_wave(client, group_chat_id=-1001)
    seed_wave(client, group_chat_id=-1002)
    private(client, "/shadowexport")
    assert export_rows(client) == []
    assert "нельзя определить однозначно" in session(client)[1][-1]["text"]


def test_shadow_export_size_limit_fails_without_document_or_private_logging(
    client, monkeypatch, caplog
):
    from studgroup import bot_admin

    seed_wave(client, description="x" * 1000)
    client.app.state.settings.telegram_bot_token = "9001:test-token"
    _current, callback = confirmation(client)
    confirm(client, callback)
    mark_nonexport_outbox_sent(client)
    monkeypatch.setattr(bot_admin, "SHADOW_EXPORT_MAX_STORED_BYTES", 10)
    documents = []
    notices = []

    async def document_request(*args, **kwargs):
        documents.append(kwargs)
        return {"ok": True}

    async def message_request(_settings, method, payload):
        notices.append((method, payload))
        return {"ok": True}

    monkeypatch.setattr(bot_admin, "telegram_document_request", document_request)
    monkeypatch.setattr(bot_admin, "telegram_request", message_request)
    asyncio.run(bot_admin.deliver(client.app.state.engine, client.app.state.settings))

    assert documents == []
    assert notices == [
        (
            "sendMessage",
            {
                "chat_id": 9001,
                "text": "Экспорт не отправлен: сохранённые данные превышают безопасный лимит.",
            },
        )
    ]
    assert export_rows(client)[0].state == "failed"
    assert PRIVATE_MARKER not in caplog.text


def test_shadow_export_rejects_tampered_recipient_and_never_retries_uncertain_upload(
    client, monkeypatch
):
    import httpx

    from studgroup import bot_admin

    seed_wave(client)
    client.app.state.settings.telegram_bot_token = "9001:test-token"
    _current, callback = confirmation(client)
    confirm(client, callback)
    mark_nonexport_outbox_sent(client)

    async def tamper():
        async with AsyncSession(client.app.state.engine) as db:
            row = await db.scalar(select(BotOutbox).where(BotOutbox.method == "sendShadowExport"))
            payload = json.loads(row.payload)
            payload["chat_id"] = 2
            row.payload = json.dumps(payload)
            await db.commit()

    calls = []

    async def document_request(*args, **kwargs):
        calls.append(kwargs)
        raise httpx.ReadTimeout("ambiguous")

    asyncio.run(tamper())
    monkeypatch.setattr(bot_admin, "telegram_document_request", document_request)
    asyncio.run(bot_admin.deliver(client.app.state.engine, client.app.state.settings))
    assert calls == []
    assert export_rows(client)[0].state == "failed"

    # A fresh confirmed request may run, but an ambiguous send is marked failed and
    # the delivery loop cannot select it again.
    _current, fresh = confirmation(client, update=102)
    confirm(client, fresh, update=103)
    mark_nonexport_outbox_sent(client)
    asyncio.run(bot_admin.deliver(client.app.state.engine, client.app.state.settings))
    assert len(calls) == 1
    asyncio.run(bot_admin.deliver(client.app.state.engine, client.app.state.settings))
    assert len(calls) == 1


def test_shadow_export_failure_reason_is_logged_without_private_text():
    from studgroup.bot_admin import shadow_export_reason
    from studgroup.export_shadow_wave import ShadowWaveUnavailable

    assert shadow_export_reason(ShadowWaveUnavailable("shadow_wave_not_found")) == (
        "shadow_wave_not_found"
    )
    assert shadow_export_reason(ValueError("shadow_export_json_too_large")) == (
        "shadow_export_json_too_large"
    )
    # anything else may carry stored message text: only the exception type is exposed
    assert shadow_export_reason(ValueError("секретный текст сообщения")) == "ValueError"
    assert shadow_export_reason(KeyError("secret")) == "KeyError"
