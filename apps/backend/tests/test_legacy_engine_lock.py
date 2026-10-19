import asyncio

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from test_processing import Provider, count, source

from studgroup.analysis.dispatch import dispatch_next
from studgroup.main import Settings
from studgroup.models import AIAttempt, Homework, RawMessage

pytest_plugins = ["test_schedule_api"]


def dispatch(client, provider, **patch):
    return asyncio.run(
        dispatch_next(
            client.app.state.engine,
            Settings(_env_file=None, ai_enabled=True, **patch),
            provider=provider,
        )
    )


def pending(client):
    async def read():
        async with AsyncSession(client.app.state.engine) as db:
            return await db.scalar(
                select(func.count())
                .select_from(RawMessage)
                .where(RawMessage.processing_state == "pending")
            )

    return asyncio.run(read())


def test_legacy_engine_is_locked_by_default():
    assert Settings(_env_file=None).legacy_engine_enabled is False
    assert Settings(_env_file=None, ai_enabled=True).legacy_engine_enabled is False


def test_ai_enabled_alone_cannot_wake_the_legacy_engine(client):
    source(client)
    provider = Provider()
    for _ in range(3):
        assert dispatch(client, provider) == "disabled"
    assert provider.calls == 0
    assert count(client, Homework) == 0
    assert count(client, AIAttempt) == 0
    assert pending(client) == 1


def test_legacy_engine_runs_only_with_the_explicit_flag(client):
    source(client)
    provider = Provider()
    assert dispatch(client, provider, legacy_engine_enabled=True) == "completed"
    assert provider.calls == 1
    assert count(client, Homework) == 1
