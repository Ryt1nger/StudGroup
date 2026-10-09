import asyncio
import json

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from studgroup.main import Settings
from studgroup.models import OwnerIncident, OwnerIncidentEpisode
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


def test_multiple_pending_errors_flush_without_commit_expiration_crash(client):
    from datetime import UTC, datetime

    async def seed():
        async with AsyncSession(client.app.state.engine) as db:
            for code in ["invalid_source_reference", "invalid_date_only", "ai_budget_limit"]:
                await incident(db, code, datetime.now(UTC))
            await db.commit()

    asyncio.run(seed())
    sent = []

    def handler(request):
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True})

    asyncio.run(flush_incidents(client.app.state.engine, config(), httpx.MockTransport(handler)))
    assert len(sent) == 3
    assert len({m["text"].split("№")[1].split("\n")[0] for m in sent}) == 3
    asyncio.run(flush_incidents(client.app.state.engine, config(), httpx.MockTransport(handler)))
    assert len(sent) == 3


def test_seconds_occurrence_time_and_delayed_delivery_are_distinct(client):
    from datetime import UTC, datetime

    at = datetime(2026, 10, 8, 17, 12, 34, tzinfo=UTC)

    async def seed():
        async with AsyncSession(client.app.state.engine) as db:
            await incident(db, "provider_error", at, detail="HTTP 503")
            await db.commit()
            row = await db.scalar(select(OwnerIncidentEpisode))
            return row

    row = asyncio.run(seed())
    from studgroup.worker import incident_text

    text = incident_text(row, sent_at=datetime(2026, 10, 8, 17, 34, 56, tzinfo=UTC))
    assert "Возникла: 08.10.2026 20:12:34 МСК" in text
    assert "Отправка уведомления: 08.10.2026 20:34:56 МСК" in text
    assert "Накопленное уведомление" in text
    assert "HTTP 503" in text


def test_budget_alert_names_limit_and_includes_parameters():
    from datetime import UTC, datetime

    row = OwnerIncidentEpisode(
        id=12,
        code="ai_daily_group_budget_limit",
        opened_at=datetime(2026, 10, 9, 8, 0, tzinfo=UTC),
        last_seen_at=datetime(2026, 10, 9, 8, 0, tzinfo=UTC),
        occurrences=1,
        first_detail="group=12345678 spent=1.00000000 limit=1.00000000",
        opening_pending=True,
        recovery_pending=False,
        legacy=False,
    )

    from studgroup.worker import incident_text

    text = incident_text(row, sent_at=datetime(2026, 10, 9, 8, 1, tzinfo=UTC))
    assert "StudGroup: лимит №000012" in text
    assert "дневной лимит" in text
    assert "Параметры лимита: group=12345678" in text


def test_repeated_error_shares_episode_but_new_error_after_recovery_has_new_number(client):
    from datetime import UTC, datetime, timedelta

    async def scenario():
        async with AsyncSession(client.app.state.engine) as db:
            at = datetime.now(UTC)
            first = await incident(db, "provider_error", at)
            assert await incident(db, "provider_error", at + timedelta(seconds=1)) == first
            assert (
                await incident(db, "provider_error", at + timedelta(seconds=2), recover=True)
                == first
            )
            second = await incident(db, "provider_error", at + timedelta(seconds=3))
            assert second != first
            await db.commit()
            episodes = (
                await db.scalars(select(OwnerIncidentEpisode).order_by(OwnerIncidentEpisode.id))
            ).all()
            assert episodes[0].occurrences == 2
            assert episodes[0].recovery_pending
            assert episodes[1].opening_pending

    asyncio.run(scenario())


def test_recovery_before_delivery_does_not_erase_initial_error_notice(client):
    mark(client)
    mark(client, recover=True)
    sent = []

    def handler(request):
        sent.append(json.loads(request.content)["text"])
        return httpx.Response(200, json={"ok": True})

    asyncio.run(flush_incidents(client.app.state.engine, config(), httpx.MockTransport(handler)))
    assert len(sent) == 2
    assert "К моменту отправки уже устранена" in sent[0]
    assert "восстановлена" in sent[1]
