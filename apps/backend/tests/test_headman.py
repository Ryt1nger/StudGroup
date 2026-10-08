import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from test_ingestion import send

from studgroup import headman_domain as domain
from studgroup.headman_digest import tick
from studgroup.models import (
    BotOutbox,
    Group,
    HeadmanAudit,
    HeadmanSettings,
    Homework,
    MaterialLink,
    Membership,
    SchedulePattern,
    User,
)

pytest_plugins = ["test_schedule_api"]


def run(client, fn):
    async def invoke():
        async with AsyncSession(client.app.state.engine, expire_on_commit=False) as db:
            result = await fn(db)
            await db.commit()
            return result

    return asyncio.run(invoke())


@pytest.fixture
def foreman(client):
    async def promote(db):
        member = await db.scalar(select(Membership))
        member.role = "headman"

    run(client, promote)
    return client


def deliver(client, text="", callback=None, uid=1, forward=None):
    client.app.state._st_test_update = getattr(client.app.state, "_st_test_update", 8000) + 1
    ident = client.app.state._st_test_update
    message = {
        "message_id": ident,
        "date": int(datetime.now(UTC).timestamp()),
        "chat": {"id": uid, "type": "private"},
        "from": {"id": uid},
        "text": text,
    }
    if forward:
        message["forward_origin"] = {"type": "hidden_user", "date": int(forward.timestamp())}
    body = {"update_id": ident, "message": message}
    if callback:
        message["message_id"] = 51 if callback.startswith("s:") else 52
        body = {
            "update_id": ident,
            "callback_query": {
                "id": str(ident),
                "from": {"id": uid},
                "message": message,
                "data": callback,
            },
        }
    response = send(client, body)
    assert response.status_code == 200, response.text


def last(client):
    async def read(db):
        row = await db.scalar(
            select(BotOutbox)
            .where(BotOutbox.method.in_(["sendMessage", "renderPanel", "panelAux"]))
            .order_by(BotOutbox.available_at.desc())
            .limit(1)
        )
        return json.loads(row.payload)

    return run(client, read)


def click(client, label):
    buttons = last(client).get("reply_markup", {}).get("inline_keyboard", [])
    button = next(b for row in buttons for b in row if label in b["text"])
    callback = button["callback_data"]
    deliver(client, callback=callback)
    return callback


def enter(client):
    deliver(client, "/st")
    click(client, "Группа")


async def actor(db):
    group = await db.scalar(select(Group).where(Group.pilot_authorized.is_(True)))
    return await domain.access(db, 1, group.id)


def test_students_cannot_open_st_or_impersonate_callback(client):
    deliver(client, "/st")
    assert "Нет роли" in last(client)["text"]
    deliver(client, callback="s:invalid:group0", uid=2)

    async def read_ack(db):
        row = await db.scalar(
            select(BotOutbox)
            .where(BotOutbox.method == "answerCallbackQuery")
            .order_by(BotOutbox.available_at.desc())
            .limit(1)
        )
        assert "устарела" in json.loads(row.payload)["text"]

    run(client, read_ack)


def test_create_is_private_until_confirm_and_replay_cannot_duplicate(foreman):
    enter(foreman)
    click(foreman, "Домашние задания")
    click(foreman, "Добавить ДЗ")
    for text in ["Математика", "Решить задачи", "Номера 1–3"]:
        deliver(foreman, text)
    click(foreman, "Дата / время")
    deliver(foreman, "12.10.2026 18:30")
    assert run(foreman, lambda db: db.scalar(select(func.count()).select_from(Homework))) == 0
    old = click(foreman, "Подтвердить изменение")
    deliver(foreman, callback=old)
    assert run(foreman, lambda db: db.scalar(select(func.count()).select_from(Homework))) == 1
    assert run(foreman, lambda db: db.scalar(select(func.count()).select_from(HeadmanAudit))) == 1

    async def ack(db):
        row = await db.scalar(
            select(BotOutbox)
            .where(BotOutbox.method == "answerCallbackQuery")
            .order_by(BotOutbox.available_at.desc())
            .limit(1)
        )
        assert "устарела" in json.loads(row.payload)["text"]

    run(foreman, ack)
    data = foreman.get("/v1/homework", headers={"Authorization": "Bearer valid"}).json()
    assert data["items"][0]["verification_state"] == "manual_confirmed"


