import asyncio
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from test_ingestion import send

from studgroup.models import BotAdminAudit, BotAdminSession, BotOutbox, Membership, User

pytest_plugins = ["test_schedule_api"]


def private(client, text, uid=9001, update=100, shared=None, callback=None):
    client.app.state.settings.bot_admin_user_id = 9001
    message = {
        "message_id": 1,
        "date": int(datetime.now(UTC).timestamp()),
        "chat": {"id": uid, "type": "private"},
        "from": {"id": uid},
        "text": text,
    }
    if shared:
        message["users_shared"] = shared
        message.pop("text")
    body = {"update_id": update, "message": message}
    if callback:
        body = {
            "update_id": update,
            "callback_query": {
                "id": str(update),
                "from": {"id": uid},
                "message": message,
                "data": callback,
            },
        }
    assert send(client, body).status_code == 200


def session(client):
    async def read():
        async with AsyncSession(client.app.state.engine, expire_on_commit=False) as db:
            s = await db.get(BotAdminSession, 9001)
            messages = (
                await db.scalars(
                    select(BotOutbox)
                    .where(BotOutbox.method.in_(["sendMessage", "renderPanel", "panelAux"]))
                    .order_by(BotOutbox.available_at)
                )
            ).all()
            return s, [json.loads(r.payload) for r in messages]

    return asyncio.run(read())


