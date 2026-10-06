import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from studgroup.models import Group, Homework, RawMessage

pytest_plugins = ["test_schedule_api"]


def lesson_id(client):
    response = client.get(
        "/v1/schedule?start=2026-10-12&end=2026-10-12", headers={"Authorization": "Bearer valid"}
    )
    return response.json()["lessons"][0]["id"]


def test_subject_page_has_related_homework_and_explicit_unconnected_sections(client):
    card = seed_card(client)
    response = client.get(
        f"/v1/lessons/{lesson_id(client)}/subject", headers={"Authorization": "Bearer valid"}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["lesson"]["subject"] == "Математика"
    assert data["lesson"]["starts_at"].startswith("2026-10-12")
    assert [row["id"] for row in data["homework"]] == [card]
    assert data["events_state"] == "ready"
    assert data["events"] == []
    assert data["materials_state"] == "not_connected"


def test_subject_page_excludes_future_deadline_beyond_selected_lesson(client):
    card_id = seed_card(client)

    async def update():
        async with AsyncSession(client.app.state.engine) as db:
            row = await db.get(Homework, uuid.UUID(card_id))
            row.deadline_at = datetime(2026, 10, 13, 6, tzinfo=UTC)
            await db.commit()

    asyncio.run(update())
    response = client.get(
        f"/v1/lessons/{lesson_id(client)}/subject", headers={"Authorization": "Bearer valid"}
    )
    assert response.json()["homework"] == []


def test_subject_page_never_exposes_other_group_or_unknown_lesson(client):
    async def foreign_id():
        async with AsyncSession(client.app.state.engine) as db:
            from studgroup.models import SchedulePattern

            other = await db.scalar(select(Group).where(Group.telegram_chat_id == -1002))
            return await db.scalar(
                select(SchedulePattern.id).where(SchedulePattern.group_id == other.id)
            )

    foreign = asyncio.run(foreign_id())
    headers = {"Authorization": "Bearer valid"}
    assert (
        client.get(f"/v1/lessons/{foreign}:2026-10-12/subject", headers=headers).status_code == 404
    )
    assert client.get("/v1/lessons/invalid/subject", headers=headers).status_code == 404
    assert client.get(f"/v1/lessons/{lesson_id(client)}/subject").status_code == 401


def test_tomorrow_uses_group_calendar_and_preserves_completion(client, monkeypatch):
    import studgroup.homework as homework_api

    now = datetime(2026, 10, 5, 22, tzinfo=UTC)  # Oct 6 in Moscow; tomorrow is Oct 7.

    class ServerClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return now.astimezone(tz)

    card_id = seed_card(client)

    async def update():
        async with AsyncSession(client.app.state.engine) as db:
            card = await db.get(Homework, uuid.UUID(card_id))
            card.deadline_at = datetime(2026, 10, 7, 6, tzinfo=UTC)
            card.delete_at = now + timedelta(days=90)
            await db.commit()

    asyncio.run(update())
    monkeypatch.setattr(homework_api, "datetime", ServerClock)
    headers = {"Authorization": "Bearer valid"}
    client.put(
        f"/v1/homework/{card_id}/completion",
        headers=headers,
        json={"completed": True, "expected_revision": 1},
    )
    response = client.get("/v1/today?day=tomorrow", headers=headers)
    assert response.status_code == 200
    card = response.json()["sections"][0]["items"][0]
    assert card["id"] == card_id
    assert card["my_state"]["completion"] == "completed"
    assert response.json()["server_time"].startswith("2026-10-05T22:")
    assert client.get("/v1/today?day=invalid", headers=headers).status_code == 422


def test_tomorrow_omits_unknown_deadlines(client):
    seed_card(client)
    response = client.get("/v1/today?day=tomorrow", headers={"Authorization": "Bearer valid"})
    assert response.status_code == 200
    assert response.json()["sections"] == []


@pytest.mark.parametrize(
    "elapsed_seconds,visible", [(-1, True), (0, False), (1, False), (86400, False)]
)
def test_past_deadline_is_archived_immediately(client, monkeypatch, elapsed_seconds, visible):
    import studgroup.homework as homework_api

    now = datetime(2026, 10, 5, 6, tzinfo=UTC)
    card_id = seed_card(client)

    class ServerClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return now.astimezone(tz)

    async def update_deadline():
        async with AsyncSession(client.app.state.engine) as db:
            card = await db.get(Homework, uuid.UUID(card_id))
            card.deadline_at = now - timedelta(seconds=elapsed_seconds)
            card.created_at = now  # A new card must not bypass the expiry window.
            card.delete_at = now + timedelta(days=90)
            await db.commit()

    asyncio.run(update_deadline())
    monkeypatch.setattr(homework_api, "datetime", ServerClock)
    headers = {"Authorization": "Bearer valid"}
    sections = client.get("/v1/today", headers=headers).json()["sections"]
    ids = [item["id"] for section in sections for item in section["items"]]
    assert (card_id in ids) is visible
    if visible:
        assert sections[0]["kind"] == "due_today"
    assert client.get(f"/v1/homework/{card_id}", headers=headers).status_code == 200


def seed_card(client):
    async def seed():
        async with AsyncSession(client.app.state.engine, expire_on_commit=False) as db:
            group = await db.scalar(select(Group).where(Group.telegram_chat_id == -1001))
            now = datetime.now(UTC)
            raw = RawMessage(
                group_id=group.id,
                telegram_message_id=3,
                text="ДЗ",
                revision=1,
                message_date=now,
                version_date=now,
                processing_state="completed",
                delete_at=now + timedelta(days=30),
            )
            db.add(raw)
            await db.flush()
            card = Homework(
                group_id=group.id,
                raw_message_id=raw.id,
                subject_id=uuid.uuid4(),
                subject_name="Математика",
                title="Задачи",
                description="Решить номера 1–3",
                created_at=now,
                updated_at=now,
                delete_at=now + timedelta(days=90),
                revision=1,
            )
            db.add(card)
            await db.commit()
            return str(card.id)

    return asyncio.run(seed())


@pytest.mark.parametrize(
    "edited,age_hours,expected",
    [(False, 0, "upcoming"), (True, 1, "new_or_changed"), (True, 25, "upcoming")],
)
def test_today_updates_requires_real_recent_edit_not_creation(client, edited, age_hours, expected):
    card_id = seed_card(client)
    now = datetime.now(UTC)

    async def update():
        async with AsyncSession(client.app.state.engine) as db:
            card = await db.get(Homework, uuid.UUID(card_id))
            card.deadline_at = now + timedelta(days=2)
            card.created_at = now - timedelta(days=3) if edited else now
            card.revision = 2 if edited else 1
            card.significant_updated_at = now - timedelta(hours=age_hours) if edited else None
            await db.commit()

    asyncio.run(update())
    response = client.get("/v1/today", headers={"Authorization": "Bearer valid"})
    assert response.status_code == 200
    assert [section["kind"] for section in response.json()["sections"]] == [expected]


def test_card_completion_and_undo(client):
    card = seed_card(client)

    # A real update keeps this undated task eligible; creation alone no longer does.
    async def edit():
        async with AsyncSession(client.app.state.engine) as db:
            row = await db.get(Homework, uuid.UUID(card))
            row.created_at = datetime.now(UTC) - timedelta(days=2)
            row.significant_updated_at = datetime.now(UTC)
            row.revision = 2
            await db.commit()

    asyncio.run(edit())
    headers = {"Authorization": "Bearer valid"}
    assert client.get("/v1/today", headers=headers).json()["empty"] is False
    detail = client.get(f"/v1/homework/{card}", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["deadline"]["state"] == "unknown"
    result = client.put(
        f"/v1/homework/{card}/completion",
        headers=headers,
        json={"completed": True, "expected_revision": 2},
    )
    assert result.status_code == 200
    assert result.json()["revision"] == 2
    assert result.json()["my_state"]["completion"] == "completed"
    assert client.get("/v1/today", headers=headers).json()["empty"] is True
    assert (
        client.put(
            f"/v1/homework/{card}/completion",
            headers=headers,
            json={"completed": False, "expected_revision": 2},
        ).status_code
        == 200
    )
    assert client.get("/v1/today", headers=headers).json()["empty"] is False


def test_completed_known_deadline_remains_until_archived(client):
    card_id = seed_card(client)
    headers = {"Authorization": "Bearer valid"}

    async def set_deadline(past=False):
        async with AsyncSession(client.app.state.engine) as db:
            card = await db.get(Homework, uuid.UUID(card_id))
            card.deadline_at = datetime.now(UTC) + timedelta(hours=-1 if past else 1)
            await db.commit()

    asyncio.run(set_deadline())
    response = client.put(
        f"/v1/homework/{card_id}/completion",
        headers=headers,
        json={"completed": True, "expected_revision": 1},
    )
    assert response.status_code == 200
    today = client.get("/v1/today", headers=headers).json()
    cards = [item for section in today["sections"] for item in section["items"]]
    assert len(cards) == 1
    assert cards[0]["id"] == card_id
    assert cards[0]["my_state"]["completion"] == "completed"
    asyncio.run(set_deadline(past=True))
    assert client.get("/v1/today", headers=headers).json()["empty"] is True
    assert (
        client.get(f"/v1/homework/{card_id}", headers=headers).json()["my_state"]["completion"]
        == "completed"
    )


def test_completed_unknown_deadline_stays_out_of_today(client):
    card_id = seed_card(client)
    headers = {"Authorization": "Bearer valid"}
    result = client.put(
        f"/v1/homework/{card_id}/completion",
        headers=headers,
        json={"completed": True, "expected_revision": 1},
    )
    completed_at = datetime.fromisoformat(result.json()["my_state"]["completed_at"])

    async def update_card():
        async with AsyncSession(client.app.state.engine) as db:
            card = await db.get(Homework, uuid.UUID(card_id))
            card.revision = 2
            card.significant_updated_at = completed_at + timedelta(seconds=1)
            await db.commit()

    asyncio.run(update_card())
    today = client.get("/v1/today", headers=headers).json()
    assert today["empty"] is True
    assert today["sections"] == []
    detail = client.get(f"/v1/homework/{card_id}", headers=headers).json()
    assert detail["my_state"]["completion"] == "completed"
    assert detail["revision"] == 2
    client.put(
        f"/v1/homework/{card_id}/completion",
        headers=headers,
        json={"completed": False, "expected_revision": 2},
    )
    assert client.get("/v1/today", headers=headers).json()["empty"] is False


def test_expired_cancelled_card_remains_available_in_details(client):
    card_id = seed_card(client)
    cancelled_at = datetime.now(UTC) - timedelta(hours=13)

    async def cancel():
        async with AsyncSession(client.app.state.engine) as db:
            card = await db.get(Homework, uuid.UUID(card_id))
            card.status = "cancelled"
            card.cancelled_at = cancelled_at
            card.updated_at = datetime.now(UTC)
            await db.commit()

    asyncio.run(cancel())
    headers = {"Authorization": "Bearer valid"}
    assert client.get("/v1/today", headers=headers).json()["sections"] == []
    detail = client.get(f"/v1/homework/{card_id}", headers=headers)
    assert detail.status_code == 200
    assert datetime.fromisoformat(detail.json()["cancelled_at"]) == cancelled_at


def test_conflicting_revision_rejected(client):
    card = seed_card(client)
    result = client.put(
        f"/v1/homework/{card}/completion",
        headers={"Authorization": "Bearer valid"},
        json={"completed": True, "expected_revision": 2},
    )
    assert result.status_code == 409
    assert result.json()["error"]["code"] == "revision_conflict"


def test_no_token_cannot_read_homework(client):
    card = seed_card(client)
    assert client.get(f"/v1/homework/{card}").status_code == 401


def test_today_includes_server_selected_lesson_even_without_homework(client, monkeypatch):
    import studgroup.homework as homework_api

    class ServerClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 5, 6, 15, tzinfo=UTC).astimezone(tz)

    monkeypatch.setattr(homework_api, "datetime", ServerClock)
    response = client.get("/v1/today", headers={"Authorization": "Bearer valid"})
    assert response.status_code == 200
    body = response.json()
    assert body["empty"] is True  # Homework emptiness does not hide a real lesson.
    assert body["next_lesson"]["state"] == "current"
    assert body["next_lesson"]["lesson"]["subject"] == "Математика"
    assert body["next_lesson"]["lesson"]["starts_at"] == "2026-10-05T09:00:00+03:00"


def test_today_returns_null_after_schedule_validity_ends(client, monkeypatch):
    import studgroup.homework as homework_api

    class ServerClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2027, 2, 1, 6, tzinfo=UTC).astimezone(tz)

    monkeypatch.setattr(homework_api, "datetime", ServerClock)
    response = client.get("/v1/today", headers={"Authorization": "Bearer valid"})
    assert response.status_code == 200
    assert response.json()["next_lesson"] is None
    assert response.json()["day_lessons_state"] == "unknown"


@pytest.mark.parametrize(
    "stamp,expected",
    [
        ("2026-10-05T06:59:59+00:00", "scheduled"),
        ("2026-10-05T07:00:00+00:00", "finished"),
        ("2026-10-05T20:00:00+00:00", "finished"),
        ("2026-10-11T06:00:00+00:00", "empty"),
    ],
)
def test_selected_day_lesson_state_uses_server_clock(client, monkeypatch, stamp, expected):
    import studgroup.homework as homework_api

    class ServerClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.fromisoformat(stamp).astimezone(tz)

    monkeypatch.setattr(homework_api, "datetime", ServerClock)
    response = client.get("/v1/today", headers={"Authorization": "Bearer valid"})
    assert response.status_code == 200
    assert response.json()["day_lessons_state"] == expected