def test_role_revocation_blocks_draft_save(foreman):
    enter(foreman)
    click(foreman, "Домашние задания")
    click(foreman, "Добавить ДЗ")

    async def revoke(db):
        member = await db.scalar(select(Membership))
        member.role = "student"

    run(foreman, revoke)
    deliver(foreman, "Предмет")
    assert "Нет прав" in last(foreman)["text"]


def test_group_boundary_and_revision_conflict(foreman):
    async def scenario(db):
        user, member, group = await actor(db)
        key, _ = await domain.create_card(
            db,
            group,
            user,
            member,
            "h",
            {"subject": "Математика", "title": "ДЗ", "description": "Задачи"},
        )
        await domain.mutate(db, group, user, member, key, 1, "edit", {"title": "Новая версия"})
        with pytest.raises(domain.StError, match="изменилась"):
            await domain.mutate(db, group, user, member, key, 1, "edit", {"title": "Старая"})
        other = await db.scalar(select(Group).where(Group.pilot_authorized.is_(False)))
        with pytest.raises(domain.StError):
            await domain.entity(db, other, key)

    run(foreman, scenario)


def test_material_convert_merge_and_undo(foreman):
    async def scenario(db):
        user, member, group = await actor(db)
        key, _ = await domain.create_card(
            db,
            group,
            user,
            member,
            "h",
            {"subject": "Математика", "title": "Задачи", "description": "Описание"},
        )
        _, row = await domain.entity(db, group, key)
        await domain.add_material(
            db, group, user, member, key, row.revision, "Учебник", "https://example.com/book"
        )
        assert await db.scalar(select(func.count()).select_from(MaterialLink)) == 1
        aid = await domain.convert(db, group, user, member, key, row.revision)
        assert row.status == "cancelled"
        await domain.undo(db, group, user, member, aid)
        assert row.status == "published"
        target, _ = await domain.create_card(
            db,
            group,
            user,
            member,
            "h",
            {"subject": "Математика", "title": "Другие", "description": "Дополнение"},
        )
        _, other = await domain.entity(db, group, target)
        aid = await domain.merge(db, group, user, member, key, row.revision, target, other.revision)
        await domain.undo(db, group, user, member, aid)
        assert row.status == "published" and other.description == "Дополнение"

    run(foreman, scenario)


def test_once_schedule_move_preserves_other_weeks_and_subject_route(foreman):
    from studgroup.headman_schedule import change

    async def scenario(db):
        user, member, group = await actor(db)
        pattern = await db.scalar(
            select(SchedulePattern).where(SchedulePattern.group_id == group.id)
        )
        ident = f"{pattern.id}:2026-10-12"
        await change(
            db,
            group,
            user,
            member,
            ident,
            1,
            0,
            "once",
            {
                "starts_at": datetime(2026, 10, 13, 7, tzinfo=UTC),
                "ends_at": datetime(2026, 10, 13, 8, tzinfo=UTC),
            },
        )
        assert pattern.weekday == 0 and pattern.revision == 1
        return ident

    ident = run(foreman, scenario)
    headers = {"Authorization": "Bearer valid"}
    calendar = foreman.get("/v1/schedule?start=2026-10-12&end=2026-10-19", headers=headers).json()
    assert next(l for l in calendar["lessons"] if l["id"] == ident)["starts_at"].startswith(
        "2026-10-13"
    )
    assert any(l["starts_at"].startswith("2026-10-19") for l in calendar["lessons"])
    assert foreman.get(f"/v1/lessons/{ident}/subject", headers=headers).status_code == 200


def test_invite_membership_check_and_single_use(foreman, monkeypatch):
    async def issue(db):
        _, member, group = await actor(db)
        return (await domain.issue_invites(db, group, member, 1))[0]

    code = run(foreman, issue)

    async def no(*args):
        return False

    monkeypatch.setattr(domain, "telegram_member", no)
    deliver(foreman, "/start invite_" + code, uid=33)
    assert "Не удалось подтвердить" in last(foreman)["text"]

    async def yes(*args):
        return True

    monkeypatch.setattr(domain, "telegram_member", yes)
    deliver(foreman, "/start invite_" + code, uid=33)
    assert "подключён" in last(foreman)["text"]
    deliver(foreman, "/start invite_" + code, uid=34)
    assert "использовано" in last(foreman)["text"]
    assert (
        run(foreman, lambda db: db.scalar(select(User.id).where(User.telegram_user_id == 34)))
        is None
    )


