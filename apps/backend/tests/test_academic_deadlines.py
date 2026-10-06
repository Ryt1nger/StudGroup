import asyncio
import json
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from studgroup.models import AcademicDeadline, Group

pytest_plugins = ["test_schedule_api"]


def seed(client):
    async def run():
        async with AsyncSession(client.app.state.engine) as db:
            group = await db.scalar(select(Group).where(Group.telegram_chat_id == -1001))
            other = await db.scalar(select(Group).where(Group.telegram_chat_id == -1002))
            for key, row_group, days in [
                ("active", group, 5),
                ("past", group, -5),
                ("foreign", other, 5),
            ]:
                now = datetime.now(UTC)
                db.add(
                    AcademicDeadline(
                        group_id=row_group.id,
                        import_key=key,
                        kind="control_point",
                        subject="Математика",
                        title=f"КТ {key}",
                        description="Проверить темы",
                        deadline_at=now + timedelta(days=days),
                        date_only=False,
                        needs_clarification=False,
                        source_message_ids=json.dumps([123]),
                        created_at=now,
                        delete_at=now + timedelta(days=90),
                    )
                )
            await db.commit()

    asyncio.run(run())


def test_control_points_are_scoped_and_separate_from_homework(client):
    seed(client)
    headers = {"Authorization": "Bearer valid"}
    response = client.get("/v1/deadlines", headers=headers)
    assert response.status_code == 200
    assert [item["title"] for item in response.json()["items"]] == ["КТ active"]
    assert client.get("/v1/homework", headers=headers).json()["items"] == []
    assert client.get("/v1/deadlines").status_code == 401
    assert [
        item["title"]
        for item in client.get("/v1/deadlines?archive=true", headers=headers).json()["items"]
    ] == ["КТ past"]


def test_subject_page_includes_real_control_points(client):
    seed(client)
    headers = {"Authorization": "Bearer valid"}
    lesson = client.get("/v1/schedule?start=2026-10-12&end=2026-10-12", headers=headers).json()[
        "lessons"
    ][0]
    body = client.get(f"/v1/lessons/{lesson['id']}/subject", headers=headers).json()
    assert body["events_state"] == "ready"
    assert [item["title"] for item in body["events"]] == ["КТ active"]
    assert body["homework"] == []


def test_event_detail_retained_archived_and_foreign_scoping(client):
    seed(client)
    headers = {"Authorization": "Bearer valid"}
    for archive in [False, True]:
        entry = client.get(f"/v1/deadlines?archive={str(archive).lower()}", headers=headers).json()[
            "items"
        ][0]
        result = client.get(f"/v1/deadlines/{entry['id']}", headers=headers)
        assert result.status_code == 200
        assert result.json()["item"] == entry
        assert client.get(f"/v1/deadlines/{entry['id']}").status_code == 401

    async def foreign():
        async with AsyncSession(client.app.state.engine) as db:
            return await db.scalar(
                select(AcademicDeadline.id).where(AcademicDeadline.import_key == "foreign")
            )

    assert client.get(f"/v1/deadlines/{asyncio.run(foreign())}", headers=headers).status_code == 404
    assert (
        client.get(
            "/v1/deadlines/aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", headers=headers
        ).status_code
        == 404
    )
