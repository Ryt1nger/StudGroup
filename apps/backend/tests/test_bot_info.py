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


@pytest.mark.parametrize(
    ("text", "needle"),
    [
        ("/start tariffs", "Тарифы"),
        ("/start howto", "Как пользоваться"),
        ("/start connect", "Подключение группы"),
        ("/start faq", "Частые вопросы"),
        ("/start support", "Поддержка"),
    ],
)
def test_deep_links_answer_any_private_user(client, text, needle):
    private(client, text, uid=2)  # not the bot admin
    reply = sent(client)[-1]
    assert reply["chat_id"] == 2
    assert needle in reply["text"]
    assert "/tariffs" in reply["text"]


def test_tariffs_publish_no_prices(client):
    private(client, "/tariffs", uid=2)
    text = sent(client)[-1]["text"]
    assert "скоро" in text
    assert "₽" not in text and "руб" not in text.lower()
    assert not any(ch.isdigit() for ch in text)


def test_howto_offers_the_webapp_button(client):
    private(client, "/help", uid=2)
    markup = sent(client)[-1]["reply_markup"]["inline_keyboard"][0][0]
    assert markup["text"] == "Открыть StudGroup"
    assert markup["web_app"]["url"] == client.app.state.settings.webapp_origin


def test_support_contact_comes_from_settings(client):
    private(client, "/support", uid=2)
    assert "скоро появятся" in sent(client)[-1]["text"]
    client.app.state.settings.support_contact = "@studgroup_support"
    private(client, "/support", uid=2, update=101)
    assert "@studgroup_support" in sent(client)[-1]["text"]


def test_invite_codes_are_not_swallowed(client):
    private(client, "/start invite_nonexistent", uid=2)
    replies = sent(client)
    assert replies and "Команды:" not in replies[-1]["text"]


def test_bare_start_still_opens_the_app(client):
    private(client, "/start", uid=2)
    reply = sent(client)[-1]
    assert reply["text"] == "Открой StudGroup через кнопку ниже."
