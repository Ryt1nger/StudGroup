"""A headman connects a brand-new group by adding the bot; every other case is refused."""

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
CLAIMANT = 77
NEW_CHAT = -100777


def setup_bot(client, monkeypatch, statuses, username="studgroup_rf_bot"):
    """``statuses`` maps (chat, user) to the Telegram status getChatMember answers with."""
    settings = client.app.state.settings
    settings.telegram_bot_token = "999:fake-no-network"
    settings.bot_username = username

    async def request(_settings, method, data):
        assert method == "getChatMember"
        status = statuses.get((data["chat_id"], data["user_id"]))
        if status is None:
            return {"ok": False, "description": "user not found"}
        return {"ok": True, "result": {"status": status}}

    monkeypatch.setattr(bot_admin, "telegram_request", request)


def panel(client):
    """The newest panel frame, ignoring plain messages such as the note posted in the group."""

    async def read(db):
        row = await db.scalar(
            select(BotOutbox)
            .where(BotOutbox.method == "renderPanel")
            .order_by(BotOutbox.available_at.desc())
            .limit(1)
        )
        return json.loads(row.payload)

    return run(client, read)


def claim(client, uid=CLAIMANT):
    deliver(client, "/start", uid=uid)
    tap(client, "Я староста", uid=uid)


