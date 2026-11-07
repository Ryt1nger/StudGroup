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


def test_ranepa_academic_code_extracts_code_without_institute_or_program():
    from studgroup.bot_start import parse_academic_group_code

    assert (
        parse_academic_group_code("Э-ОБ-385-04-26-02 (ЭМИТ) 38.03.05 Бизнес-информатика")
        == "Э-ОБ-385-04-26-02"
    )
    assert parse_academic_group_code("  э-об-385-04-26-02  ") == "Э-ОБ-385-04-26-02"
    assert parse_academic_group_code("только название программы") is None
    assert parse_academic_group_code("") is None


def test_welcome_has_six_buttons_and_separate_role_selection(foreman):
    deliver(foreman, "/start", uid=33)
    assert labels(foreman) == [
        "Подключить группу",
        "О нас",
        "Помощь",
        "Поддержка",
        "Партнёрка",
        "Тарифы",
    ]
    assert "Привет!" in last(foreman)["text"]
    assert "Я студент" not in labels(foreman)
    tap(foreman, "Подключить группу")
    assert labels(foreman) == ["Я студент", "Я староста", "‹ Назад"]
    assert "Как ты хочешь подключиться" in last(foreman)["text"]
    tap(foreman, "Назад")
    assert "Подключить группу" in labels(foreman)


def test_promo_is_optional_and_does_not_claim_to_apply_unavailable_codes(foreman):
    deliver(foreman, "/start", uid=33)
    tap(foreman, "Подключить группу")
    tap(foreman, "Я староста")
    assert "Промокоды пока не активированы" in last(foreman)["text"]
    tap(foreman, "Ввести промокод")
    deliver(foreman, "FAKECODE", uid=33)
    assert "Промокоды пока не активированы" in last(foreman)["text"]
    tap(foreman, "Пропустить")
    assert "Подключение группы" in last(foreman)["text"]


def test_student_joins_by_typing_a_code_and_the_code_is_removed_from_the_chat(foreman, monkeypatch):
    allow_telegram(monkeypatch)
    code = issue(foreman)
    deliver(foreman, "/start", uid=33)
    tap(foreman, "Подключить группу")
    tap(foreman, "Я студент")
    assert "Нет промокода" in labels(foreman)
    tap(foreman, "Нет промокода")
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
    assert "Главное меню" in labels(foreman)
    tap(foreman, "Главное меню")
    assert "Вы подключены" in last(foreman)["text"]


def test_a_wrong_code_keeps_the_person_on_the_panel_with_a_way_forward(foreman, monkeypatch):
    allow_telegram(monkeypatch)
    deliver(foreman, "/start", uid=33)
    tap(foreman, "Подключить группу")
    tap(foreman, "Я студент")
    tap(foreman, "Нет промокода")
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


def test_shared_group_link_allows_multiple_verified_students(foreman, monkeypatch):
    allow_telegram(monkeypatch)

    async def make(db):
        _, member, group = await actor(db)
        return await domain.issue_group_link(db, group, member)

    code = run(foreman, make)
    for uid in (33, 34):
        deliver(foreman, f"/start invite_{code}", uid=uid)
        assert "Готово! Вы подключены" in last(foreman)["text"]

    async def joined(db):
        from studgroup.models import GroupInvitation
        from studgroup.security import token_hash

        members = (
            await db.scalars(select(Membership.role).where(Membership.role == "student"))
        ).all()
        invitation = await db.get(GroupInvitation, token_hash(code))
        return members, invitation.reusable, invitation.used_by

    members, reusable, used_by = run(foreman, joined)
    assert len(members) == 2
    assert reusable and used_by is None


def test_shared_link_rejects_a_member_of_another_group(foreman, monkeypatch):
    import uuid

    from studgroup.models import Group

    allow_telegram(monkeypatch)

    async def prepare(db):
        _, member, group = await actor(db)
        code = await domain.issue_group_link(db, group, member)
        other = Group(
            id=uuid.uuid4(),
            telegram_chat_id=-1000033,
            name="Другая группа",
            timezone="Europe/Moscow",
            status="active",
            pilot_authorized=True,
        )
        student = User(id=uuid.uuid4(), telegram_user_id=33, display_name="Студент")
        db.add_all([other, student])
        await db.flush()
        db.add(
            Membership(
                id=uuid.uuid4(),
                group_id=other.id,
                user_id=student.id,
                role="student",
                status="active",
            )
        )
        return code

    code = run(foreman, prepare)
    deliver(foreman, f"/start invite_{code}", uid=33)
    assert "уже подключены к другой группе" in last(foreman)["text"]


