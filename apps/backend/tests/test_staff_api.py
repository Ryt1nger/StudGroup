"""Headman/deputy cabinet API: role checks, timetable edits, days off, settings, card deadlines."""

import asyncio
import json
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from test_deadline_application_db import MSK, proposal, run_one, seed

from studgroup.api import role_permissions
from studgroup.models import CanonicalAcademicItem, Membership, ScheduleChange

pytest_plugins = ["test_schedule_api"]
AUTH = {"Authorization": "Bearer valid"}


def db_run(client, fn):
    async def invoke():
        async with AsyncSession(client.app.state.engine, expire_on_commit=False) as db:
            result = await fn(db)
            await db.commit()
            return result

    return asyncio.run(invoke())


def make(client, role):
    async def promote(db):
        (await db.scalar(select(Membership))).role = role

    db_run(client, promote)


def changes(client):
    async def read(db):
        rows = await db.scalars(select(ScheduleChange).order_by(ScheduleChange.schedule_version))
        return [(r.operation, r.input_method, r.actor_role, r.schedule_version) for r in rows]

    return db_run(client, read)


def week(client):
    response = client.get("/v1/staff/schedule?start=2026-10-12&end=2026-10-18", headers=AUTH)
    assert response.status_code == 200, response.text
    return response.json()


def test_permissions_follow_the_role():
    assert "schedule.write" not in role_permissions("student")
    assert {"schedule.write", "deadline.manage"} <= set(role_permissions("deputy"))
    assert "group.settings.write" not in role_permissions("deputy")
    assert "group.settings.write" in role_permissions("headman")


def test_a_student_cannot_use_the_cabinet(client):
    for method, path in [
        ("get", "/v1/staff/schedule?start=2026-10-12&end=2026-10-18"),
        ("get", "/v1/staff/deadline-queue"),
        ("post", "/v1/staff/no-lesson-days"),
    ]:
        response = getattr(client, method)(path, headers=AUTH)
        assert response.status_code == 403, (path, response.text)
        assert response.json()["error"]["code"] == "permission_denied"


def test_recurring_lesson_change_is_audited_and_stale_revisions_are_rejected(client):
    make(client, "deputy")
    data = week(client)
    assert data["schedule_version"] == 0 and data["no_lesson_days"] == []
    lesson = data["schedule"]["lessons"][0]
    assert data["lesson_edits"] == [
        {
            "lesson_id": lesson["id"],
            "pattern_revision": 1,
            "override_revision": 0,
            "week": "all",
            "single_day": False,
        }
    ]
    body = {
        "scope": "recurring",
        "pattern_revision": 1,
        "override_revision": 0,
        "starts_at": "2026-10-13T09:00:00+03:00",
        "ends_at": "2026-10-13T10:30:00+03:00",
        "location": "305",
    }
    path = f"/v1/staff/schedule/lessons/{lesson['id']}"
    response = client.patch(path, json=body, headers=AUTH)
    assert response.status_code == 200, response.text
    assert response.json() == {"schedule_version": 1}
    moved = week(client)["schedule"]["lessons"]
    assert [(l["starts_at"][:10], l["location"]) for l in moved] == [("2026-10-13", "305")]
    stale = client.patch(path.replace("2026-10-12", "2026-10-13"), json=body, headers=AUTH)
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "action_rejected"
    assert changes(client) == [("lesson_recurring", "cabinet", "deputy", 1)]


def test_lesson_patch_validates_its_fields(client):
    make(client, "headman")
    lesson = week(client)["schedule"]["lessons"][0]
    path = f"/v1/staff/schedule/lessons/{lesson['id']}"
    base = {"scope": "once", "pattern_revision": 1, "override_revision": 0}
    for patch in [{}, {"starts_at": "2026-10-12T09:00:00+03:00"}, {"cancelled": None}]:
        response = client.patch(path, json={**base, **patch}, headers=AUTH)
        assert response.status_code == 422, patch
    naive = {**base, "starts_at": "2026-10-12T09:00:00", "ends_at": "2026-10-12T10:00:00"}
    assert client.patch(path, json=naive, headers=AUTH).status_code == 422
    cancelled = client.patch(path, json={**base, "cancelled": True}, headers=AUTH)
    assert cancelled.status_code == 200, cancelled.text
    statuses = {l["id"]: l["status"] for l in week(client)["schedule"]["lessons"]}
    assert statuses[lesson["id"]] == "cancelled"


