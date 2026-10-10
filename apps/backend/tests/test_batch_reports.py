import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from test_ai_schedule import seed
from test_live_two_pass import CascadeProvider

from studgroup.ai import BatchExtraction, BatchResult, TokenUsage
from studgroup.batch_reports import parse_target_key, review_reason, review_section
from studgroup.main import Settings
from studgroup.models import (
    AICandidate,
    AIControl,
    AIJob,
    AIRun,
    BotOutbox,
    GroupAIProfile,
    RawMessage,
)
from studgroup.processing import process_next

pytest_plugins = ["test_schedule_api"]


def test_legacy_two_part_run_target_is_generation_one():
    raw_id = "78ac2474-b6f1-4207-b80e-66f4bfab16cd"
    assert parse_target_key(f"{raw_id}:3", 1) == (raw_id, "3", 1)
    assert parse_target_key(f"{raw_id}:3:2", 1) == (raw_id, "3", 2)


def test_rolling_deploy_closes_legacy_run_before_generation_two(client):
    seed(client, at("06:55:00"), mid=1)

    async def seed_legacy_run():
        async with AsyncSession(client.app.state.engine) as db:
            raw = await db.scalar(select(RawMessage))
            raw.analysis_generation = 2
            db.add(
                GroupAIProfile(
                    group_id=raw.group_id,
                    generation=2,
                    state="backfill",
                    backfill_requested=True,
                    structure_json='{"version":1,"mode":"unstructured"}',
                    source_count=1,
                    requested_at=at("06:59:00"),
                    mapped_at=at("06:59:01"),
                )
            )
            db.add(
                AIRun(
                    id=uuid.uuid4(),
                    group_id=raw.group_id,
                    slot=at("07:00:00"),
                    analysis_generation=1,
                    targets=json.dumps([f"{raw.id}:{raw.revision}"]),
                    started_at=at("07:00:00"),
                )
            )
            await db.commit()

    asyncio.run(seed_legacy_run())
    assert run(client, CascadeProvider(), "07:00:05") == "idle"

    async def inspect():
        async with AsyncSession(client.app.state.engine) as db:
            legacy = await db.scalar(select(AIRun))
            assert legacy.finished_at is not None
            assert legacy.outcome == "completed"

    asyncio.run(inspect())


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
    assert run(client, provider, "07:00:05") == "screened"
    assert len(notices(client)) == 1  # All light passes finish before deep analysis.
    assert run(client, provider, "07:00:10") == "completed"
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


def test_aggregate_finish_exposes_model_repairs_as_warnings(client):
    class Repaired(CascadeProvider):
        async def extract_batch(self, *args, **kwargs):
            result = await super().extract_batch(*args, **kwargs)
            result.diagnostics = {"normalized_source_reference": 1}
            return result

    seed(client, at("06:55:00"))
    provider = Repaired()
    assert run(client, provider, "07:00:00") == "screened"
    assert run(client, provider, "07:00:05") == "completed"
    final = next(text for text in notices(client) if "Итог прогона" in text)
    assert "Завершён с предупреждениями" in final
    assert "Исправлено/отклонено в ответах модели: normalized_source_reference: 1" in final


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


def test_retry_freezes_group_and_new_hour_until_unfinished_stage_recovers(client):
    seed(client, at("06:55:00"), mid=1)
    seed(client, at("06:56:00"), mid=2)
    broken = CascadeProvider(failure="provider_unreachable")
    assert run(client, broken, "07:00:00") == "screened"
    assert run(client, broken, "07:00:05") == "screened"
    assert run(client, broken, "07:59:30") == "retry"
    seed(client, at("07:05:00"), mid=3)
    waiting = CascadeProvider()
    assert run(client, waiting, "08:00:00") == "idle"
    assert waiting.screen_calls == waiting.calls == 0
    assert run(client, waiting, "08:00:31") == "completed"
    assert waiting.screen_calls == 0 and waiting.calls == 1
    assert run(client, waiting, "08:00:36") == "completed"
    assert waiting.calls == 2  # The old run finishes before message #3 can start.
    assert run(client, waiting, "08:00:41") == "screened"
    assert waiting.screen_calls == 1


def test_repeated_quality_failures_pause_remaining_deep_work_until_next_hour(client):
    for mid in range(1, 5):
        seed(client, at("06:55:00") + timedelta(seconds=mid), mid=mid)
    provider = CascadeProvider(failure="invalid_source_reference")
    for second in range(4):
        assert run(client, provider, f"07:00:0{second}") == "screened"
    for second in range(4, 7):
        assert run(client, provider, f"07:00:0{second}") == "failed"
    assert run(client, provider, "07:00:07") == "idle"
    assert provider.calls == 3

    async def inspect():
        async with AsyncSession(client.app.state.engine) as db:
            control = await db.get(AIControl, 1)
            assert control.provider_failure_streak == 3
            assert control.provider_circuit_reason == "invalid_source_reference"
            assert control.provider_circuit_open_until.replace(tzinfo=None) == at(
                "08:00:00"
            ).astimezone(UTC).replace(tzinfo=None)

    asyncio.run(inspect())

    recovered = CascadeProvider()
    assert run(client, recovered, "08:00:00") == "completed"
    assert recovered.screen_calls == 0
    assert recovered.calls == 1


