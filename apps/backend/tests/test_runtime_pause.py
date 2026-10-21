import asyncio

import pytest

from studgroup import runtime
from studgroup.main import Settings


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

    async def dispatch(engine, settings):
        return "idle"

    async def flush(engine, settings):
        return None

    async def sleep(seconds):
        pauses.append(seconds)
        if len(pauses) == 2:
            raise asyncio.CancelledError

    monkeypatch.setattr(runtime, "dispatch_next", dispatch)
    monkeypatch.setattr("studgroup.worker.flush_incidents", flush)
    monkeypatch.setattr(runtime.asyncio, "sleep", sleep)
    settings = Settings(_env_file=None, ai_loop_pause_seconds=configured)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(runtime.processing_loop(None, settings))

    assert pauses == [configured, configured]
