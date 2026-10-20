"""The package worker (new engine) for groups in mode ``new``: gating, isolation, idempotency."""

import asyncio
import json
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from test_analysis_shadow import context_payload, proposal
from test_processing import source

from studgroup.ai import ProviderFailure
from studgroup.analysis.contracts import ANALYSIS_ENGINE_VERSION, ANALYSIS_SCHEMA_VERSION
from studgroup.analysis.dispatch import dispatch_next
from studgroup.analysis.package_worker import process_package_next
from studgroup.analysis.pilot import transition_mode
from studgroup.analysis.provider import ProviderResponse, ProviderUsage
from studgroup.main import Settings
from studgroup.models import (
    AcademicDeadline,
    BotOutbox,
    CanonicalAcademicItem,
    Group,
    GroupAnalysisSettings,
    Homework,
    Notification,
    RawMessage,
    SchedulePattern,
    ShadowEvaluationRun,
)

pytest_plugins = ["test_schedule_api"]

BASE = datetime.now(UTC) - timedelta(hours=3)


class PackageProvider:
    """Answers both passes of a package; the second pass proposes one card citing two sources."""

    def __init__(
        self,
        *,
        failure=None,
        cited=(31, 33),
        propose=True,
        update_existing=False,
        subject=None,
        garbage_first=0,
        outside_context=False,
    ):
        self.outside_context = outside_context
        self.garbage_first = garbage_first  # this many context answers are not valid JSON
        self.subject = subject
        self.requests = []
        self.failure = failure
        self.cited = cited
        self.propose = propose
        self.update_existing = update_existing
        self.targets = []
        self.offered = []

    async def complete(self, request):
        self.requests.append(request)
        if self.failure:
            raise ProviderFailure(self.failure, True)
        revision = request.user_payload["source_revision"]
        if request.prompt_version == "cplus-context-1" and self.garbage_first > 0:
            self.garbage_first -= 1
            return ProviderResponse(
                content="это не JSON",
                usage=ProviderUsage(prompt_tokens=10, completion_tokens=5),
                model="fake",
                finish_reason="stop",
                payload_sha256=str(len(self.requests)).zfill(64),
            )
        if request.prompt_version == "cplus-context-1":
            self.targets = list(request.user_payload["target_message_ids"])
            payload = context_payload(
                source_revision=revision,
                relevant_source_ids=[*self.targets, 9999] if self.outside_context else self.targets,
                discussion_facts=[],
            )
            if self.outside_context:
                # a reply parent that precedes the window: the model cites a message not in it
                payload["discussion_facts"] = [
                    {
                        "fact": "Ответ на сообщение вне окна",
                        "evidence": [
                            {"telegram_message_id": 9999, "revision": 1, "fact_paths": ["subject"]}
                        ],
                    }
                ]
        else:
            items = []
            self.offered = request.user_payload.get("existing_items", [])
            if self.propose:
                item = proposal(source_id=self.cited[0]).model_dump(mode="json")
                if self.subject is not None:
                    item["subject"] = self.subject
                if self.update_existing and self.offered:
                    # What the real model is told to do: copy the trusted id and revision.
                    item["action"] = "update"
                    item["existing_item_id"] = self.offered[0]["id"]
                    item["expected_item_revision"] = self.offered[0]["revision"]
                    item["description"] = "Решить задачи 1–4."
                item["evidence"] = [
                    {"telegram_message_id": mid, "revision": 1, "fact_paths": ["description"]}
                    for mid in self.cited
                ]
                items.append(item)
            payload = {
                "schema_version": ANALYSIS_SCHEMA_VERSION,
                "engine_version": ANALYSIS_ENGINE_VERSION,
                "source_revision": revision,
                "proposals": items,
                "explicit_empty": not items,
            }
        return ProviderResponse(
            content=json.dumps(payload),
            usage=ProviderUsage(prompt_tokens=10, completion_tokens=5),
            model="fake",
            finish_reason="stop",
            payload_sha256=str(len(self.requests)).zfill(64),
        )


