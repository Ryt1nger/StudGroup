"""Adding the bot to a new group makes the adder its headman; the bot never writes in the group."""

import json
import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from test_bot_start import deliver, labels, last, run
from test_bot_start import foreman as foreman  # noqa: PLC0414 -- pytest fixture
from test_bot_start import tap as base_tap
from test_ingestion import send

from studgroup import bot_admin
from studgroup import headman_domain as domain
from studgroup.models import BotOutbox, Group, GroupAIProfile, Membership, RawMessage, User

pytest_plugins = ["test_schedule_api"]
ADDER = 77
OTHER = 78
NEW_CHAT = -100777
BOT = 999


def tap(client, label, uid=33):
    """Complete explicit consents in legacy headman tests after confirmation."""
    button = base_tap(client, label, uid=uid)
    if label == "Да, подключить" and "Принимаю условия использования" in labels(client):
        base_tap(client, "Принимаю условия использования", uid=uid)
        base_tap(client, "Даю отдельное согласие", uid=uid)
    return button


def finish_group_identifier(
    client,
    uid=ADDER,
    value="Э-ОБ-385-04-26-02 (ЭМИТ) 38.03.05 Бизнес-информатика",
):
    """Complete the new RANEPA identifier step before invitations."""
    assert "идентификатор группы РАНХиГС" in last(client)["text"]
    deliver(client, value, uid=uid)
    assert "Официальный код: Э-ОБ-385-04-26-02" in last(client)["text"]
    assert "Да, сохранить" in labels(client)
    tap(client, "Да, сохранить", uid=uid)


def setup_bot(client, monkeypatch, statuses, *, list_works=True, username="studgroup_rf_bot"):
    """``statuses`` maps (chat, user) to the Telegram status of that person in that chat.

    ``list_works=False`` makes both lookups fail, as when Telegram cannot answer.
    """
    settings = client.app.state.settings
    settings.telegram_bot_token = f"{BOT}:fake-no-network"
    settings.bot_username = username
    settings.legal_operator_name = "Тестовый оператор"
    settings.legal_operator_address = "Москва, тестовый адрес"
    settings.legal_operator_contact = "legal@example.test"
    settings.legal_data_region = "Россия, Москва"
    settings.legal_documents_approved = True

    async def request(_settings, method, data):
        if not list_works:
            return {"ok": False, "description": "not available"}
        if method == "getChatAdministrators":
            admins = [
                {"status": status, "user": {"id": user}}
                for (chat, user), status in statuses.items()
                if chat == data["chat_id"] and status in {"creator", "administrator"}
            ]
            return {"ok": True, "result": admins}
        assert method == "getChatMember"
        status = statuses.get((data["chat_id"], data["user_id"]))
        if status is None:
            return {"ok": False, "description": "user not found"}
        return {"ok": True, "result": {"status": status}}

    monkeypatch.setattr(bot_admin, "telegram_request", request)


def panel(client, user=None):
    """The newest private panel frame (for ``user`` when given)."""

    async def read(db):
        rows = await db.scalars(
            select(BotOutbox)
            .where(BotOutbox.method == "renderPanel")
            .order_by(BotOutbox.available_at.desc())
        )
        for row in rows:
            data = json.loads(row.payload)
            if user is None or data["chat_id"] == user:
                return data
        return None

    return run(client, read)


def messages_to(client, chat):
    """Every ordinary message the bot queued for this chat (a group chat must stay empty)."""

    async def read(db):
        rows = await db.scalars(select(BotOutbox).where(BotOutbox.method == "sendMessage"))
        return [d for d in (json.loads(r.payload) for r in rows) if d.get("chat_id") == chat]

    return run(client, read)


def next_update(client):
    client.app.state._st_test_update = getattr(client.app.state, "_st_test_update", 8000) + 1
    return client.app.state._st_test_update


def added_to_chat(client, chat=NEW_CHAT, by=ADDER, status="administrator", title="ИВТ-21"):
    body = {
        "update_id": next_update(client),
        "my_chat_member": {
            "chat": {"id": chat, "type": "supergroup", "title": title},
            "from": {"id": by},
            "new_chat_member": {"status": status, "user": {"id": BOT, "is_bot": True}},
        },
    }
    assert send(client, body).status_code == 200