def test_forward_keeps_source_time(foreman):
    sent = datetime.now(UTC) - timedelta(days=9)
    deliver(foreman, "Решить номера", forward=sent)
    click(foreman, "Добавить ДЗ")
    deliver(foreman, "Математика")
    deliver(foreman, "Номера")
    click(foreman, "Срок пока неизвестен")
    click(foreman, "Подтвердить изменение")

    async def check(db):
        row = await db.scalar(select(Homework))
        assert domain.utc(row.source_message_at).date() == sent.date()

    run(foreman, check)
    assert (
        foreman.get("/v1/homework", headers={"Authorization": "Bearer valid"}).json()["items"] == []
    )


def test_digest_nonempty_dedup_and_opt_in(foreman):
    now = datetime.now(UTC)

    async def seed(db):
        _, member, group = await actor(db)
        db.add(
            HeadmanSettings(
                membership_id=member.id, questions_enabled=True, digest_times="07:00,18:00"
            )
        )
        db.add(
            Homework(
                group_id=group.id,
                subject_id=uuid.uuid4(),
                subject_name="Математика",
                title="Уточнить",
                description="Задачи",
                status="needs_clarification",
                created_at=now,
                updated_at=now,
                delete_at=now + timedelta(days=90),
            )
        )

    run(foreman, seed)
    midday = now.replace(hour=10, minute=0)
    asyncio.run(tick(foreman.app.state.engine, midday))
    asyncio.run(tick(foreman.app.state.engine, midday + timedelta(minutes=1)))
    assert run(foreman, lambda db: db.scalar(select(func.count()).select_from(BotOutbox))) == 1
    assert "Уточнить" in last(foreman)["text"]


def test_deputy_cannot_manage_members_or_override_headman(foreman):
    async def scenario(db):
        user, member, group = await actor(db)
        key, _ = await domain.create_card(
            db,
            group,
            user,
            member,
            "h",
            {"subject": "Математика", "title": "ДЗ", "description": "Задачи"},
        )
        member.role = "deputy"
        _, row = await domain.entity(db, group, key)
        with pytest.raises(domain.StError, match="решение старосты"):
            await domain.mutate(
                db, group, user, member, key, row.revision, "edit", {"title": "Новая"}
            )
        with pytest.raises(domain.StError):
            await domain.issue_invites(db, group, member, 1)

    run(foreman, scenario)


def test_empty_digest_and_night_do_not_send_messages(foreman):
    asyncio.run(tick(foreman.app.state.engine))
    assert run(foreman, lambda db: db.scalar(select(func.count()).select_from(BotOutbox))) == 0

    async def seed(db):
        _, member, group = await actor(db)
        db.add(
            HeadmanSettings(membership_id=member.id, questions_enabled=True, digest_times="07:00")
        )
        now = datetime.now(UTC)
        db.add(
            Homework(
                group_id=group.id,
                subject_id=uuid.uuid4(),
                subject_name="Математика",
                title="Вопрос",
                description="Уточнить",
                status="needs_clarification",
                created_at=now,
                updated_at=now,
                delete_at=now + timedelta(days=90),
            )
        )

    run(foreman, seed)
    asyncio.run(tick(foreman.app.state.engine, datetime.now(UTC).replace(hour=22)))  # 01:00 Moscow
    assert run(foreman, lambda db: db.scalar(select(func.count()).select_from(BotOutbox))) == 0


