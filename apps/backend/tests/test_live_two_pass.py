import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from test_ai_schedule import seed, stamp
from test_processing import Provider, count, source

from studgroup.ai import ScreenDecision, ScreenResult, TokenUsage
from studgroup.main import Settings
from studgroup.models import AIAttempt, AIJob, Homework, RawMessage
from studgroup.processing import process_next

pytest_plugins = ["test_schedule_api"]


class CascadeProvider(Provider):
    def __init__(self, signal=True, **kwargs):
        super().__init__(**kwargs)
        self.signal = signal
        self.screen_calls = 0
        self.screen_context = []

    async def screen_batch(self, messages, timezone, target_message_id):
        self.screen_calls += 1
        self.screen_context = messages
        return ScreenResult(
            decision=ScreenDecision(
                signal=self.signal, source_message_ids=[target_message_id] if self.signal else []
            ),
            usage=TokenUsage(prompt_tokens=40, completion_tokens=10),
            model="deepseek-flash",
        )


def run(client, provider, now=None, **patch):
    settings = Settings(_env_file=None, ai_enabled=True, ai_live_two_pass=True, **patch)
    return asyncio.run(process_next(client.app.state.engine, settings, provider, now=now))


def test_every_message_reaches_model_and_only_model_discards_chatter(client):
    source(client, text="Всем привет")
    provider = CascadeProvider(signal=False)
    assert run(client, provider) == "completed"
    assert provider.screen_calls == 1
    assert provider.calls == 0
    assert count(client, AIAttempt) == 1
    assert count(client, Homework) == 0
    assert run(client, provider) == "idle"


def test_rolling_deploy_restores_unpaid_local_discard_without_replaying_ai(client):
    source(client, text="матан 16")
    now = datetime.now(UTC)

    async def discard():
        async with AsyncSession(client.app.state.engine) as db:
            raw = await db.scalar(select(RawMessage))
            raw.processing_state = "completed"
            raw.live_received_at = now
            await db.commit()

    asyncio.run(discard())
    provider = CascadeProvider(signal=False)
    assert run(client, provider) == "completed"
    assert provider.screen_calls == 1
    assert run(client, provider) == "idle"
    assert provider.screen_calls == 1


def test_keyword_free_task_gets_surrounding_context_and_publishes_without_tail(client):
    source(client, text="матан 16")
    source(client, mid=32, text="Это из сборника, страница 75")
    provider = CascadeProvider()
    assert run(client, provider) == "screened"
    assert {m["message_id"] for m in provider.screen_context} == {31, 32}
    assert count(client, Homework) == 0
    assert run(client, provider) == "completed"
    assert provider.calls == provider.screen_calls == 1
    assert count(client, Homework) == 1  # Neighbor has not been screened yet.


def test_deep_outage_keeps_screen_checkpoint_and_resumes_only_deep_stage(client):
    source(client, text="матан 16")
    now = datetime.now(UTC)
    provider = CascadeProvider(failure="provider_unreachable")
    assert run(client, provider, now) == "screened"
    assert run(client, provider, now + timedelta(seconds=1)) == "retry"
    recovered = CascadeProvider()
    assert run(client, recovered, now + timedelta(minutes=6)) == "completed"
    assert recovered.screen_calls == 0
    assert recovered.calls == 1
    assert count(client, Homework) == 1

    async def inspect():
        async with AsyncSession(client.app.state.engine) as db:
            job = await db.scalar(select(AIJob))
            assert job.screen_checkpoint and job.state == "done"
            attempts = (await db.scalars(select(AIAttempt).order_by(AIAttempt.created_at))).all()
            assert [a.outcome for a in attempts] == ["screened", "retry", "completed"]

    asyncio.run(inspect())


def test_screen_and_deep_continue_in_same_hourly_slot_but_new_text_waits(client):
    seed(client, stamp("06:55:00"))
    provider = CascadeProvider()
    assert run(client, provider, stamp("07:00:00"), ai_schedule_enabled=True) == "screened"
    assert run(client, provider, stamp("07:00:05"), ai_schedule_enabled=True) == "completed"
    seed(client, stamp("07:05:00"), mid=2)
    assert run(client, provider, stamp("07:20:00"), ai_schedule_enabled=True) == "idle"
    assert run(client, provider, stamp("07:30:00"), ai_schedule_enabled=True) == "idle"
    assert provider.screen_calls == provider.calls == 1
    assert run(client, provider, stamp("08:00:00"), ai_schedule_enabled=True) == "screened"
    assert provider.screen_calls == 2 and provider.calls == 1


def test_edit_invalidates_old_screen_checkpoint(client):
    source(client, text="матан 16")
    provider = CascadeProvider()
    assert run(client, provider) == "screened"

    async def edit():
        async with AsyncSession(client.app.state.engine) as db:
            raw = await db.scalar(select(RawMessage))
            raw.revision += 1
            raw.text = "матан 17"
            await db.commit()

    asyncio.run(edit())
    assert run(client, provider) == "screened"
    assert provider.screen_calls == 2
    assert provider.calls == 0