def typed_in_chat(client, text, by, chat=NEW_CHAT, **extra):
    ident = next_update(client)
    body = {
        "update_id": ident,
        "message": {
            "message_id": ident,
            "date": int(datetime.now(UTC).timestamp()),
            "chat": {"id": chat, "type": "supergroup", "title": "ИВТ-21"},
            "from": {"id": by},
            "text": text,
            **extra,
        },
    }
    assert send(client, body).status_code == 200


def group_count(client):
    return run(client, lambda db: db.scalar(select(func.count()).select_from(Group)))


def new_group(client):
    async def read(db):
        group = await db.scalar(select(Group).where(Group.telegram_chat_id == NEW_CHAT))
        if group is None:
            return None
        rows = await db.execute(
            select(Membership.role, User.telegram_user_id)
            .join(User, User.id == Membership.user_id)
            .where(Membership.group_id == group.id)
        )
        return group, rows.all()

    return run(client, read)


def test_adding_the_bot_only_discovers_the_group_until_private_confirmation(foreman, monkeypatch):
    setup_bot(
        foreman, monkeypatch, {(NEW_CHAT, BOT): "administrator", (NEW_CHAT, ADDER): "creator"}
    )
    before = group_count(foreman)
    added_to_chat(foreman)
    group, members = new_group(foreman)
    assert group_count(foreman) == before + 1
    assert group.name == "ИВТ-21" and not group.pilot_authorized and group.status == "active"
    assert group.bot_added_at is not None
    assert members == []  # adding the bot never automatically grants role
    assert panel(foreman, ADDER) is None
    assert messages_to(foreman, NEW_CHAT) == []
    typed_in_chat(foreman, "домашнее задание на завтра", by=OTHER)

    async def recorded(db):
        return await db.scalar(
            select(func.count()).select_from(RawMessage).where(RawMessage.group_id == group.id)
        )

    assert run(foreman, recorded) == 0  # no collection or AI before confirmation
    deliver(foreman, "/start", uid=ADDER)
    tap(foreman, "Подключить группу", uid=ADDER)
    tap(foreman, "Я староста", uid=ADDER)
    tap(foreman, "Нет промокода", uid=ADDER)
    tap(foreman, "Проверить: ИВТ-21", uid=ADDER)
    assert "Да, подключить" in labels(foreman)
    assert new_group(foreman)[1] == []
    tap(foreman, "Да, подключить", uid=ADDER)
    assert new_group(foreman)[1] == [("headman", ADDER)]
    assert new_group(foreman)[0].pilot_authorized
    assert new_group(foreman)[0].academic_group_code_required
    assert "Создать приглашение" not in labels(foreman)
    finish_group_identifier(foreman)
    assert panel(foreman, ADDER)["text"].startswith("Готово! Группа «ИВТ-21» подключена")
    assert new_group(foreman)[0].academic_group_code == "Э-ОБ-385-04-26-02"
    assert labels(foreman) == ["Создать приглашение"]

    async def profile(db):
        return await db.get(GroupAIProfile, group.id)

    assert run(foreman, profile) is not None  # the whole environment exists at once
    added_to_chat(foreman)  # a repeated event changes nothing and says nothing
    assert new_group(foreman)[1] == [("headman", ADDER)]
    assert messages_to(foreman, NEW_CHAT) == []


def test_bot_promotion_advances_waiting_headman_without_claiming_role(foreman, monkeypatch):
    statuses = {(NEW_CHAT, BOT): "member", (NEW_CHAT, ADDER): "creator"}
    setup_bot(foreman, monkeypatch, statuses)
    deliver(foreman, "/start", uid=ADDER)
    tap(foreman, "Подключить группу", uid=ADDER)
    tap(foreman, "Я староста", uid=ADDER)
    tap(foreman, "Нет промокода", uid=ADDER)
    assert "Проверить подключение" in labels(foreman)

    added_to_chat(foreman, status="member")
    assert "Проверить подключение" in labels(foreman)
    assert new_group(foreman)[1] == []
    assert not new_group(foreman)[0].pilot_authorized

    statuses[(NEW_CHAT, BOT)] = "administrator"
    added_to_chat(foreman, status="administrator")
    assert "Найдена группа «ИВТ-21»" in panel(foreman, ADDER)["text"]
    assert "Да, подключить" in labels(foreman)
    assert new_group(foreman)[1] == []
    assert not new_group(foreman)[0].pilot_authorized
    assert messages_to(foreman, NEW_CHAT) == []

    tap(foreman, "Да, подключить", uid=ADDER)
    assert new_group(foreman)[1] == [("headman", ADDER)]
    assert new_group(foreman)[0].pilot_authorized
    assert "идентификатор группы РАНХиГС" in panel(foreman, ADDER)["text"]


