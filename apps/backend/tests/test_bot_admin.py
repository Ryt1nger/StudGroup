import asyncio
import json
from datetime import UTC, datetime

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


def test_explain_shows_context_and_model_answers(client, monkeypatch):
    from test_ai_schedule import seed
    from test_batch_reports import at

    from studgroup import explain as explain_module

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
    assert "ИИ получил" in text and "Глубокий проход ответил" in text
    private(client, "/explain abc", update=103)
    assert "Формат" in session(client)[1][-1]["text"]
