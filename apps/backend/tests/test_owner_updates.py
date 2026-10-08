import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from studgroup.main import Settings
from studgroup.models import BotOutbox, Group, HeadmanAudit, Notification, User
from studgroup.owner_updates import tick

pytest_plugins = ["test_schedule_api"]


def settings(enabled=True, owner=5282463254):
    return Settings(
        _env_file=None,
        owner_telegram_user_id=owner,
        owner_update_notifications_enabled=enabled,
        owner_update_notifications_since=datetime.now(UTC) - timedelta(minutes=1),
        webapp_origin="https://studgroup-frontend.onrender.com",
        telegram_bot_token="test",
    )


async def seed(engine, count=1, manual=False):
    async with AsyncSession(engine) as db:
        group = await db.scalar(select(Group).where(Group.telegram_chat_id == -1001))
        user = await db.scalar(select(User))
        for index in range(count):
            now = datetime.now(UTC)
            if manual:
                key = f"h:{uuid.uuid4()}"
                db.add(
                    HeadmanAudit(
                        id=uuid.uuid4(),
                        group_id=group.id,
                        actor_id=user.id,
                        entity_key=key,
                        operation="material",
                        before="{}",
                        after=json.dumps(
                            {
                                key: {
                                    "fields": {"subject_name": "Матан", "title": "Задачи"},
                                    "materials": [{"title": "Учебник"}],
                                }
                            }
                        ),
                        created_at=now,
                    )
                )
            else:
                db.add(
                    Notification(
                        id=uuid.uuid4(),
                        group_id=group.id,
                        event_key=str(uuid.uuid4()),
                        title="Матан",
                        body=f"Задача {index}",
                        entity_type="homework",
                        entity_id=uuid.uuid4(),
                        created_at=now,
                        delete_at=now + timedelta(days=90),
                    )
                )
        await db.commit()


async def outbox(engine):
    async with AsyncSession(engine) as db:
        return (await db.scalars(select(BotOutbox))).all()


def test_private_feed_deduplicates_and_catches_bursts_beyond_first_page(client):
    engine = client.app.state.engine
    asyncio.run(seed(engine, count=105))
    config = settings()
    for _ in range(3):
        asyncio.run(tick(engine, config))
    rows = asyncio.run(outbox(engine))
    assert len(rows) == 105
    assert {json.loads(row.payload)["chat_id"] for row in rows} == {5282463254}
    assert all("/homework/" in row.payload for row in rows)


def test_material_changes_are_included_even_without_student_inbox_notice(client):
    engine = client.app.state.engine
    asyncio.run(seed(engine, manual=True))
    asyncio.run(tick(engine, settings()))
    rows = asyncio.run(outbox(engine))
    assert len(rows) == 1
    assert "Изменены материалы" in json.loads(rows[0].payload)["text"]
    assert "Учебник" in json.loads(rows[0].payload)["text"]


def test_disabled_mode_or_group_owner_id_sends_nothing(client):
    engine = client.app.state.engine
    asyncio.run(seed(engine))
    asyncio.run(tick(engine, settings(enabled=False)))
    asyncio.run(tick(engine, settings(owner=-1001)))
    assert asyncio.run(outbox(engine)) == []


def test_test_mode_ignores_history_before_activation(client):
    engine = client.app.state.engine
    asyncio.run(seed(engine))
    config = settings()
    config.owner_update_notifications_since = datetime.now(UTC) + timedelta(seconds=1)
    asyncio.run(tick(engine, config))
    assert asyncio.run(outbox(engine)) == []


def test_turning_off_testing_mode_supersedes_pending_test_notifications(client):
    from studgroup.bot_admin import deliver

    engine = client.app.state.engine
    asyncio.run(seed(engine))
    asyncio.run(tick(engine, settings()))
    asyncio.run(deliver(engine, settings(enabled=False)))
    assert asyncio.run(outbox(engine))[0].state == "superseded"


def test_new_review_proposal_is_labelled_unpublished_and_not_repeated(client):
    from test_processing import Provider, run, source

    class Uncertain(Provider):
        async def extract_batch(self, *args, **kwargs):
            result = await super().extract_batch(*args, **kwargs)
            result.batch.assignments[0].confidence = 50
            return result

    source(client)
    assert run(client, Uncertain()) == "completed"
    config = settings()
    asyncio.run(tick(client.app.state.engine, config))
    asyncio.run(tick(client.app.state.engine, config))
    rows = asyncio.run(outbox(client.app.state.engine))
    assert len(rows) == 1
    assert "требует проверки" in json.loads(rows[0].payload)["text"]


def test_delivery_outage_does_not_abandon_testing_updates_after_five_attempts(client, monkeypatch):
    from studgroup import bot_admin

    async def failed(*args):
        return {"ok": False}

    monkeypatch.setattr(bot_admin, "telegram_request", failed)
    engine = client.app.state.engine
    config = settings()
    asyncio.run(seed(engine))
    asyncio.run(tick(engine, config))

    async def retry():
        async with AsyncSession(engine) as db:
            row = await db.scalar(select(BotOutbox))
            row.available_at = datetime.now(UTC) - timedelta(seconds=1)
            await db.commit()
        await bot_admin.deliver(engine, config)

    for _ in range(6):
        asyncio.run(retry())
    row = asyncio.run(outbox(engine))[0]
    assert row.attempts == 6
    assert row.state == "pending"
