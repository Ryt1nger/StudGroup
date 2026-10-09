import asyncio
import json
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from test_ai_schedule import seed
from test_live_two_pass import CascadeProvider

from studgroup.main import Settings
from studgroup.models import AIRun, BotOutbox
from studgroup.processing import process_next

pytest_plugins = ["test_schedule_api"]


def at(value):
    return datetime.fromisoformat("2026-10-09T" + value + "+03:00")


def config():
    return Settings(
        _env_file=None,
        ai_enabled=True,
        ai_schedule_enabled=True,
        ai_live_two_pass=True,
        owner_telegram_user_id=5282463254,
        owner_update_notifications_enabled=True,
        owner_update_notifications_since=at("00:00:00") - timedelta(days=1),
    )


def run(client, provider, time):
    return asyncio.run(process_next(client.app.state.engine, config(), provider, now=at(time)))


def notices(client):
    async def read():
        async with AsyncSession(client.app.state.engine) as db:
            return [
                json.loads(r.payload)["text"] for r in (await db.scalars(select(BotOutbox))).all()
            ]

    return asyncio.run(read())


def test_two_messages_and_four_stages_emit_only_one_start_and_one_aggregate_finish(client):
    seed(client, at("06:55:00"), mid=1)
    seed(client, at("06:56:00"), mid=2)
    provider = CascadeProvider()
    assert run(client, provider, "07:00:00") == "screened"
    assert len(notices(client)) == 1
    assert run(client, provider, "07:00:05") == "completed"
    assert len(notices(client)) == 1  # Second target still pending, not an ended run.
    assert run(client, provider, "07:00:10") == "screened"
    assert len(notices(client)) == 1
    assert run(client, provider, "07:00:15") == "completed"
    texts = notices(client)
    assert len(texts) == 2
    final = next(t for t in texts if "Итог прогона" in t)
    assert "Сообщений в пакете: 2" in final
    assert "всего 500" in final
    assert "$0.000348" in final
    assert "Карточек создано: 2" in final
    assert run(client, provider, "07:00:20") == "idle"
    assert len(notices(client)) == 2


def test_retry_and_restart_keep_same_run_and_preserve_unknown_reservation(client):
    seed(client, at("06:55:00"))
    broken = CascadeProvider(failure="provider_unreachable")
    assert run(client, broken, "07:00:00") == "screened"
    assert run(client, broken, "07:00:05") == "retry"
    assert len(notices(client)) == 1  # Incidents have an independent sender, not fake run finishes.
    recovered = CascadeProvider()
    assert run(client, recovered, "07:06:00") == "completed"
    texts = notices(client)
    assert len(texts) == 2
    assert "Неуточнённый резерв: $0.012000" in texts[-1]
    assert "Запросов/попыток: 3" in texts[-1]
    assert recovered.screen_calls == 0


def test_new_messages_belong_to_next_slot_and_silence_produces_no_report(client):
    provider = CascadeProvider(signal=False)
    assert run(client, provider, "07:00:00") == "idle"
    assert notices(client) == []
    seed(client, at("06:55:00"))
    assert run(client, provider, "07:00:05") == "completed"
    seed(client, at("07:05:00"), mid=2)
    assert run(client, provider, "07:20:00") == "idle"
    assert len(notices(client)) == 2
    assert run(client, provider, "07:30:00") == "idle"
    assert run(client, provider, "08:00:00") == "completed"
    assert len(notices(client)) == 4

    async def check():
        async with AsyncSession(client.app.state.engine) as db:
            rows = (await db.scalars(select(AIRun))).all()
            assert len(rows) == 2
            assert all(r.finished_at for r in rows)

    asyncio.run(check())


def test_snapshot_spans_more_than_worker_claim_page(client):
    for mid in range(1, 52):
        seed(client, at("06:55:00"), mid=mid)
    provider = CascadeProvider(signal=False)
    for index in range(51):
        assert (
            asyncio.run(
                process_next(
                    client.app.state.engine,
                    config(),
                    provider,
                    now=at("07:00:00") + timedelta(seconds=index * 2),
                )
            )
            == "completed"
        )
        assert len(notices(client)) == (2 if index == 50 else 1)
    assert "Сообщений в пакете: 51" in notices(client)[-1]


def test_sender_suppresses_legacy_stage_notice_but_delivers_card_updates(client, monkeypatch):
    from studgroup import bot_admin

    calls = []

    async def request(settings, method, payload):
        calls.append(payload["text"])
        return {"ok": True}

    monkeypatch.setattr(bot_admin, "telegram_request", request)

    async def check():
        settings = config()
        settings.telegram_bot_token = "test-token"
        now = at("07:00:00")
        async with AsyncSession(client.app.state.engine) as db:
            for suffix, text in [("run-start:old", "old stage"), ("notice:card", "card changed")]:
                db.add(
                    BotOutbox(
                        method="sendMessage",
                        payload=json.dumps({"chat_id": 5282463254, "text": text}),
                        state="pending",
                        attempts=0,
                        available_at=now,
                        dedup_key=f"owner-test:5282463254:{suffix}",
                    )
                )
            await db.commit()
        await bot_admin.deliver(client.app.state.engine, settings)
        assert calls == []
        await bot_admin.deliver(client.app.state.engine, settings)
        assert calls == ["card changed"]
        async with AsyncSession(client.app.state.engine) as db:
            assert {r.state for r in (await db.scalars(select(BotOutbox))).all()} == {
                "sent",
                "superseded",
            }

    asyncio.run(check())