def test_academic_code_back_returns_to_setup_not_main_menu(foreman, monkeypatch):
    setup_bot(
        foreman, monkeypatch, {(NEW_CHAT, BOT): "administrator", (NEW_CHAT, ADDER): "creator"}
    )
    deliver(foreman, "/start", uid=ADDER)
    tap(foreman, "Подключить группу", uid=ADDER)
    tap(foreman, "Я староста", uid=ADDER)
    tap(foreman, "Нет промокода", uid=ADDER)
    added_to_chat(foreman)
    tap(foreman, "Да, подключить", uid=ADDER)
    assert "идентификатор группы РАНХиГС" in last(foreman)["text"]
    assert "‹ Назад" in labels(foreman)
    assert "В главное меню" not in labels(foreman)

    tap(foreman, "‹ Назад", uid=ADDER)
    assert "Группа «ИВТ-21» подтверждена" in last(foreman)["text"]
    assert labels(foreman) == ["Продолжить: указать ID"]
    tap(foreman, "Продолжить: указать ID", uid=ADDER)
    assert "идентификатор группы РАНХиГС" in last(foreman)["text"]
    assert new_group(foreman)[1] == [("headman", ADDER)]


def test_auto_advance_requires_eligible_waiting_administrator(foreman, monkeypatch):
    statuses = {(NEW_CHAT, BOT): "administrator", (NEW_CHAT, ADDER): "member"}
    setup_bot(foreman, monkeypatch, statuses)
    deliver(foreman, "/start", uid=ADDER)
    tap(foreman, "Подключить группу", uid=ADDER)
    tap(foreman, "Я староста", uid=ADDER)
    tap(foreman, "Нет промокода", uid=ADDER)
    added_to_chat(foreman)
    assert "Проверить подключение" in labels(foreman)
    assert new_group(foreman)[1] == []

    # Telegram verifies the person is now an administrator; a new status update
    # advances the same waiting screen without exposing other groups.
    statuses[(NEW_CHAT, ADDER)] = "administrator"
    added_to_chat(foreman)
    assert "Да, подключить" in labels(foreman)
    assert new_group(foreman)[1] == []


def test_bot_added_by_non_onboarding_admin_never_sends_unsolicited_message(foreman, monkeypatch):
    setup_bot(
        foreman, monkeypatch, {(NEW_CHAT, BOT): "administrator", (NEW_CHAT, ADDER): "creator"}
    )
    added_to_chat(foreman)
    assert panel(foreman, ADDER) is None
    assert new_group(foreman)[1] == []
    assert messages_to(foreman, NEW_CHAT) == []


def test_ranepa_code_can_be_suggested_from_telegram_title(foreman, monkeypatch):
    setup_bot(
        foreman,
        monkeypatch,
        {(NEW_CHAT, BOT): "administrator", (NEW_CHAT, ADDER): "creator"},
    )
    added_to_chat(
        foreman,
        title="Э-ОБ-385-04-26-02 (ЭМИТ) 38.03.05 Бизнес-информатика",
    )
    deliver(foreman, "/start", uid=ADDER)
    tap(foreman, "Подключить группу", uid=ADDER)
    tap(foreman, "Я староста", uid=ADDER)
    tap(foreman, "Нет промокода", uid=ADDER)
    tap(foreman, "Проверить:", uid=ADDER)
    tap(foreman, "Да, подключить", uid=ADDER)
    assert "Подтвердить Э-ОБ-385-04-26-02" in labels(foreman)
    tap(foreman, "Подтвердить Э-ОБ-385-04-26-02", uid=ADDER)
    assert "Официальный код: Э-ОБ-385-04-26-02" in last(foreman)["text"]
    tap(foreman, "Да, сохранить", uid=ADDER)
    assert new_group(foreman)[0].academic_group_code == "Э-ОБ-385-04-26-02"