def seed(client, offsets=(0, 1, 2)):
    texts = {
        0: "По математике решить задачи 1–3",
        1: "кто-нибудь сделал?",
        2: "напоминаю: задачи 1–3 по математике",
    }
    for index, offset in enumerate(offsets):
        source(
            client,
            mid=31 + index,
            text=texts.get(index, f"болтовня {index}"),
            when=BASE + timedelta(minutes=offset),
        )


def make_new(client, budget="0.50"):
    async def run():
        async with AsyncSession(client.app.state.engine) as db:
            group = await db.scalar(select(Group).where(Group.telegram_chat_id == -1001))
            now = datetime.now(UTC)
            await transition_mode(
                db,
                group_id=group.id,
                target_mode="shadow",
                actor="test",
                reason="rehearsal",
                now=now,
                shadow_daily_budget_usd=Decimal(budget),
            )
            await transition_mode(
                db,
                group_id=group.id,
                target_mode="new",
                actor="test",
                reason="rehearsal",
                now=now,
                gate_approved=True,
                shadow_daily_budget_usd=Decimal(budget),
            )
            await db.commit()

    asyncio.run(run())


def settings(**patch):
    return Settings(_env_file=None, new_engine_enabled=True, **patch)


def step(client, provider, cfg=None, now=None):
    return asyncio.run(
        process_package_next(client.app.state.engine, cfg or settings(), provider=provider, now=now)
    )


def count(client, model, *where):
    async def read():
        async with AsyncSession(client.app.state.engine) as db:
            return await db.scalar(select(func.count()).select_from(model).where(*where))

    return asyncio.run(read())


def states(client):
    async def read():
        async with AsyncSession(client.app.state.engine) as db:
            rows = (
                await db.execute(
                    select(RawMessage.telegram_message_id, RawMessage.processing_state).order_by(
                        RawMessage.telegram_message_id
                    )
                )
            ).all()
            return {mid: state for mid, state in rows}

    return asyncio.run(read())


def test_new_engine_is_off_by_default_and_without_it_nothing_runs(client):
    seed(client)
    make_new(client)
    provider = PackageProvider()
    off = Settings(_env_file=None)
    assert off.new_engine_enabled is False
    assert step(client, provider, off) == "disabled"
    assert provider.requests == []
    assert set(states(client).values()) == {"pending"}
    assert count(client, ShadowEvaluationRun) == 0


def test_new_engine_gate_honours_the_absolute_cutoff(client):
    seed(client)
    make_new(client)
    provider = PackageProvider()
    expired = settings(new_engine_enabled_until=datetime.now(UTC) - timedelta(minutes=1))
    assert step(client, provider, expired) == "disabled"
    assert provider.requests == []


def test_one_package_two_provider_calls_one_card_for_two_mentions(client):
    seed(client)
    make_new(client)
    provider = PackageProvider()
    assert step(client, provider) == "completed"
    # Two passes for the WHOLE package (three targets), not two per message.
    assert len(provider.requests) == 2
    assert count(client, Homework) == 1
    assert count(client, CanonicalAcademicItem) == 1
    assert set(states(client).values()) == {"completed"}
    # No student notification unless explicitly enabled for the new engine.
    assert count(client, Notification) == 0
    assert count(client, BotOutbox) == 0

    async def run_row():
        async with AsyncSession(client.app.state.engine) as db:
            row = await db.scalar(select(ShadowEvaluationRun))
            return row.state, row.raw_message_id, json.loads(row.metrics_json)

    state, raw_id, metrics = asyncio.run(run_row())
    assert (state, raw_id) == ("completed", None)
    assert metrics["package"]["targets"] == [31, 32, 33]
    assert "promotion" in metrics
    # every proposal's outcome is explained by a reason code
    assert metrics["promotion"]["reasons"] == {"created:projection_created": 1}


