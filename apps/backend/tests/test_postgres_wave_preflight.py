"""Opt-in PostgreSQL gate: wave preflight is read-only, text-free, one package per revision."""

import asyncio
import json
import os
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from studgroup.analysis.routing import configure_group_mode
from studgroup.analysis.shadow_worker import prepare_backfill_wave
from studgroup.analysis.wave_preflight import wave_preflight
from studgroup.main import Settings
from studgroup.models import Group, MessageAttachment, RawMessage, ShadowEvaluationRun

pytestmark = pytest.mark.skipif(
    not os.environ.get("POSTGRES_TEST_URL"), reason="PostgreSQL not configured"
)


def test_wave_preflight_counts_one_package_per_revision_and_never_retries_failed():
    url = os.environ["POSTGRES_TEST_URL"]
    now = datetime.now(UTC).replace(microsecond=0)
    secret = "СЕКРЕТНЫЙ-ТЕКСТ"
    off = Settings(
        _env_file=None,
        ai_enabled=False,
        ai_shadow_enabled=False,
        ai_shadow_canary_only=True,
        ai_shadow_backfill_key="wave-t",
        ai_shadow_backfill_limit=3,
    )

    async def scenario():
        engine = create_async_engine(url, poolclass=NullPool)
        group_id = uuid.uuid4()
        try:
            async with AsyncSession(engine) as db:
                await db.execute(delete(Group).where(Group.name.like("Disposable %")))
                db.add(
                    Group(
                        id=group_id,
                        telegram_chat_id=-1004400000000 - int(uuid.uuid4().int % 10**6),
                        name="Disposable wave E2E",
                        timezone="Europe/Moscow",
                        pilot_authorized=True,
                        bot_added_at=now - timedelta(days=3),
                    )
                )
                await db.flush()
                await configure_group_mode(
                    db,
                    group_id,
                    "shadow",
                    now - timedelta(days=2),
                    shadow_daily_budget_usd=Decimal("0.50"),
                    shadow_sample_percent=100,
                )
                rows = {}
                spec = [
                    (1, f"{secret} Подготовить доклад про клетку для всей группы", None),
                    (2, "Уже проанализировано успешно в прошлый раз", None),
                    (3, "Ранее упавший запуск (старый дефект контекста)", None),
                    (4, "ок", None),
                    (5, "", 1),
                    (6, f"{secret} Решить задачи 3–7 из учебника, стр. 41", None),
                    (7, "Просто болтовня без задания совсем", None),
                ]
                for index, (mid, text, reply) in enumerate(spec):
                    stamp = now - timedelta(hours=8 - index)
                    row = RawMessage(
                        group_id=group_id,
                        telegram_message_id=mid,
                        text=text,
                        reply_to_message_id=reply,
                        message_thread_id=591,
                        message_date=stamp,
                        version_date=stamp,
                        revision=1,
                        imported=False,
                        live_received_at=stamp,
                        delete_at=now + timedelta(days=30),
                    )
                    db.add(row)
                    await db.flush()
                    rows[mid] = row
                db.add(
                    MessageAttachment(
                        raw_message_id=rows[5].id,
                        group_id=group_id,
                        source_revision=1,
                        kind="document",
                        stable_file_id="f",
                        telegram_file_id="tf",
                        file_name="x.pdf",
                        created_at=now,
                        delete_at=now + timedelta(days=30),
                    )
                )
                for mid, state, cost in ((2, "completed", Decimal("0.0027")), (3, "failed", 0)):
                    db.add(
                        ShadowEvaluationRun(
                            group_id=group_id,
                            raw_message_id=rows[mid].id,
                            source_revision=1,
                            analysis_generation=rows[mid].analysis_generation,
                            source_snapshot_sha256="c" * 64,
                            engine_version="cplus-shadow-1",
                            state=state,
                            metrics_json="{}",
                            charged_usd=cost,
                            created_at=now,
                            delete_at=now + timedelta(days=30),
                        )
                    )
                await db.commit()

            async with AsyncSession(engine) as db:
                before = await db.scalar(select(func.count()).select_from(ShadowEvaluationRun))
                plan = await wave_preflight(
                    db, off, now + timedelta(minutes=1), since=now - timedelta(days=5), wave_size=3
                )
                await db.rollback()
                after = await db.scalar(select(func.count()).select_from(ShadowEvaluationRun))
            assert before == after  # read-only
            assert plan["status"] == "not_prepared" and plan["blocking_codes"] == []
            assert plan["eligible_messages"] == 7
            # 7 messages minus the completed one and the failed one: failed is NOT a new package.
            assert plan["uncovered_packages_total"] == 5
            assert plan["failed_runs_in_window"] == 1
            assert plan["failed_runs_are_excluded_and_not_retried"] is True
            assert plan["current_revision_runs_by_state"] == {"completed": 1, "failed": 1}
            wave = plan["next_wave_if_prepared"]
            assert wave["size"] == 3 and wave["first_telegram_message_id"] == 1
            assert wave["last_telegram_message_id"] == 5  # oldest first, no keyword filtering
            assert wave["composition"]["files_without_text"] == 1
            assert wave["composition"]["text_under_30_chars"] == 1
            assert plan["cost"]["measured_cost_per_run_usd"] == "0.00270000"
            assert Decimal(plan["cost"]["wave_hard_cap_usd"]) == Decimal("0.12")
            assert secret not in json.dumps(plan, ensure_ascii=False)

            assert await prepare_backfill_wave(engine, off) == "wave_prepared_3"
            async with AsyncSession(engine) as db:
                ready = await wave_preflight(
                    db, off, now + timedelta(minutes=2), since=now - timedelta(days=5), wave_size=3
                )
                await db.rollback()
                states = dict(
                    (
                        await db.execute(
                            select(ShadowEvaluationRun.state, func.count()).group_by(
                                ShadowEvaluationRun.state
                            )
                        )
                    ).all()
                )
            assert ready["status"] == "ready", ready["blocking_codes"]
            prepared = ready["prepared_wave"]
            assert prepared["prepared"] == 3 and prepared["expired_running_rows"] == 0
            assert prepared["foreign_prepared_rows"] == 0 and prepared["envelope_errors"] == 0
            assert Decimal(prepared["max_package_upper_bound_usd"]) <= Decimal("0.04")
            assert Decimal(prepared["sum_estimated_cost_usd"]) > 0
            assert states == {"completed": 1, "failed": 1, "queued": 3}
            assert ready["uncovered_packages_total"] == 2
            assert secret not in json.dumps(ready, ensure_ascii=False)

            blocked = off.model_copy(update={"ai_shadow_enabled": True})
            async with AsyncSession(engine) as db:
                refused = await wave_preflight(
                    db,
                    blocked,
                    now + timedelta(minutes=3),
                    since=now - timedelta(days=5),
                    wave_size=3,
                )
                await db.rollback()
            assert refused["status"] == "blocked"
            assert "shadow_not_disabled_during_preflight" in refused["blocking_codes"]
            async with AsyncSession(engine) as db:
                group = await db.get(Group, group_id)
                group.pilot_authorized = False
                await db.commit()
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_spread_wave_covers_days_and_topics_skips_failed_and_is_idempotent():
    url = os.environ["POSTGRES_TEST_URL"]
    now = datetime.now(UTC).replace(microsecond=0)
    spread = Settings(
        _env_file=None,
        ai_enabled=False,
        ai_shadow_enabled=False,
        ai_shadow_canary_only=True,
        ai_shadow_backfill_key="wave-s",
        ai_shadow_backfill_limit=4,
        ai_shadow_backfill_spread=True,
    )

    async def scenario():
        engine = create_async_engine(url, poolclass=NullPool)
        group_id = uuid.uuid4()
        try:
            async with AsyncSession(engine) as db:
                await db.execute(delete(Group).where(Group.name.like("Disposable %")))
                db.add(
                    Group(
                        id=group_id,
                        telegram_chat_id=-1004400000000 - int(uuid.uuid4().int % 10**6),
                        name="Disposable spread E2E",
                        timezone="Europe/Moscow",
                        pilot_authorized=True,
                        bot_added_at=now - timedelta(days=5),
                    )
                )
                await db.flush()
                await configure_group_mode(
                    db,
                    group_id,
                    "shadow",
                    now - timedelta(days=4),
                    shadow_daily_budget_usd=Decimal("0.50"),
                    shadow_sample_percent=100,
                )
                rows = []
                number = 1
                for day in (4, 3, 2):
                    for index in range(5):
                        stamp = now - timedelta(days=day, minutes=-index * 7)
                        row = RawMessage(
                            group_id=group_id,
                            telegram_message_id=number,
                            text=f"обычная переписка {number}",
                            message_thread_id=(None, 241)[index % 2],
                            message_date=stamp,
                            version_date=stamp,
                            revision=1,
                            imported=False,
                            live_received_at=stamp,
                            delete_at=now + timedelta(days=30),
                        )
                        db.add(row)
                        await db.flush()
                        rows.append(row)
                        number += 1
                db.add(
                    ShadowEvaluationRun(
                        group_id=group_id,
                        raw_message_id=rows[0].id,
                        source_revision=1,
                        analysis_generation=rows[0].analysis_generation,
                        source_snapshot_sha256="c" * 64,
                        engine_version="cplus-shadow-1",
                        state="failed",
                        metrics_json="{}",
                        created_at=now,
                        delete_at=now + timedelta(days=30),
                    )
                )
                await db.commit()

            at = now + timedelta(minutes=1)
            since = now - timedelta(days=6)
            async with AsyncSession(engine) as db:
                preview = await wave_preflight(db, spread, at, since=since, wave_size=4)
                await db.rollback()
            nxt = preview["next_wave_if_prepared"]
            assert nxt["selection"] == "spread" and nxt["size"] == 4
            assert nxt["coverage"]["distinct_days"] == 3 and nxt["coverage"]["distinct_topics"] == 2
            assert 1 not in nxt["coverage"]["telegram_message_ids"]  # failed one is never picked

            assert await prepare_backfill_wave(engine, spread) == "wave_prepared_4"
            assert await prepare_backfill_wave(engine, spread) == "wave_exists"
            async with AsyncSession(engine) as db:
                after = await wave_preflight(
                    db, spread, at + timedelta(minutes=1), since=since, wave_size=4
                )
                await db.rollback()
                queued = (
                    await db.scalars(
                        select(ShadowEvaluationRun.raw_message_id).where(
                            ShadowEvaluationRun.state == "queued"
                        )
                    )
                ).all()
            assert len(queued) == len(set(queued)) == 4
            wave = after["prepared_wave"]
            assert after["status"] == "ready", after["blocking_codes"]
            assert wave["prepared"] == 4 and wave["expired_running_rows"] == 0
            assert wave["coverage"]["distinct_days"] == 3
            assert (
                wave["coverage"]["telegram_message_ids"] == nxt["coverage"]["telegram_message_ids"]
            )
            assert set(wave["context_overlap"]) == {
                "distinct_messages_in_all_packages",
                "messages_shared_by_several_packages",
                "packages_sharing_context",
                "targets_inside_another_package",
            }
            async with AsyncSession(engine) as db:
                group = await db.get(Group, group_id)
                group.pilot_authorized = False
                await db.commit()
        finally:
            await engine.dispose()

    asyncio.run(scenario())
