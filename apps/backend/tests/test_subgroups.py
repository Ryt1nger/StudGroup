"""Split subjects: recognition, safe visibility and both Telegram setup flows."""

import json
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from test_bot_start import labels, tap
from test_headman import actor, click, deliver, enter, last, run
from test_headman import foreman as foreman_fixture  # noqa: F401

from studgroup import subgroups
from studgroup.models import (
    BotOutbox,
    Group,
    Homework,
    Membership,
    MemberSubgroup,
    Subgroup,
    SubjectSplit,
    User,
)

pytest_plugins = ["test_schedule_api"]


def type_answer(client, value, uid=1):
    deliver(client, value, uid=uid)


def configure_english(client):
    enter(client)
    click(client, "Подгруппы")
    click(client, "Добавить предмет")
    type_answer(client, "Английский язык")
    type_answer(client, "2")
    type_answer(client, "Иванова, 1 группа, ауд 305")
    type_answer(client, "Петров, 2 группа")
    assert "Подгруппа 1 · Иванова" in last(client)["text"]
    click(client, "Сохранить")


def add_student(client, telegram_id=33):
    async def create(db):
        _, _, group = await actor(db)
        user = User(telegram_user_id=telegram_id, display_name="Студент", username=None)
        db.add(user)
        await db.flush()
        member = Membership(user_id=user.id, group_id=group.id, role="student", status="active")
        db.add(member)
        await db.flush()
        return member.id

    return run(client, create)


def test_recognition_accepts_surnames_and_group_numbers_but_not_ambiguous_text():
    first = Subgroup(
        position=1,
        name="Гр. 1 · Иванова",
        signals=json.dumps(["Иванова", "1 группа", "ауд 305"]),
    )
    second = Subgroup(
        position=2,
        name="Гр. 2 · Петров",
        signals=json.dumps(["Петров", "2 группа"]),
    )
    rows = [first, second]
    assert subgroups.recognise(rows, text="У Ивановой завтра тест") is first
    assert subgroups.recognise(rows, text="Вторая подгруппа сдаёт эссе") is second
    assert subgroups.recognise(rows, label="Гр. 2 · Петров") is second
    assert subgroups.recognise(rows, text="Иванова и Петров проводят общий экзамен") is None
    assert subgroups.recognise(rows, text="Сделать упражнения 1–3") is None


def test_headman_configures_split_and_existing_student_gets_prompt(foreman_fixture):  # noqa: F811
    add_student(foreman_fixture)
    configure_english(foreman_fixture)

    async def state(db):
        split = await db.scalar(select(SubjectSplit))
        rows = (
            await db.scalars(
                select(Subgroup).where(Subgroup.split_id == split.id).order_by(Subgroup.position)
            )
        ).all()
        notices = (
            await db.scalars(
                select(BotOutbox)
                .where(BotOutbox.method == "sendMessage")
                .order_by(BotOutbox.available_at.desc())
            )
        ).all()
        payloads = [json.loads(row.payload) for row in notices]
        return split, rows, payloads

    split, rows, payloads = run(foreman_fixture, state)
    assert split.subject_name == "Английский язык"
    assert [row.name for row in rows] == ["Подгруппа 1 · Иванова", "Подгруппа 2 · Петров"]
    assert any(p.get("chat_id") == 33 and "Выберите свою" in p.get("text", "") for p in payloads)


def test_student_picks_and_changes_subgroup_from_start_panel(foreman_fixture):  # noqa: F811
    add_student(foreman_fixture)
    configure_english(foreman_fixture)

    deliver(foreman_fixture, "/start subgroups", uid=33)
    assert "К какой подгруппе" in last(foreman_fixture)["text"]
    assert "Подгруппа 1 · Иванова" in labels(foreman_fixture)
    tap(foreman_fixture, "Подгруппа 1", uid=33)
    assert "Английский язык: Подгруппа 1 · Иванова" in last(foreman_fixture)["text"]
    tap(foreman_fixture, "Изменить", uid=33)
    tap(foreman_fixture, "Подгруппа 2", uid=33)

    async def picked(db):
        user = await db.scalar(select(User).where(User.telegram_user_id == 33))
        member = await db.scalar(select(Membership).where(Membership.user_id == user.id))
        choice = await db.scalar(
            select(MemberSubgroup).where(MemberSubgroup.membership_id == member.id)
        )
        subgroup = await db.get(Subgroup, choice.subgroup_id)
        hidden = await subgroups.hidden(db, member.group_id, user.id)
        return subgroup.name, len(hidden)

    assert run(foreman_fixture, picked) == ("Подгруппа 2 · Петров", 1)


def test_unanswered_student_sees_all_subgroups(foreman_fixture):  # noqa: F811
    member_id = add_student(foreman_fixture)
    configure_english(foreman_fixture)

    async def state(db):
        member = await db.get(Membership, member_id)
        return await subgroups.hidden(db, member.group_id, member.user_id)

    assert run(foreman_fixture, state) == set()