def test_a_second_pass_does_nothing_and_never_publishes_twice(client):
    seed(client)
    make_new(client)
    provider = PackageProvider()
    assert step(client, provider) == "completed"
    assert step(client, provider) == "idle"
    assert step(client, provider) == "idle"
    assert len(provider.requests) == 2
    assert count(client, Homework) == 1
    assert count(client, ShadowEvaluationRun) == 1


def test_new_messages_form_the_next_package_and_update_the_same_card(client):
    seed(client)
    make_new(client)
    provider = PackageProvider()
    assert step(client, provider) == "completed"
    source(client, mid=34, text="по математике добавили задачу 4", when=BASE + timedelta(minutes=5))
    follow_up = PackageProvider(cited=(34,), update_existing=True)
    assert step(client, follow_up) == "completed"
    assert set(states(client).values()) == {"completed"}
    assert count(client, ShadowEvaluationRun) == 2
    # The model is shown the earlier card's trusted identity, and its update lands on that card
    # instead of cloning it.
    assert len(follow_up.offered) == 1
    assert count(client, Homework) == 1
    assert count(client, CanonicalAcademicItem) == 1


def test_empty_package_marks_targets_completed_and_writes_nothing(client):
    seed(client)
    make_new(client)
    provider = PackageProvider(propose=False)
    assert step(client, provider) == "completed"
    assert set(states(client).values()) == {"completed"}
    assert count(client, Homework) == 0
    assert count(client, AcademicDeadline) == 0


def test_provider_failure_retries_then_parks_targets_as_failed(client):
    seed(client)
    make_new(client)
    provider = PackageProvider(failure="provider_unreachable")
    now = datetime.now(UTC)
    outcomes = [step(client, provider, now=now + timedelta(hours=hour)) for hour in range(1, 5)]
    assert outcomes[:2] == ["retry", "retry"]
    assert "failed" in outcomes
    assert set(states(client).values()) == {"failed"}
    assert count(client, Homework) == 0
    # The failed package is not re-planned in a loop.
    assert step(client, provider, now=now + timedelta(hours=9)) in {"idle", "settled"}


def test_daily_budget_defers_the_package_without_calling_the_provider(client):
    seed(client)
    make_new(client, budget="0.01")
    provider = PackageProvider()
    assert step(client, provider) == "budget_deferred"
    assert provider.requests == []
    assert set(states(client).values()) == {"pending"}
    assert count(client, Homework) == 0


def test_hourly_schedule_only_takes_messages_received_before_the_slot(client):
    # Fixed 12:20-12:22 Moscow on a Monday, so the messages can never straddle a slot boundary.
    arrival = datetime(2026, 10, 19, 9, 22, tzinfo=UTC)
    for index in range(3):
        source(
            client,
            mid=31 + index,
            text="по математике задачи 1–3",
            when=arrival - timedelta(minutes=2 - index),
        )
    make_new(client)
    provider = PackageProvider()
    cfg = settings(ai_schedule_enabled=True)
    # Inside the slot in which the messages arrived, nothing is taken yet.
    assert step(client, provider, cfg, now=arrival + timedelta(minutes=1)) in {"idle", "settled"}
    assert provider.requests == []
    next_slot = arrival.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1, minutes=1)
    assert step(client, provider, cfg, now=next_slot) == "completed"
    assert set(states(client).values()) == {"completed"}


def test_notifications_are_only_requested_when_enabled(client):
    seed(client)
    make_new(client)
    assert step(client, PackageProvider(), settings(new_engine_notifications=True)) == "completed"
    assert count(client, Notification) >= 1


def test_legacy_group_is_never_touched_by_the_new_engine(client):
    seed(client)
    provider = PackageProvider()
    # The group stays in legacy mode: the package worker must ignore it completely.
    assert step(client, provider) == "idle"
    assert provider.requests == []
    assert set(states(client).values()) == {"pending"}