def test_admin_id_flow_requires_confirmation_and_is_idempotent(client):
    private(client, "/admin")
    s, msg = session(client)
    group = msg[-1]["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
    private(client, "", update=101, callback=group)
    s, _ = session(client)
    private(client, "", update=102, callback=f"a:{s.nonce}:id")
    private(client, "123456789", update=103)
    s, _ = session(client)

    async def count():
        async with AsyncSession(client.app.state.engine) as db:
            return await db.scalar(
                select(func.count(User.id)).where(User.telegram_user_id == 123456789)
            )

    assert asyncio.run(count()) == 0
    command = f"a:{s.nonce}:yes"
    private(client, "", update=104, callback=command)
    private(client, "", update=104, callback=command)
    private(client, "", update=105, callback=command)
    assert asyncio.run(count()) == 1

    async def check():
        async with AsyncSession(client.app.state.engine) as db:
            assert await db.scalar(select(func.count(BotAdminAudit.id))) == 1
            u = await db.scalar(select(User).where(User.telegram_user_id == 123456789))
            m = await db.scalar(select(Membership).where(Membership.user_id == u.id))
            assert (m.role, m.status) == ("student", "active")

    asyncio.run(check())


def test_nonowner_has_no_admin_session_or_group_list(client):
    private(client, "/admin", uid=2)
    s, msg = session(client)
    assert s is None
    assert "недоступна" in msg[-1]["text"]
    assert msg[-1].get("reply_markup", {}).get("inline_keyboard", []) == []


def test_nonowner_cannot_read_shadow_status(client):
    private(client, "/shadowstatus", uid=2)
    s, messages = session(client)
    assert s is None
    assert messages == []


def test_native_picker_is_bound_to_its_request(client):
    private(client, "/admin")
    _, msg = session(client)
    private(
        client,
        "",
        update=101,
        callback=msg[-1]["reply_markup"]["inline_keyboard"][0][0]["callback_data"],
    )
    s, _ = session(client)
    private(client, "", update=102, callback=f"a:{s.nonce}:pick")
    s, msg = session(client)
    assert msg[-1]["reply_markup"]["keyboard"][0][0]["request_users"]["user_is_bot"] is False
    private(
        client, "", update=103, shared={"request_id": s.request_id + 1, "users": [{"user_id": 123}]}
    )
    assert session(client)[0].step == "pick"
    private(
        client, "", update=104, shared={"request_id": s.request_id, "users": [{"user_id": 123}]}
    )
    assert session(client)[0].step == "confirm"


def test_resetrun_needs_confirmation_and_clears_only_ai_artefacts(client):
    from test_ai_schedule import seed
    from test_batch_reports import at, config
    from test_live_two_pass import CascadeProvider

    from studgroup.models import AIAttempt, AIJob, AIRun, GroupAIProfile, RawMessage
    from studgroup.processing import process_next

    seed(client, at("06:55:00"), mid=1)
    asyncio.run(
        process_next(client.app.state.engine, config(), CascadeProvider(), now=at("07:00:00"))
    )

    async def counts():
        async with AsyncSession(client.app.state.engine) as db:
            return tuple(
                [
                    await db.scalar(select(func.count()).select_from(m))
                    for m in (AIRun, AIJob, AIAttempt, RawMessage)
                ]
            )

    before = asyncio.run(counts())
    assert before[0] == 1 and before[1] == 1 and before[2] >= 1
    private(client, "/st", update=99)  # previous headman route must not swallow it
    private(client, "/resetrun")
    s, msg = session(client)
    assert "Будет удалено" in msg[-1]["text"]
    assert asyncio.run(counts()) == before  # nothing deleted before confirmation
    private(client, "", update=101, callback=f"a:{s.nonce}:reset")
    runs, jobs, attempts, raws = asyncio.run(counts())
    assert (runs, jobs, attempts) == (0, 0, 0)
    assert raws == before[3]

    async def state():
        async with AsyncSession(client.app.state.engine) as db:
            raw = await db.scalar(select(RawMessage))
            profile = await db.scalar(select(GroupAIProfile))
            return raw.processing_state, profile.state

    assert asyncio.run(state()) == ("pending", "mapping")
    private(client, "", update=102, callback=f"a:{s.nonce}:reset")  # stale button: no effect
    assert asyncio.run(counts())[:3] == (0, 0, 0)


def test_shadowrun_requires_confirmation_and_only_opens_read_only_replay(client):
    from test_ai_schedule import seed
    from test_batch_reports import at

    from studgroup.models import (
        Group,
        GroupAnalysisModeHistory,
        GroupAnalysisSettings,
        Homework,
        ShadowEvaluationRun,
    )

    seed(client, at("06:55:00"), mid=1)

    async def state():
        async with AsyncSession(client.app.state.engine) as db:
            group = await db.scalar(select(Group).where(Group.pilot_authorized.is_(True)))
            settings = await db.get(GroupAnalysisSettings, group.id)
            return (
                settings,
                await db.scalar(select(func.count(Homework.id))),
                await db.scalar(select(func.count(ShadowEvaluationRun.id))),
                await db.scalar(select(func.count(GroupAnalysisModeHistory.id))),
                group.bot_added_at,
            )

    before = asyncio.run(state())
    private(client, "/shadowrun 0.25 100")
    s, msg = session(client)
    assert "C+ не создаёт" in msg[-1]["text"]
    assert asyncio.run(state())[:4] == before[:4]

    private(client, "", update=101, callback=f"a:{s.nonce}:shadow:0.25:100")
    configured, homework, runs, history, bot_added_at = asyncio.run(state())
    assert configured.analysis_mode == "shadow"
    assert configured.shadow_daily_budget_usd == Decimal("0.25")
    assert configured.shadow_sample_percent == 100
    assert configured.updated_at == bot_added_at
    assert (homework, runs, history) == (before[1], 0, 1)

    private(client, "/shadowstatus", update=102)
    status_text = session(client)[1][-1]["text"]
    assert "Режим: shadow" in status_text
    assert "Ошибки по этапам:" in status_text
    assert "Коды причин:" in status_text


def test_shadowstatus_reports_latest_completed_aggregates_without_private_content(client):
    from studgroup.models import Group, ShadowEvaluationRun

    async def seed_completed():
        async with AsyncSession(client.app.state.engine) as db:
            group = await db.scalar(select(Group).where(Group.pilot_authorized.is_(True)))
            now = datetime.now(UTC)
            db.add(
                ShadowEvaluationRun(
                    group_id=group.id,
                    batch_id=None,
                    job_id=None,
                    raw_message_id=None,
                    source_revision=7,
                    analysis_generation=None,
                    source_snapshot_sha256="a" * 64,
                    engine_version="cplus-shadow-1",
                    state="completed",
                    input_json=json.dumps(
                        {
                            "messages": [
                                {"text": "PRIVATE MESSAGE ONE"},
                                {"text": "PRIVATE MESSAGE TWO"},
                            ]
                        }
                    ),
                    memory_json="[]",
                    context_result_json='{"discussion_facts": []}',
                    validation_result_json=json.dumps(
                        {
                            "accepted": [{"title": "PRIVATE TITLE"}],
                            "rejected": [
                                {
                                    "proposal_ordinal": 2,
                                    "reason_code": "proposal_schema_invalid",
                                    "schema_issues": [
                                        {
                                            "path": "proposals[2].evidence[0].fact_paths",
                                            "error_type": "missing",
                                            "expected": "required_field",
                                            "received_type": "missing",
                                        }
                                    ],
                                },
                                {"proposal_ordinal": 3, "reason_code": "missing_evidence"},
                            ],
                            "raw_proposal_count": 4,
                        }
                    ),
                    provider_payload_hashes="[]",
                    metrics_json=json.dumps(
                        {"accepted_items": 2, "rejected_items": 2, "raw_proposal_count": 4}
                    ),
                    attempts=1,
                    available_at=now,
                    lease_until=None,
                    last_error=None,
                    prompt_tokens=321,
                    completion_tokens=123,
                    prompt_cache_hit_tokens=0,
                    reserved_usd=Decimal(0),
                    charged_usd=Decimal("0.00248550"),
                    latency_ms=100,
                    created_at=now,
                    completed_at=now,
                    delete_at=now + timedelta(days=30),
                )
            )
            await db.commit()

    asyncio.run(seed_completed())
    private(client, "/shadowstatus", update=102)
    text = session(client)[1][-1]["text"]
    assert "Последний shadow_completed:" in text
    assert "Сообщений: 2; найдено AI-сущностей: 4" in text
    assert "Принято: 2; отклонено: 2" in text
    assert "Причины отклонений: missing_evidence: 1, proposal_schema_invalid: 1" in text
    assert "Структурные ошибки:" in text
    assert "#2 proposals[2].evidence[0].fact_paths: missing" in text
    assert "ожидалось=required_field; получено=missing" in text
    assert "AI-проход 1: completed; AI-проход 2: completed" in text
    assert "Токены canary: вход 321, выход 123; расход: $0.00248550" in text
    assert "PRIVATE" not in text


def test_explain_shows_context_and_model_answers(client, monkeypatch):
    from pydantic import SecretStr
    from test_ai_schedule import seed
    from test_batch_reports import at

    from studgroup import explain as explain_module

    client.app.state.settings.deepseek_api_key = SecretStr("test-key-not-a-secret")
    seed(client, at("06:55:00"), mid=7)

    async def fake_complete(self, payload):
        screening = payload["max_tokens"] == 250
        content = (
            '{"signal":true,"source_message_ids":[7]}'
            if screening
            else '{"assignments":[],"online_lessons":[]}'
        )
        return {
            "model": "fake",
            "choices": [{"message": {"content": content}, "finish_reason": "stop"}],
            "usage": {
                "prompt_tokens": 1,
                "completion_tokens": 1,
                "total_tokens": 2,
                "prompt_cache_hit_tokens": 0,
                "prompt_cache_miss_tokens": 1,
            },
        }

    monkeypatch.setattr(explain_module.DeepSeekProvider, "_complete", fake_complete, raising=True)
    private(client, "/explain 7")
    _, msg = session(client)
    text = msg[-1]["text"]
    assert "ИИ получил" in text and "Глубокий проход (сырой)" in text
    assert "Итог backend" in text and "Исправления backend" in text
    private(client, "/explain abc", update=103)
    assert "Формат" in session(client)[1][-1]["text"]


def test_explain_without_key_reports_configuration_error_without_calling_api(client, monkeypatch):
    from pydantic import SecretStr
    from test_ai_schedule import seed
    from test_batch_reports import at

    from studgroup import explain as explain_module

    client.app.state.settings.deepseek_api_key = SecretStr("")
    seed(client, at("06:55:00"), mid=7)

    async def unexpected_request(*args, **kwargs):
        raise AssertionError("An unconfigured provider must not call the API")

    monkeypatch.setattr(explain_module.DeepSeekProvider, "_complete", unexpected_request)
    private(client, "/explain 7")
    assert "Ошибка ИИ: provider_not_configured" in session(client)[1][-1]["text"]


def _sample_card():
    return {
        "run_id": "r",
        "ordinal": 0,
        "target_message_id": 4029,
        "promoted": False,
        "visibility": "held",
        "attention_reasons": ["conflicting_deadlines"],
        "action": "create",
        "kind": "homework",
        "subject": "Математический анализ",
        "title": "Предел функции в бесконечности",
        "description": "Подготовить тему.",
        "deadline_at": datetime(2026, 10, 15, 20, 59, tzinfo=UTC),
        "deadline_date_only": False,
        "deadline_basis": "explicit",
        "audience": {"scope": "group", "label": None},
        "sources": [
            {
                "telegram_message_id": 4029,
                "message_date": datetime(2026, 10, 10, 9, 0, tzinfo=UTC),
                "text": "#матан - предел функции в бесконечности",
                "fact_paths": ["title"],
            }
        ],
    }


def _mark_bot_added(client):
    from studgroup.models import Group

    async def mark():
        async with AsyncSession(client.app.state.engine) as db:
            group = await db.scalar(select(Group).where(Group.pilot_authorized.is_(True)))
            group.bot_added_at = datetime(2026, 10, 8, 15, 25, tzinfo=UTC)
            await db.commit()

    asyncio.run(mark())


def test_shadowcards_goes_only_to_the_owner_with_sources_and_links(client, monkeypatch):
    from studgroup.analysis import backfill

    _mark_bot_added(client)

    async def fake_review(db, **kwargs):
        assert kwargs["limit"] in {5, 10}
        return [_sample_card()] * kwargs["limit"]

    monkeypatch.setattr(backfill, "review", fake_review)
    private(client, "/shadowcards", uid=2)
    assert session(client)[1] == []  # a stranger gets neither cards nor an error with content
    private(client, "/shadowcards", update=103)
    texts = [m["text"] for m in session(client)[1]]
    assert texts[0].startswith("Принятые shadow-карточки")
    assert "студентам не опубликованы" in texts[0]
    body = "\n".join(texts[1:])
    assert "#матан - предел функции в бесконечности" in body
    assert "conflicting_deadlines" in body and "№4029" in body
    assert "https://t.me/c/" in body and "/4029" in body
    assert "№1–5" in texts[0] and "/shadowcards 5" in texts[0]
    private(client, "/shadowcards 5", update=105)
    assert any("№6–10" in m["text"] for m in session(client)[1])


def test_shadowcards_reports_empty_without_inventing_cards(client):
    _mark_bot_added(client)
    private(client, "/shadowcards", update=104)
    assert "пока нет" in session(client)[1][-1]["text"]
