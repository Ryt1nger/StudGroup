import asyncio
import json

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from test_bot_admin import private

from studgroup import bot_info
from studgroup.models import BotOutbox

pytest_plugins = ["test_schedule_api"]


def sent(client):
    async def read():
        async with AsyncSession(client.app.state.engine) as db:
            rows = (
                await db.scalars(
                    select(BotOutbox)
                    .where(BotOutbox.method == "sendMessage")
                    .order_by(BotOutbox.available_at)
                )
            ).all()
            return [json.loads(r.payload) for r in rows]

    return asyncio.run(read())


@pytest.mark.parametrize(
    ("text", "topic"),
    [
        ("/start tariffs", "tariffs"),
        ("/start howto", "howto"),
        ("/start help", "howto"),
        ("/start connect", "connect"),
        ("/start faq", "faq"),
        ("/start support", "support"),
        ("/start@studgroup_rf_bot FAQ", "faq"),
        ("/tariffs", "tariffs"),
        ("/help", "howto"),
        ("/help@studgroup_rf_bot", "howto"),
        ("/connect", "connect"),
        ("/support", "support"),
        # Not info topics: must keep falling through to the existing flows.
        ("/start", None),
        ("/start invite_abc123", None),
        ("/start unknown", None),
        ("/start tariffs extra", None),
        ("/st", None),
        ("/admin", None),
        ("hello", None),
        ("", None),
    ],
)
def test_topic_for(text, topic):
    assert bot_info.topic_for(text) == topic


def panel_frames(client):
    async def read():
        async with AsyncSession(client.app.state.engine) as db:
            rows = (
                await db.scalars(
                    select(BotOutbox)
                    .where(BotOutbox.method == "renderPanel")
                    .order_by(BotOutbox.available_at)
                )
            ).all()
            return [json.loads(r.payload) for r in rows]

    return asyncio.run(read())


def buttons(frame):
    return [b for row in frame["reply_markup"]["inline_keyboard"] for b in row]


@pytest.mark.parametrize(
    ("text", "needle"),
    [
        ("/start tariffs", "Тарифы"),
        ("/start howto", "Как пользоваться"),
        ("/start connect", "Подключение группы"),
        ("/start faq", "Частые вопросы"),
        ("/start support", "Поддержка"),
        ("/tariffs", "Тарифы"),
        ("/faq", "Частые вопросы"),
    ],
)
def test_topics_open_on_the_start_panel_for_any_private_user(client, text, needle):
    private(client, text, uid=2)  # not the bot admin
    assert sent(client) == []  # no loose message: the panel is the only thing sent
    frame = panel_frames(client)[-1]
    assert frame["chat_id"] == 2 and needle in frame["text"]
    assert "Команды:" not in frame["text"]
    assert "‹ Назад" in [b["text"] for b in buttons(frame)]


@pytest.mark.parametrize("topic", ["tariffs", "howto", "connect", "faq", "support"])
def test_no_topic_asks_people_to_type_a_command(topic):
    import re

    for contact in ("", "@studgroup_support"):
        text = bot_info.render(topic, contact)
        assert not re.search(r"(?<![\w/])/[A-Za-z_]{3,}", text), text


def test_tariffs_publish_no_prices(client):
    private(client, "/tariffs", uid=2)
    text = panel_frames(client)[-1]["text"]
    assert "скоро" in text
    assert "₽" not in text and "руб" not in text.lower()
    assert not any(ch.isdigit() for ch in text)


def test_howto_offers_the_webapp_button(client):
    private(client, "/help", uid=2)
    button = next(b for b in buttons(panel_frames(client)[-1]) if "web_app" in b)
    assert button["text"] == "Открыть StudGroup"
    assert button["web_app"]["url"] == client.app.state.settings.webapp_origin


def test_support_contact_comes_from_settings(client):
    private(client, "/support", uid=2)
    assert "скоро появятся" in panel_frames(client)[-1]["text"]
    client.app.state.settings.support_contact = "@studgroup_support"
    private(client, "/support", uid=2, update=101)
    assert "@studgroup_support" in panel_frames(client)[-1]["text"]


def test_invite_codes_are_not_swallowed(client):
    private(client, "/start invite_" + "x" * 32, uid=2)
    frames = panel_frames(client)
    assert frames and "Команды:" not in frames[-1]["text"]
    assert "Приглашение использовано, отозвано или истекло" in frames[-1]["text"]


def test_bare_start_opens_the_welcome_panel_with_buttons(client):
    private(client, "/start", uid=2)
    frame = panel_frames(client)[-1]
    labels = [b["text"] for row in frame["reply_markup"]["inline_keyboard"] for b in row]
    assert "Я студент" in labels and "Я староста" in labels
    assert "Команды:" not in frame["text"]
