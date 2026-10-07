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
                    .where(BotOutbox.method == "sendMessage")
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
    assert "reply_markup" not in msg[-1]


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