def test_subgroup_profile_api_lists_and_updates_choice(client):
    async def seed(db):
        group = await db.scalar(select(Group).where(Group.telegram_chat_id == -1001))
        split = await subgroups.create_split(
            db,
            group,
            "Информатика",
            [("Гр. 1 · Сидоров", ["Сидоров"]), ("Гр. 2 · Орлова", ["Орлова"])],
        )
        rows = (await subgroups.splits(db, group.id))[0][1]
        return str(split.id), [str(row.id) for row in rows]

    split_id, subgroup_ids = run(client, seed)
    headers = {"Authorization": "Bearer valid"}
    before = client.get("/v1/subgroups", headers=headers)
    assert before.status_code == 200
    assert before.json()["items"][0]["selected_subgroup_id"] is None

    changed = client.put(
        f"/v1/subgroups/{split_id}",
        headers=headers,
        json={"subgroup_id": subgroup_ids[1]},
    )
    assert changed.status_code == 200
    assert changed.json()["items"][0]["selected_subgroup_id"] == subgroup_ids[1]
    after = client.get("/v1/subgroups", headers=headers)
    assert after.json()["items"][0]["selected_subgroup_id"] == subgroup_ids[1]


def test_subgroup_profile_api_rejects_option_from_another_subject(client):
    async def seed(db):
        group = await db.scalar(select(Group).where(Group.telegram_chat_id == -1001))
        first = await subgroups.create_split(
            db, group, "Английский", [("A", ["A1"]), ("B", ["B1"])]
        )
        second = await subgroups.create_split(
            db, group, "Информатика", [("1", ["Иванов"]), ("2", ["Петров"])]
        )
        configured = await subgroups.splits(db, group.id)
        foreign = next(rows[0] for split, rows in configured if split.id == second.id)
        return str(first.id), str(second.id), str(foreign.id)

    first_id, second_id, foreign_id = run(client, seed)
    assert first_id != second_id
    response = client.put(
        f"/v1/subgroups/{first_id}",
        headers={"Authorization": "Bearer valid"},
        json={"subgroup_id": foreign_id},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_request"


def test_homework_list_hides_only_the_other_chosen_subgroup(client):
    async def seed(db):
        group = await db.scalar(select(Group).where(Group.telegram_chat_id == -1001))
        user = await db.scalar(select(User).where(User.telegram_user_id == 1))
        member = await db.scalar(select(Membership).where(Membership.user_id == user.id))
        split = await subgroups.create_split(
            db, group, "Английский", [("Иванова", ["Иванова"]), ("Петров", ["Петров"])]
        )
        options = (await subgroups.splits(db, group.id))[0][1]
        await subgroups.choose(db, member, split.id, options[0].id)
        now = datetime.now(UTC)
        for title, subgroup_id in [
            ("Для Ивановой", options[0].id),
            ("Для Петрова", options[1].id),
            ("Общее", None),
        ]:
            db.add(
                Homework(
                    group_id=group.id,
                    subject_id=uuid.uuid4(),
                    subject_name="Английский",
                    title=title,
                    description=title,
                    status="published",
                    created_at=now,
                    updated_at=now,
                    delete_at=now + timedelta(days=30),
                    subgroup_id=subgroup_id,
                )
            )

    run(client, seed)
    response = client.get("/v1/homework", headers={"Authorization": "Bearer valid"})
    assert response.status_code == 200
    titles = {row["title"] for row in response.json()["items"]}
    assert titles == {"Для Ивановой", "Общее"}
    labelled = next(row for row in response.json()["items"] if row["title"] == "Для Ивановой")
    assert labelled["subgroup"]["name"] == "Иванова"


def test_owner_can_preview_headman_path_without_writing_data(foreman_fixture):  # noqa: F811
    foreman_fixture.app.state.settings.owner_telegram_user_id = 1
    deliver(foreman_fixture, "/test_subgroups")
    assert labels(foreman_fixture)[:2] == ["Пройти как староста", "Пройти как студент"]

    tap(foreman_fixture, "как староста", uid=1)
    for label in [
        "Добавить предмет",
        "Английский язык",
        "2 подгруппы",
        "Иванова",
        "Петров",
        "Сохранить демо-настройку",
    ]:
        tap(foreman_fixture, label, uid=1)
    assert "Путь старосты пройден" in last(foreman_fixture)["text"]

    async def count(db):
        return len((await db.scalars(select(SubjectSplit))).all())

    assert run(foreman_fixture, count) == 0


def test_owner_can_preview_student_path_and_change_choice(foreman_fixture):  # noqa: F811
    foreman_fixture.app.state.settings.bot_admin_user_id = 1
    deliver(foreman_fixture, "/test_subgroups")
    tap(foreman_fixture, "как студент", uid=1)
    tap(foreman_fixture, "Гр. 1 · Иванова", uid=1)
    tap(foreman_fixture, "Гр. 2 · Орлова", uid=1)
    assert "Английский язык: Гр. 1 · Иванова" in last(foreman_fixture)["text"]
    assert "Информатика: Гр. 2 · Орлова" in last(foreman_fixture)["text"]
    tap(foreman_fixture, "Изменить английский", uid=1)
    tap(foreman_fixture, "Гр. 2 · Петров", uid=1)
    assert "Английский язык: Гр. 2 · Петров" in last(foreman_fixture)["text"]


def test_subgroup_preview_is_owner_only(foreman_fixture):  # noqa: F811
    foreman_fixture.app.state.settings.owner_telegram_user_id = 1
    deliver(foreman_fixture, "/test_subgroups", uid=33)
    assert "только владельцу" in last(foreman_fixture)["text"]
    assert labels(foreman_fixture) == ["‹ В начало"]
