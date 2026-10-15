"""Opt-in PostgreSQL E2E coverage for the published student card boundary."""

import asyncio
import hashlib
import hmac
import json
import os
import uuid
from datetime import UTC, date, datetime, time, timedelta
from urllib.parse import urlencode

import pytest
import test_student_card_api as student_cards
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from studgroup.main import Settings, create_app
from studgroup.models import Base, Group, Membership, SchedulePattern, User, WebSession
from studgroup.security import token_hash

pytestmark = pytest.mark.skipif(
    not os.environ.get("POSTGRES_TEST_URL"), reason="PostgreSQL not configured"
)
BOT_TOKEN = "postgres-student-e2e-token"


def signed_init_data(user_id: int, *, now: datetime) -> str:
    fields = {
        "auth_date": str(int(now.timestamp())),
        "user": json.dumps(
            {"id": user_id, "first_name": "Тестовый", "last_name": "Студент"},
            ensure_ascii=False,
            separators=(",", ":"),
        ),
    }
    secret = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    data = "\n".join(f"{key}={value}" for key, value in sorted(fields.items()))
    fields["hash"] = hmac.new(secret, data.encode(), hashlib.sha256).hexdigest()
    return urlencode(fields)


@pytest.fixture
def postgres_client():
    url = os.environ["POSTGRES_TEST_URL"]
    app = create_app(Settings(database_url=url, telegram_bot_token=BOT_TOKEN))

    async def reset_and_seed():
        async with app.state.engine.begin() as connection:
            tables = ", ".join(f'"{table.name}"' for table in Base.metadata.sorted_tables)
            await connection.execute(text(f"TRUNCATE TABLE {tables} RESTART IDENTITY CASCADE"))
        async with AsyncSession(app.state.engine) as db:
            user = User(id=uuid.uuid4(), telegram_user_id=1, display_name="Анна")
            group = Group(
                id=uuid.uuid4(),
                telegram_chat_id=-1001,
                name="Тестовая группа PostgreSQL",
                timezone="Europe/Moscow",
                first_week_anchor=date(2026, 10, 5),
                pilot_authorized=True,
            )
            other = Group(
                id=uuid.uuid4(),
                telegram_chat_id=-1002,
                name="Другая тестовая группа",
                timezone="Europe/Moscow",
            )
            member = Membership(
                id=uuid.uuid4(),
                user_id=user.id,
                group_id=group.id,
                role="student",
                status="active",
            )
            db.add_all([user, group, other])
            await db.flush()
            db.add(member)
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
            await db.commit()

    with TestClient(app) as client:
        # TestClient owns an event loop while the fixture helpers use asyncio.run().
        # NullPool keeps asyncpg connections from crossing those loop boundaries.
        client.portal.call(app.state.engine.dispose)
        app.state.engine = create_async_engine(url, poolclass=NullPool)
        asyncio.run(reset_and_seed())
        yield client
    asyncio.run(app.state.engine.dispose())


def test_postgres_published_card_sources_materials_shadow_isolation_and_completion(
    postgres_client,
):
    student_cards.test_published_canonical_card_reaches_student_api_without_shadow_or_private_file_ids(
        postgres_client
    )
    card_id = postgres_client.get(
        "/v1/homework", headers={"Authorization": "Bearer valid"}
    ).json()["items"][0]["id"]
    undone = postgres_client.put(
        f"/v1/homework/{card_id}/completion",
        headers={"Authorization": "Bearer valid"},
        json={"completed": False, "expected_revision": 2},
    )
    assert undone.status_code == 200
    assert undone.json()["my_state"]["completion"] == "pending"


def test_postgres_cancelled_card_missing_subject_and_unavailable_sources(postgres_client):
    student_cards.test_missing_subject_unavailable_sources_and_cancellation_are_explicit(
        postgres_client
    )


def test_postgres_subjects_are_group_scoped(postgres_client):
    student_cards.test_subject_overview_is_group_scoped_and_excludes_unknown_subject(
        postgres_client
    )


def test_postgres_non_homework_event_keeps_kind_and_deadline_evidence(postgres_client):
    student_cards.test_published_non_homework_card_keeps_kind_sources_and_deadline_evidence(
        postgres_client
    )


def test_postgres_telegram_init_data_bootstrap_and_tamper_rejection(postgres_client):
    now = datetime.now(UTC)
    valid = signed_init_data(1, now=now)
    response = postgres_client.post("/v1/session/bootstrap", json={"init_data": valid})
    assert response.status_code == 200
    assert response.json()["session"]["access_state"] == "active"
    credential = response.json()["access_token"]
    assert postgres_client.get(
        "/v1/subjects", headers={"Authorization": f"Bearer {credential}"}
    ).status_code == 200

    tampered = valid[:-1] + ("0" if valid[-1] != "0" else "1")
    rejected = postgres_client.post("/v1/session/bootstrap", json={"init_data": tampered})
    assert rejected.status_code == 401
    assert rejected.json()["error"]["code"] == "initdata_invalid"