def test_student_with_no_configured_subgroups_sees_no_subgroup_question(foreman, monkeypatch):
    allow_telegram(monkeypatch)
    code = issue(foreman)
    deliver(foreman, f"/start invite_{code}", uid=33)
    assert "Готово! Вы подключены" in last(foreman)["text"]
    assert "подгруппе" not in last(foreman)["text"]
    deliver(foreman, "/start", uid=33)
    assert "Мои подгруппы" not in labels(foreman)
    assert "Вы подключены" in last(foreman)["text"]


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
    assert text.startswith("Вы подключены к «Группа»") and "/" not in text
    assert {"Кабинет старосты", "Создать приглашение", "Открыть StudGroup"} <= set(labels(foreman))


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
    press(client, "start", "Помощь", 301)
    press(client, "start", "Назад", 302)
    assert [d["message_id"] for m, d in calls if m == "editMessageText"] == [first, first]
    input_message(client, 303, "/start")
    drain(client)
    deleted = {d["message_id"] for m, d in calls if m == "deleteMessage"}
    assert first in deleted and 300 in deleted
    assert current(client, "start")[0] != first


def test_connection_status_checks_live_telegram_without_changing_membership(foreman, monkeypatch):
    from studgroup import bot_connect

    foreman.app.state.settings.telegram_bot_token = "999:test"
    calls = []

    async def status(_settings, chat, telegram_id):
        calls.append((chat, telegram_id))
        return "administrator" if telegram_id == 999 else "creator"

    monkeypatch.setattr(bot_connect, "chat_status", status)
    deliver(foreman, "/start", uid=1)
    tap(foreman, "Проверить подключение", uid=1)
    assert "Участие в Telegram подтверждено" in last(foreman)["text"]
    assert "Бот — администратор группы" in last(foreman)["text"]
    assert len(calls) == 2
    tap(foreman, "Проверить ещё раз", uid=1)
    assert len(calls) == 4
    tap(foreman, "Главное меню", uid=1)
    assert "Создать приглашение" in labels(foreman)


def test_connection_status_does_not_revoke_access_on_telegram_failure(foreman, monkeypatch):
    from studgroup import bot_connect

    foreman.app.state.settings.telegram_bot_token = "999:test"

    async def unavailable(*_args):
        return None

    monkeypatch.setattr(bot_connect, "chat_status", unavailable)
    deliver(foreman, "/start", uid=1)
    tap(foreman, "Проверить подключение", uid=1)
    assert "Это не изменило ваш доступ" in last(foreman)["text"]
    assert "Доступ в StudGroup: активен" in last(foreman)["text"]
    tap(foreman, "Поддержка", uid=1)
    assert "Поддержка" in last(foreman)["text"]


def test_notification_preferences_can_be_changed_with_bot_buttons(foreman):
    from studgroup.models import UserProfile

    deliver(foreman, "/start", uid=1)
    assert "Уведомления" in labels(foreman)
    tap(foreman, "Уведомления", uid=1)
    assert "✅ Все уведомления" in labels(foreman)
    tap(foreman, "Все уведомления", uid=1)
    assert "❌ Все уведомления" in labels(foreman)

    async def saved(db):
        user = await db.scalar(select(User).where(User.telegram_user_id == 1))
        profile = await db.get(UserProfile, user.id)
        return profile.settings

    preferences = run(foreman, saved)
    assert preferences["enabled"] is False
    assert preferences["deadlines"] is True
    tap(foreman, "Напоминание о сроке", uid=1)
    assert "За 3 часа" in labels(foreman)
    tap(foreman, "За 3 часа", uid=1)
    assert "Напоминание о сроке: За 3 часа" in labels(foreman)
    assert run(foreman, saved)["deadline_lead"] == "3h"
    tap(foreman, "Главное меню", uid=1)
    assert "Уведомления" in labels(foreman)


def test_the_cabinet_opens_by_a_button_straight_into_the_main_menu_and_back(foreman):
    deliver(foreman, "/start", uid=1)
    tap(foreman, "Кабинет старосты", uid=1)
    assert last(foreman)["text"].startswith("Староста · Группа")
    assert {"Домашние задания", "Расписание", "‹ В начало"} <= set(labels(foreman))
    tap(foreman, "Расписание", uid=1)
    assert "‹ Главное меню" in labels(foreman)
    tap(foreman, "Главное меню", uid=1)
    tap(foreman, "В начало", uid=1)
    assert last(foreman)["text"].startswith("Вы подключены к «Группа»")


