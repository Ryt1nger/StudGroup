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
from studgroup.models import Group, GroupAIProfile, Membership, User

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
    assert new_group(foreman) is None
    assert "не администратор" in last(foreman)["text"]
    statuses[(NEW_CHAT, 999)] = "administrator"
    added_to_chat(foreman, status="administrator")
    assert new_group(foreman)[1] == [("headman", CLAIMANT)]


def test_a_person_who_is_not_a_telegram_admin_cannot_connect_the_group(foreman, monkeypatch):
    setup_bot(
        foreman, monkeypatch, {(NEW_CHAT, 999): "administrator", (NEW_CHAT, CLAIMANT): "member"}
    )
    claim(foreman)
    added_to_chat(foreman)
    assert new_group(foreman) is None
    assert "только её администратор" in last(foreman)["text"]


def test_nobody_is_made_headman_without_having_asked(foreman, monkeypatch):
    setup_bot(
        foreman, monkeypatch, {(NEW_CHAT, 999): "administrator", (NEW_CHAT, CLAIMANT): "creator"}
    )
    before = group_count(foreman)
    added_to_chat(foreman)  # CLAIMANT never pressed «Я староста»
    assert group_count(foreman) == before


def test_a_group_already_in_the_database_cannot_be_taken_over(foreman, monkeypatch):
    setup_bot(foreman, monkeypatch, {(-1001, 999): "administrator", (-1001, CLAIMANT): "creator"})
    claim(foreman)
    added_to_chat(foreman, chat=-1001, title="Группа")

    async def roles(db):
        rows = await db.execute(select(Membership.role))
        return sorted(rows.scalars())

    assert run(foreman, roles) == ["headman"]  # the pilot group's own headman, nobody else
    assert "уже есть в StudGroup" in last(foreman)["text"]


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
    assert new_group(foreman) is None
    assert "Один человек может быть только в одной группе" in last(foreman)["text"]


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
