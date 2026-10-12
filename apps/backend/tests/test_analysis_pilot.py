import asyncio
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from studgroup.analysis.contracts import (
    ANALYSIS_ENGINE_VERSION,
    ANALYSIS_SCHEMA_VERSION,
    SourceMessage,
)
from studgroup.analysis.dispatch import dispatch_next
from studgroup.analysis.pilot import emergency_rollback, transition_mode
from studgroup.analysis.validation import validate_provider_content
from studgroup.analysis.writer import apply_validated_batch
from studgroup.main import Settings
from studgroup.models import (
    Group,
    GroupAnalysisModeHistory,
    Homework,
    Notification,
    RawMessage,
)

pytest_plugins = ["test_schedule_api"]
NOW = datetime(2026, 10, 12, 12, tzinfo=UTC)


def proposal(title, **patch):
    return {
        "destination": "task",
        "semantic_kind": "homework",
        "subject": "Математика",
        "title": title,
        "description": title,
        "deadline": {"at": None, "date_only": False, "basis": "unknown"},
        "audience": {"scope": "group", "label": None},
        "confidence": 90,
        "attention_reasons": [],
        "evidence": [
            {
                "telegram_message_id": 201,
                "revision": 1,
                "fact_paths": ["subject", "description"],
            }
        ],
    } | patch


def validate(items):
    return validate_provider_content(
        json.dumps(
            {
                "schema_version": ANALYSIS_SCHEMA_VERSION,
                "engine_version": ANALYSIS_ENGINE_VERSION,
                "source_revision": 1,
                "proposals": items,
                "explicit_empty": not items,
            }
        ),
        allowed_sources={201: 1},
        source_revision=1,
    )


async def seed_source(db, group):
    row = RawMessage(
        group_id=group.id,
        telegram_message_id=201,
        sender_id=1,
        text="По математике решить задачи 1–3 и отдельно задачи 4–6",
        message_date=NOW,
        version_date=NOW,
        revision=1,
        delete_at=NOW + timedelta(days=30),
    )
    db.add(row)
    await db.flush()
    return SourceMessage(
        telegram_message_id=201,
        revision=1,
        message_date=NOW,
        text=row.text,
    )


def test_writer_keeps_valid_siblings_and_every_proposal_outcome(client):
    async def run():
        async with AsyncSession(client.app.state.engine) as db:
            group = await db.scalar(select(Group).where(Group.telegram_chat_id == -1001))
            source = await seed_source(db, group)
            result = validate(
                [
                    proposal("Решить задачи 1–3"),
                    proposal("Некорректно", confidence=101),
                    proposal("Решить задачи 4–6"),
                ]
            )
            assert result.accepted_ordinals == [0, 2]
            applied = await apply_validated_batch(
                db,
                group_id=group.id,
                validation=result,
                messages=[source],
                now=NOW,
            )
            await db.commit()
            assert {outcome.proposal_ordinal for outcome in applied.outcomes} == {0, 1, 2}
            assert applied.product_writes == 2
            assert await db.scalar(select(func.count()).select_from(Homework)) == 2
            assert await db.scalar(select(func.count()).select_from(Notification)) == 2

    asyncio.run(run())


def test_mutation_contract_requires_identity_and_revision():
    result = validate([proposal("Уточнить задание", action="update")])
    assert not result.accepted
    assert result.rejected[0].reason_code == "proposal_schema_invalid"


def test_pilot_transition_and_rollback_preserve_product_rows(client):
    async def run():
        async with AsyncSession(client.app.state.engine) as db:
            group = await db.scalar(select(Group).where(Group.telegram_chat_id == -1001))
            db.add(
                Homework(
                    group_id=group.id,
                    subject_id=group.id,
                    subject_name="Математика",
                    title="Сохранить при откате",
                    created_at=NOW,
                    updated_at=NOW,
                    delete_at=NOW + timedelta(days=30),
                )
            )
            with pytest.raises(ValueError, match="analysis_mode_transition_not_allowed"):
                await transition_mode(
                    db,
                    group_id=group.id,
                    target_mode="new",
                    actor="owner",
                    reason="skip shadow",
                    now=NOW,
                    gate_approved=True,
                )
            await transition_mode(
                db,
                group_id=group.id,
                target_mode="shadow",
                actor="owner",
                reason="start comparison",
                now=NOW,
                shadow_daily_budget_usd=Decimal("0.01"),
            )
            with pytest.raises(ValueError, match="new_writer_requires_explicit_gate"):
                await transition_mode(
                    db,
                    group_id=group.id,
                    target_mode="new",
                    actor="owner",
                    reason="pilot",
                    now=NOW,
                )
            new = await transition_mode(
                db,
                group_id=group.id,
                target_mode="new",
                actor="owner",
                reason="approved pilot",
                now=NOW,
                gate_approved=True,
            )
            assert new.new_writes and not new.legacy_writes
            legacy = await emergency_rollback(
                db,
                group_id=group.id,
                actor="owner",
                reason="rollback rehearsal",
                now=NOW + timedelta(minutes=1),
            )
            await db.commit()
            assert legacy.legacy_writes and not legacy.new_writes
            assert await db.scalar(select(func.count()).select_from(Homework)) == 1
            assert await db.scalar(
                select(func.count()).select_from(GroupAnalysisModeHistory)
            ) == 3

    asyncio.run(run())


def test_new_mode_cannot_fall_through_to_legacy_worker(client):
    class ForbiddenProvider:
        calls = 0

        async def extract_batch(self, *args, **kwargs):
            self.calls += 1
            raise AssertionError("legacy provider must not run for new mode")

    async def run():
        async with AsyncSession(client.app.state.engine) as db:
            group = await db.scalar(select(Group).where(Group.telegram_chat_id == -1001))
            await seed_source(db, group)
            await transition_mode(
                db,
                group_id=group.id,
                target_mode="shadow",
                actor="test",
                reason="route rehearsal",
                now=NOW,
            )
            await transition_mode(
                db,
                group_id=group.id,
                target_mode="new",
                actor="test",
                reason="route isolation",
                now=NOW,
                gate_approved=True,
            )
            await db.commit()
        provider = ForbiddenProvider()
        status = await dispatch_next(
            client.app.state.engine,
            Settings(ai_enabled=True, ai_total_budget_usd=100, ai_daily_group_budget_usd=1),
            provider,
            now=NOW,
        )
        assert status == "idle"
        assert provider.calls == 0
        async with AsyncSession(client.app.state.engine) as db:
            assert await db.scalar(select(func.count()).select_from(Homework)) == 0

    asyncio.run(run())
