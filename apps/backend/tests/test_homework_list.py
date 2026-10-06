import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from studgroup.models import Group, Homework, PersonalCompletion, User, WebSession
from studgroup.security import token_hash

pytest_plugins = ["test_schedule_api"]
HEADERS = {"Authorization": "Bearer valid"}
NOW = datetime(2026, 10, 5, 6, tzinfo=UTC)


@pytest.fixture
def cards(client, monkeypatch):
    import studgroup.homework as homework_api

    clock = [NOW]

    class ServerClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return clock[0].astimezone(tz)

    monkeypatch.setattr(homework_api, "datetime", ServerClock)

    async def seed():
        async with AsyncSession(client.app.state.engine) as db:
            group = await db.scalar(select(Group).where(Group.telegram_chat_id == -1001))
            other = await db.scalar(select(Group).where(Group.telegram_chat_id == -1002))
            user = await db.scalar(select(User).where(User.telegram_user_id == 1))
            another_user = User(telegram_user_id=2, display_name="Другой студент")
            db.add(another_user)
            await db.flush()
            specs = [
                ("today", NOW + timedelta(hours=2), "published", None),
                ("overdue", NOW - timedelta(hours=6), "published", None),
                ("old", NOW - timedelta(days=3), "published", None),
                ("unknown", None, "needs_clarification", None),
                ("done", None, "published", None),
                ("cancelled_recent", None, "cancelled", NOW - timedelta(hours=11)),
                ("cancelled_expired", None, "cancelled", NOW - timedelta(hours=12)),
                ("cancelled_unknown", None, "cancelled", None),
                ("week", NOW + timedelta(days=3), "published", None),
                ("date_only", NOW.replace(hour=0), "published", None),
                ("retention_expired", None, "published", None),
                ("other_group", None, "published", None),
            ]
            ids = {}
            for index, (title, deadline, status, cancelled_at) in enumerate(specs):
                row = Homework(
                    group_id=other.id if title == "other_group" else group.id,
                    subject_id=uuid.uuid4(),
                    subject_name="Математика",
                    title=title,
                    created_at=NOW - timedelta(days=5),  # Ties exercise id ordering.
                    updated_at=NOW,  # Must not restart cancellation visibility.
                    deadline_at=deadline,
                    deadline_date_only=title == "date_only",
                    status=status,
                    cancelled_at=cancelled_at,
                    delete_at=NOW - timedelta(seconds=1)
                    if title == "retention_expired"
                    else NOW + timedelta(days=90),
                )
                db.add(row)
                await db.flush()
                ids[title] = str(row.id)
                if title in {"done", "cancelled_recent", "unknown"}:
                    db.add(
                        PersonalCompletion(
                            homework_id=row.id,
                            user_id=another_user.id if title == "unknown" else user.id,
                            completed_at=NOW - timedelta(hours=1),
                            completed_revision=1,
                        )
                    )
            current = await db.get(WebSession, token_hash("valid"))
            db.add(
                WebSession(
                    token_hash=token_hash("another-session"),
                    user_id=current.user_id,
                    membership_id=current.membership_id,
                    expires_at=datetime.now(UTC) + timedelta(hours=1),
                )
            )
            await db.commit()
            return ids

    return asyncio.run(seed()), clock


def titles(response):
    assert response.status_code == 200, response.text
    return {item["title"] for item in response.json()["items"]}


def test_active_and_archive_lists_preserve_details(client, cards):
    ids, _ = cards
    response = client.get("/v1/homework", headers=HEADERS)
    assert titles(response) == {
        "today", "unknown", "done", "week", "date_only",
    }
    archive = client.get("/v1/homework?filter=archive", headers=HEADERS)
    assert titles(archive) == {
        "overdue", "old", "cancelled_recent", "cancelled_expired", "cancelled_unknown",
    }
    assert all(item["status"] == "cancelled" for item in archive.json()["items"]
               if item["title"].startswith("cancelled"))
    assert response.json()["next_cursor"] is None
    assert response.json()["group_timezone"] == "Europe/Moscow"
    assert response.json()["processing"]["state"] == "idle"
    for title in ("cancelled_expired", "old", "done"):
        assert client.get(f"/v1/homework/{ids[title]}", headers=HEADERS).status_code == 200
    assert client.get(f"/v1/homework/{ids['other_group']}", headers=HEADERS).status_code == 404


@pytest.mark.parametrize(
    "filter_name,expected",
    [
        ("today", {"today", "date_only"}),
        ("week", {"today", "week", "date_only"}),
        ("mine", {"done"}),
    ],
)
def test_server_filters_and_personal_completion(client, cards, filter_name, expected):
    assert titles(client.get(f"/v1/homework?filter={filter_name}", headers=HEADERS)) == expected


def test_keyset_pages_with_tied_creation_times_have_no_duplicates(client, cards):
    expected = client.get("/v1/homework", headers=HEADERS).json()["items"]
    collected = []
    cursor = None
    for _ in range(10):
        params = {"limit": 2}
        if cursor:
            params["cursor"] = cursor
        response = client.get("/v1/homework", params=params, headers=HEADERS)
        assert response.status_code == 200
        body = response.json()
        collected.extend(body["items"])
        cursor = body["next_cursor"]
        if cursor is None:
            break
    assert [item["id"] for item in collected] == [item["id"] for item in expected]
    assert len({item["id"] for item in collected}) == 5


def test_today_filter_uses_group_day_not_utc_day(client, cards):
    ids, clock = cards

    async def move_deadline():
        async with AsyncSession(client.app.state.engine) as db:
            item = await db.get(Homework, uuid.UUID(ids["week"]))
            item.deadline_at = NOW.replace(hour=23)  # Oct 6 02:00 in Moscow.
            await db.commit()

    asyncio.run(move_deadline())
    assert "week" not in titles(client.get("/v1/homework?filter=today", headers=HEADERS))
    clock[0] = NOW.replace(hour=21, minute=30)  # Oct 6 00:30 in Moscow.
    assert "week" in titles(client.get("/v1/homework?filter=today", headers=HEADERS))
    assert "date_only" not in titles(client.get("/v1/homework?filter=today", headers=HEADERS))


def test_cursor_rejects_tampering_wrong_filter_session_and_expiry(client, cards):
    _, clock = cards
    cursor = client.get("/v1/homework?limit=1", headers=HEADERS).json()["next_cursor"]
    for params, headers in [
        ({"cursor": cursor + "x"}, HEADERS),
        ({"cursor": "not-a-cursor"}, HEADERS),
        ({"cursor": cursor, "filter": "mine"}, HEADERS),
        ({"cursor": cursor}, {"Authorization": "Bearer another-session"}),
    ]:
        response = client.get("/v1/homework", params=params, headers=headers)
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "invalid_request"
    clock[0] += timedelta(minutes=10)
    response = client.get("/v1/homework", params={"cursor": cursor}, headers=HEADERS)
    assert response.status_code == 200
    assert datetime.fromisoformat(response.json()["server_time"]) == NOW
    assert datetime.fromisoformat(response.json()["generated_at"]) == clock[0]
    clock[0] += timedelta(minutes=5)
    assert client.get("/v1/homework", params={"cursor": cursor}, headers=HEADERS).status_code == 422


@pytest.mark.parametrize("query", ["filter=invalid", "limit=0", "limit=101"])
def test_invalid_query(client, query):
    response = client.get(f"/v1/homework?{query}", headers=HEADERS)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_request"


def test_list_requires_active_session(client):
    assert client.get("/v1/homework").status_code == 401
    assert client.get("/v1/homework", headers={"Authorization": "Bearer expired"}).status_code == 401
