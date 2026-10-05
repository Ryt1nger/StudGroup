import asyncio
import uuid
from datetime import UTC, date, datetime, time, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession

from studgroup.main import Settings, create_app
from studgroup.models import Base, Group, Membership, SchedulePattern, User, WebSession
from studgroup.security import token_hash


@pytest.fixture
def client(tmp_path):
    settings = Settings(database_url=f"sqlite+aiosqlite:///{tmp_path}/test.db")
    app = create_app(settings)
    with TestClient(app) as http:

        async def seed():
            async with app.state.engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with AsyncSession(app.state.engine) as db:
                user = User(id=uuid.uuid4(), telegram_user_id=1, display_name="Анна")
                group = Group(
                    id=uuid.uuid4(),
                    telegram_chat_id=-1001,
                    name="Группа",
                    timezone="Europe/Moscow",
                    first_week_anchor=date(2026, 10, 5),
                    pilot_authorized=True,
                )
                other = Group(
                    id=uuid.uuid4(), telegram_chat_id=-1002, name="Чужая", timezone="Europe/Moscow"
                )
                member = Membership(
                    id=uuid.uuid4(),
                    user_id=user.id,
                    group_id=group.id,
                    role="student",
                    status="active",
                )
                db.add_all([user, group, other, member])
                await db.flush()
                for row, subject in [(group, "Математика"), (other, "Секретный предмет")]:
                    db.add(
                        SchedulePattern(
                            group_id=row.id,
                            subject=subject,
                            weekday=0,
                            starts=time(9),
                            ends=time(10),
                            valid_from=date(2026, 9, 1),
                            valid_until=date(2027, 1, 31),
                            week="all",
                        )
                    )
                db.add(
                    WebSession(
                        token_hash=token_hash("valid"),
                        user_id=user.id,
                        membership_id=member.id,
                        expires_at=datetime.now(UTC) + timedelta(hours=1),
                    )
                )
                db.add(
                    WebSession(
                        token_hash=token_hash("expired"),
                        user_id=user.id,
                        membership_id=member.id,
                        expires_at=datetime.now(UTC) - timedelta(seconds=1),
                    )
                )
                await db.commit()

        asyncio.run(seed())
        yield http


def test_schedule_is_group_scoped(client):
    response = client.get(
        "/v1/schedule?start=2026-10-05&end=2026-10-11", headers={"Authorization": "Bearer valid"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["selected_week"] == "first"
    assert [row["subject"] for row in body["lessons"]] == ["Математика"]
    assert body["lessons"][0]["starts_at"].endswith("+03:00")


def test_expired_and_missing_credentials(client):
    url = "/v1/schedule?start=2026-10-05&end=2026-10-11"
    assert client.get(url).status_code == 401
    result = client.get(url, headers={"Authorization": "Bearer expired"})
    assert result.status_code == 401
    assert result.json()["error"]["code"] == "session_expired"


def test_rejects_large_range(client):
    response = client.get(
        "/v1/schedule?start=2026-10-05&end=2027-01-01", headers={"Authorization": "Bearer valid"}
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_request"