def test_adding_a_one_off_lesson(client):
    make(client, "headman")
    body = {
        "subject": "Консультация",
        "starts_at": "2026-10-15T15:00:00+03:00",
        "ends_at": "2026-10-15T16:00:00+03:00",
    }
    response = client.post("/v1/staff/schedule/lessons", json=body, headers=AUTH)
    assert response.status_code == 201, response.text
    data = week(client)
    added = [l for l in data["schedule"]["lessons"] if l["subject"] == "Консультация"]
    edits = {e["lesson_id"]: e for e in data["lesson_edits"]}
    assert len(added) == 1 and edits[added[0]["id"]]["single_day"] is True
    backwards = {**body, "ends_at": "2026-10-15T14:00:00+03:00"}
    response = client.post("/v1/staff/schedule/lessons", json=backwards, headers=AUTH)
    assert response.status_code == 409


def test_days_without_lessons(client):
    make(client, "deputy")
    body = {"day": "2026-11-04", "reason": "Праздник"}
    assert client.post("/v1/staff/no-lesson-days", json=body, headers=AUTH).status_code == 201
    again = client.post("/v1/staff/no-lesson-days", json=body, headers=AUTH)
    assert again.status_code == 409
    listed = client.get("/v1/staff/no-lesson-days", headers=AUTH).json()["items"]
    assert listed == [{"day": "2026-11-04", "reason": "Праздник"}]
    ranged = client.get("/v1/staff/no-lesson-days?start=2026-11-05", headers=AUTH).json()
    assert ranged["items"] == []
    assert client.delete("/v1/staff/no-lesson-days/2026-11-04", headers=AUTH).status_code == 200
    assert client.delete("/v1/staff/no-lesson-days/2026-11-04", headers=AUTH).status_code == 404
    assert [c[0] for c in changes(client)] == ["no_lesson_day_add", "no_lesson_day_remove"]


def test_group_settings_are_headman_only_and_versioned(client):
    make(client, "deputy")
    body = {"schedule_version": 0, "first_week_anchor": "2026-10-12"}
    assert client.patch("/v1/staff/group/settings", json=body, headers=AUTH).status_code == 403
    make(client, "headman")
    bad_zone = {"schedule_version": 0, "timezone": "Mars/Olympus"}
    response = client.patch("/v1/staff/group/settings", json=bad_zone, headers=AUTH)
    assert response.status_code == 422
    empty = client.patch("/v1/staff/group/settings", json={"schedule_version": 0}, headers=AUTH)
    assert empty.status_code == 422
    response = client.patch("/v1/staff/group/settings", json=body, headers=AUTH)
    assert response.status_code == 200, response.text
    assert response.json() == {
        "schedule_version": 1,
        "timezone": "Europe/Moscow",
        "first_week_anchor": "2026-10-12",
    }
    stale = client.patch("/v1/staff/group/settings", json=body, headers=AUTH)
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "revision_conflict"


def inferred_card(client):
    async def create(db):
        group = await seed(db, "номер 5 письменно")
        return (await run_one(db, group, "номер 5 письменно", proposal())).item.id

    return db_run(client, create)


def test_the_queue_and_confirming_an_inferred_deadline(client):
    item_id = inferred_card(client)
    make(client, "deputy")
    queue = client.get("/v1/staff/deadline-queue", headers=AUTH).json()["items"]
    assert [card["id"] for card in queue] == [str(item_id)]
    card = queue[0]
    assert card["deadline"]["basis"] == "schedule_inferred"
    assert card["deadline"]["label"] == "К следующему занятию"
    assert card["staff_note"] and card["locked_fields"] == []
    assert "deadline_inferred_confirm" in card["attention_reasons"]
    assert card["sources"]
    path = f"/v1/staff/items/{item_id}/deadline"
    stale = client.post(path, json={"action": "confirm", "revision": 99}, headers=AUTH)
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "revision_conflict"
    done = client.post(path, json={"action": "confirm", "revision": card["revision"]}, headers=AUTH)
    assert done.status_code == 200, done.text
    confirmed = done.json()
    assert confirmed["revision"] == card["revision"] + 1
    assert confirmed["deadline"]["at"] == card["deadline"]["at"]
    assert confirmed["deadline"]["reason"] == "headman_confirmed"
    assert confirmed["deadline"]["label"] is None and confirmed["staff_note"] is None
    assert confirmed["locked_fields"] == ["deadline"]
    assert "deadline_inferred_confirm" not in confirmed["attention_reasons"]
    assert client.get("/v1/staff/deadline-queue", headers=AUTH).json()["items"] == []


