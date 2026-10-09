import asyncio
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from studgroup.ai import (
    BatchExtraction,
    BatchResult,
    ProviderFailure,
    SourcedExtraction,
    TokenUsage,
)
from studgroup.main import Settings
from studgroup.models import (
    AcademicDeadline,
    AIAttempt,
    AICandidate,
    AIControl,
    AIJob,
    Group,
    Homework,
    OwnerIncident,
    RawMessage,
)
from studgroup.processing import process_next, retain_until

pytest_plugins = ["test_schedule_api"]


def source(client, mid=31, reply=None, text="ДЗ: решить задачи"):
    async def run():
        async with AsyncSession(client.app.state.engine) as db:
            group = await db.scalar(select(Group).where(Group.telegram_chat_id == -1001))
            now = datetime.now(UTC)
            raw = RawMessage(
                group_id=group.id,
                telegram_message_id=mid,
                reply_to_message_id=reply,
                text=text,
                message_date=now,
                version_date=now,
                revision=1,
                delete_at=now + timedelta(days=30),
            )
            db.add(raw)
            await db.commit()

    asyncio.run(run())


class Provider:
    def __init__(self, kind="homework", failure=None, date=None, ids=None):
        self.calls = 0
        self.kind, self.failure, self.date, self.ids = kind, failure, date, ids

    async def extract_batch(self, context, timezone, schedule, target_message_id):
        self.calls += 1
        if self.failure:
            raise ProviderFailure(self.failure, True)
        values = {
            "kind": self.kind,
            "subject": "Математика",
            "title": "Решить задачи",
            "description": "Номера 1–3",
            "deadline_at": self.date or (datetime.now(UTC) + timedelta(days=2)).isoformat(),
            "deadline_date_only": False,
            "urgency": "normal",
            "confidence": 95,
            "source_message_ids": self.ids or [target_message_id],
        }
        item = SourcedExtraction.model_validate_json(json.dumps(values))
        return BatchResult(
            batch=BatchExtraction(assignments=[item]),
            usage=TokenUsage(prompt_tokens=100, completion_tokens=100),
            model="deepseek-flash",
            prompt_version="test",
        )


def run(client, provider, **patch):
    return asyncio.run(
        process_next(client.app.state.engine, Settings(ai_enabled=True, **patch), provider)
    )


def count(client, model):
    async def read():
        async with AsyncSession(client.app.state.engine) as db:
            return await db.scalar(select(func.count()).select_from(model))

    return asyncio.run(read())


def test_worker_publishes_once_and_records_actual_cost(client):
    source(client)
    provider = Provider()
    assert run(client, provider) == "completed"
    assert run(client, provider) == "idle"
    assert provider.calls == 1
    assert count(client, Homework) == 1
    assert count(client, AIJob) == 1

    async def billing():
        async with AsyncSession(client.app.state.engine) as db:
            attempt = await db.scalar(select(AIAttempt))
            assert attempt.prompt_tokens == 100
            assert attempt.charged_usd == Decimal("0.00015")
            assert (await db.get(AIControl, 1)).spent_usd == attempt.charged_usd

    asyncio.run(billing())


def test_budget_prevents_call_and_keeps_message_pending(client):
    source(client)
    provider = Provider()
    assert run(client, provider, ai_daily_group_budget_usd=0.001) == "idle"
    assert provider.calls == 0
    assert count(client, AIAttempt) == 0


def test_hourly_safety_limit_pauses_only_until_rolling_spend_expires(client):
    source(client)
    assert run(client, Provider(), ai_hourly_group_budget_usd=0.012) == "completed"
    source(client, mid=32)
    provider = Provider()
    assert run(client, provider, ai_hourly_group_budget_usd=0.012) == "idle"
    assert provider.calls == 0

    async def inspect():
        async with AsyncSession(client.app.state.engine) as db:
            row = await db.get(OwnerIncident, "ai_hourly_safety_limit")
            assert row.active

    asyncio.run(inspect())


def test_three_consecutive_spend_risk_failures_open_auto_circuit(client):
    for mid in range(31, 35):
        source(client, mid=mid)
    provider = Provider(failure="provider_unreachable")
    config = {
        "ai_circuit_failure_threshold": 3,
        "ai_circuit_cooldown_seconds": 900,
        "ai_hourly_group_budget_usd": 1,
    }
    assert run(client, provider, **config) == "retry"
    assert run(client, provider, **config) == "retry"
    assert run(client, provider, **config) == "retry"
    assert run(client, provider, **config) == "idle"
    assert provider.calls == 3

    async def inspect():
        async with AsyncSession(client.app.state.engine) as db:
            control = await db.get(AIControl, 1)
            incident = await db.get(OwnerIncident, "ai_circuit_open")
            assert control.provider_failure_streak == 3
            assert control.provider_circuit_open_until is not None
            assert control.provider_circuit_reason == "provider_unreachable"
            assert incident.active

    asyncio.run(inspect())