def test_new_group_is_never_touched_by_the_legacy_engine_even_when_it_is_unlocked(client):
    seed(client)
    make_new(client)
    cfg = Settings(_env_file=None, ai_enabled=True, legacy_engine_enabled=True)

    class Forbidden:
        calls = 0

        async def extract_batch(self, *args, **kwargs):
            Forbidden.calls += 1
            raise AssertionError("legacy provider must not run for a new-mode group")

        async def screen_batch(self, *args, **kwargs):
            Forbidden.calls += 1
            raise AssertionError("legacy provider must not run for a new-mode group")

    status = asyncio.run(
        dispatch_next(client.app.state.engine, cfg, provider=Forbidden(), now=datetime.now(UTC))
    )
    assert status in {"idle", "disabled"}
    assert Forbidden.calls == 0
    assert set(states(client).values()) == {"pending"}


def test_dispatch_routes_new_groups_to_the_package_worker(client):
    seed(client)
    make_new(client)
    provider = PackageProvider()
    status = asyncio.run(
        dispatch_next(client.app.state.engine, settings(), provider=provider, now=datetime.now(UTC))
    )
    assert status == "package_completed"
    assert count(client, Homework) == 1


def test_route_budget_row_is_the_group_cap(client):
    make_new(client, budget="0.33")

    async def read():
        async with AsyncSession(client.app.state.engine) as db:
            return (await db.scalar(select(GroupAnalysisSettings))).shadow_daily_budget_usd

    assert Decimal(asyncio.run(read())) == Decimal("0.33")


# A fixed Monday (12:00 Moscow) so the timetable arithmetic below never depends on today's date.
MONDAY = datetime(2026, 10, 19, 9, 0, tzinfo=UTC)


def seed_fixed(client, texts):
    for index, text in enumerate(texts):
        source(client, mid=31 + index, text=text, when=MONDAY + timedelta(minutes=index))


def seed_timetable(client, subject="Математика"):
    async def run():
        async with AsyncSession(client.app.state.engine) as db:
            group = await db.scalar(select(Group).where(Group.telegram_chat_id == -1001))
            await db.execute(
                SchedulePattern.__table__.delete().where(SchedulePattern.group_id == group.id)
            )
            for weekday in range(7):
                db.add(
                    SchedulePattern(
                        group_id=group.id,
                        subject=subject,
                        weekday=weekday,
                        starts=time(10, 40),
                        ends=time(12, 10),
                        valid_from=date(2026, 10, 5),
                        valid_until=date(2026, 12, 27),
                        week="all",
                    )
                )
            await db.commit()

    asyncio.run(run())


def clear_timetable(client):
    async def run():
        async with AsyncSession(client.app.state.engine) as db:
            await db.execute(SchedulePattern.__table__.delete())
            await db.commit()

    asyncio.run(run())


def first_item(client):
    async def read():
        async with AsyncSession(client.app.state.engine) as db:
            item = await db.scalar(select(CanonicalAcademicItem))
            homework = await db.scalar(select(Homework))
            return item, homework

    return asyncio.run(read())


def test_homework_without_a_date_gets_the_next_lesson_of_its_subject_marked_as_inferred(client):
    seed_fixed(client, ["По математике решить задачи 1–3", "ок", "тоже по математике, задачи 1–3"])
    seed_timetable(client)
    make_new(client)
    assert step(client, PackageProvider(), now=MONDAY + timedelta(hours=1)) == "completed"
    item, homework = first_item(client)
    assert item.deadline_basis == "schedule_inferred"
    assert item.deadline_at is not None
    assert item.deadline_at.replace(tzinfo=UTC).astimezone().date() >= date(2026, 10, 20)
    assert homework.deadline_at is not None


