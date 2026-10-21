"""Private /start walkthrough: welcome panel, student join by code or link, panel hygiene."""

import json
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from test_bot_panels import current, drain, input_message, press
from test_bot_panels import transport as transport  # noqa: PLC0414 -- pytest fixture
from test_headman import actor, deliver, last, run
from test_headman import foreman as foreman  # noqa: PLC0414 -- pytest fixture

from studgroup import headman_domain as domain
from studgroup.models import BotOutbox, Membership, User

pytest_plugins = ["test_schedule_api"]
SETTINGS_URL = "http://localhost:5173"


def labels(client):
    markup = last(client).get("reply_markup", {}).get("inline_keyboard", [])
    return [button["text"] for row in markup for button in row]


def tap(client, label, uid=33):
    markup = last(client)["reply_markup"]["inline_keyboard"]
    button = next(b for row in markup for b in row if label in b["text"])
    deliver(client, callback=button["callback_data"], uid=uid)
    return button


def issue(client):
    async def make(db):
        _, member, group = await actor(db)
        return (await domain.issue_invites(db, group, member, 1))[0]

    return run(client, make)


def deletions(client):
    async def read(db):
        rows = await db.scalars(select(BotOutbox).where(BotOutbox.method == "deleteMessage"))
        return [json.loads(row.payload)["message_id"] for row in rows]

    return run(client, read)


def allow_telegram(monkeypatch, answer=True):
    async def check(*args):
        return answer

    monkeypatch.setattr(domain, "telegram_member", check)


def test_welcome_offers_roles_and_info_buttons_not_commands(foreman):
    deliver(foreman, "/start", uid=33)
    shown = labels(foreman)
    assert shown[:2] == ["Я студент", "Я староста"]
    assert {"Как это работает", "Частые вопросы", "Поддержка"} <= set(shown)
    assert "/" not in last(foreman)["text"]
    tap(foreman, "Как это работает")
    assert "Как пользоваться StudGroup" in last(foreman)["text"]
    assert "Команды:" not in last(foreman)["text"] and labels(foreman) == ["‹ Назад"]
    tap(foreman, "Назад")
    assert "Кто вы?" in last(foreman)["text"]


def test_student_joins_by_typing_a_code_and_the_code_is_removed_from_the_chat(foreman, monkeypatch):
    allow_telegram(monkeypatch)
    code = issue(foreman)
    deliver(foreman, "/start", uid=33)
    tap(foreman, "Я студент")
    assert "Подключение к группе" in last(foreman)["text"]
    tap(foreman, "Ввести код")
    assert "одним сообщением" in last(foreman)["text"]
    deliver(foreman, code, uid=33)
    typed = foreman.app.state._st_test_update
    final = last(foreman)
    assert "Готово! Вы подключены" in final["text"]
    web_app = next(b for row in final["reply_markup"]["inline_keyboard"] for b in row)
    assert web_app["web_app"]["url"] == SETTINGS_URL
    assert typed in deletions(foreman)
    assert code not in json.dumps(final)

    async def joined(db):
        user = await db.scalar(select(User).where(User.telegram_user_id == 33))
        return await db.scalar(select(Membership.role).where(Membership.user_id == user.id))

    assert run(foreman, joined) == "student"


def test_a_wrong_code_keeps_the_person_on_the_panel_with_a_way_forward(foreman, monkeypatch):
    allow_telegram(monkeypatch)
    deliver(foreman, "/start", uid=33)
    tap(foreman, "Я студент")
    tap(foreman, "Ввести код")
    deliver(foreman, "abc", uid=33)
    assert "не похоже на код" in last(foreman)["text"]
    assert labels(foreman) == ["‹ Назад"]  # still waiting: the next message is tried as a code
    deliver(foreman, "x" * 32, uid=33)
    assert "использовано, отозвано или истекло" in last(foreman)["text"]
    deliver(foreman, issue(foreman), uid=33)
    assert "Готово!" in last(foreman)["text"]


def test_an_invitation_link_joins_in_one_tap_and_removes_the_link_message(foreman, monkeypatch):
    allow_telegram(monkeypatch)
    code = issue(foreman)
    deliver(foreman, f"/start invite_{code}", uid=33)
    tapped = foreman.app.state._st_test_update
    assert "Готово! Вы подключены" in last(foreman)["text"]
    assert tapped in deletions(foreman)


def test_a_failed_telegram_check_is_explained_on_the_panel(foreman, monkeypatch):
    allow_telegram(monkeypatch, answer=False)
    deliver(foreman, f"/start invite_{issue(foreman)}", uid=33)
    assert "Не удалось подтвердить участие" in last(foreman)["text"]
    assert "Ввести код" in labels(foreman)


def test_buttons_of_an_older_screen_stop_working_after_a_new_start(foreman):
    deliver(foreman, "/start", uid=33)
    old = last(foreman)["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
    deliver(foreman, "/start", uid=33)
    deliver(foreman, callback=old, uid=33)

    async def acknowledgement(db):
        row = await db.scalar(
            select(BotOutbox)
            .where(BotOutbox.method == "answerCallbackQuery")
            .order_by(BotOutbox.available_at.desc())
            .limit(1)
        )
        return json.loads(row.payload)["text"]

    assert "Отправь /start" in run(foreman, acknowledgement)


def test_a_connected_person_sees_the_app_and_staff_a_pointer_to_the_cabinet(foreman):
    deliver(foreman, "/start", uid=1)  # uid 1 is the pilot group's headman
    text = last(foreman)["text"]
    assert text.startswith("Вы подключены к «Группа»") and "/st" in text
    assert "Открыть StudGroup" in labels(foreman)


def test_plain_text_outside_the_code_step_still_reaches_the_headman_flow(foreman):
    deliver(foreman, "/start", uid=1)
    sent = datetime.now(UTC) - timedelta(days=1)
    deliver(foreman, "Решить номера", forward=sent, uid=1)
    assert "Добавить ДЗ" in labels(foreman)


def test_invite_links_use_the_bot_username_when_configured(foreman):
    from studgroup import bot_start

    settings = foreman.app.state.settings
    assert bot_start.invite_link(settings, "CODE") == "/start invite_CODE"
    settings.bot_username = "@studgroup_rf_bot"
    assert (
        bot_start.invite_link(settings, "CODE") == "https://t.me/studgroup_rf_bot?start=invite_CODE"
    )
    assert (
        bot_start.extract_code("https://t.me/studgroup_rf_bot?start=invite_" + "A" * 32) == "A" * 32
    )
    assert bot_start.extract_code("/start invite_" + "b" * 32) == "b" * 32
    assert bot_start.extract_code("ЭТО НЕ КОД") is None


def test_start_panel_edits_one_message_and_a_new_start_replaces_it(transport):
    client, calls = transport
    input_message(client, 300, "/start")
    drain(client)
    first, _ = current(client, "start")
    press(client, "start", "Как это работает", 301)
    press(client, "start", "Назад", 302)
    assert [d["message_id"] for m, d in calls if m == "editMessageText"] == [first, first]
    input_message(client, 303, "/start")
    drain(client)
    deleted = {d["message_id"] for m, d in calls if m == "deleteMessage"}
    assert first in deleted and 300 in deleted
    assert current(client, "start")[0] != first
