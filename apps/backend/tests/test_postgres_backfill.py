"""Opt-in PostgreSQL gate: window backfill -> shadow -> review -> guarded promotion."""

import asyncio
import json
import os
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool
from test_analysis_hybrid import envelope
from test_analysis_shadow import context_payload

from studgroup.analysis import backfill
from studgroup.analysis.extraction_strategy import EXTRACTION_PROMPT_VERSION
from studgroup.analysis.provider import ProviderResponse, ProviderUsage
from studgroup.analysis.routing import configure_group_mode
from studgroup.analysis.shadow_worker import process_shadow_next
from studgroup.main import Settings
from studgroup.models import (
    AcademicDeadline,
    BotOutbox,
    CanonicalAcademicItem,
    CanonicalProjectionLink,
    Group,
    Homework,
    Notification,
    RawMessage,
    ShadowEvaluationRun,
)

pytestmark = pytest.mark.skipif(
    not os.environ.get("POSTGRES_TEST_URL"), reason="PostgreSQL not configured"
)
MSK = ZoneInfo("Europe/Moscow")


class ScriptedProvider:
    def __init__(self, script):
        self.script = script
        self.target = None
        self.calls = 0

    async def complete(self, request):
        self.calls += 1
        payload = request.user_payload
        if request.prompt_version == "cplus-context-1":
            self.target = payload["target_message_ids"][-1]
            content = json.dumps(
                context_payload(
                    source_revision=payload["source_revision"],
                    relevant_source_ids=[self.target],
                    discussion_facts=[],
                )
            )
        else:
            assert request.prompt_version == EXTRACTION_PROMPT_VERSION
            content = envelope(
                self.script[self.target], source_revision=payload["source_revision"]
            )
        return ProviderResponse(
            content=content,
            usage=ProviderUsage(prompt_tokens=600, completion_tokens=200),
            model="fake",
            finish_reason="stop",
            payload_sha256=str(self.calls).zfill(64),
        )


def card(subject, title, description, mids, *, at=None, audience="group", kind="homework"):
    return {
        "semantic_kind": kind,
        "subject": subject,
        "title": title,
        "description": description,
        "confidence": 90,
        "audience": {"scope": audience, "label": None},
        "deadline": (
            {"at": at, "date_only": False, "basis": "explicit"}
            if at
            else {"at": None, "date_only": False, "basis": "unknown"}
        ),
        "evidence": [
            {"telegram_message_id": mid, "revision": 1, "fact_paths": ["description"]}
            for mid in mids
        ],
    }


