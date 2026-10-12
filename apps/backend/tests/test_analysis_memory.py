import asyncio
import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from studgroup.analysis.context import assemble_memory_context
from studgroup.analysis.memory import propose_fact, transition_fact
from studgroup.models import (
    Group,
    GroupKnowledgeEvidence,
    GroupKnowledgeFact,
    GroupKnowledgeHistory,
    RawMessage,
)

pytest_plugins = ["test_schedule_api"]


async def seed_source(db, group, mid=1, revision=1):
    now = datetime(2026, 10, 12, 9, tzinfo=UTC)
    row = RawMessage(
        group_id=group.id,
        telegram_message_id=mid,
        sender_id=1,
        text="#матан",
        message_date=now,
        version_date=now,
        revision=revision,
        delete_at=now + timedelta(days=30),
    )
    db.add(row)
    await db.flush()
    return row


def test_model_fact_stays_proposed_until_authoritative_transition(client):
    async def run():
        async with AsyncSession(client.app.state.engine, expire_on_commit=False) as db:
            group = await db.scalar(select(Group).where(Group.telegram_chat_id == -1001))
            await seed_source(db, group)
            fact = await propose_fact(
                db,
                group_id=group.id,
                fact_type="subject_alias",
                normalized_key="#матан",
                value={"subject": "Математический анализ"},
                confidence=99,
                evidence=[(1, 1)],
                now=datetime.now(UTC),
            )
            assert fact.status == "proposed"
            with pytest.raises(ValueError, match="verification_requires_authoritative_source"):
                await transition_fact(
                    db,
                    fact,
                    "verified",
                    verification_source="model_confidence",
                    reason_code="not_allowed",
                    now=datetime.now(UTC),
                )
            await transition_fact(
                db,
                fact,
                "verified",
                verification_source="headman_manual",
                reason_code="headman_confirmed",
                now=datetime.now(UTC),
            )
            await db.commit()
            assert fact.status == "verified"
            assert await db.scalar(select(func.count()).select_from(GroupKnowledgeEvidence)) == 1
            assert await db.scalar(select(func.count()).select_from(GroupKnowledgeHistory)) == 2

    asyncio.run(run())


def test_fact_cannot_cite_stale_or_other_group_source(client):
    async def run():
        async with AsyncSession(client.app.state.engine) as db:
            groups = (await db.scalars(select(Group).order_by(Group.telegram_chat_id))).all()
            source_group = groups[0]
            other_group = Group(
                telegram_chat_id=-2002,
                name="Other",
                timezone="Europe/Moscow",
                pilot_authorized=True,
            )
            db.add(other_group)
            await db.flush()
            await seed_source(db, source_group, mid=7, revision=2)
            with pytest.raises(ValueError, match="knowledge_evidence_outside_group_or_stale"):
                await propose_fact(
                    db,
                    group_id=other_group.id,
                    fact_type="subject_alias",
                    normalized_key="#матан",
                    value={"subject": "Математический анализ"},
                    confidence=80,
                    evidence=[(7, 1)],
                    now=datetime.now(UTC),
                )

    asyncio.run(run())


def test_context_is_group_isolated_bounded_and_deterministic():
    class Fact:
        def __init__(self, group_id, key, status, value, revision=1):
            self.group_id = group_id
            self.normalized_key = key
            self.fact_type = "subject_alias"
            self.status = status
            self.value_json = json.dumps(value, ensure_ascii=False)
            self.revision = revision

    group_id = "group-1"
    facts = [
        Fact(group_id, "#матан", "verified", {"subject": "Матан"}),
        Fact(group_id, "#русский", "proposed", {"subject": "Русский язык"}),
    ]
    first = assemble_memory_context(group_id, "задача по матану", facts, max_bytes=300)
    second = assemble_memory_context(group_id, "задача по матану", list(reversed(facts)), max_bytes=300)
    assert first == second
    assert first[0]["key"] == "#матан"
    assert len(json.dumps(first, ensure_ascii=False).encode()) <= 300
    with pytest.raises(ValueError, match="cross_group_memory_candidate"):
        assemble_memory_context("group-2", "матан", facts)


def test_same_fact_proposal_is_idempotent(client):
    async def run():
        async with AsyncSession(client.app.state.engine, expire_on_commit=False) as db:
            group = await db.scalar(select(Group).where(Group.telegram_chat_id == -1001))
            await seed_source(db, group)
            values = {
                "group_id": group.id,
                "fact_type": "subject_alias",
                "normalized_key": "#матан",
                "value": {"subject": "Математический анализ"},
                "confidence": 90,
                "evidence": [(1, 1)],
                "now": datetime.now(UTC),
            }
            first = await propose_fact(db, **values)
            await db.commit()
            second = await propose_fact(db, **values)
            await db.commit()
            assert first.id == second.id
            assert await db.scalar(select(func.count()).select_from(GroupKnowledgeFact)) == 1

    asyncio.run(run())


def test_memory_rejects_unrestricted_chat_summary(client):
    async def run():
        async with AsyncSession(client.app.state.engine) as db:
            group = await db.scalar(select(Group).where(Group.telegram_chat_id == -1001))
            await seed_source(db, group)
            with pytest.raises(ValueError, match="unsupported_knowledge_fact_type"):
                await propose_fact(
                    db,
                    group_id=group.id,
                    fact_type="chat_summary",
                    normalized_key="all",
                    value={"text": "Полная переписка"},
                    confidence=90,
                    evidence=[(1, 1)],
                    now=datetime.now(UTC),
                )

    asyncio.run(run())
