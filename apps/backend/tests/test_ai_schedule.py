import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from test_ingestion import delivery, send
from test_processing import Provider

from studgroup.ai_schedule import signal, window
from studgroup.main import Settings
from studgroup.models import AIJob, Group, GroupAIActivity, RawMessage
from studgroup.processing import process_next

pytest_plugins = ["test_schedule_api"]


def stamp(value):
    return datetime.fromisoformat("2026-10-08T" + value + "+03:00")


def config():
    return Settings(_env_file=None, ai_enabled=True, ai_schedule_enabled=True)


def seed(client, received, mid=1, imported=False, activity=None):
    received = received.astimezone(UTC)

    async def insert():
        async with AsyncSession(client.app.state.engine) as db:
            group = await db.scalar(select(Group).where(Group.telegram_chat_id == -1001))
            if not imported and group.bot_added_at is None:
                group.bot_added_at = received
            db.add(
                RawMessage(
                    id=uuid.uuid4(),
                    group_id=group.id,
                    telegram_message_id=mid,
                    text="ДЗ: решить задачи",
                    message_date=received,
                    version_date=received,
                    live_received_at=received if not imported else None,
                    imported=imported,
                    processing_state="pending",
                    delete_at=received + timedelta(days=30),
                )
            )
            if not imported:
                await signal(db, group.id, activity or received)
            await db.commit()

    asyncio.run(insert())


def run(client, now, provider):
    return asyncio.run(process_next(client.app.state.engine, config(), provider, now))


@pytest.mark.parametrize(
    "time,allowed,extended",
    [
        ("06:59:00", False, False),
        ("07:00:00", True, False),
        ("22:59:00", True, False),
        ("23:00:00", True, True),
        ("23:59:00", True, True),
        ("00:00:00", False, False),
    ],
)
def test_daily_windows(time, allowed, extended):
    result = window(stamp(time), "Europe/Moscow")
    assert (result is not None) is allowed
    if result:
        assert result[2] is extended
        assert result[0].astimezone(stamp(time).tzinfo).minute == 0


def test_silence_never_calls_provider_and_import_does_not_wake_schedule(client):
    provider = Provider()
    assert run(client, stamp("08:00:00"), provider) == "idle"
    seed(client, stamp("07:50:00"), imported=True)
    assert run(client, stamp("08:00:00"), provider) == "idle"
    assert provider.calls == 0


def test_live_messages_wait_for_hour_and_are_not_repeated(client):
    provider = Provider()
    seed(client, stamp("06:55:00"))
    assert run(client, stamp("06:59:00"), provider) == "outside_hours"
    assert run(client, stamp("07:00:00"), provider) == "completed"
    assert run(client, stamp("07:15:00"), provider) == "idle"
    seed(client, stamp("07:05:00"), mid=2)
    assert run(client, stamp("07:20:00"), provider) == "idle"
    assert run(client, stamp("07:30:00"), provider) == "idle"
    assert run(client, stamp("08:00:00"), provider) == "completed"
    assert provider.calls == 2


def test_new_hour_requires_activity_newer_than_the_previous_package(client):
    provider = Provider()
    seed(client, stamp("06:55:00"))
    assert run(client, stamp("07:00:00"), provider) == "completed"

    async def add_pending_without_signal():
        async with AsyncSession(client.app.state.engine) as db:
            group = await db.scalar(select(Group).where(Group.telegram_chat_id == -1001))
            received = stamp("07:05:00").astimezone(UTC)
            db.add(
                RawMessage(
                    id=uuid.uuid4(),
                    group_id=group.id,
                    telegram_message_id=2,
                    text="ДЗ: решить задачи",
                    message_date=received,
                    version_date=received,
                    live_received_at=received,
                    processing_state="pending",
                    delete_at=received + timedelta(days=30),
                )
            )
            await db.commit()

    asyncio.run(add_pending_without_signal())
    assert run(client, stamp("08:00:00"), provider) == "idle"
    assert provider.calls == 1

    async def wake_with_activity():
        async with AsyncSession(client.app.state.engine) as db:
            group = await db.scalar(select(Group).where(Group.telegram_chat_id == -1001))
            await signal(db, group.id, stamp("07:06:00").astimezone(UTC))
            await db.commit()

    asyncio.run(wake_with_activity())
    assert run(client, stamp("08:00:01"), provider) == "completed"
    assert provider.calls == 2


def test_late_extension_requires_recent_group_signal(client):
    provider = Provider()
    seed(client, stamp("22:00:00"))
    assert run(client, stamp("23:00:00"), provider) == "idle"
    assert provider.calls == 0
    seed(client, stamp("22:50:00"), mid=2)
    assert run(client, stamp("23:00:00"), provider) == "completed"
    assert run(client, stamp("23:00:05"), provider) == "completed"
    assert provider.calls == 2
    assert run(client, stamp("00:00:00"), provider) == "outside_hours"


def test_failed_request_is_not_retried_before_next_hour(client):
    provider = Provider(failure="provider_unavailable")
    seed(client, stamp("07:00:00"))
    run(client, stamp("07:00:00"), provider)
    assert provider.calls == 1
    assert run(client, stamp("07:10:00"), provider) == "idle"
    run(client, stamp("08:00:00"), provider)
    assert provider.calls == 2


def test_connection_recovery_retries_only_unfinished_job_in_same_slot(client):
    provider = Provider(failure="provider_unreachable")
    seed(client, stamp("07:00:00"))
    assert run(client, stamp("07:00:00"), provider) == "retry"
    seed(client, stamp("07:05:00"), mid=2)
    assert run(client, stamp("07:00:20"), provider) == "idle"
    assert run(client, stamp("07:00:30"), provider) == "retry"
    provider.failure = None
    assert run(client, stamp("07:01:30"), provider) == "completed"
    assert run(client, stamp("07:02:00"), provider) == "idle"
    assert provider.calls == 3
    assert run(client, stamp("08:00:00"), provider) == "completed"
    assert provider.calls == 4


def test_expired_running_lease_resumes_after_restart_in_same_slot(client):
    import asyncio

    class Interrupted(Provider):
        async def extract_batch(self, *args, **kwargs):
            raise asyncio.CancelledError

    seed(client, stamp("07:00:00"))
    with pytest.raises(asyncio.CancelledError):
        run(client, stamp("07:00:00"), Interrupted())
    recovered = Provider()
    assert run(client, stamp("07:02:00"), recovered) == "idle"
    assert run(client, stamp("07:03:01"), recovered) == "completed"
    assert run(client, stamp("07:04:00"), recovered) == "idle"
    assert recovered.calls == 1


def test_webhook_signal_is_idempotent_and_noop_edits_are_free(client):
    send(client, delivery())
    send(client, delivery())
    send(client, delivery(update_id=2, edited=1791200100))

    async def read():
        async with AsyncSession(client.app.state.engine) as db:
            activity = await db.scalar(select(GroupAIActivity))
            assert activity.signal_count == 1
            raw = await db.scalar(select(RawMessage))
            assert raw.revision == 1 and raw.live_received_at is not None
            assert await db.scalar(select(func.count()).select_from(AIJob)) == 0

    asyncio.run(read())