def test_postgres_backfill_review_and_guarded_promotion():
    url = os.environ["POSTGRES_TEST_URL"]
    now = datetime.now(UTC).replace(microsecond=0)
    local = now.astimezone(MSK)
    soon, later, past = (local + timedelta(days=2), local + timedelta(days=4),
                         local - timedelta(days=1))

    def ddmm(value):
        return value.strftime("%d.%m")

    def iso(value):
        return value.replace(hour=23, minute=59, second=0, microsecond=0).isoformat()

    texts = {
        9101: f"Физика: подготовить конспект параграфа до {ddmm(soon)}",
        9102: f"Математика: решить задачи 1–3 до {ddmm(past)}",
        9103: f"Уточнение по физике: конспект параграфа до {ddmm(later)}",
        9104: "Старосте: подойти к преподавателю по информатике",
    }
    script = {
        9101: [card("Физика", "Конспект параграфа", "Подготовить конспект параграфа.", [9101],
                    at=iso(soon))],
        9102: [card("Математика", "Решить задачи 1–3", "Решить задачи 1–3.", [9102],
                    at=iso(past))],
        9103: [card("Физика", "Конспект параграфа", "Подготовить конспект параграфа.",
                    [9101, 9103], at=iso(later))],
        9104: [card("Информатика", "Личное поручение по информатике",
                    "Подойти к преподавателю по информатике.", [9104], audience="personal")],
    }

    async def scenario():
        engine = create_async_engine(url, poolclass=NullPool)
        group_id = uuid.uuid4()
        settings = Settings(database_url=url, ai_enabled=False, ai_shadow_enabled=True)
        try:
            async with AsyncSession(engine) as db:
                await db.execute(delete(Group).where(Group.name.like("Disposable %")))
                db.add(
                    Group(
                        id=group_id,
                        telegram_chat_id=-int(uuid.uuid4().int % 10**12) - 50000,
                        name="Disposable backfill E2E",
                        timezone="Europe/Moscow",
                        pilot_authorized=True,
                        bot_added_at=now - timedelta(days=10),
                    )
                )
                await db.flush()
                await configure_group_mode(
                    db, group_id, "shadow", now - timedelta(days=9),
                    shadow_daily_budget_usd=Decimal("0.50"), shadow_sample_percent=100,
                )
                for index, (mid, text) in enumerate(texts.items()):
                    stamp = now - timedelta(hours=10 - index)
                    db.add(
                        RawMessage(
                            group_id=group_id, telegram_message_id=mid, text=text,
                            message_date=stamp, version_date=stamp, revision=1,
                            imported=False, live_received_at=stamp,
                            delete_at=now + timedelta(days=30),
                        )
                    )
                # A message from before the requested window must be ignored.
                old = now - timedelta(days=5)
                db.add(
                    RawMessage(
                        group_id=group_id, telegram_message_id=9000, text="старое сообщение",
                        message_date=old, version_date=old, revision=1, imported=False,
                        live_received_at=old, delete_at=now + timedelta(days=30),
                    )
                )
                await db.commit()
            since = now - timedelta(days=1)
            common = {"group_id": group_id, "since": since, "now": now}

            async def counts():
                async with AsyncSession(engine) as db:
                    result = {}
                    for model in (Homework, AcademicDeadline, Notification,
                                  CanonicalAcademicItem, CanonicalProjectionLink):
                        query = select(func.count()).select_from(model)
                        if hasattr(model, "group_id"):
                            query = query.where(model.group_id == group_id)
                        result[model.__tablename__] = await db.scalar(query)
                    result["outbox"] = await db.scalar(select(func.count()).select_from(BotOutbox))
                    return result

            zero = await counts()

            async with AsyncSession(engine) as db:
                planned = await backfill.plan(db, **common)
                assert planned["eligible_messages"] == 4
                assert planned["messages_without_run"] == 4
                assert planned["estimated_cost_usd"] > 0
                dry = await backfill.enqueue_window(db, **common, limit=10, apply=False)
                assert dry == {"would_enqueue": 4, "limit": 10}
                assert await db.scalar(select(func.count(ShadowEvaluationRun.id))) is not None
            async with AsyncSession(engine) as db:
                first = await backfill.enqueue_window(db, **common, limit=3, apply=True)
                await db.commit()
                assert first["enqueued"] == 3
            async with AsyncSession(engine) as db:
                rest = await backfill.enqueue_window(db, **common, limit=10, apply=True)
                again = await backfill.enqueue_window(db, **common, limit=10, apply=True)
                await db.commit()
                assert (rest["enqueued"], again["enqueued"]) == (1, 0)  # resumable, idempotent

            provider = ScriptedProvider(script)
            statuses = [await process_shadow_next(engine, settings, provider, now) for _ in range(6)]
            assert statuses.count("completed") == 4 and statuses[-1] == "idle"
            assert await counts() == zero  # shadow alone never publishes

            async with AsyncSession(engine) as db:
                cards = await backfill.review(db, **common, limit=20)
                assert len(cards) == 4
                third = next(c for c in cards if c["target_message_id"] == 9103)
                assert [s["telegram_message_id"] for s in third["sources"]] == [9101, 9103]
                assert third["sources"][0]["text"] == texts[9101]
                held = next(c for c in cards if c["target_message_id"] == 9104)
                assert held["visibility"] == "held"

            async with AsyncSession(engine) as db:
                dry_run = await backfill.promote(db, **common, limit=10, apply=False)
                await db.rollback()
            assert dry_run["totals"]["runs_would_promote"] == 4
            assert await counts() == zero  # dry-run leaves no trace

            async with AsyncSession(engine) as db:
                done = await backfill.promote(db, **common, limit=10, apply=True, actor="test")
                await db.commit()
            outcomes = done["outcomes"]
            assert outcomes["skipped_expired"] == 1
            assert outcomes["linked_to_existing_item"] == 1
            after = await counts()
            assert after["homework"] == 1  # clarification updated, did not duplicate
            assert after["canonical_academic_items"] == 1
            assert after["notifications"] == 0 and after["outbox"] == zero["outbox"]
            async with AsyncSession(engine) as db:
                item = await db.scalar(
                    select(CanonicalAcademicItem).where(CanonicalAcademicItem.group_id == group_id)
                )
                assert item.revision == 2 and item.state == "active"
                homework = await db.scalar(select(Homework).where(Homework.group_id == group_id))
                assert homework.deadline_at.astimezone(MSK).date() == later.date()
                assert homework.revision == 2

            async with AsyncSession(engine) as db:
                repeat = await backfill.promote(db, **common, limit=10, apply=True)
                await db.commit()
            assert repeat["totals"].get("runs_considered", 0) == 0
            assert repeat["totals"]["runs_already_promoted"] == 4
            assert await counts() == after

            # A run lost to a pipeline defect is re-run from its snapshot; history survives.
            async with AsyncSession(engine) as db:
                run = await db.scalar(
                    select(ShadowEvaluationRun).order_by(ShadowEvaluationRun.created_at).limit(1)
                )
                run_id = run.id
                run.state, run.last_error = "failed", "invalid_shadow_context"
                run.metrics_json = json.dumps({"preparation_key": "canary-1", "accepted_items": 0})
                before_validation = run.validation_result_json
                before_attempts, before_cost = run.attempts, run.charged_usd
                await db.commit()
            async with AsyncSession(engine) as db:
                requeued = await backfill.requeue_failed(db, **common, limit=10, apply=True)
                await db.commit()
                assert requeued["requeued"] == 1
            async with AsyncSession(engine) as db:
                run = await db.get(ShadowEvaluationRun, run_id)
                metrics = json.loads(run.metrics_json)
                assert run.state == "queued" and run.attempts == before_attempts
                assert run.charged_usd == before_cost  # cost history is cumulative
                assert metrics["preparation_key"] == "canary-1"  # canary marker kept
                (entry,) = metrics["history"]
                assert entry["state"] == "failed" and entry["last_error"] == "invalid_shadow_context"
                assert entry["validation_result_json"] == before_validation
                assert metrics["attempt_base"] == before_attempts
            async with AsyncSession(engine) as db:  # not repeated: no unbounded paid loop
                again = await backfill.requeue_failed(db, **common, limit=10, apply=True)
                assert again["requeued"] == 0
            canary = Settings(
                database_url=url, ai_enabled=False, ai_shadow_enabled=True,
                ai_shadow_canary_only=True,
            )
            assert await process_shadow_next(engine, canary, ScriptedProvider(script), now) == (
                "completed"
            )
            async with AsyncSession(engine) as db:
                run = await db.get(ShadowEvaluationRun, run_id)
                metrics = json.loads(run.metrics_json)
                assert run.state == "completed" and metrics["history"][0]["state"] == "failed"
                assert metrics["accepted_items"] >= 1 and run.attempts == before_attempts + 1
                assert run.charged_usd > before_cost
                again = await backfill.requeue_failed(db, **common, limit=10, apply=True)
                assert again["requeued"] == 0  # completed by new code
            async with AsyncSession(engine) as db:
                group = await db.get(Group, group_id)
                group.pilot_authorized = False
                await db.commit()
        finally:
            await engine.dispose()

    asyncio.run(scenario())
