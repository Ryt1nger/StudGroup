import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from studgroup.models import Group, Notification
from studgroup.notifications import record_change

pytest_plugins = ["test_schedule_api"]
HEADERS = {"Authorization": "Bearer valid"}


def test_import_is_quiet_and_live_changes_are_deduplicated(client):
    async def run():
        async with AsyncSession(client.app.state.engine) as db:
            group = await db.scalar(select(Group).where(Group.pilot_authorized.is_(True)))
            raw = SimpleNamespace(id=uuid.uuid4(), revision=1, imported=True, group_id=group.id)
            entity = SimpleNamespace(id=uuid.uuid4(), subject_name="Математика", title="Задание")
            now = datetime.now(UTC)
            await record_change(db, raw, entity, "homework", now)
            assert (await db.scalars(select(Notification))).all() == []
            raw.revision = 2
            await record_change(db, raw, entity, "homework", now)
            await record_change(db, raw, entity, "homework", now)
            await db.commit()
            assert len((await db.scalars(select(Notification))).all()) == 1

    asyncio.run(run())


def test_inbox_scoped_and_personal_read_state_is_persistent(client):
    async def seed():
        async with AsyncSession(client.app.state.engine) as db:
            groups = (await db.scalars(select(Group))).all()
            for g in groups:
                db.add(
                    Notification(
                        id=uuid.uuid4(),
                        group_id=g.id,
                        event_key=str(g.id),
                        title="Наше" if g.pilot_authorized else "Чужое",
                        body="Срок изменён",
                        entity_type="homework",
                        entity_id=uuid.uuid4(),
                        created_at=datetime.now(UTC),
                        delete_at=datetime.now(UTC) + timedelta(days=90),
                    )
                )
            await db.commit()

    asyncio.run(seed())
    assert client.get("/v1/notifications").status_code == 401
    r = client.get("/v1/notifications", headers=HEADERS).json()
    assert r["unread_count"] == 1
    assert [x["title"] for x in r["items"]] == ["Наше"]
    assert client.post("/v1/notifications/read-all", headers=HEADERS).json()["unread_count"] == 0
    assert client.get("/v1/notifications", headers=HEADERS).json()["items"][0]["read"] is True