def added_to_chat(client, chat=NEW_CHAT, by=CLAIMANT, status="administrator", title="ИВТ-21"):
    client.app.state._st_test_update = getattr(client.app.state, "_st_test_update", 8000) + 1
    body = {
        "update_id": client.app.state._st_test_update,
        "my_chat_member": {
            "chat": {"id": chat, "type": "supergroup", "title": title},
            "from": {"id": by},
            "new_chat_member": {"status": status, "user": {"id": 999, "is_bot": True}},
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


def test_the_headman_screen_explains_the_steps_and_offers_the_add_to_group_button(
    foreman, monkeypatch
):
    setup_bot(foreman, monkeypatch, {})
    claim(foreman)
    frame = last(foreman)
    assert "Добавьте бота в чат вашей группы" in frame["text"]
    assert "/start@studgroup_rf_bot" in frame["text"]
    button = frame["reply_markup"]["inline_keyboard"][0][0]
    assert button["url"] == "https://t.me/studgroup_rf_bot?startgroup=connect"
    foreman.app.state.settings.bot_username = ""
    claim(foreman, uid=78)
    assert labels(foreman) == ["‹ Назад"]


def test_adding_the_bot_as_admin_creates_the_group_and_makes_the_claimant_its_headman(
    foreman, monkeypatch
):
    setup_bot(
        foreman,
        monkeypatch,
        {(NEW_CHAT, 999): "administrator", (NEW_CHAT, CLAIMANT): "creator"},
    )
    claim(foreman)
    before = group_count(foreman)
    added_to_chat(foreman)
    group, members = new_group(foreman)
    assert group_count(foreman) == before + 1
    assert group.name == "ИВТ-21" and group.pilot_authorized and group.status == "active"
    assert group.bot_added_at is not None
    assert members == [("headman", CLAIMANT)]

    async def profile(db):
        return await db.get(GroupAIProfile, group.id)

    ready = run(foreman, profile)
    assert ready is not None and ready.state == "mapping"
    frame = last(foreman)
    assert "Группа «ИВТ-21» подключена" in frame["text"]
    assert "Создать приглашение" in labels(foreman)
    # the claim is spent: a second event for the same chat cannot change anything
    added_to_chat(foreman)
    assert new_group(foreman)[1] == [("headman", CLAIMANT)]


def test_a_bot_that_is_not_yet_admin_waits_and_connects_after_promotion(foreman, monkeypatch):
    statuses = {(NEW_CHAT, 999): "member", (NEW_CHAT, CLAIMANT): "creator"}
    setup_bot(foreman, monkeypatch, statuses)
    claim(foreman)
    added_to_chat(foreman, status="member")
    assert new_group(foreman)[1] == []  # the group exists, nobody is its headman yet
    assert "не администратор" in panel(foreman)["text"]
    statuses[(NEW_CHAT, 999)] = "administrator"
    added_to_chat(foreman, status="administrator")
    assert new_group(foreman)[1] == [("headman", CLAIMANT)]


def test_a_person_who_is_not_a_telegram_admin_cannot_connect_the_group(foreman, monkeypatch):
    setup_bot(
        foreman, monkeypatch, {(NEW_CHAT, 999): "administrator", (NEW_CHAT, CLAIMANT): "member"}
    )
    claim(foreman)
    added_to_chat(foreman)
    assert new_group(foreman)[1] == []
    assert "только администратор этой группы" in panel(foreman)["text"]


def test_adding_the_bot_without_a_claim_still_creates_the_group_with_no_headman(
    foreman, monkeypatch
):
    setup_bot(
        foreman, monkeypatch, {(NEW_CHAT, 999): "administrator", (NEW_CHAT, CLAIMANT): "creator"}
    )
    before = group_count(foreman)
    added_to_chat(foreman)  # CLAIMANT never pressed «Я староста»
    group, members = new_group(foreman)
    assert group_count(foreman) == before + 1 and members == []
    assert group.pilot_authorized and group.status == "active"

    async def environment(db):
        return await db.get(GroupAIProfile, group.id), (
            await db.scalars(select(BotOutbox).where(BotOutbox.method == "sendMessage"))
        ).all()

    profile, sent = run(foreman, environment)
    assert profile is not None
    hint = [json.loads(m.payload) for m in sent if json.loads(m.payload)["chat_id"] == NEW_CHAT]
    assert len(hint) == 1 and "Староста пока не выбран" in hint[0]["text"]
    button = hint[0]["reply_markup"]["inline_keyboard"][0][0]
    assert button["url"] == "https://t.me/studgroup_rf_bot?start=headman"
    added_to_chat(foreman)  # a repeated event neither duplicates the group nor repeats the note
    assert group_count(foreman) == before + 1
    assert len(run(foreman, environment)[1]) == len(sent)


def test_a_group_without_a_headman_is_marked_in_the_owner_list(foreman, monkeypatch):
    from test_bot_admin import private

    setup_bot(foreman, monkeypatch, {})
    added_to_chat(foreman)
    foreman.app.state.settings.bot_admin_user_id = 9001
    private(foreman, "/admin", uid=9001, update=900)
    markup = last(foreman)["reply_markup"]["inline_keyboard"]
    names = [button["text"] for row in markup for button in row]
    assert any(name.startswith("ИВТ-21") and "староста не выбран" in name for name in names)
    assert any(name == "Группа" for name in names)  # the pilot group has its headman


def test_an_administrator_picks_a_headmanless_group_later(foreman, monkeypatch):
    statuses = {(NEW_CHAT, 999): "administrator", (NEW_CHAT, CLAIMANT): "creator"}
    setup_bot(foreman, monkeypatch, statuses)
    added_to_chat(foreman, by=555)  # someone else added the bot; the group has no headman
    assert new_group(foreman)[1] == []
    deliver(foreman, "/start", uid=CLAIMANT)
    tap(foreman, "Я староста", uid=CLAIMANT)
    assert "Стать старостой: ИВТ-21" in labels(foreman)
    tap(foreman, "Стать старостой", uid=CLAIMANT)
    assert new_group(foreman)[1] == [("headman", CLAIMANT)]
    assert "Группа «ИВТ-21» подключена" in last(foreman)["text"]


def test_only_groups_the_person_administers_are_offered(foreman, monkeypatch):
    setup_bot(
        foreman, monkeypatch, {(NEW_CHAT, 999): "administrator", (NEW_CHAT, CLAIMANT): "member"}
    )
    added_to_chat(foreman, by=555)
    claim(foreman)
    assert not any("ИВТ-21" in label for label in labels(foreman))
    assert "ИВТ-21" not in last(foreman)["text"]


def test_a_second_claimant_cannot_take_a_group_that_got_a_headman(foreman, monkeypatch):
    statuses = {
        (NEW_CHAT, 999): "administrator",
        (NEW_CHAT, CLAIMANT): "creator",
        (NEW_CHAT, 78): "administrator",
    }
    setup_bot(foreman, monkeypatch, statuses)
    claim(foreman, uid=78)
    added_to_chat(foreman, by=555)
    deliver(foreman, "/start", uid=CLAIMANT)
    tap(foreman, "Я староста", uid=CLAIMANT)
    tap(foreman, "Стать старостой", uid=CLAIMANT)
    assert new_group(foreman)[1] == [("headman", CLAIMANT)]
    # the stale pick of the other person is refused: the group is no longer headman-less
    deliver(foreman, "/start", uid=78)
    assert "Я студент" in labels(foreman)  # still no group of their own


def test_a_headmanless_group_is_finished_by_start_typed_in_the_group(foreman, monkeypatch):
    statuses = {(NEW_CHAT, 999): "administrator", (NEW_CHAT, CLAIMANT): "creator"}
    setup_bot(foreman, monkeypatch, statuses)
    added_to_chat(foreman, by=555)
    claim(foreman)
    foreman.app.state._st_test_update += 1
    body = {
        "update_id": foreman.app.state._st_test_update,
        "message": {
            "message_id": 9,
            "date": int(datetime.now(UTC).timestamp()),
            "chat": {"id": NEW_CHAT, "type": "supergroup", "title": "ИВТ-21"},
            "from": {"id": CLAIMANT},
            "text": "/start@studgroup_rf_bot",
        },
    }
    assert send(foreman, body).status_code == 200
    assert new_group(foreman)[1] == [("headman", CLAIMANT)]


def test_the_deep_link_from_the_group_note_opens_the_headman_screen(foreman, monkeypatch):
    setup_bot(foreman, monkeypatch, {})
    deliver(foreman, "/start headman", uid=CLAIMANT)
    assert "Подключение группы" in last(foreman)["text"]
    assert "Добавить бота в группу" in json.dumps(last(foreman), ensure_ascii=False)


def test_a_group_already_in_the_database_cannot_be_taken_over(foreman, monkeypatch):
    setup_bot(foreman, monkeypatch, {(-1001, 999): "administrator", (-1001, CLAIMANT): "creator"})
    claim(foreman)
    added_to_chat(foreman, chat=-1001, title="Группа")

    async def roles(db):
        rows = await db.execute(select(Membership.role))
        return sorted(rows.scalars())

    assert run(foreman, roles) == ["headman"]  # the pilot group's own headman, nobody else
    assert "уже есть староста" in last(foreman)["text"]


def test_a_person_who_joined_a_group_after_claiming_cannot_connect_another(foreman, monkeypatch):
    setup_bot(
        foreman, monkeypatch, {(NEW_CHAT, 999): "administrator", (NEW_CHAT, CLAIMANT): "creator"}
    )
    claim(foreman)

    async def join_pilot_group(db):
        group = await db.scalar(select(Group).where(Group.telegram_chat_id == -1001))
        user = User(id=uuid.uuid4(), telegram_user_id=CLAIMANT, display_name="Участник")
        db.add(user)
        await db.flush()
        db.add(Membership(user_id=user.id, group_id=group.id, role="student", status="active"))

    run(foreman, join_pilot_group)
    added_to_chat(foreman)
    assert new_group(foreman)[1] == []
    assert "Один человек может быть только в одной группе" in panel(foreman)["text"]


def test_a_start_command_in_the_group_is_a_fallback_trigger(foreman, monkeypatch):
    setup_bot(
        foreman, monkeypatch, {(NEW_CHAT, 999): "administrator", (NEW_CHAT, CLAIMANT): "creator"}
    )
    claim(foreman)
    foreman.app.state._st_test_update += 1
    body = {
        "update_id": foreman.app.state._st_test_update,
        "message": {
            "message_id": 5,
            "date": int(datetime.now(UTC).timestamp()),
            "chat": {"id": NEW_CHAT, "type": "supergroup", "title": "ИВТ-21"},
            "from": {"id": CLAIMANT},
            "text": "/start@studgroup_rf_bot connect",
        },
    }
    assert send(foreman, body).status_code == 200
    assert new_group(foreman)[1] == [("headman", CLAIMANT)]


def test_the_new_headman_invites_a_student_who_joins_by_the_link(foreman, monkeypatch):
    setup_bot(
        foreman, monkeypatch, {(NEW_CHAT, 999): "administrator", (NEW_CHAT, CLAIMANT): "creator"}
    )
    claim(foreman)
    added_to_chat(foreman)
    tap(foreman, "Создать приглашение", uid=CLAIMANT)
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


def test_a_deputy_gets_no_invite_button(foreman, monkeypatch):
    async def make_deputy(db):
        (await db.scalar(select(Membership))).role = "deputy"

    run(foreman, make_deputy)
    deliver(foreman, "/start", uid=1)
    assert "Создать приглашение" not in labels(foreman)
    assert json.dumps(last(foreman)).count("invite") == 0