def test_legacy_headman_can_add_official_group_code_without_new_registration(foreman):
    deliver(foreman, "/start", uid=1)
    assert "Создать приглашение" in labels(foreman)
    assert "Указать ID группы РАНХиГС" in labels(foreman)
    tap(foreman, "Указать ID группы РАНХиГС", uid=1)
    deliver(foreman, "неверный код", uid=1)
    assert "Не удалось распознать код" in last(foreman)["text"]
    deliver(foreman, "Э-ОБ-385-04-26-02", uid=1)
    assert "Да, сохранить" in labels(foreman)
    deliver(foreman, "/start", uid=1)
    assert "Да, сохранить" in labels(foreman)  # confirm survives a restart
    tap(foreman, "Да, сохранить", uid=1)
    assert "Официальный ID РАНХиГС: Э-ОБ-385-04-26-02" in last(foreman)["text"]
    assert "Создать приглашение" in labels(foreman)


def test_a_bot_without_admin_rights_is_discovered_but_cannot_confirm_headman(foreman, monkeypatch):
    setup_bot(
        foreman,
        monkeypatch,
        {(NEW_CHAT, BOT): "member", (NEW_CHAT, ADDER): "creator"},
    )
    added_to_chat(foreman, status="member")
    assert new_group(foreman)[1] == []
    assert panel(foreman, ADDER) is None
    deliver(foreman, "/start", uid=ADDER)
    tap(foreman, "Подключить группу", uid=ADDER)
    tap(foreman, "Я староста", uid=ADDER)
    tap(foreman, "Нет промокода", uid=ADDER)
    tap(foreman, "Проверить: ИВТ-21", uid=ADDER)
    tap(foreman, "Да, подключить", uid=ADDER)
    assert "назначьте StudGroup администратором" in last(foreman)["text"]
    assert new_group(foreman)[1] == []
    assert messages_to(foreman, NEW_CHAT) == []


def test_the_service_message_about_the_new_member_names_the_adder(foreman, monkeypatch):
    setup_bot(foreman, monkeypatch, {(NEW_CHAT, ADDER): "administrator"})
    typed_in_chat(foreman, None, by=ADDER, new_chat_members=[{"id": BOT, "is_bot": True}])
    assert new_group(foreman)[1] == []


def test_an_ordinary_member_who_adds_the_bot_is_not_made_headman(foreman, monkeypatch):
    setup_bot(foreman, monkeypatch, {(NEW_CHAT, BOT): "administrator", (NEW_CHAT, ADDER): "member"})
    added_to_chat(foreman)
    group, members = new_group(foreman)
    assert group is not None and members == []  # the group waits for a real administrator
    assert messages_to(foreman, NEW_CHAT) == []
    assert panel(foreman, ADDER) is None


def test_when_telegram_cannot_confirm_rights_the_adder_is_not_trusted(foreman, monkeypatch):
    setup_bot(foreman, monkeypatch, {}, list_works=False)
    added_to_chat(foreman)
    assert new_group(foreman)[1] == []


def test_a_group_that_already_has_a_headman_is_never_touched(foreman, monkeypatch):
    setup_bot(foreman, monkeypatch, {(-1001, BOT): "administrator", (-1001, ADDER): "creator"})
    added_to_chat(foreman, chat=-1001, title="Группа")

    async def roles(db):
        rows = await db.execute(select(Membership.role))
        return sorted(rows.scalars())

    assert run(foreman, roles) == ["headman"]  # the pilot group's own headman, nobody else
    assert messages_to(foreman, -1001) == []


def test_a_person_who_already_belongs_to_a_group_is_not_made_headman_of_another(
    foreman, monkeypatch
):
    setup_bot(foreman, monkeypatch, {(NEW_CHAT, BOT): "administrator", (NEW_CHAT, 1): "creator"})
    added_to_chat(foreman, by=1)  # uid 1 is the pilot group's headman
    group, members = new_group(foreman)
    assert group is not None and members == []
    assert messages_to(foreman, NEW_CHAT) == []