def test_circuit_cooldown_allows_probe_and_valid_response_closes_it(client):
    source(client)
    now = datetime.now(UTC)

    async def seed():
        async with AsyncSession(client.app.state.engine) as db:
            control = await db.get(AIControl, 1)
            if control is None:
                control = AIControl(id=1, spent_usd=Decimal(0), provider_failure_streak=0)
                db.add(control)
            control.provider_failure_streak = 3
            control.provider_circuit_open_until = now - timedelta(seconds=1)
            control.provider_circuit_reason = "provider_error"
            from studgroup.processing import incident

            await incident(db, "ai_circuit_open", now - timedelta(minutes=20))
            await db.commit()

    asyncio.run(seed())
    provider = Provider()
    result = asyncio.run(
        process_next(
            client.app.state.engine,
            Settings(ai_enabled=True, ai_hourly_group_budget_usd=1),
            provider,
            now=now,
        )
    )
    assert result == "completed"
    assert provider.calls == 1

    async def inspect():
        async with AsyncSession(client.app.state.engine) as db:
            control = await db.get(AIControl, 1)
            incident = await db.get(OwnerIncident, "ai_circuit_open")
            assert control.provider_failure_streak == 0
            assert control.provider_circuit_open_until is None
            assert control.provider_circuit_reason is None
            assert not incident.active

    asyncio.run(inspect())


def test_one_source_cannot_retry_a_spend_risk_stage_forever(client):
    source(client)
    start = datetime.now(UTC)
    provider = Provider(failure="provider_unreachable")
    settings = Settings(
        ai_enabled=True,
        ai_hourly_group_budget_usd=10,
        ai_daily_group_budget_usd=10,
        ai_total_budget_usd=10,
        ai_circuit_failure_threshold=100,
        ai_max_recoverable_attempts_per_stage=3,
    )
    for step in range(3):
        assert (
            asyncio.run(
                process_next(
                    client.app.state.engine,
                    settings,
                    provider,
                    now=start + timedelta(minutes=6 * step),
                )
            )
            == "retry"
        )
    assert (
        asyncio.run(
            process_next(
                client.app.state.engine,
                settings,
                provider,
                now=start + timedelta(minutes=18),
            )
        )
        == "idle"
    )
    assert provider.calls == 3

    async def inspect():
        async with AsyncSession(client.app.state.engine) as db:
            job = await db.scalar(select(AIJob))
            raw = await db.scalar(select(RawMessage))
            incident = await db.get(OwnerIncident, "processing_exhausted")
            assert job.state == "failed"
            assert raw.processing_state == "failed"
            assert incident.active

    asyncio.run(inspect())


def test_outage_is_durable_delayed_and_conservatively_charged(client):
    source(client)
    provider = Provider(failure="provider_unreachable")
    assert run(client, provider) == "retry"
    assert run(client, provider) == "idle"
    assert provider.calls == 1

    async def inspect():
        async with AsyncSession(client.app.state.engine) as db:
            assert (await db.scalar(select(AIJob))).state == "retry"
            assert (await db.scalar(select(AIAttempt))).charged_usd == Decimal("0.012")

    asyncio.run(inspect())


def test_confirmed_rejection_releases_attempt_and_global_budget(client):
    source(client)

    class Rejected:
        async def extract_batch(self, *args, **kwargs):
            raise ProviderFailure("rate_limited", True, reservation_releasable=True)

    assert run(client, Rejected()) == "retry"

    async def inspect():
        async with AsyncSession(client.app.state.engine) as db:
            assert (await db.scalar(select(AIAttempt))).charged_usd == Decimal(0)
            assert (await db.get(AIControl, 1)).spent_usd == Decimal(0)

    asyncio.run(inspect())
    # A different message can consume the otherwise blocked daily budget.
    source(client, mid=32)
    assert run(client, Provider(), ai_daily_group_budget_usd=0.012) == "completed"


def test_control_point_is_never_published_as_homework(client):
    source(client, text="КТ: решить задачи")
    assert run(client, Provider(kind="control_point")) == "completed"
    assert count(client, AcademicDeadline) == 1
    assert count(client, Homework) == 0


