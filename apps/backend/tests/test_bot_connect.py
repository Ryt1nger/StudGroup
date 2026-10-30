"""Adding the bot to a new group makes the adder its headman; the bot never writes in the group."""

import json
import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from test_bot_start import deliver, labels, last, run, tap
from test_bot_start import foreman as foreman  # noqa: PLC0414 -- pytest fixture
from test_ingestion import send

from studgroup import bot_admin
from studgroup import headman_domain as domain
from studgroup.models import BotOutbox, Group, GroupAIProfile, Membership, User

pytest_plugins = ["test_schedule_api"]
ADDER = 77
OTHER = 78
NEW_CHAT = -100777
BOT = 999


def setup_bot(client, monkeypatch, statuses, *, list_works=True, username="studgroup_rf_bot"):
    """``statuses`` maps (chat, user) to the Telegram status of that person in that chat.

    ``list_works=False`` makes both lookups fail, as when Telegram cannot answer.
    """
    settings = client.app.state.settings
    settings.telegram_bot_token = f"{BOT}:fake-no-network"
    settings.bot_username = username

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


def test_whoever_adds_the_bot_becomes_the_headman_and_the_bot_stays_silent(foreman, monkeypatch):
    setup_bot(
        foreman, monkeypatch, {(NEW_CHAT, BOT): "administrator", (NEW_CHAT, ADDER): "creator"}
    )
    before = group_count(foreman)
    added_to_chat(foreman)  # nobody pressed «Я староста» first: the adder is enough
    group, members = new_group(foreman)
    assert group_count(foreman) == before + 1
    assert group.name == "ИВТ-21" and group.pilot_authorized and group.status == "active"
    assert group.bot_added_at is not None
    assert members == [("headman", ADDER)]
    assert messages_to(foreman, NEW_CHAT) == []  # not a word in the group
    assert panel(foreman, ADDER)["text"].startswith("Готово! Группа «ИВТ-21» подключена")
    assert "Создать приглашение" in json.dumps(panel(foreman, ADDER), ensure_ascii=False)

    async def profile(db):
        return await db.get(GroupAIProfile, group.id)

    assert run(foreman, profile) is not None  # the whole environment exists at once
    added_to_chat(foreman)  # a repeated event changes nothing and says nothing
    assert new_group(foreman)[1] == [("headman", ADDER)]
    assert messages_to(foreman, NEW_CHAT) == []


def test_a_bot_that_is_not_an_administrator_still_connects_and_the_headman_is_told(
    foreman, monkeypatch
):
    setup_bot(foreman, monkeypatch, {(NEW_CHAT, BOT): "member", (NEW_CHAT, ADDER): "creator"})
    added_to_chat(foreman, status="member")
    assert new_group(foreman)[1] == [("headman", ADDER)]
    assert "Сделайте бота администратором" in panel(foreman, ADDER)["text"]
    assert messages_to(foreman, NEW_CHAT) == []


def test_the_service_message_about_the_new_member_names_the_adder(foreman, monkeypatch):
    setup_bot(foreman, monkeypatch, {(NEW_CHAT, ADDER): "administrator"})
    typed_in_chat(foreman, None, by=ADDER, new_chat_members=[{"id": BOT, "is_bot": True}])
    assert new_group(foreman)[1] == [("headman", ADDER)]


def test_an_ordinary_member_who_adds_the_bot_is_not_made_headman(foreman, monkeypatch):
    setup_bot(foreman, monkeypatch, {(NEW_CHAT, BOT): "administrator", (NEW_CHAT, ADDER): "member"})
    added_to_chat(foreman)
    group, members = new_group(foreman)
    assert group is not None and members == []  # the group waits for a real administrator
    assert messages_to(foreman, NEW_CHAT) == []
    assert panel(foreman, ADDER) is None


def test_when_telegram_cannot_say_the_adder_is_trusted(foreman, monkeypatch):
    setup_bot(foreman, monkeypatch, {}, list_works=False)
    added_to_chat(foreman)
    assert new_group(foreman)[1] == [("headman", ADDER)]


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
    tap(foreman, "Я староста", uid=OTHER)
    assert "Стать старостой: ИВТ-21" in labels(foreman)
    tap(foreman, "Стать старостой", uid=OTHER)
    assert new_group(foreman)[1] == [("headman", OTHER)]
    assert "Группа «ИВТ-21» подключена" in last(foreman)["text"]


def test_only_groups_the_person_administers_are_offered(foreman, monkeypatch):
    setup_bot(foreman, monkeypatch, {(NEW_CHAT, BOT): "administrator", (NEW_CHAT, ADDER): "member"})
    added_to_chat(foreman)
    deliver(foreman, "/start", uid=ADDER)
    tap(foreman, "Я староста", uid=ADDER)
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
        tap(foreman, "Я староста", uid=uid)
        assert "Стать старостой: ИВТ-21" in labels(foreman)  # both were offered the group
    tap(foreman, "Стать старостой", uid=79)  # 79 picks first with the button they were shown
    assert new_group(foreman)[1] == [("headman", 79)]
    # OTHER presses the button that was valid a moment ago
    markup = panel(foreman, OTHER)["reply_markup"]["inline_keyboard"]
    stale = next(b for row in markup for b in row if "Стать старостой" in b["text"])
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
    assert any(name.startswith("ИВТ-21") and "староста не выбран" in name for name in names)
    assert any(name == "Группа" for name in names)  # the pilot group has its headman


def test_the_headman_screen_explains_that_adding_the_bot_is_enough(foreman, monkeypatch):
    setup_bot(foreman, monkeypatch, {})
    deliver(foreman, "/start", uid=ADDER)
    tap(foreman, "Я староста", uid=ADDER)
    frame = last(foreman)
    assert "тот, кто добавил бота, сразу становится" in frame["text"]
    assert "Бот в группе ничего не пишет" in frame["text"]
    assert frame["reply_markup"]["inline_keyboard"][0][0]["url"] == (
        "https://t.me/studgroup_rf_bot?startgroup=connect"
    )
    foreman.app.state.settings.bot_username = ""
    deliver(foreman, "/start", uid=OTHER)
    tap(foreman, "Я староста", uid=OTHER)
    assert labels(foreman) == ["‹ Назад"]


def test_the_deep_link_opens_the_headman_screen(foreman, monkeypatch):
    setup_bot(foreman, monkeypatch, {})
    deliver(foreman, "/start headman", uid=ADDER)
    assert "Подключение группы" in last(foreman)["text"]


def test_the_new_headman_invites_a_student_who_joins_by_the_link(foreman, monkeypatch):
    setup_bot(
        foreman, monkeypatch, {(NEW_CHAT, BOT): "administrator", (NEW_CHAT, ADDER): "creator"}
    )
    added_to_chat(foreman)
    tap(foreman, "Создать приглашение", uid=ADDER)
    text = last(foreman)["text"]
    link = next(line for line in text.splitlines() if line.startswith("https://t.me/"))
    assert link.startswith("https://t.me/studgroup_rf_bot?start=invite_")

    async def telegram_member(*args):
        return True

    monkeypatch.setattr(domain, "telegram_member", telegram_member)
    deliver(foreman, "/start " + link.split("?start=")[1], uid=88)
    assert "Готово! Вы подключены к «ИВТ-21»" in last(foreman)["text"]

    async def roles(db):
        group = await db.scalar(select(Group).where(Group.telegram_chat_id == NEW_CHAT))
        rows = await db.execute(select(Membership.role).where(Membership.group_id == group.id))
        return sorted(rows.scalars())

    assert run(foreman, roles) == ["headman", "student"]


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
