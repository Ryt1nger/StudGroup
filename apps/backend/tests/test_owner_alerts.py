import asyncio
import json

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from studgroup.main import Settings
from studgroup.models import OwnerIncident
from studgroup.processing import incident
from studgroup.worker import flush_incidents

pytest_plugins = ["test_schedule_api"]


def config(owner=5282463254):
    return Settings(
        _env_file=None, owner_telegram_user_id=owner, telegram_bot_token="private-test-token"
    )


def mark(client, recover=False):
    from datetime import UTC, datetime

    async def write():
        async with AsyncSession(client.app.state.engine) as db:
            await incident(db, "provider_unreachable", datetime.now(UTC), recover=recover)
            await db.commit()

    asyncio.run(write())


def test_incident_and_recovery_notify_only_owner_without_repeat_spam(client):
    sent = []

    def handler(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True})

    transport = httpx.MockTransport(handler)
    mark(client)
    mark(client)
    asyncio.run(flush_incidents(client.app.state.engine, config(), transport))
    asyncio.run(flush_incidents(client.app.state.engine, config(), transport))
    assert len(sent) == 1
    assert sent[0]["chat_id"] == 5282463254
    assert "API DeepSeek" in sent[0]["text"]
    assert "private-test-token" not in sent[0]["text"]
    mark(client, recover=True)
    asyncio.run(flush_incidents(client.app.state.engine, config(), transport))
    assert len(sent) == 2
    assert "восстановлена" in sent[1]["text"]
    assert {message["chat_id"] for message in sent} == {5282463254}


def test_telegram_failure_keeps_alert_pending_for_delivery_retry(client):
    mark(client)
    asyncio.run(
        flush_incidents(
            client.app.state.engine,
            config(),
            httpx.MockTransport(lambda r: httpx.Response(403, json={"ok": False})),
        )
    )

    async def read():
        async with AsyncSession(client.app.state.engine) as db:
            return (await db.scalar(select(OwnerIncident))).notification_pending

    assert asyncio.run(read())
    asyncio.run(
        flush_incidents(
            client.app.state.engine,
            config(),
            httpx.MockTransport(lambda r: httpx.Response(200, json={"ok": True})),
        )
    )
    assert not asyncio.run(read())


def test_alert_cannot_be_sent_to_group_even_with_bad_owner_configuration(client):
    mark(client)

    def handler(request):
        raise AssertionError("must not send an owner alert to a group")

    asyncio.run(
        flush_incidents(client.app.state.engine, config(-1001), httpx.MockTransport(handler))
    )
