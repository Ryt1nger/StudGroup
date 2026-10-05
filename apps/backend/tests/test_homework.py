import asyncio
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from studgroup.models import Group, Homework, RawMessage

pytest_plugins = ["test_schedule_api"]


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


def test_card_completion_and_undo(client):
    card = seed_card(client)
    headers = {"Authorization": "Bearer valid"}
    assert client.get("/v1/today", headers=headers).json()["empty"] is False
    detail = client.get(f"/v1/homework/{card}", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["deadline"]["state"] == "unknown"
    result = client.put(
        f"/v1/homework/{card}/completion",
        headers=headers,
        json={"completed": True, "expected_revision": 1},
    )
    assert result.status_code == 200
    assert result.json()["revision"] == 1
    assert result.json()["my_state"]["completion"] == "completed"
    assert client.get("/v1/today", headers=headers).json()["empty"] is True
    assert (
        client.put(
            f"/v1/homework/{card}/completion",
            headers=headers,
            json={"completed": False, "expected_revision": 1},
        ).status_code
        == 200
    )
    assert client.get("/v1/today", headers=headers).json()["empty"] is False


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