def test_setting_a_date_only_deadline_locks_it_against_a_schedule_rebuild(client):
    item_id = inferred_card(client)
    make(client, "headman")
    card = client.get(f"/v1/staff/items/{item_id}", headers=AUTH).json()
    body = {
        "action": "set",
        "revision": card["revision"],
        "deadline_at": "2026-10-20T15:00:00+03:00",
        "date_only": True,
    }
    response = client.post(f"/v1/staff/items/{item_id}/deadline", json=body, headers=AUTH)
    assert response.status_code == 200, response.text
    deadline = response.json()["deadline"]
    assert deadline["date_only"] is True and deadline["basis"] == "explicit"
    assert deadline["reason"] == "headman_set"

    async def check(db):
        item = await db.get(CanonicalAcademicItem, item_id)
        assert item.deadline_at.astimezone(MSK).date() == date(2026, 10, 20)
        assert item.deadline_at.astimezone(MSK).hour == 0
        assert json.loads(item.locked_fields) == ["deadline"]
        return item.deadline_at

    before = db_run(client, check)
    data = week(client)
    lesson = data["schedule"]["lessons"][0]
    move = {
        "scope": "recurring",
        "pattern_revision": data["lesson_edits"][0]["pattern_revision"],
        "override_revision": 0,
        "starts_at": "2026-10-16T10:40:00+03:00",
        "ends_at": "2026-10-16T12:10:00+03:00",
    }
    path = f"/v1/staff/schedule/lessons/{lesson['id']}"
    assert client.patch(path, json=move, headers=AUTH).status_code == 200
    assert db_run(client, check) == before
    missing = {"action": "set", "revision": card["revision"] + 1}
    response = client.post(f"/v1/staff/items/{item_id}/deadline", json=missing, headers=AUTH)
    assert response.status_code == 422


def test_field_locks_can_be_set_and_released(client):
    item_id = inferred_card(client)
    make(client, "headman")
    path = f"/v1/staff/items/{item_id}/locks"
    card = client.get(f"/v1/staff/items/{item_id}", headers=AUTH).json()
    bad = {"revision": card["revision"], "locked_fields": ["audience"]}
    assert client.put(path, json=bad, headers=AUTH).status_code == 422
    body = {"revision": card["revision"], "locked_fields": ["title", "subject"]}
    locked = client.put(path, json=body, headers=AUTH).json()
    assert locked["locked_fields"] == ["subject", "title"]
    assert locked["revision"] == card["revision"] + 1
    released = client.put(
        path, json={"revision": locked["revision"], "locked_fields": []}, headers=AUTH
    ).json()
    assert released["locked_fields"] == []
    assert client.get("/v1/staff/items/not-a-uuid", headers=AUTH).status_code == 422


def test_students_see_the_neutral_label_and_then_the_confirmed_deadline(client):
    from test_deadline_application_db import SENT

    from studgroup.analysis.projection import project_item
    from studgroup.models import CanonicalProjectionLink

    item_id = inferred_card(client)

    async def publish(db):
        item = await db.get(CanonicalAcademicItem, item_id)
        await project_item(db, item, SENT, writer_mode="new")
        return (await db.get(CanonicalProjectionLink, item_id)).entity_id

    homework_id = db_run(client, publish)
    deadline = client.get(f"/v1/homework/{homework_id}", headers=AUTH).json()["deadline"]
    assert (deadline["certainty"], deadline["label"]) == ("inferred", "К следующему занятию")
    make(client, "headman")
    revision = client.get(f"/v1/staff/items/{item_id}", headers=AUTH).json()["revision"]
    body = {"action": "confirm", "revision": revision}
    assert client.post(f"/v1/staff/items/{item_id}/deadline", json=body, headers=AUTH).is_success
    make(client, "student")
    deadline = client.get(f"/v1/homework/{homework_id}", headers=AUTH).json()["deadline"]
    assert (deadline["certainty"], deadline["origin"], deadline["label"]) == (
        "confirmed",
        "manual",
        None,
    )