def test_stale_undo_does_not_overwrite_newer_edit(foreman):
    async def scenario(db):
        user, member, group = await actor(db)
        key, _ = await domain.create_card(
            db,
            group,
            user,
            member,
            "h",
            {"subject": "Математика", "title": "ДЗ", "description": "Задачи"},
        )
        aid = await domain.mutate(
            db, group, user, member, key, 1, "edit", {"title": "Первая правка"}
        )
        await domain.mutate(db, group, user, member, key, 2, "edit", {"title": "Вторая правка"})
        with pytest.raises(domain.StError, match="изменилась"):
            await domain.undo(db, group, user, member, aid)
        _, row = await domain.entity(db, group, key)
        assert row.title == "Вторая правка"

    run(foreman, scenario)


def test_settings_require_confirmation_and_summary_opens(foreman):
    enter(foreman)
    click(foreman, "Настройки")
    click(foreman, "Включить вопросы")
    assert (
        run(foreman, lambda db: db.scalar(select(func.count()).select_from(HeadmanSettings))) == 0
    )
    click(foreman, "Подтвердить изменение")

    async def check(db):
        config = await db.scalar(select(HeadmanSettings))
        assert config.questions_enabled is True

    run(foreman, check)
    click(foreman, "Главное меню")
    click(foreman, "Сводка группы")
    assert "Ожидают разбора" in last(foreman)["text"]


def test_owner_role_assignment_is_explicit(client):
    from test_bot_admin import private, session

    private(client, "/admin")
    s, messages = session(client)
    private(
        client,
        "",
        update=101,
        callback=messages[-1]["reply_markup"]["inline_keyboard"][0][0]["callback_data"],
    )
    s, _ = session(client)
    private(client, "", update=102, callback=f"a:{s.nonce}:headman")
    private(client, "1", update=103)
    assert run(client, lambda db: db.scalar(select(Membership.role))) == "student"
    s, _ = session(client)
    private(client, "", update=104, callback=f"a:{s.nonce}:yes")
    assert run(client, lambda db: db.scalar(select(Membership.role))) == "headman"


def test_homework_and_control_points_have_separate_lists_search_and_archive(foreman):
    async def seed(db):
        user, member, group = await actor(db)
        values = {
            "subject": "Математика",
            "title": "Общая задача",
            "description": "Описание",
            "deadline_at": datetime.now(UTC) + timedelta(days=3),
        }
        await domain.create_card(db, group, user, member, "h", values.copy())
        await domain.create_card(db, group, user, member, "e", values.copy())
        values["title"] = "АрхивКТ"
        key, _ = await domain.create_card(db, group, user, member, "e", values)
        await domain.mutate(db, group, user, member, key, 1, "cancel")

    run(foreman, seed)
    enter(foreman)
    root = [b["text"] for r in last(foreman)["reply_markup"]["inline_keyboard"] for b in r]
    assert "Домашние задания" in root and "КТ" in root and "ДЗ и КТ" not in root
    click(foreman, "Домашние задания")
    buttons = [b["text"] for r in last(foreman)["reply_markup"]["inline_keyboard"] for b in r]
    assert "Добавить ДЗ" in buttons and "Добавить КТ" not in buttons
    assert not any(label.startswith("КТ ·") for label in buttons)
    click(foreman, "Главное меню")
    click(foreman, "КТ")
    buttons = [b["text"] for r in last(foreman)["reply_markup"]["inline_keyboard"] for b in r]
    assert "Добавить КТ" in buttons and "Добавить ДЗ" not in buttons
    assert not any(label.startswith("ДЗ ·") for label in buttons)
    click(foreman, "Архив")
    click(foreman, "Найти по")
    deliver(foreman, "АрхивКТ")
    assert "КТ · 1" in last(foreman)["text"]
    assert any(
        "АрхивКТ" in b["text"] for r in last(foreman)["reply_markup"]["inline_keyboard"] for b in r
    )


def test_expired_invite_never_activates_user(foreman):
    from studgroup.models import GroupInvitation

    async def issue(db):
        _, member, group = await actor(db)
        code = (await domain.issue_invites(db, group, member, 1))[0]
        invitation = await db.scalar(select(GroupInvitation))
        invitation.expires_at = datetime.now(UTC) - timedelta(seconds=1)
        return code

    code = run(foreman, issue)
    deliver(foreman, "/start invite_" + code, uid=55)
    assert "истекло" in last(foreman)["text"]
    assert (
        run(foreman, lambda db: db.scalar(select(User.id).where(User.telegram_user_id == 55)))
        is None
    )