def test_reply_updates_existing_task_without_duplicate(client):
    source(client)
    assert run(client, Provider()) == "completed"
    source(client, mid=32, reply=31, text="Сдать до 15 октября")
    assert run(client, Provider(date="2026-10-15T10:00:00+03:00", ids=[31, 32])) == "completed"
    assert count(client, Homework) == 1

    async def check():
        async with AsyncSession(client.app.state.engine) as db:
            task = await db.scalar(select(Homework))
            assert task.revision == 2
            assert task.significant_updated_at is not None
            assert task.deadline_at.day == 15

    asyncio.run(check())


def test_adjacent_deadline_fragment_amends_one_cited_task(client):
    source(client)
    assert run(client, Provider()) == "completed"
    source(client, mid=32, text="К следующей среде")
    assert run(client, Provider(date="2026-10-07T10:00:00+03:00", ids=[31, 32])) == "completed"
    assert count(client, Homework) == 1


def test_date_fragment_without_academic_neighbor_does_not_spend(client):
    source(client, text="Завтра")
    provider = Provider()
    assert run(client, provider) == "idle"
    assert provider.calls == 0


def test_chatter_is_filtered_without_spending(client):
    source(client, text="Всем привет")
    provider = Provider()
    assert run(client, provider) == "idle"
    assert provider.calls == 0
    assert count(client, AIAttempt) == 0


def test_long_term_tasks_are_retained_until_after_event():
    created = datetime(2026, 10, 6, tzinfo=UTC)
    deadline = datetime(2027, 5, 1, tzinfo=UTC)
    assert retain_until(created, deadline) == deadline + timedelta(days=7)
    assert retain_until(created, None) == created + timedelta(days=90)


def test_cutoff_stops_calls_at_and_after_midnight(client):
    source(client)
    now = datetime(2026, 10, 7, 21, tzinfo=UTC)
    provider = Provider()
    settings = Settings(ai_enabled=True, ai_enabled_until=now)
    assert (
        asyncio.run(process_next(client.app.state.engine, settings, provider, now=now))
        == "scheduled_off"
    )
    assert (
        asyncio.run(
            process_next(client.app.state.engine, settings, provider, now=now + timedelta(hours=12))
        )
        == "scheduled_off"
    )
    assert provider.calls == 0
    assert count(client, AIAttempt) == 0


def test_active_request_is_cancelled_at_cutoff_without_publishing(client):
    source(client)
    now = datetime.now(UTC)

    class SlowProvider(Provider):
        async def extract_batch(self, *args, **kwargs):
            self.calls += 1
            await asyncio.sleep(2)
            return await super().extract_batch(*args, **kwargs)

    provider = SlowProvider()
    settings = Settings(ai_enabled=True, ai_enabled_until=now + timedelta(seconds=1))
    assert (
        asyncio.run(process_next(client.app.state.engine, settings, provider, now=now)) == "retry"
    )
    assert provider.calls == 1
    assert count(client, Homework) == 0


def test_import_history_review_is_processed_only_when_group_is_explicitly_enabled(client):
    source(client)

    async def prepare():
        async with AsyncSession(client.app.state.engine) as db:
            row = await db.scalar(select(RawMessage))
            row.imported = True
            row.processing_state = "needs_context"
            row.delete_at = datetime.now(UTC) - timedelta(days=1)
            await db.commit()

    asyncio.run(prepare())
    provider = Provider()
    assert run(client, provider) == "idle"
    assert provider.calls == 0
    assert run(client, provider, ai_import_chat_id=-1001) == "completed"
    assert provider.calls == 1


def test_expired_worker_cannot_overwrite_new_attempt(client):
    source(client)
    now = datetime.now(UTC)
    settings = Settings(ai_enabled=True)

    class SlowProvider(Provider):
        async def extract_batch(self, *args, **kwargs):
            assert (
                await process_next(
                    client.app.state.engine,
                    settings,
                    Provider(date="2026-10-15T10:00:00+03:00"),
                    now=now + timedelta(minutes=4),
                )
                == "completed"
            )
            return await super().extract_batch(*args, **kwargs)

    assert (
        asyncio.run(
            process_next(
                client.app.state.engine,
                settings,
                SlowProvider(date="2026-10-10T10:00:00+03:00"),
                now=now,
            )
        )
        == "superseded"
    )

    async def inspect():
        async with AsyncSession(client.app.state.engine) as db:
            assert (await db.scalar(select(Homework))).deadline_at.day == 15
            assert (await db.scalar(select(AIJob))).state == "done"
            assert (await db.get(AIControl, 1)).spent_usd == Decimal("0.00030")

    asyncio.run(inspect())