def test_without_a_timetable_the_deadline_stays_unknown_and_is_never_invented(client):
    seed_fixed(client, ["По математике решить задачи 1–3", "ок", "тоже по математике, задачи 1–3"])
    clear_timetable(client)
    make_new(client)
    assert step(client, PackageProvider(), now=MONDAY + timedelta(hours=1)) == "completed"
    item, homework = first_item(client)
    assert item.deadline_basis == "unknown"
    assert item.deadline_at is None
    assert homework.deadline_at is None


def test_the_timetable_confirms_the_subject_even_when_the_message_does_not_name_it(client):
    seed_fixed(client, ["решить задачи 1–3", "ок", "напоминаю про задачи 1–3"])
    seed_timetable(client)
    make_new(client)
    assert step(client, PackageProvider(), now=MONDAY + timedelta(hours=1)) == "completed"
    item, homework = first_item(client)
    assert item.subject == "Математика"
    assert homework.subject_name == "Математика"


def test_a_worker_that_dies_mid_package_is_recovered_without_publishing_twice(client):
    seed(client)
    make_new(client)

    class Crash(PackageProvider):
        async def complete(self, request):
            raise asyncio.CancelledError  # the worker process dies mid-package

    now = datetime.now(UTC)
    try:
        step(client, Crash(), now=now)
    except asyncio.CancelledError:
        pass
    assert set(states(client).values()) == {"pending"}
    assert count(client, Homework) == 0
    provider = PackageProvider()
    later = now + timedelta(minutes=10)  # the 3-minute lease has expired
    assert step(client, provider, now=later) == "completed"
    assert step(client, provider, now=later) == "idle"
    assert count(client, Homework) == 1
    assert count(client, ShadowEvaluationRun) == 1


def reopen(client, *, apply):
    from studgroup.analysis.backfill import reopen_window

    async def run():
        async with AsyncSession(client.app.state.engine) as db:
            group = await db.scalar(select(Group).where(Group.telegram_chat_id == -1001))
            result = await reopen_window(
                db,
                group_id=group.id,
                since=BASE - timedelta(days=1),
                now=datetime.now(UTC),
                apply=apply,
            )
            await db.commit()
            return result

    return asyncio.run(run())


def test_reopen_window_is_dry_run_first_and_only_touches_processing_state(client):
    seed(client)
    make_new(client)
    assert step(client, PackageProvider()) == "completed"
    cards = count(client, Homework)
    dry = reopen(client, apply=False)
    assert dry["would_reopen"] == {"completed": 3} and dry["reopened"] == 0
    assert set(states(client).values()) == {"completed"}
    done = reopen(client, apply=True)
    assert done["reopened"] == 3
    assert set(states(client).values()) == {"pending"}
    assert count(client, Homework) == cards  # nothing deleted or edited
    # A fresh package is planned for the same messages; the repeat run is a new analysis.
    assert step(client, PackageProvider(update_existing=True)) == "completed"
    assert count(client, Homework) == cards


def test_reopen_refuses_a_group_that_is_not_in_new_mode(client):
    seed(client)
    try:
        reopen(client, apply=True)
    except ValueError as error:
        assert str(error) == "reopen_requires_new_mode"
    else:
        raise AssertionError("reopen must require mode new")


def test_the_lesson_running_when_the_message_was_sent_gives_the_subject(client):
    # 12:00 Moscow on a Monday: the Mathematics lesson (10:40-12:10) is running; the model named a
    # subject that is not in the timetable, so the card editor drops it and the timetable decides.
    seed_fixed(client, ["решить задачи 1–3", "ок", "напоминаю про задачи 1–3"])
    seed_timetable(client)
    make_new(client)
    provider = PackageProvider(subject="Физика")
    assert step(client, provider, now=MONDAY + timedelta(hours=1)) == "completed"
    item, homework = first_item(client)
    assert item.subject == "Математика"
    assert homework.subject_name == "Математика"