def test_start_typed_in_a_headmanless_group_needs_a_confirmed_administrator(foreman, monkeypatch):
    statuses = {
        (NEW_CHAT, BOT): "administrator",
        (NEW_CHAT, ADDER): "member",
        (NEW_CHAT, OTHER): "creator",
    }
    setup_bot(foreman, monkeypatch, statuses)
    added_to_chat(foreman)  # the adder is an ordinary member: no headman yet
    assert new_group(foreman)[1] == []
    typed_in_chat(foreman, "/start@studgroup_rf_bot", by=ADDER)
    assert new_group(foreman)[1] == []  # a member typing a command proves nothing
    typed_in_chat(foreman, "/start@studgroup_rf_bot", by=OTHER)
    assert new_group(foreman)[1] == []
    deliver(foreman, "/start", uid=OTHER)
    tap(foreman, "Подключить группу", uid=OTHER)
    tap(foreman, "Я староста", uid=OTHER)
    tap(foreman, "Нет промокода", uid=OTHER)
    tap(foreman, "Проверить: ИВТ-21", uid=OTHER)
    tap(foreman, "Да, подключить", uid=OTHER)
    assert new_group(foreman)[1] == [("headman", OTHER)]
    assert messages_to(foreman, NEW_CHAT) == []


def test_start_typed_when_telegram_cannot_confirm_does_not_make_a_headman(foreman, monkeypatch):
    setup_bot(foreman, monkeypatch, {}, list_works=False)
    typed_in_chat(foreman, "/start@studgroup_rf_bot", by=ADDER)
    group, members = new_group(foreman)
    assert group is not None and members == []


def test_an_administrator_takes_a_headmanless_group_with_the_start_screen(foreman, monkeypatch):
    statuses = {
        (NEW_CHAT, BOT): "administrator",
        (NEW_CHAT, ADDER): "member",
        (NEW_CHAT, OTHER): "creator",
    }
    setup_bot(foreman, monkeypatch, statuses)
    added_to_chat(foreman)
    assert new_group(foreman)[1] == []
    deliver(foreman, "/start", uid=OTHER)
    tap(foreman, "Подключить группу", uid=OTHER)
    tap(foreman, "Я староста", uid=OTHER)
    tap(foreman, "Нет промокода", uid=OTHER)
    assert "Проверить: ИВТ-21" in labels(foreman)
    tap(foreman, "Проверить: ИВТ-21", uid=OTHER)
    tap(foreman, "Да, подключить", uid=OTHER)
    assert new_group(foreman)[1] == [("headman", OTHER)]
    finish_group_identifier(foreman, uid=OTHER)
    assert "Группа «ИВТ-21» подключена" in last(foreman)["text"]


def test_only_groups_the_person_administers_are_offered(foreman, monkeypatch):
    setup_bot(foreman, monkeypatch, {(NEW_CHAT, BOT): "administrator", (NEW_CHAT, ADDER): "member"})
    added_to_chat(foreman)
    deliver(foreman, "/start", uid=ADDER)
    tap(foreman, "Подключить группу", uid=ADDER)
    tap(foreman, "Я староста", uid=ADDER)
    tap(foreman, "Нет промокода", uid=ADDER)
    assert not any("ИВТ-21" in label for label in labels(foreman))
    assert "ИВТ-21" not in last(foreman)["text"]


def test_the_role_cannot_be_taken_twice_even_with_a_stale_button(foreman, monkeypatch):
    statuses = {
        (NEW_CHAT, BOT): "administrator",
        (NEW_CHAT, ADDER): "member",
        (NEW_CHAT, OTHER): "administrator",
        (NEW_CHAT, 79): "administrator",
    }
    setup_bot(foreman, monkeypatch, statuses)
    added_to_chat(foreman)
    for uid in (OTHER, 79):
        deliver(foreman, "/start", uid=uid)
        tap(foreman, "Подключить группу", uid=uid)
        tap(foreman, "Я староста", uid=uid)
        tap(foreman, "Нет промокода", uid=uid)
        assert "Проверить: ИВТ-21" in labels(foreman)
        tap(foreman, "Проверить: ИВТ-21", uid=uid)
    tap(foreman, "Да, подключить", uid=79)
    assert new_group(foreman)[1] == [("headman", 79)]
    # OTHER presses the button that was valid a moment ago
    markup = panel(foreman, OTHER)["reply_markup"]["inline_keyboard"]
    stale = next(b for row in markup for b in row if "Да, подключить" in b["text"])
    deliver(foreman, callback=stale["callback_data"], uid=OTHER)
    assert new_group(foreman)[1] == [("headman", 79)]
    assert "уже есть староста" in panel(foreman, OTHER)["text"]


