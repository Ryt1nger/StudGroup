"""Runtime acknowledgements are generation-fenced and alert only the owner."""

import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from studgroup.models import BackgroundProcess, BotOutbox
from studgroup.process_runtime import ProcessGate, report_process, watchdog_once

pytest_plugins = ["test_schedule_api"]


def test_runtime_acknowledgement_and_heartbeat(client):
    settings = client.app.state.settings
    settings.bot_admin_user_id = 1
    engine = client.app.state.engine
    owner = {"Authorization": "Bearer valid"}
    body = client.post("/v1/admin/processes/sdo/enable", headers=owner).json()
    generation = body["generation"]

    async def exercise():
        gate = ProcessGate(engine, settings, "sdo", False)
        assert await gate.begin() == generation
        async with AsyncSession(engine) as db:
            row = await db.get(BackgroundProcess, "sdo")
            assert row.status == "starting"
            assert row.applied_generation != generation
        await gate.succeeded(generation)
        await gate.succeeded(generation)
        async with AsyncSession(engine) as db:
            row = await db.get(BackgroundProcess, "sdo")
            assert row.status == "running"
            assert row.applied_generation == generation
            assert row.heartbeat_at is not None
            notices = (
                await db.scalars(
                    select(BotOutbox).where(BotOutbox.dedup_key.startswith("process-control:"))
                )
            ).all()
            assert len(notices) == 1
            assert "Процесс запущен" in notices[0].payload
        future = datetime.now(UTC) + timedelta(hours=1)
        await watchdog_once(engine, settings, now=future)
        await watchdog_once(engine, settings, now=future)
        async with AsyncSession(engine) as db:
            row = await db.get(BackgroundProcess, "sdo")
            assert row.error_code == "heartbeat_missed"
            notices = (
                await db.scalars(
                    select(BotOutbox).where(BotOutbox.dedup_key.startswith("process-control:"))
                )
            ).all()
            assert len(notices) == 2

    asyncio.run(exercise())

    async def recovered():
        assert await report_process(engine, settings, "sdo", generation, "running")
        async with AsyncSession(engine) as db:
            row = await db.get(BackgroundProcess, "sdo")
            assert row.status == "running"
            assert row.error_code == "heartbeat_missed"  # preserve the audit record

    asyncio.run(recovered())
    processes = client.get("/v1/admin/processes", headers=owner).json()["processes"]
    assert next(row for row in processes if row["name"] == "sdo")["error_active"] is False

    disabled = client.post("/v1/admin/processes/sdo/disable", headers=owner).json()

    async def stopped():
        assert not await report_process(engine, settings, "sdo", generation, "running")
        gate = ProcessGate(engine, settings, "sdo", True)
        assert await gate.begin() is None
        async with AsyncSession(engine) as db:
            row = await db.get(BackgroundProcess, "sdo")
            assert row.status == "stopped"
            assert row.applied_generation == disabled["generation"]

    asyncio.run(stopped())


def test_process_sleep_wakes_for_new_urgent_request(monkeypatch):
    from studgroup.process_runtime import Intent

    elapsed = 0.0

    async def sleep(seconds):
        nonlocal elapsed
        elapsed += seconds

    async def intent(*args):
        return Intent(True, 1)

    async def wake():
        return elapsed >= 5

    monkeypatch.setattr("studgroup.process_runtime.monotonic", lambda: elapsed)
    monkeypatch.setattr("studgroup.process_runtime.asyncio.sleep", sleep)
    monkeypatch.setattr("studgroup.process_runtime.read_intent", intent)
    gate = ProcessGate(None, None, "sdo", True)
    asyncio.run(gate.sleep(1, 3600, wake=wake))
    assert elapsed == 5