def test_repeated_empty_deep_results_complete_without_false_failure_circuit(client):
    class EmptyDeep(CascadeProvider):
        async def extract_batch(self, *args, **kwargs):
            self.calls += 1
            return BatchResult(
                batch=BatchExtraction(assignments=[], online_lessons=[]),
                usage=TokenUsage(prompt_tokens=100, completion_tokens=20),
                model="deepseek-flash",
                prompt_version="test-empty",
            )

    for mid in range(1, 5):
        seed(client, at("06:55:00") + timedelta(seconds=mid), mid=mid)
    provider = EmptyDeep()
    for second in range(4):
        assert run(client, provider, f"07:00:0{second}") == "screened"
    for second in range(4, 8):
        assert run(client, provider, f"07:00:0{second}") == "completed"
    assert run(client, provider, "07:00:08") == "idle"
    assert provider.calls == 4

    async def inspect():
        async with AsyncSession(client.app.state.engine) as db:
            control = await db.get(AIControl, 1)
            assert control.provider_failure_streak == 0
            assert control.provider_circuit_reason is None
            assert control.provider_circuit_open_until is None

    asyncio.run(inspect())


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


def test_idle_worker_closes_run_with_unclaimable_targets_and_reports_them(client):
    seed(client, at("06:55:00"), mid=1)
    seed(client, at("06:56:00"), mid=2)
    provider = CascadeProvider(signal=False)
    assert run(client, provider, "07:00:00") == "completed"
    assert len([t for t in notices(client) if "Итог прогона" in t]) == 0

    async def strand_second_message():
        async with AsyncSession(client.app.state.engine) as db:
            pending = (
                await db.scalars(select(RawMessage).where(RawMessage.processing_state == "pending"))
            ).all()
            assert len(pending) == 1
            pending[0].live_received_at = pending[0].live_received_at + timedelta(hours=3)
            await db.commit()

    asyncio.run(strand_second_message())
    assert run(client, provider, "07:00:10") == "idle"
    final = [t for t in notices(client) if "Итог прогона" in t]
    assert len(final) == 1
    assert "Завершён не полностью" in final[0]
    assert "не обработано в этом окне: 1" in final[0]
    assert run(client, provider, "07:00:20") == "idle"
    assert len([t for t in notices(client) if "Итог прогона" in t]) == 1


def test_stale_package_with_pre_install_history_does_not_block_deep_stage_or_report(client):
    seed(client, at("06:55:00"), mid=1)
    provider = CascadeProvider()
    assert run(client, provider, "07:00:00") == "screened"

    async def inject_history():
        async with AsyncSession(client.app.state.engine) as db:
            old = at("06:00:00") - timedelta(days=3)
            raw = RawMessage(
                id=uuid.uuid4(),
                group_id=(await db.scalar(select(AIRun))).group_id,
                telegram_message_id=900,
                text="старое ДЗ",
                message_date=old,
                version_date=old,
                imported=True,
                processing_state="pending",
                delete_at=old + timedelta(days=30),
            )
            db.add(raw)
            package = await db.scalar(select(AIRun))
            package.targets = json.dumps([*json.loads(package.targets), f"{raw.id}:1:1"])
            await db.commit()

    asyncio.run(inject_history())
    assert run(client, provider, "07:00:10") == "completed"
    finals = [t for t in notices(client) if "Итог прогона" in t]
    assert len(finals) == 1
    assert "Сообщений в пакете: 1" in finals[0]


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


def test_review_reason_explains_why_a_fragment_was_not_published():
    assert "уверенность 60" in review_reason({"kind": "homework", "confidence": 60})
    assert "модель просит контекст" in review_reason({"kind": "needs_context", "confidence": 90})
    text = review_reason(
        {"kind": "homework", "confidence": 90, "subject": "Физика", "title": "Задачи"}
    )
    assert "описание" in text and "срок" in text and "предмет" not in text


def test_review_section_lists_message_fragment_and_reason(client):
    seed(client, at("06:55:00"), mid=7)
    provider = CascadeProvider(signal=False)
    assert run(client, provider, "07:00:00") == "completed"

    async def add_candidate():
        async with AsyncSession(client.app.state.engine) as db:
            job = await db.scalar(select(AIJob))
            job_id, group_id = job.id, job.group_id
            db.add(
                AICandidate(
                    job_id=job_id,
                    group_id=group_id,
                    ordinal=0,
                    payload=json.dumps({"kind": "homework", "confidence": 60, "subject": "Физика"}),
                    state="review",
                    created_at=at("07:00:00"),
                    delete_at=at("07:00:00") + timedelta(days=30),
                )
            )
            await db.commit()
            return await review_section(db, {job_id})

    text = asyncio.run(add_candidate())
    assert "На проверку (1)" in text
    assert "#7" in text and "ДЗ: решить задачи" in text
    assert "уверенность 60" in text and "не хватило: название, описание, срок" in text
