import asyncio

import pytest

from studgroup import process_runtime, runtime
from studgroup.main import Settings
from studgroup.processing_health import BacklogHealth


def test_pause_defaults_to_four_seconds_and_is_configurable():
    assert Settings(_env_file=None).ai_loop_pause_seconds == 4
    assert Settings(_env_file=None, ai_loop_pause_seconds=1).ai_loop_pause_seconds == 1
    with pytest.raises(ValueError):
        Settings(_env_file=None, ai_loop_pause_seconds=-1)
    with pytest.raises(ValueError):
        Settings(_env_file=None, ai_loop_pause_seconds=61)


@pytest.mark.parametrize("configured", [1, 4])
def test_processing_loop_sleeps_for_the_configured_pause(monkeypatch, configured):
    pauses = []
    clock = [0.0]

    async def read_intent(engine, name, default):
        return process_runtime.Intent(True, 0)

    async def report_process(*args, **kwargs):
        return True

    async def ai_health(settings):
        return None

    async def dispatch(engine, settings):
        return "idle"

    async def flush(engine, settings):
        return None

    async def sleep(seconds):
        pauses.append(seconds)
        clock[0] += seconds
        if len(pauses) == 2:
            raise asyncio.CancelledError

    async def inspect_health(engine, settings):
        return BacklogHealth(0, 0, ())

    recoveries = []

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def commit(self):
            return None

    async def record_recovery(db, code, now, recover=False, detail=None):
        recoveries.append((code, recover))

    monkeypatch.setattr(process_runtime, "read_intent", read_intent)
    monkeypatch.setattr(process_runtime, "report_process", report_process)
    monkeypatch.setattr(process_runtime, "monotonic", lambda: clock[0])
    monkeypatch.setattr(runtime, "ai_health", ai_health)
    monkeypatch.setattr(runtime, "AsyncSession", lambda engine: FakeSession())
    monkeypatch.setattr(runtime, "incident", record_recovery)
    monkeypatch.setattr(runtime, "inspect", inspect_health)
    monkeypatch.setattr(runtime, "dispatch_next", dispatch)
    monkeypatch.setattr("studgroup.worker.flush_incidents", flush)
    monkeypatch.setattr(runtime.asyncio, "sleep", sleep)
    settings = Settings(_env_file=None, ai_loop_pause_seconds=configured)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(runtime.processing_loop(None, settings))

    assert pauses == [configured, configured]
    # The persisted owner alert must recover even if this process just restarted.
    assert recoveries == [("ai_processing_paused", True)]
