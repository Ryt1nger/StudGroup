"""Opt-in PostgreSQL gate for the hybrid adapter: migrations must already be applied."""

import asyncio
import json
import os
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool
from test_analysis_hybrid import drop_claim, envelope
from test_analysis_shadow import HybridProvider

from studgroup.analysis.extraction_strategy import EXTRACTION_PROMPT_VERSION
from studgroup.analysis.repair import REPAIR_PROMPT_VERSION
from studgroup.analysis.routing import configure_group_mode
from studgroup.analysis.shadow import SHADOW_PACKAGE_CAP_USD
from studgroup.main import Settings, create_app
from studgroup.models import (
    AcademicDeadline,
    BotOutbox,
    CanonicalAcademicItem,
    CanonicalItemEvidence,
    CanonicalProjectionLink,
    Group,
    Homework,
    Notification,
    ShadowEvaluationRun,
)
from studgroup.worker import sweep_once

pytestmark = pytest.mark.skipif(
    not os.environ.get("POSTGRES_TEST_URL"), reason="PostgreSQL not configured"
)

PRODUCT_TABLES = (
    Homework,
    AcademicDeadline,
    Notification,
    CanonicalAcademicItem,
    CanonicalItemEvidence,
    CanonicalProjectionLink,
)


class RealShapedProvider(HybridProvider):
    """Pass 2 answers like DeepSeek: ISO strings and one structurally broken sibling."""

    def __init__(self):
        super().__init__()
        self.repair_calls = 0
        self.target = None

    async def complete(self, request):
        if request.prompt_version == "cplus-context-1":
            self.target = request.user_payload["target_message_ids"][-1]
        if request.prompt_version == EXTRACTION_PROMPT_VERSION:
            self.requests.append(request)
            message = next(
                row for row in request.user_payload["messages"]
                if row["telegram_message_id"] == self.target
            )
            revision = request.user_payload["source_revision"]
            if "конспект" in message["text"]:
                ref = [{"telegram_message_id": message["telegram_message_id"],
                        "revision": message["revision"], "fact_paths": ["description"]}]
                good = {
                    "semantic_kind": "homework", "subject": "Физика",
                    "title": "Конспект параграфа",
                    "description": "Подготовить конспект параграфа.", "confidence": 90,
                    "deadline": {"at": "2026-10-15T23:59:00+03:00", "date_only": False,
                                 "basis": "explicit"},
                    "evidence": ref,
                }
                bad = good | {
                    "title": "Конспект второго параграфа",
                    "deadline": {"at": None, "date_only": True, "basis": "explicit"},
                }
                proposals = [good, bad]
            else:
                proposals = []
            return await self._respond(envelope(proposals, source_revision=revision))
        if request.prompt_version == REPAIR_PROMPT_VERSION:
            self.requests.append(request)
            self.repair_calls += 1
            return await self._respond(json.dumps({"repairs": drop_claim(request)}))
        return await super().complete(request)

    async def _respond(self, content):
        from studgroup.analysis.provider import ProviderResponse, ProviderUsage

        return ProviderResponse(
            content=content,
            usage=ProviderUsage(prompt_tokens=700, completion_tokens=300),
            model="fake",
            finish_reason="stop",
            payload_sha256=str(len(self.requests)) * 64,
        )