def test_outage_stays_pending_until_recovery_without_replaying_completed_jobs(client):
    source(client)
    now = datetime.now(UTC)
    provider = Provider(failure="provider_unreachable")
    settings = Settings(ai_enabled=True)
    for minutes, outcome in [(0, "retry"), (6, "retry"), (12, "retry")]:
        assert (
            asyncio.run(
                process_next(
                    client.app.state.engine,
                    settings,
                    provider,
                    now=now + timedelta(minutes=minutes),
                )
            )
            == outcome
        )
    assert provider.calls == 3
    recovered = Provider()
    assert (
        asyncio.run(
            process_next(
                client.app.state.engine, settings, recovered, now=now + timedelta(minutes=18)
            )
        )
        == "completed"
    )
    assert (
        asyncio.run(
            process_next(
                client.app.state.engine, settings, recovered, now=now + timedelta(minutes=19)
            )
        )
        == "idle"
    )
    assert recovered.calls == 1
    assert count(client, Homework) == 1


def test_old_transport_exhaustion_is_recovered_after_processor_restart(client):
    source(client)
    assert run(client, Provider(failure="provider_unreachable")) == "retry"

    async def abandon():
        async with AsyncSession(client.app.state.engine) as db:
            job = await db.scalar(select(AIJob))
            raw = await db.scalar(select(RawMessage))
            job.state = "failed"
            job.attempts = 2
            raw.processing_state = "failed"
            await db.commit()

    asyncio.run(abandon())
    recovered = Provider()
    assert run(client, recovered) == "completed"
    assert run(client, recovered) == "idle"
    assert recovered.calls == 1


def test_uncertain_candidate_is_retained_for_review(client):
    source(client)

    class UncertainProvider(Provider):
        async def extract_batch(self, *args, **kwargs):
            result = await super().extract_batch(*args, **kwargs)
            result.batch.assignments[0].confidence = 50
            return result

    assert run(client, UncertainProvider()) == "completed"
    assert count(client, Homework) == 0
    assert count(client, AICandidate) == 1
    assert run(client, UncertainProvider()) == "idle"

    async def inspect():
        async with AsyncSession(client.app.state.engine) as db:
            assert (await db.scalar(select(RawMessage))).processing_state == "needs_context"
            candidate = await db.scalar(select(AICandidate))
            assert candidate.state == "review"
            assert json.loads(candidate.payload)["confidence"] == 50

    asyncio.run(inspect())


def test_multiple_tasks_are_saved_without_silently_dropping_one(client):
    source(client)

    class MultipleProvider(Provider):
        async def extract_batch(self, *args, **kwargs):
            result = await super().extract_batch(*args, **kwargs)
            second = result.batch.assignments[0].model_copy(deep=True)
            second.title = "Другие задачи"
            result.batch.assignments.append(second)
            return result

    assert run(client, MultipleProvider()) == "completed"
    assert count(client, Homework) == 0
    assert count(client, AICandidate) == 2
    assert run(client, MultipleProvider()) == "idle"


def test_inferred_date_is_not_shifted_by_repeated_missing_deadline(client):
    class InferredProvider(Provider):
        async def extract_batch(self, *args, **kwargs):
            result = await super().extract_batch(*args, **kwargs)
            result.batch.assignments[0]._deadline_basis = "provisional_next_subject_lesson"
            return result

    source(client)
    assert run(client, InferredProvider(date="2026-10-07T10:00:00+03:00")) == "completed"
    source(client, mid=32, reply=31, text="ДЗ: те же задачи")
    assert (
        run(client, InferredProvider(date="2026-10-14T10:00:00+03:00", ids=[31, 32])) == "completed"
    )

    async def inspect():
        async with AsyncSession(client.app.state.engine) as db:
            row = await db.scalar(select(Homework))
            assert row.deadline_at.day == 7
            assert row.verification_state == "inferred"

    asyncio.run(inspect())


def test_webhook_to_worker_to_authenticated_api(client):
    from test_ingestion import delivery, send

    payload = delivery()
    payload["message"]["date"] = int(datetime.now(UTC).timestamp())
    assert send(client, payload).status_code == 200
    assert run(client, Provider()) == "completed"
    response = client.get("/v1/homework?filter=all", headers={"Authorization": "Bearer valid"})
    assert response.status_code == 200
    assert len(response.json()["items"]) == 1
    task_id = response.json()["items"][0]["id"]
    detail = client.get(f"/v1/homework/{task_id}", headers={"Authorization": "Bearer valid"})
    assert detail.status_code == 200
    assert client.get(f"/v1/homework/{task_id}").status_code == 401
