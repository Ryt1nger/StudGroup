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
    GroupAIProfile,
    GroupTopic,
    Homework,
    OwnerIncident,
    RawMessage,
    SchedulePattern,
)
from studgroup.processing import process_next, retain_until

pytest_plugins = ["test_schedule_api"]


def source(client, mid=31, reply=None, text="ДЗ: решить задачи", *, when=None, imported=False):
    async def run():
        async with AsyncSession(client.app.state.engine) as db:
            group = await db.scalar(select(Group).where(Group.telegram_chat_id == -1001))
            now = when or datetime.now(UTC)
            if not imported and group.bot_added_at is None:
                group.bot_added_at = now
            raw = RawMessage(
                group_id=group.id,
                telegram_message_id=mid,
                reply_to_message_id=reply,
                text=text,
                message_date=now,
                version_date=now,
                revision=1,
                imported=imported,
                delete_at=now + timedelta(days=30),
                live_received_at=None if imported else now,
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


def test_structure_map_precedes_generation_scoped_post_install_analysis(client):
    source(client, text="#матан ДЗ: решить задачи")
    source(
        client,
        mid=30,
        text="Старое сообщение до появления бота",
        when=datetime.now(UTC) - timedelta(days=2),
        imported=True,
    )
    requested = datetime.now(UTC)

    async def seed_bootstrap():
        async with AsyncSession(client.app.state.engine) as db:
            raw = await db.scalar(select(RawMessage))
            db.add(
                AIJob(
                    raw_message_id=raw.id,
                    group_id=raw.group_id,
                    source_revision=raw.revision,
                    analysis_generation=1,
                    state="done",
                    attempts=1,
                    available_at=requested,
                    created_at=requested,
                )
            )
            db.add(
                GroupTopic(
                    group_id=raw.group_id,
                    telegram_thread_id=77,
                    name="Математика",
                    first_seen_at=requested,
                    updated_at=requested,
                )
            )
            db.add(
                GroupAIProfile(
                    group_id=raw.group_id,
                    generation=2,
                    state="mapping",
                    backfill_requested=True,
                    source_count=0,
                    requested_at=requested,
                )
            )
            await db.commit()

    asyncio.run(seed_bootstrap())

    class CapturingProvider(Provider):
        async def extract_batch(self, context, *args, **kwargs):
            self.context = context
            return await super().extract_batch(context, *args, **kwargs)

    provider = CapturingProvider()
    assert run(client, provider) == "structure_mapped"
    assert provider.calls == 0
    assert run(client, provider) == "completed"
    assert provider.calls == 1
    target = next(item for item in provider.context if item["is_target"])
    assert target["group_structure"]["topics"][0]["name"] == "Математика"
    assert target["group_structure"]["hashtags"] == ["#матан"]
    assert run(client, provider) == "backfill_completed"

    async def inspect():
        async with AsyncSession(client.app.state.engine) as db:
            jobs = (await db.scalars(select(AIJob).order_by(AIJob.analysis_generation))).all()
            profile = await db.scalar(select(GroupAIProfile))
            assert [job.analysis_generation for job in jobs] == [1, 2]
            assert profile.state == "live"
            assert profile.source_count == 1
            old = await db.scalar(select(RawMessage).where(RawMessage.telegram_message_id == 30))
            assert old.processing_state == "completed"

    asyncio.run(inspect())


def test_group_without_topics_gets_valid_unstructured_map(client):
    source(client, text="Всем привет")
    requested = datetime.now(UTC)

    async def seed_profile():
        async with AsyncSession(client.app.state.engine) as db:
            raw = await db.scalar(select(RawMessage))
            for pattern in (await db.scalars(select(SchedulePattern))).all():
                await db.delete(pattern)
            db.add(
                GroupAIProfile(
                    group_id=raw.group_id,
                    generation=1,
                    state="mapping",
                    backfill_requested=True,
                    source_count=0,
                    requested_at=requested,
                )
            )
            await db.commit()

    asyncio.run(seed_profile())
    assert run(client, Provider()) == "structure_mapped"

    async def inspect():
        async with AsyncSession(client.app.state.engine) as db:
            profile = await db.scalar(select(GroupAIProfile))
            assert json.loads(profile.structure_json)["mode"] == "unstructured"
            assert profile.state == "backfill"

    asyncio.run(inspect())


def test_budget_prevents_call_and_keeps_message_pending(client):
    source(client)
    provider = Provider()
    assert run(client, provider, ai_daily_group_budget_usd=0.001) == "idle"
    assert provider.calls == 0
    assert count(client, AIAttempt) == 0

    async def inspect():
        async with AsyncSession(client.app.state.engine) as db:
            row = await db.get(OwnerIncident, "ai_daily_group_budget_limit")
            assert row.active

    asyncio.run(inspect())


def test_total_budget_limit_has_distinct_owner_incident(client):
    source(client)
    provider = Provider()
    assert run(client, provider, ai_total_budget_usd=0.001) == "idle"
    assert provider.calls == 0

    async def inspect():
        async with AsyncSession(client.app.state.engine) as db:
            row = await db.get(OwnerIncident, "ai_total_budget_limit")
            assert row.active

    asyncio.run(inspect())


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


def test_quality_failure_is_terminal_without_paid_retry_or_owner_incident(client):
    source(client)

    class InvalidCitation:
        def __init__(self):
            self.calls = 0

        async def extract_batch(self, *args, **kwargs):
            self.calls += 1
            raise ProviderFailure(
                "invalid_source_reference",
                True,
                usage=TokenUsage(prompt_tokens=100, completion_tokens=100),
            )

    provider = InvalidCitation()
    assert run(client, provider) == "failed"
    assert run(client, provider) == "idle"
    assert provider.calls == 1

    async def inspect():
        async with AsyncSession(client.app.state.engine) as db:
            job = await db.scalar(select(AIJob))
            assert job.state == "failed"
            assert await db.get(OwnerIncident, "invalid_source_reference") is None

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


def test_configuration_failure_sleeps_job_until_next_hour_without_spend(client):
    source(client)
    start = datetime.now(UTC).replace(minute=5, second=0, microsecond=0)

    class BadKey:
        def __init__(self):
            self.calls = 0

        async def extract_batch(self, *args, **kwargs):
            self.calls += 1
            raise ProviderFailure("invalid_api_key", False, reservation_releasable=True)

    provider = BadKey()
    settings = Settings(ai_enabled=True)
    assert (
        asyncio.run(process_next(client.app.state.engine, settings, provider, now=start)) == "retry"
    )
    assert (
        asyncio.run(
            process_next(
                client.app.state.engine,
                settings,
                provider,
                now=start + timedelta(minutes=30),
            )
        )
        == "idle"
    )
    assert provider.calls == 1

    async def inspect():
        async with AsyncSession(client.app.state.engine) as db:
            job = await db.scalar(select(AIJob))
            attempt = await db.scalar(select(AIAttempt))
            assert job.state == "retry"
            assert job.available_at.replace(tzinfo=UTC) == (start + timedelta(hours=1)).replace(
                minute=0
            )
            assert attempt.charged_usd == 0

    asyncio.run(inspect())

    recovered = Provider()
    assert (
        asyncio.run(
            process_next(
                client.app.state.engine,
                settings,
                recovered,
                now=(start + timedelta(hours=1)).replace(minute=0),
            )
        )
        == "completed"
    )
    assert recovered.calls == 1


def test_running_provider_call_blocks_a_second_group_globally(client):
    source(client)

    async def add_second_group():
        async with AsyncSession(client.app.state.engine) as db:
            now = datetime.now(UTC)
            group = Group(
                telegram_chat_id=-2002,
                name="Second group",
                timezone="Europe/Moscow",
                status="active",
                pilot_authorized=True,
            )
            db.add(group)
            await db.flush()
            db.add(
                RawMessage(
                    group_id=group.id,
                    telegram_message_id=31,
                    text="ДЗ: решить задачи",
                    message_date=now,
                    version_date=now,
                    revision=1,
                    processing_state="pending",
                    delete_at=now + timedelta(days=30),
                )
            )
            await db.commit()

    asyncio.run(add_second_group())
    settings = Settings(ai_enabled=True)
    nested = Provider()

    class ReentrantProvider(Provider):
        async def extract_batch(self, *args, **kwargs):
            self.nested_outcome = await process_next(
                client.app.state.engine,
                settings,
                nested,
                now=datetime.now(UTC),
            )
            return await super().extract_batch(*args, **kwargs)

    first = ReentrantProvider()
    assert asyncio.run(process_next(client.app.state.engine, settings, first)) == "completed"
    assert first.nested_outcome == "idle"
    assert nested.calls == 0
    assert asyncio.run(process_next(client.app.state.engine, settings, nested)) == "completed"
    assert nested.calls == 1


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


def test_request_does_not_start_when_processing_window_is_about_to_close(client):
    source(client)
    now = datetime.now(UTC)

    provider = Provider()
    settings = Settings(ai_enabled=True, ai_enabled_until=now + timedelta(seconds=1))
    assert (
        asyncio.run(process_next(client.app.state.engine, settings, provider, now=now))
        == "window_closing"
    )
    assert provider.calls == 0
    assert count(client, AIAttempt) == 0
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


def test_uncertain_complete_assignment_is_published_and_marked_for_review(client):
    source(client)

    class UncertainProvider(Provider):
        async def extract_batch(self, *args, **kwargs):
            result = await super().extract_batch(*args, **kwargs)
            result.batch.assignments[0].confidence = 50
            return result

    assert run(client, UncertainProvider()) == "completed"
    assert count(client, Homework) == 1
    assert count(client, AICandidate) == 1
    assert run(client, UncertainProvider()) == "idle"

    async def inspect():
        async with AsyncSession(client.app.state.engine) as db:
            assert (await db.scalar(select(RawMessage))).processing_state == "completed"
            candidate = await db.scalar(select(AICandidate))
            assert candidate.state == "published"
            assert json.loads(candidate.payload)["confidence"] == 50
            card = await db.scalar(select(Homework))
            assert card.status == "published"
            assert card.verification_state == "needs_clarification"

    asyncio.run(inspect())


def test_unexpected_needs_context_response_is_retained_as_private_candidate(client):
    source(client)

    class NeedsContextProvider(Provider):
        async def extract_batch(self, *args, **kwargs):
            item = SourcedExtraction(
                kind="needs_context",
                subject=None,
                title=None,
                description=None,
                deadline_at=None,
                deadline_date_only=False,
                urgency="normal",
                confidence=30,
                source_message_ids=[31],
            )
            return BatchResult(
                batch=BatchExtraction(assignments=[item]),
                usage=TokenUsage(prompt_tokens=100, completion_tokens=30),
                model="deepseek-flash",
                prompt_version="test",
            )

    assert run(client, NeedsContextProvider()) == "completed"
    assert count(client, Homework) == 0
    assert count(client, AICandidate) == 1

    async def inspect():
        async with AsyncSession(client.app.state.engine) as db:
            candidate = await db.scalar(select(AICandidate))
            assert candidate.state == "review"
            assert json.loads(candidate.payload)["kind"] == "needs_context"

    asyncio.run(inspect())


def test_concrete_test_with_unknown_subject_is_retained_for_review(client):
    source(client, mid=3981, text="У нас тест в СДО закроется через час")

    class IncompleteTestProvider(Provider):
        async def extract_batch(self, *args, **kwargs):
            item = SourcedExtraction(
                kind="test",
                subject=None,
                title="Тест в СДО",
                description="Тест в СДО закроется через час",
                deadline_at=None,
                deadline_date_only=False,
                urgency="normal",
                confidence=70,
                source_message_ids=[3981],
            )
            return BatchResult(
                batch=BatchExtraction(assignments=[item]),
                usage=TokenUsage(prompt_tokens=100, completion_tokens=30),
                model="deepseek-flash",
                prompt_version="test",
            )

    assert run(client, IncompleteTestProvider()) == "completed"
    assert count(client, Homework) == 0
    assert count(client, AICandidate) == 1

    async def inspect():
        async with AsyncSession(client.app.state.engine) as db:
            raw = await db.scalar(select(RawMessage))
            candidate = await db.scalar(select(AICandidate))
            assert raw.processing_state == "needs_context"
            assert candidate.state == "review"
            assert json.loads(candidate.payload)["kind"] == "test"

    asyncio.run(inspect())


def test_overlapping_source_cluster_updates_one_card_instead_of_duplicating(client):
    source(client, mid=4063, text="#русский")
    source(client, mid=4067, text="#русский\nДз по ПИР: Актуальность, Предмет, Задача")
    provider = Provider(ids=[4063, 4067])

    assert run(client, provider) == "completed"
    assert run(client, provider) == "completed"
    assert count(client, Homework) == 1

    async def inspect():
        async with AsyncSession(client.app.state.engine) as db:
            card = await db.scalar(select(Homework))
            anchor = await db.get(RawMessage, card.raw_message_id)
            assert anchor.telegram_message_id == 4063

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
    assert run(client, Provider()) == "structure_mapped"
    assert run(client, Provider()) == "completed"
    response = client.get("/v1/homework?filter=all", headers={"Authorization": "Bearer valid"})
    assert response.status_code == 200
    assert len(response.json()["items"]) == 1
    task_id = response.json()["items"][0]["id"]
    detail = client.get(f"/v1/homework/{task_id}", headers={"Authorization": "Bearer valid"})
    assert detail.status_code == 200
    assert client.get(f"/v1/homework/{task_id}").status_code == 401