def test_postgres_hybrid_shadow_accepts_cards_without_touching_product_data():
    url = os.environ["POSTGRES_TEST_URL"]
    chat_id = -int(uuid.uuid4().int % 10**12) - 40000

    async def seed():
        engine = create_async_engine(url, poolclass=NullPool)
        group_id = uuid.uuid4()
        try:
            async with AsyncSession(engine) as db:
                await db.execute(delete(Group).where(Group.name.like("Disposable %")))
                db.add(
                    Group(
                        id=group_id,
                        telegram_chat_id=chat_id,
                        name="Disposable hybrid E2E",
                        timezone="Europe/Moscow",
                        pilot_authorized=True,
                        bot_added_at=datetime.now(UTC) - timedelta(minutes=1),
                    )
                )
                await db.flush()
                await configure_group_mode(
                    db,
                    group_id,
                    "shadow",
                    datetime.now(UTC),
                    shadow_daily_budget_usd=Decimal("0.20"),
                    shadow_sample_percent=100,
                )
                await db.commit()
            return group_id
        finally:
            await engine.dispose()

    async def counts(group_id):
        engine = create_async_engine(url, poolclass=NullPool)
        try:
            async with AsyncSession(engine) as db:
                result = {}
                for model in PRODUCT_TABLES:
                    column = getattr(model, "group_id", None)
                    query = select(func.count()).select_from(model)
                    if column is not None:
                        query = query.where(column == group_id)
                    result[model.__tablename__] = await db.scalar(query)
                result["bot_outbox"] = await db.scalar(select(func.count()).select_from(BotOutbox))
                return result
        finally:
            await engine.dispose()

    async def rows(group_id):
        engine = create_async_engine(url, poolclass=NullPool)
        try:
            async with AsyncSession(engine) as db:
                return (
                    await db.scalars(
                        select(ShadowEvaluationRun)
                        .where(ShadowEvaluationRun.group_id == group_id)
                        .order_by(ShadowEvaluationRun.created_at)
                    )
                ).all()
        finally:
            await engine.dispose()

    config = Settings(
        database_url=url,
        telegram_webhook_secret="hybrid-e2e-secret",
        ai_enabled=False,
        ai_shadow_enabled=True,
        ai_output_repair_enabled=True,
    )
    provider = RealShapedProvider()
    app = create_app(config)
    with TestClient(app) as client:
        group_id = asyncio.run(seed())
        before = asyncio.run(counts(group_id))
        now = int(datetime.now(UTC).timestamp())
        base = int(uuid.uuid4().int % 1_000_000_000) + 20_000
        headers = {"X-Telegram-Bot-Api-Secret-Token": "hybrid-e2e-secret"}
        texts = ["Физика: подготовить конспект параграфа до 15.10", "Всем привет!"]
        for offset, text in enumerate(texts):
            update = {
                "update_id": base + offset,
                "message": {
                    "message_id": 4400 + offset,
                    "date": now + offset,
                    "chat": {"id": chat_id, "type": "supergroup"},
                    "text": text,
                    "from": {"id": 42},
                },
            }
            for _retry in range(2):  # Telegram redelivery is idempotent
                response = client.post("/v1/telegram/webhook", json=update, headers=headers)
                assert response.status_code == 200
        for _ in range(12):
            asyncio.run(sweep_once(config, provider))
            current = asyncio.run(rows(group_id))
            if len(current) == 2 and all(row.state == "completed" for row in current):
                break
        runs = asyncio.run(rows(group_id))
        assert len(runs) == 2 and all(row.state == "completed" for row in runs)
        metrics = [json.loads(row.metrics_json) for row in runs]
        assert sorted(m["accepted_items"] for m in metrics) == [0, 2]
        repaired = next(m for m in metrics if m["accepted_items"] == 2)
        assert repaired["repair"]["applied"] == 1 and repaired["rejected_items"] == 0
        assert provider.repair_calls == 1
        assert all(row.reserved_usd == 0 for row in runs)
        assert all(Decimal(0) < row.charged_usd < SHADOW_PACKAGE_CAP_USD for row in runs)
        # Shadow isolation: nothing a student can see, no notifications, no canonical rows.
        assert asyncio.run(counts(group_id)) == before
        # Idempotency: further sweeps neither re-run nor duplicate sources.
        for _ in range(3):
            asyncio.run(sweep_once(config, provider))
        assert len(asyncio.run(rows(group_id))) == 2
        assert provider.repair_calls == 1

        async def cleanup():
            engine = create_async_engine(url, poolclass=NullPool)
            try:
                async with AsyncSession(engine) as db:
                    group = await db.get(Group, group_id)
                    group.pilot_authorized = False
                    await db.commit()
            finally:
                await engine.dispose()

        asyncio.run(cleanup())