def test_a_group_without_a_headman_is_marked_in_the_owner_list(foreman, monkeypatch):
    from test_bot_admin import private

    setup_bot(foreman, monkeypatch, {(NEW_CHAT, ADDER): "member"})
    added_to_chat(foreman)
    foreman.app.state.settings.bot_admin_user_id = 9001
    private(foreman, "/admin", uid=9001, update=900)
    markup = last(foreman)["reply_markup"]["inline_keyboard"]
    names = [button["text"] for row in markup for button in row]
    assert any(
        name.startswith("ИВТ-21") and "ожидает активации" in name and "староста не выбран" in name
        for name in names
    )
    assert any(name == "Группа" for name in names)  # the pilot group has its headman


def test_the_headman_screen_explains_that_adding_the_bot_is_enough(foreman, monkeypatch):
    setup_bot(foreman, monkeypatch, {})
    deliver(foreman, "/start", uid=ADDER)
    tap(foreman, "Подключить группу", uid=ADDER)
    tap(foreman, "Я староста", uid=ADDER)
    tap(foreman, "Нет промокода", uid=ADDER)
    frame = last(foreman)
    assert "Роль старосты выдаётся только после проверки" in frame["text"]
    assert "Бот в группе ничего не пишет" in frame["text"]
    assert frame["reply_markup"]["inline_keyboard"][0][0]["url"] == (
        "https://t.me/studgroup_rf_bot?startgroup=connect"
    )
    foreman.app.state.settings.bot_username = ""
    deliver(foreman, "/start", uid=OTHER)
    tap(foreman, "Подключить группу", uid=OTHER)
    tap(foreman, "Я староста", uid=OTHER)
    tap(foreman, "Нет промокода", uid=OTHER)
    assert labels(foreman) == ["Проверить подключение", "‹ Назад"]


def test_the_deep_link_opens_the_headman_screen(foreman, monkeypatch):
    setup_bot(foreman, monkeypatch, {})
    deliver(foreman, "/start headman", uid=ADDER)
    assert "Подключение группы" in last(foreman)["text"]


def test_the_new_headman_invites_a_student_who_joins_by_the_link(foreman, monkeypatch):
    setup_bot(
        foreman, monkeypatch, {(NEW_CHAT, BOT): "administrator", (NEW_CHAT, ADDER): "creator"}
    )
    added_to_chat(foreman)
    deliver(foreman, "/start", uid=ADDER)
    tap(foreman, "Подключить группу", uid=ADDER)
    tap(foreman, "Я староста", uid=ADDER)
    tap(foreman, "Нет промокода", uid=ADDER)
    tap(foreman, "Проверить: ИВТ-21", uid=ADDER)
    tap(foreman, "Да, подключить", uid=ADDER)
    finish_group_identifier(foreman)
    tap(foreman, "Создать приглашение", uid=ADDER)
    text = last(foreman)["text"]
    link = next(line for line in text.splitlines() if line.startswith("https://t.me/"))
    assert link.startswith("https://t.me/studgroup_rf_bot?start=invite_")

    async def telegram_member(*args):
        return True

    monkeypatch.setattr(domain, "telegram_member", telegram_member)
    deliver(foreman, "/start " + link.split("?start=")[1], uid=88)
    base_tap(foreman, "Принимаю условия использования", uid=88)
    base_tap(foreman, "Даю отдельное согласие", uid=88)
    assert "Готово! Вы подключены" in last(foreman)["text"]

    async def roles(db):
        group = await db.scalar(select(Group).where(Group.telegram_chat_id == NEW_CHAT))
        rows = await db.execute(select(Membership.role).where(Membership.group_id == group.id))
        return sorted(rows.scalars())

    assert run(foreman, roles) == ["headman", "student"]


