"""Headman/deputy editing of cards from the Mini App: rights, revisions, locks, audit."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from test_homework import seed_card

from studgroup.models import (
    AcademicDeadline,
    Group,
    HeadmanAudit,
    HeadmanDecision,
    Homework,
    Membership,
    RawMessage,
)

pytest_plugins = ["test_schedule_api"]
HEADERS = {"Authorization": "Bearer valid"}


def set_role(client, role):
    async def run():
        async with AsyncSession(client.app.state.engine) as db:
            member = await db.scalar(select(Membership))
            member.role = role
            await db.commit()

    asyncio.run(run())


def seed_deadline(client):
    async def run():
        async with AsyncSession(client.app.state.engine, expire_on_commit=False) as db:
            group = await db.scalar(select(Group).where(Group.telegram_chat_id == -1001))
            now = datetime.now(UTC)
            raw = RawMessage(
                group_id=group.id,
                telegram_message_id=9,
                text="КТ",
                revision=1,
                message_date=now,
                version_date=now,
                processing_state="completed",
                delete_at=now + timedelta(days=30),
            )
            db.add(raw)
            await db.flush()
            row = AcademicDeadline(
                group_id=group.id,
                raw_message_id=raw.id,
                import_key=f"k:{uuid.uuid4()}",
                kind="control_point",
                subject="Математика",
                title="КТ1",
                description="Тема: пределы",
                created_at=now,
                delete_at=now + timedelta(days=90),
                source_message_ids="[9]",
                needs_clarification=True,
                revision=1,
            )
            db.add(row)
            await db.commit()
            return str(row.id)

    return asyncio.run(run())


def count(client, model):
    async def run():
        async with AsyncSession(client.app.state.engine) as db:
            return await db.scalar(select(func.count()).select_from(model))

    return asyncio.run(run())


def test_a_student_cannot_edit_and_the_card_is_unchanged(client):
    card = seed_card(client)
    response = client.patch(
        f"/v1/homework/{card}", json={"expected_revision": 1, "title": "Взлом"}, headers=HEADERS
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "permission_denied"
    detail = client.get(f"/v1/homework/{card}", headers=HEADERS).json()
    assert detail["title"] == "Задачи" and detail["revision"] == 1
    assert "homework.edit" not in detail["permissions"]


def test_the_headman_sees_the_edit_permissions_and_edits_any_field(client):
    card = seed_card(client)
    set_role(client, "headman")
    assert (
        "homework.edit" in client.get(f"/v1/homework/{card}", headers=HEADERS).json()["permissions"]
    )
    response = client.patch(
        f"/v1/homework/{card}",
        json={
            "expected_revision": 1,
            "title": "  Задачи   по пределам ",
            "description": "Решить номера 1–5",
            "subject_name": "Математический анализ",
            "deadline": {"at": "2026-11-02T10:00:00+03:00", "date_only": False},
        },
        headers=HEADERS,
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["title"] == "Задачи по пределам"
    assert data["description"] == "Решить номера 1–5"
    assert data["subject"]["name"] == "Математический анализ"
    assert data["deadline"]["state"] == "known" and data["deadline"]["at"].startswith(
        "2026-11-02T07:00"
    )
    assert data["revision"] == 2
    assert data["verification_state"] == "manual_confirmed"
    assert count(client, HeadmanAudit) == 1

    # the decision is locked, so the AI can never overwrite this edit
    async def decision():
        async with AsyncSession(client.app.state.engine) as db:
            row = await db.scalar(select(HeadmanDecision))
            return row.locked, row.entity_key.startswith("h:")

    assert asyncio.run(decision()) == (True, True)


def test_a_stale_revision_is_rejected_with_a_conflict_and_nothing_changes(client):
    card = seed_card(client)
    set_role(client, "headman")
    assert (
        client.patch(
            f"/v1/homework/{card}",
            json={"expected_revision": 1, "title": "Первая правка"},
            headers=HEADERS,
        ).status_code
        == 200
    )
    stale = client.patch(
        f"/v1/homework/{card}",
        json={"expected_revision": 1, "title": "Устаревшая"},
        headers=HEADERS,
    )
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "revision_conflict"
    assert client.get(f"/v1/homework/{card}", headers=HEADERS).json()["title"] == "Первая правка"


def test_invalid_edits_are_rejected_and_do_not_change_the_card(client):
    card = seed_card(client)
    set_role(client, "headman")
    for body in (
        {"expected_revision": 1},
        {"expected_revision": 1, "title": "   "},
        {"expected_revision": 1, "title": "x" * 300},
        {"expected_revision": 1, "subject_name": ""},
        {"expected_revision": 1, "deadline": {"at": None, "date_only": True}},
        {"expected_revision": 1, "deadline": {"at": "2026-11-02T10:00:00", "date_only": False}},
        {"expected_revision": 0, "title": "Новое"},
        {"expected_revision": 1, "status": "cancelled"},
        {"expected_revision": 1, "title": None},
    ):
        response = client.patch(f"/v1/homework/{card}", json=body, headers=HEADERS)
        assert response.status_code == 422, body
    assert client.get(f"/v1/homework/{card}", headers=HEADERS).json()["revision"] == 1


def test_the_deadline_can_be_cleared_and_the_description_can_be_emptied(client):
    card = seed_card(client)
    set_role(client, "headman")
    client.patch(
        f"/v1/homework/{card}",
        json={"expected_revision": 1, "deadline": {"at": "2026-11-02T10:00:00+03:00"}},
        headers=HEADERS,
    )
    cleared = client.patch(
        f"/v1/homework/{card}",
        json={"expected_revision": 2, "deadline": None, "description": ""},
        headers=HEADERS,
    ).json()
    assert cleared["deadline"]["state"] == "unknown"
    assert cleared["description"] is None


def test_editing_only_the_description_does_not_notify_students(client):
    from studgroup.models import Notification

    card = seed_card(client)
    set_role(client, "headman")
    client.patch(
        f"/v1/homework/{card}",
        json={"expected_revision": 1, "description": "Новое описание"},
        headers=HEADERS,
    )
    assert count(client, Notification) == 0
    client.patch(
        f"/v1/homework/{card}",
        json={"expected_revision": 2, "title": "Новое название"},
        headers=HEADERS,
    )
    assert count(client, Notification) == 1


def test_a_deputy_can_edit_but_not_a_decision_the_headman_confirmed(client):
    card = seed_card(client)
    set_role(client, "headman")
    assert (
        client.patch(
            f"/v1/homework/{card}",
            json={"expected_revision": 1, "title": "Решение старосты"},
            headers=HEADERS,
        ).status_code
        == 200
    )
    set_role(client, "deputy")
    blocked = client.patch(
        f"/v1/homework/{card}",
        json={"expected_revision": 2, "title": "Правка помощника"},
        headers=HEADERS,
    )
    assert blocked.status_code == 403
    assert client.get(f"/v1/homework/{card}", headers=HEADERS).json()["title"] == "Решение старосты"


def test_a_revoked_headman_cannot_edit_any_more(client):
    card = seed_card(client)
    set_role(client, "headman")
    assert (
        client.patch(
            f"/v1/homework/{card}", json={"expected_revision": 1, "title": "Ок"}, headers=HEADERS
        ).status_code
        == 200
    )
    set_role(client, "student")
    assert (
        client.patch(
            f"/v1/homework/{card}",
            json={"expected_revision": 2, "title": "Уже нельзя"},
            headers=HEADERS,
        ).status_code
        == 403
    )


def test_a_card_of_another_group_is_not_found_for_the_editor(client):
    card = seed_card(client)
    set_role(client, "headman")

    async def move():
        async with AsyncSession(client.app.state.engine) as db:
            other = await db.scalar(select(Group).where(Group.telegram_chat_id == -1002))
            row = await db.get(Homework, uuid.UUID(card))
            row.group_id = other.id
            await db.commit()

    asyncio.run(move())
    response = client.patch(
        f"/v1/homework/{card}", json={"expected_revision": 1, "title": "Чужая"}, headers=HEADERS
    )
    assert response.status_code == 404


def test_the_headman_edits_a_deadline_and_the_response_matches_the_detail(client):
    deadline = seed_deadline(client)
    detail = client.get(f"/v1/deadlines/{deadline}", headers=HEADERS).json()
    assert detail["permissions"] == []
    set_role(client, "headman")
    assert client.get(f"/v1/deadlines/{deadline}", headers=HEADERS).json()["permissions"] == [
        "homework.edit",
        "homework.material.write",
    ]
    response = client.patch(
        f"/v1/deadlines/{deadline}",
        json={
            "expected_revision": 1,
            "kind": "test",
            "title": "Тест по пределам",
            "subject": "Математический анализ",
            "description": "Формат: письменно",
            "deadline": {"at": "2026-11-05T00:00:00+03:00", "date_only": True},
            "date_hint": "в среду",
        },
        headers=HEADERS,
    )
    assert response.status_code == 200, response.text
    item = response.json()["item"]
    assert (item["kind"], item["title"], item["subject"]) == (
        "test",
        "Тест по пределам",
        "Математический анализ",
    )
    assert item["date_only"] is True and item["deadline_at"].startswith("2026-11-04T21:00")
    assert item["needs_clarification"] is False and item["revision"] == 2
    assert "homework.edit" in response.json()["permissions"]
    assert client.get(f"/v1/deadlines/{deadline}", headers=HEADERS).json()["item"] == item


def test_a_student_cannot_edit_a_deadline_and_an_unknown_kind_is_rejected(client):
    deadline = seed_deadline(client)
    assert (
        client.patch(
            f"/v1/deadlines/{deadline}",
            json={"expected_revision": 1, "title": "Взлом"},
            headers=HEADERS,
        ).status_code
        == 403
    )
    set_role(client, "headman")
    assert (
        client.patch(
            f"/v1/deadlines/{deadline}",
            json={"expected_revision": 1, "kind": "party"},
            headers=HEADERS,
        ).status_code
        == 422
    )
    stale = client.patch(
        f"/v1/deadlines/{deadline}",
        json={"expected_revision": 5, "title": "Не сейчас"},
        headers=HEADERS,
    )
    assert stale.status_code == 409


def test_an_edit_clears_the_clarification_mark_for_good(client):
    set_role(client, "headman")
    card = seed_card(client)

    async def unsettle():
        async with AsyncSession(client.app.state.engine) as db:
            row = await db.get(Homework, uuid.UUID(card))
            row.status, row.verification_state = "needs_clarification", "needs_clarification"
            await db.commit()

    asyncio.run(unsettle())
    assert client.get(f"/v1/homework/{card}", headers=HEADERS).json()["verification_state"] == (
        "needs_clarification"
    )
    deadline = {"at": "2026-11-05T10:00:00+03:00", "date_only": False}
    response = client.patch(
        f"/v1/homework/{card}",
        json={"expected_revision": 1, "deadline": deadline},
        headers=HEADERS,
    )
    body = response.json()
    assert response.status_code == 200
    assert (
        body["verification_state"] == "manual_confirmed" and body["status"] != "needs_clarification"
    )
    assert body["deadline"]["certainty"] == "confirmed" and body["deadline"]["origin"] == "manual"
    assert client.get(f"/v1/homework/{card}", headers=HEADERS).json()["deadline"]["certainty"] == (
        "confirmed"
    )


def test_an_event_edit_clears_its_clarification_mark(client):
    set_role(client, "headman")
    event = seed_deadline(client)
    first = client.get(f"/v1/deadlines/{event}", headers=HEADERS).json()["item"]
    assert first["needs_clarification"] is True
    response = client.patch(
        f"/v1/deadlines/{event}",
        json={
            "expected_revision": 1,
            "deadline": {"at": "2026-11-05T00:00:00+03:00", "date_only": True},
        },
        headers=HEADERS,
    )
    item = response.json()["item"]
    assert item["needs_clarification"] is False
    assert item["deadline_certainty"] == "confirmed" and item["deadline_origin"] == "manual"


def listed(client, filter_):
    response = client.get(f"/v1/homework?filter={filter_}", headers=HEADERS)
    assert response.status_code == 200, response.text
    return [item["id"] for item in response.json()["items"]]


def test_a_student_cannot_delete_a_card_and_nothing_changes(client):
    card = seed_card(client)
    response = client.delete(f"/v1/homework/{card}?expected_revision=1", headers=HEADERS)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "permission_denied"
    detail = client.get(f"/v1/homework/{card}", headers=HEADERS).json()
    assert detail["status"] != "cancelled" and detail["revision"] == 1
    assert count(client, HeadmanAudit) == 0


def test_the_headman_deletes_a_card_it_leaves_the_active_lists_and_stays_in_the_archive(client):
    card = seed_card(client)
    assert card in listed(client, "all")
    set_role(client, "headman")
    response = client.delete(f"/v1/homework/{card}?expected_revision=1", headers=HEADERS)
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["status"] == "cancelled" and data["cancelled_at"] is not None
    assert data["revision"] == 2
    assert card not in listed(client, "all")
    assert card in listed(client, "archive")

    async def journal():
        async with AsyncSession(client.app.state.engine) as db:
            audit = await db.scalar(select(HeadmanAudit))
            decision = await db.scalar(select(HeadmanDecision))
            return audit.operation, decision.locked

    # journalled like the bot's «Отменить», and locked so the AI cannot bring the card back
    assert asyncio.run(journal()) == ("cancel", True)


def test_deleting_twice_is_harmless_and_a_stale_revision_is_a_conflict(client):
    card = seed_card(client)
    set_role(client, "headman")
    stale = client.delete(f"/v1/homework/{card}?expected_revision=7", headers=HEADERS)
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "revision_conflict"
    assert client.get(f"/v1/homework/{card}", headers=HEADERS).json()["status"] != "cancelled"
    first = client.delete(f"/v1/homework/{card}?expected_revision=1", headers=HEADERS)
    again = client.delete(f"/v1/homework/{card}?expected_revision=1", headers=HEADERS)
    assert first.status_code == again.status_code == 200
    assert again.json()["revision"] == 2
    assert count(client, HeadmanAudit) == 1
    assert client.delete(f"/v1/homework/{card}", headers=HEADERS).status_code == 422


def test_a_deputy_cannot_delete_a_decision_the_headman_confirmed(client):
    card = seed_card(client)
    set_role(client, "headman")
    client.patch(
        f"/v1/homework/{card}",
        json={"expected_revision": 1, "title": "Решение старосты"},
        headers=HEADERS,
    )
    set_role(client, "deputy")
    blocked = client.delete(f"/v1/homework/{card}?expected_revision=2", headers=HEADERS)
    assert blocked.status_code == 403
    assert client.get(f"/v1/homework/{card}", headers=HEADERS).json()["status"] != "cancelled"


def test_a_card_of_another_group_cannot_be_deleted(client):
    card = seed_card(client)
    set_role(client, "headman")

    async def move():
        async with AsyncSession(client.app.state.engine) as db:
            other = await db.scalar(select(Group).where(Group.telegram_chat_id == -1002))
            row = await db.get(Homework, uuid.UUID(card))
            row.group_id = other.id
            await db.commit()

    asyncio.run(move())
    response = client.delete(f"/v1/homework/{card}?expected_revision=1", headers=HEADERS)
    assert response.status_code == 404


def test_the_journal_can_undo_a_deleted_card(client):
    from studgroup import headman_domain as domain
    from studgroup.models import User

    card = seed_card(client)
    set_role(client, "headman")
    assert (
        client.delete(f"/v1/homework/{card}?expected_revision=1", headers=HEADERS).status_code
        == 200
    )

    async def undo():
        async with AsyncSession(client.app.state.engine, expire_on_commit=False) as db:
            audit = await db.scalar(select(HeadmanAudit))
            group = await db.scalar(select(Group).where(Group.telegram_chat_id == -1001))
            member = await db.scalar(select(Membership))
            user = await db.get(User, member.user_id)
            await domain.undo(db, group, user, member, audit.id)
            await db.commit()

    asyncio.run(undo())
    restored = client.get(f"/v1/homework/{card}", headers=HEADERS).json()
    assert restored["status"] != "cancelled" and restored["cancelled_at"] is None
    assert card in listed(client, "all")


def test_the_headman_deletes_an_event_and_students_cannot(client):
    deadline = seed_deadline(client)
    assert (
        client.delete(f"/v1/deadlines/{deadline}?expected_revision=1", headers=HEADERS).status_code
        == 403
    )
    set_role(client, "headman")
    stale = client.delete(f"/v1/deadlines/{deadline}?expected_revision=4", headers=HEADERS)
    assert stale.status_code == 409
    response = client.delete(f"/v1/deadlines/{deadline}?expected_revision=1", headers=HEADERS)
    assert response.status_code == 200, response.text
    item = response.json()["item"]
    assert item["cancelled_at"] is not None and item["revision"] == 2
    again = client.delete(f"/v1/deadlines/{deadline}?expected_revision=1", headers=HEADERS)
    assert again.status_code == 200 and again.json()["item"]["revision"] == 2
    assert count(client, HeadmanAudit) == 1
