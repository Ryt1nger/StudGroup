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


def test_outage_stops_after_two_attempts(client):
    source(client)
    now = datetime.now(UTC)
    provider = Provider(failure="provider_unreachable")
    settings = Settings(ai_enabled=True)
    for minutes, outcome in [(0, "retry"), (6, "failed"), (12, "idle")]:
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
    assert provider.calls == 2


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