def test_headman_can_skip_subgroups_after_issuing_an_invite(foreman, monkeypatch):
    setup_bot(
        foreman,
        monkeypatch,
        {(NEW_CHAT, BOT): "administrator", (NEW_CHAT, ADDER): "creator"},
    )
    added_to_chat(foreman)
    deliver(foreman, "/start", uid=ADDER)
    tap(foreman, "Подключить группу", uid=ADDER)
    tap(foreman, "Я староста", uid=ADDER)
    tap(foreman, "Нет промокода", uid=ADDER)
    tap(foreman, "Проверить: ИВТ-21", uid=ADDER)
    tap(foreman, "Да, подключить", uid=ADDER)
    finish_group_identifier(foreman)
    tap(foreman, "Создать приглашение", uid=ADDER)
    assert "Далее: подгруппы" in labels(foreman)
    tap(foreman, "Далее: подгруппы", uid=ADDER)
    assert "Да, настроить" in labels(foreman)
    tap(foreman, "Нет, пропустить", uid=ADDER)
    assert "защищённой формы Mini App" in last(foreman)["text"]
    assert "Пропустить" in labels(foreman)
    tap(foreman, "Пропустить", uid=ADDER)
    tap(foreman, "Кабинет старосты", uid=ADDER)
    assert "Староста · ИВТ-21" in last(foreman)["text"]


def test_headman_finishes_after_optional_first_subgroup_subject(foreman, monkeypatch):
    setup_bot(
        foreman, monkeypatch, {(NEW_CHAT, BOT): "administrator", (NEW_CHAT, ADDER): "creator"}
    )
    deliver(foreman, "/start", uid=ADDER)
    tap(foreman, "Подключить группу", uid=ADDER)
    tap(foreman, "Я староста", uid=ADDER)
    tap(foreman, "Нет промокода", uid=ADDER)
    added_to_chat(foreman)
    tap(foreman, "Да, подключить", uid=ADDER)
    finish_group_identifier(foreman)
    tap(foreman, "Создать приглашение", uid=ADDER)
    tap(foreman, "Далее: подгруппы", uid=ADDER)
    tap(foreman, "Да, настроить", uid=ADDER)
    assert "Добавить предмет" in labels(foreman)
    assert "Далее" in labels(foreman)

    tap(foreman, "Добавить предмет", uid=ADDER)
    deliver(foreman, "Английский", uid=ADDER)
    deliver(foreman, "2", uid=ADDER)
    deliver(foreman, "Иванова", uid=ADDER)
    deliver(foreman, "2 группа", uid=ADDER)
    tap(foreman, "Сохранить", uid=ADDER)
    assert labels(foreman) == ["Добавить ещё", "К предметам", "Далее"]

    tap(foreman, "К предметам", uid=ADDER)
    assert any("Английский" in label for label in labels(foreman))
    assert "Далее" in labels(foreman)
    tap(foreman, "Далее", uid=ADDER)
    assert "Профиль можно заполнить в приложении" in last(foreman)["text"]
    assert {"Открыть StudGroup", "Подключить СДО / ЛК", "Кабинет старосты"} <= set(labels(foreman))
    tap(foreman, "Кабинет старосты", uid=ADDER)
    assert "Староста · ИВТ-21" in last(foreman)["text"]


def test_a_deputy_gets_no_invite_button(foreman):
    async def make_deputy(db):
        (await db.scalar(select(Membership))).role = "deputy"

    run(foreman, make_deputy)
    deliver(foreman, "/start", uid=1)
    assert "Создать приглашение" not in labels(foreman)
    assert json.dumps(last(foreman)).count("invite") == 0


def test_a_person_who_joined_a_group_is_not_asked_who_they_are(foreman, monkeypatch):
    setup_bot(
        foreman, monkeypatch, {(NEW_CHAT, BOT): "administrator", (NEW_CHAT, OTHER): "administrator"}
    )
    added_to_chat(foreman, by=555)  # an unconfirmed adder: the group stays headman-less

    async def join_pilot_group(db):
        group = await db.scalar(select(Group).where(Group.telegram_chat_id == -1001))
        user = User(id=uuid.uuid4(), telegram_user_id=OTHER, display_name="Участник")
        db.add(user)
        await db.flush()
        db.add(Membership(user_id=user.id, group_id=group.id, role="student", status="active"))

    run(foreman, join_pilot_group)
    deliver(foreman, "/start", uid=OTHER)
    assert "Я староста" not in labels(foreman)
    assert new_group(foreman)[1] == []  # 555 is not an administrator, so nobody was assigned