def test_a_message_outside_any_lesson_never_gets_a_guessed_subject(client):
    evening = MONDAY + timedelta(hours=9)  # 21:00 Moscow, no lesson
    for index, text in enumerate(["решить задачи 1–3", "ок", "напоминаю про задачи 1–3"]):
        source(client, mid=31 + index, text=text, when=evening + timedelta(minutes=index))
    seed_timetable(client)
    make_new(client)
    provider = PackageProvider(subject="Физика")
    assert step(client, provider, now=evening + timedelta(hours=2)) == "completed"
    item, _homework = first_item(client)
    assert item.subject is None


def test_an_unambiguous_hashtag_beats_the_lesson_that_happened_to_be_running(client):
    seed_fixed(client, ["#матан решить задачи 1–3", "ок", "напоминаю про задачи 1–3"])
    seed_timetable(client, subject="Математический анализ")
    make_new(client)
    provider = PackageProvider(subject="Физика")
    assert step(client, provider, now=MONDAY + timedelta(hours=1)) == "completed"
    item, _homework = first_item(client)
    assert item.subject == "Математический анализ"


def source_raw(client, mid, text, when, *, thread=None, sender=None):
    async def run():
        async with AsyncSession(client.app.state.engine) as db:
            group = await db.scalar(select(Group).where(Group.telegram_chat_id == -1001))
            if group.bot_added_at is None:
                group.bot_added_at = when
            db.add(
                RawMessage(
                    group_id=group.id,
                    telegram_message_id=mid,
                    sender_id=sender,
                    message_thread_id=thread,
                    text=text,
                    message_date=when,
                    version_date=when,
                    revision=1,
                    delete_at=when + timedelta(days=30),
                    live_received_at=when,
                )
            )
            await db.commit()

    asyncio.run(run())


def test_a_card_citing_an_unlinked_message_is_published_with_a_flag_not_held(client):
    # The model cites two messages with no reply, topic or author link between them.
    source_raw(client, 31, "По математике решить задачи 1–3", BASE, sender=1)
    source_raw(
        client,
        32,
        "Математика, те же задачи 1–3",
        BASE + timedelta(minutes=300),
        thread=7,
        sender=2,
    )
    source_raw(client, 33, "ок", BASE + timedelta(minutes=301), sender=1)
    make_new(client)
    provider = PackageProvider(cited=(31, 32))
    assert step(client, provider, now=BASE + timedelta(hours=8)) == "completed"
    item, homework = first_item(client)
    assert homework is not None and item is not None
    assert "foreign_source_attached" in json.loads(item.attention_reasons)
    assert item.state == "active"
    assert count(client, Homework) == 1


def test_a_malformed_model_answer_is_retried_instead_of_parking_the_whole_package(client):
    seed(client)
    make_new(client)
    provider = PackageProvider(garbage_first=1)
    now = datetime.now(UTC)
    assert step(client, provider, now=now) == "retry"
    assert set(states(client).values()) == {"pending"}
    assert step(client, provider, now=now + timedelta(hours=1)) == "completed"
    assert set(states(client).values()) == {"completed"}
    assert count(client, Homework) == 1


def test_a_model_that_keeps_answering_garbage_ends_in_failed_after_the_attempt_limit(client):
    seed(client)
    make_new(client)
    provider = PackageProvider(garbage_first=99)
    now = datetime.now(UTC)
    outcomes = [step(client, provider, now=now + timedelta(hours=hour)) for hour in range(1, 5)]
    assert outcomes[:2] == ["retry", "retry"]
    assert "failed" in outcomes
    assert set(states(client).values()) == {"failed"}


def test_a_context_reference_outside_the_package_does_not_lose_the_package(client):
    seed(client)
    make_new(client)
    provider = PackageProvider(outside_context=True)
    assert step(client, provider) == "completed"
    assert set(states(client).values()) == {"completed"}
    assert count(client, Homework) == 1
    assert len(provider.requests) == 2  # no retry was needed
