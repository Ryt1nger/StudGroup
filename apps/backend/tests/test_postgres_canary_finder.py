"""Opt-in PostgreSQL gate: the canary finder is read-only, text-free and skips analysed messages."""

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

from studgroup.analysis.canary_finder import find_candidates
from studgroup.analysis.routing import configure_group_mode
from studgroup.models import Group, MessageAttachment, RawMessage, ShadowEvaluationRun

pytestmark = pytest.mark.skipif(
    not os.environ.get("POSTGRES_TEST_URL"), reason="PostgreSQL not configured"
)


def test_finder_ranks_supplemented_tasks_skips_analysed_ones_and_leaks_no_text():
    url = os.environ["POSTGRES_TEST_URL"]
    now = datetime.now(UTC).replace(microsecond=0)
    secret = "СЕКРЕТНЫЙ-ТЕКСТ"

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
                        name="Disposable finder E2E",
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
                    (2, "Темы: 1) строение 2) функции", 1),
                    (3, "", 1),  # a captionless file replying to 1
                    (4, f"{secret} Решить задачи 3–7 из учебника, стр. 41", None),
                    (
                        5,
                        f"{secret} Подготовить реферат про Ломоносова (уже проанализировано)",
                        None,
                    ),
                    (6, "Просто болтовня без задания и без ссылок вообще никаких", None),
                ]
                for index, (mid, text, reply) in enumerate(spec):
                    stamp = now - timedelta(hours=5 - index)
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
                        raw_message_id=rows[3].id,
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
                db.add(
                    ShadowEvaluationRun(
                        group_id=group_id,
                        raw_message_id=rows[5].id,
                        source_revision=1,
                        analysis_generation=rows[5].analysis_generation,
                        source_snapshot_sha256="c" * 64,
                        engine_version="cplus-shadow-1",
                        state="completed",
                        metrics_json="{}",
                        created_at=now,
                        delete_at=now + timedelta(days=30),
                    )
                )
                await db.commit()
            async with AsyncSession(engine) as db:
                before = await db.scalar(select(func.count()).select_from(ShadowEvaluationRun))
                result = await find_candidates(db, now)
                await db.rollback()
                after = await db.scalar(select(func.count()).select_from(ShadowEvaluationRun))
            assert before == after  # read-only
            ids = [row["telegram_message_id"] for row in result["candidates"]]
            assert ids == [1]  # replies + file + text reply; 4 has no supplement, 5 is analysed
            best = result["candidates"][0]
            assert best["direct_replies"] == 2 and best["reply_files"] == 1
            assert best["reply_text_messages"] == 1 and best["estimated_cost_usd"] <= 0.04
            assert secret not in json.dumps(result, ensure_ascii=False)
            async with AsyncSession(engine) as db:
                group = await db.get(Group, group_id)
                group.pilot_authorized = False
                await db.commit()
        finally:
            await engine.dispose()

    asyncio.run(scenario())
