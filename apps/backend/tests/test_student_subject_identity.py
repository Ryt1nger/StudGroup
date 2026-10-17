"""Student API must use the one subject_id contract (see studgroup.subject_identity)."""

import asyncio
import json
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from studgroup.models import AcademicDeadline, Group
from studgroup.subject_identity import subject_uuid

pytest_plugins = ["test_schedule_api"]
HEADERS = {"Authorization": "Bearer valid"}


def seed_deadlines(client, own_names, foreign_name):
    async def seed():
        async with AsyncSession(client.app.state.engine, expire_on_commit=False) as db:
            own = await db.scalar(select(Group).where(Group.telegram_chat_id == -1001))
            foreign = await db.scalar(select(Group).where(Group.telegram_chat_id == -1002))
            now = datetime.now(UTC)
            created = []
            for group, name in [(own, n) for n in own_names] + [(foreign, foreign_name)]:
                row = AcademicDeadline(
                    group_id=group.id,
                    import_key=f"test:{group.id}:{name}",
                    kind="exam",
                    subject=name,
                    title=f"Экзамен: {name}",
                    description="",
                    deadline_at=now + timedelta(days=5),
                    date_only=True,
                    needs_clarification=False,
                    source_message_ids=json.dumps([]),
                    created_at=now,
                    delete_at=now + timedelta(days=90),
                )
                db.add(row)
                await db.flush()
                created.append((group.id, name, row.id))
            await db.commit()
            return created

    return asyncio.run(seed())


def test_deadline_subject_id_is_the_casefold_formula_not_a_normalised_one(client):
    names = ["Ёж и Лес", "Еж и Лес", "Дифф  уравнения", "Дифф уравнения"]
    created = seed_deadlines(client, names, "Чужой предмет")
    ids = {}
    for group_id, name, row_id in created[:-1]:
        item = client.get(f"/v1/deadlines/{row_id}", headers=HEADERS).json()["item"]
        assert item["confirmed_subject"]["name"] == name
        assert item["confirmed_subject"]["id"] == str(subject_uuid(group_id, name))
        ids[name] = item["confirmed_subject"]["id"]
    # ё/е and doubled spaces are different subjects: nothing is merged automatically
    assert len(set(ids.values())) == 4


def test_overview_keeps_variants_apart_and_never_leaks_another_group(client):
    names = ["Ёж и Лес", "Еж и Лес", "Ёж и лес"]
    created = seed_deadlines(client, names, "Чужой предмет")
    group_id = created[0][0]
    response = client.get("/v1/subjects", headers=HEADERS)
    assert response.status_code == 200
    items = {item["name"]: item for item in response.json()["items"]}
    # "Ёж и Лес" and "Ёж и лес" differ only by case: one subject; "Еж и Лес" is another
    assert items["Ёж и Лес"]["deadline_count"] == 2
    assert items["Еж и Лес"]["deadline_count"] == 1
    assert items["Ёж и Лес"]["id"] == str(subject_uuid(group_id, "Ёж и Лес"))
    assert items["Еж и Лес"]["id"] == str(subject_uuid(group_id, "Еж и Лес"))
    assert items["Ёж и Лес"]["id"] != items["Еж и Лес"]["id"]
    assert "Чужой предмет" not in items


def test_same_name_in_two_groups_is_two_subject_ids(client):
    created = seed_deadlines(client, ["Общий предмет"], "Общий предмет")
    (own_group, _, own_row), (foreign_group, _, _) = created
    item = client.get(f"/v1/deadlines/{own_row}", headers=HEADERS).json()["item"]
    assert item["confirmed_subject"]["id"] == str(subject_uuid(own_group, "Общий предмет"))
    assert subject_uuid(own_group, "Общий предмет") != subject_uuid(foreign_group, "Общий предмет")


def test_placeholder_subjects_are_hidden_but_not_given_an_id(client):
    created = seed_deadlines(client, ["Предмет не указан", "Неизвестно"], "Чужой предмет")
    for _, _, row_id in created[:2]:
        item = client.get(f"/v1/deadlines/{row_id}", headers=HEADERS).json()["item"]
        assert item["confirmed_subject"] is None
    names = [i["name"] for i in client.get("/v1/subjects", headers=HEADERS).json()["items"]]
    assert "Предмет не указан" not in names
    assert "Неизвестно" not in names
