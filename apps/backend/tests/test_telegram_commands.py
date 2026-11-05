"""Telegram's slash-command menu is intentionally smaller than the button UI."""

import asyncio
from types import SimpleNamespace

from studgroup import telegram_commands


def test_command_menu_only_exposes_start_to_students_and_admin_to_owner(monkeypatch):
    calls = []

    async def request(_settings, method, payload):
        calls.append((method, payload))
        return {"ok": True}

    monkeypatch.setattr(telegram_commands, "telegram_request", request)
    settings = SimpleNamespace(telegram_bot_token="fake", bot_admin_user_id=9001)
    assert asyncio.run(telegram_commands.configure(settings))
    assert [call[0] for call in calls] == ["setMyCommands", "setMyCommands"]
    assert [c["command"] for c in calls[0][1]["commands"]] == ["start"]
    assert [c["command"] for c in calls[1][1]["commands"]] == ["start", "admin"]
    assert calls[1][1]["scope"] == {"type": "chat", "chat_id": 9001}