def test_opening_the_cabinet_replaces_the_start_screen_instead_of_adding_one(transport):
    client, calls = transport
    input_message(client, 400, "/start")
    drain(client)
    start_mid, _ = current(client, "start")
    press(client, "start", "Кабинет старосты", 401)
    cabinet_mid, frame = current(client, "st")
    assert cabinet_mid != start_mid and frame["text"].startswith("Староста · Группа")
    assert start_mid in {d["message_id"] for m, d in calls if m == "deleteMessage"}
    press(client, "st", "В начало", 402)
    assert cabinet_mid in {d["message_id"] for m, d in calls if m == "deleteMessage"}
    assert current(client, "start")[0] not in {start_mid, cabinet_mid}


def test_no_screen_of_the_cabinet_menu_tells_people_to_type_a_command(foreman):
    deliver(foreman, "/start", uid=1)
    tap(foreman, "Кабинет старосты", uid=1)
    seen = [last(foreman)["text"]]
    for label in ["Участники", "Настройки", "Журнал", "Сводка"]:
        tap(foreman, label, uid=1)
        seen.append(last(foreman)["text"])
        tap(foreman, "Главное меню", uid=1)
    assert not [text for text in seen if " /" in text or text.startswith("/")]


def test_the_welcome_screen_reaches_every_info_topic_by_buttons(foreman):
    deliver(foreman, "/start", uid=33)
    assert {"О нас", "Помощь", "Партнёрка", "Тарифы", "Поддержка"} <= set(labels(foreman))

    tap(foreman, "О нас")
    assert "О нас" in last(foreman)["text"]
    tap(foreman, "Назад")

    tap(foreman, "Помощь")
    assert "Как пользоваться StudGroup" in last(foreman)["text"]
    assert "Открыть StudGroup" in labels(foreman)
    tap(foreman, "Назад")

    tap(foreman, "Партнёрка")
    assert "Партнёрская программа" in last(foreman)["text"]
    assert "вознаграждения пока не опубликованы" in last(foreman)["text"]
    tap(foreman, "Назад")

    tap(foreman, "Тарифы")
    assert "Тарифы StudGroup скоро появятся" in last(foreman)["text"]
    assert "₽" not in last(foreman)["text"]
    tap(foreman, "Назад")

    tap(foreman, "Поддержка")
    assert "Поддержка" in last(foreman)["text"]
    tap(foreman, "Назад")
    assert "Подключить группу" in labels(foreman)


def test_an_info_command_replaces_the_screen_instead_of_adding_a_message(transport):
    client, calls = transport
    input_message(client, 500, "/tariffs")
    drain(client)
    first, _ = current(client, "start")
    input_message(client, 501, "/faq")
    drain(client)
    deleted = {d["message_id"] for m, d in calls if m == "deleteMessage"}
    assert first in deleted and 500 in deleted  # the old screen and the typed command are removed
    second, frame = current(client, "start")
    assert second != first and "Частые вопросы" in frame["text"]
    sent = [d for m, d in calls if m == "sendMessage"]
    assert len(sent) == 2  # one panel message per command, never a pile


def test_shared_group_link_expires_after_three_hours(foreman, monkeypatch):
    """Shared invitations expire server-side; a forwarded old URL cannot grant access."""
    from studgroup.models import GroupInvitation
    from studgroup.security import token_hash

    allow_telegram(monkeypatch)

    async def prepare(db):
        _, member, group = await actor(db)
        code = await domain.issue_group_link(db, group, member)
        invitation = await db.scalar(
            select(GroupInvitation).where(GroupInvitation.token_hash == token_hash(code))
        )
        remaining = invitation.expires_at - datetime.now(UTC)
        assert timedelta(hours=2, minutes=59) < remaining <= timedelta(hours=3)
        invitation.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        return code

    code = run(foreman, prepare)
    deliver(foreman, f"/start invite_{code}", uid=33)
    assert "истекло" in last(foreman)["text"]

    async def check(db):
        return await db.scalar(select(User.id).where(User.telegram_user_id == 33))

    assert run(foreman, check) is None


def test_rotating_shared_link_revokes_previous_link(foreman, monkeypatch):
    allow_telegram(monkeypatch)

    async def prepare(db):
        _, member, group = await actor(db)
        old_code = await domain.issue_group_link(db, group, member)
        new_code = await domain.issue_group_link(db, group, member)
        return old_code, new_code

    old_code, new_code = run(foreman, prepare)
    deliver(foreman, f"/start invite_{old_code}", uid=33)
    assert "отозвано" in last(foreman)["text"]
    deliver(foreman, f"/start invite_{new_code}", uid=33)
    assert "Готово! Вы подключены" in last(foreman)["text"]
