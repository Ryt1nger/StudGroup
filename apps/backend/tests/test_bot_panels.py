import asyncio
import json
from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from test_headman import foreman as foreman  # noqa: PLC0414 -- expose imported pytest fixture
from test_headman import run
from test_ingestion import send

from studgroup import bot_admin
from studgroup.models import BotOutbox, BotPanel

pytest_plugins = ["test_schedule_api"]


@pytest.fixture
def transport(foreman, monkeypatch):
    client = foreman
    client.app.state.settings.bot_admin_user_id = 1
    client.app.state.settings.telegram_bot_token = "fake-no-network"
    calls = []
    seq = 1000

    async def request(settings, method, data):
        nonlocal seq
        calls.append((method, dict(data)))
        if method == "sendMessage":
            seq += 1
            return {"ok": True, "result": {"message_id": seq}}
        return {"ok": True, "result": True}

    monkeypatch.setattr(bot_admin, "telegram_request", request)
    return client, calls


def input_message(client, ident, text="", callback=None, message_id=None):
    msg = {
        "message_id": message_id or ident,
        "date": int(datetime.now(UTC).timestamp()),
        "chat": {"id": 1, "type": "private"},
        "from": {"id": 1},
        "text": text,
    }
    value = {"update_id": ident, "message": msg}
    if callback:
        value = {
            "update_id": ident,
            "callback_query": {
                "id": str(ident),
                "from": {"id": 1},
                "message": msg,
                "data": callback,
            },
        }
    assert send(client, value).status_code == 200


def drain(client):
    async def send_all():
        for _ in range(100):
            async with AsyncSession(client.app.state.engine) as db:
                pending = await db.scalar(
                    select(func.count()).select_from(BotOutbox).where(BotOutbox.state == "pending")
                )
            if not pending:
                return
            await bot_admin.deliver(client.app.state.engine, client.app.state.settings)
        raise AssertionError("outbox did not drain")

    asyncio.run(send_all())


def current(client, mode):
    async def read(db):
        owner = await db.scalar(
            select(BotPanel).where(BotPanel.chat_id == 1, BotPanel.mode == mode)
        )
        row = await db.scalar(
            select(BotOutbox)
            .where(BotOutbox.panel_id == owner.id, BotOutbox.method == "renderPanel")
            .order_by(BotOutbox.available_at.desc())
            .limit(1)
        )
        return owner.message_id, json.loads(row.payload)

    return run(client, read)


def press(client, mode, label, ident):
    mid, data = current(client, mode)
    button = next(
        b for r in data["reply_markup"]["inline_keyboard"] for b in r if label in b["text"]
    )
    input_message(client, ident, callback=button["callback_data"], message_id=mid)
    drain(client)


def test_panels_edit_only_their_own_message_and_start_is_separate(transport):
    client, calls = transport
    input_message(client, 100, "/admin")
    drain(client)
    admin_mid, _ = current(client, "admin")
    input_message(client, 101, "/st")
    drain(client)
    st_mid, _ = current(client, "st")
    assert st_mid != admin_mid
    press(client, "st", "Группа", 102)
    press(client, "st", "ДЗ и КТ", 103)
    assert [d["message_id"] for m, d in calls if m == "editMessageText"] == [st_mid, st_mid]
    input_message(client, 104, "/start")
    drain(client)
    start_mid = 1003
    input_message(client, 105, "/st")
    drain(client)
    deleted = {d["message_id"] for m, d in calls if m == "deleteMessage"}
    assert st_mid in deleted and 101 in deleted
    assert admin_mid not in deleted and 100 not in deleted
    assert start_mid not in deleted and 104 not in deleted
    assert current(client, "st")[0] != st_mid


def test_latest_callback_controls_where_text_input_goes(transport):
    client, calls = transport
    input_message(client, 200, "/st")
    drain(client)
    press(client, "st", "Группа", 201)
    press(client, "st", "ДЗ и КТ", 202)
    press(client, "st", "Добавить ДЗ", 203)  # ST awaits subject text.
    input_message(client, 204, "/admin")
    drain(client)
    press(client, "admin", "Группа", 205)
    press(client, "admin", "Добавить по ID", 206)  # Admin also awaits text.
    before = len(calls)
    input_message(client, 207, "123456789")
    drain(client)
    admin_mid, data = current(client, "admin")
    assert "123456789" in data["text"]
    assert any(m == "editMessageText" and d["message_id"] == admin_mid for m, d in calls[before:])
    # Restore ST focus by clicking its still-active back button.
    press(client, "st", "Главное меню", 208)
    press(client, "st", "ДЗ и КТ", 209)
    press(client, "st", "Добавить ДЗ", 210)
    input_message(client, 211, "Математика")
    drain(client)
    assert "Название задания" in current(client, "st")[1]["text"]
    assert current(client, "admin")[0] == admin_mid


def test_restart_supersedes_queued_old_frames(transport):
    client, calls = transport
    input_message(client, 300, "/st")
    input_message(client, 301, "/st")
    drain(client)
    assert sum(m == "sendMessage" for m, _ in calls) == 1
    assert any(m == "deleteMessage" and d["message_id"] == 300 for m, d in calls)


def test_uneditable_panel_replacement_is_scoped(transport, monkeypatch):
    client, _calls = transport
    input_message(client, 400, "/st")
    drain(client)
    old, _ = current(client, "st")
    original = bot_admin.telegram_request

    async def missing(settings, method, data):
        if method == "editMessageText":
            return {
                "ok": False,
                "error_code": 400,
                "description": "Bad Request: message to edit not found",
            }
        return await original(settings, method, data)

    monkeypatch.setattr(bot_admin, "telegram_request", missing)
    press(client, "st", "Группа", 401)
    assert current(client, "st")[0] != old


def test_old_undeletable_panel_has_its_inline_buttons_disabled(transport, monkeypatch):
    client, calls = transport
    input_message(client, 500, "/st")
    drain(client)
    old, _ = current(client, "st")
    original = bot_admin.telegram_request

    async def limited(settings, method, data):
        if method == "deleteMessage":
            return {
                "ok": False,
                "error_code": 400,
                "description": "Bad Request: message can't be deleted",
            }
        return await original(settings, method, data)

    monkeypatch.setattr(bot_admin, "telegram_request", limited)
    input_message(client, 501, "/st")
    drain(client)
    assert any(
        method == "editMessageReplyMarkup"
        and data["message_id"] == old
        and data["reply_markup"] == {"inline_keyboard": []}
        for method, data in calls
    )
