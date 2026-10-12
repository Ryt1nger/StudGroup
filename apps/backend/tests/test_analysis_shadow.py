import asyncio
import json
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from studgroup.analysis.context_strategy import validate_context
from studgroup.analysis.contracts import (
    ANALYSIS_ENGINE_VERSION,
    ANALYSIS_SCHEMA_VERSION,
    AnalysisInput,
    ProposedAcademicItem,
    SourceMessage,
)
from studgroup.analysis.evaluation import (
    aggregate_display_reviews,
    aggregate_operations,
    compare_entities,
)
from studgroup.analysis.provider import ProviderResponse, ProviderUsage
from studgroup.analysis.routing import (
    authorize_shadow_run,
    configure_group_mode,
    get_route,
)
from studgroup.analysis.shadow import persist_shadow_summary, run_shadow
from studgroup.models import (
    AcademicDeadline,
    BotOutbox,
    Group,
    Homework,
    Notification,
    ShadowEvaluationRun,
)

pytest_plugins = ["test_schedule_api"]
GROUP_ID = UUID("00000000-0000-0000-0000-000000000001")
NOW = datetime(2026, 10, 12, 12, tzinfo=UTC)


def analysis_input(group_id=GROUP_ID):
    return AnalysisInput(
        group_id=group_id,
        group_timezone="Europe/Moscow",
        source_revision=1,
        target_message_ids=[10],
        messages=[
            SourceMessage(
                telegram_message_id=10,
                revision=1,
                message_date=NOW,
                text="По математике решить задачи 1–3",
            )
        ],
    )


def proposal(*, source_id=10, kind="homework", audience="group"):
    return ProposedAcademicItem(
        destination="task" if kind == "homework" else "deadline",
        semantic_kind=kind,
        subject="Математика",
        title="Решить задачи 1–3",
        description="Решить задачи 1–3.",
        deadline={"at": None, "date_only": False, "basis": "unknown"},
        audience={"scope": audience, "label": None},
        confidence=90,
        evidence=[
            {
                "telegram_message_id": source_id,
                "revision": 1,
                "fact_paths": ["description"],
            }
        ],
    )


def context_payload(**patch):
    return {
        "schema_version": ANALYSIS_SCHEMA_VERSION,
        "engine_version": ANALYSIS_ENGINE_VERSION,
        "source_revision": 1,
        "relevant_source_ids": [10],
        "discussion_facts": [
            {
                "fact": "Задание относится к математике",
                "evidence": [
                    {
                        "telegram_message_id": 10,
                        "revision": 1,
                        "fact_paths": ["subject"],
                    }
                ],
            }
        ],
        "conflicts": [],
        "memory_proposals": [],
    } | patch


def extraction_payload():
    return {
        "schema_version": ANALYSIS_SCHEMA_VERSION,
        "engine_version": ANALYSIS_ENGINE_VERSION,
        "source_revision": 1,
        "proposals": [proposal().model_dump(mode="json")],
        "explicit_empty": False,
    }


class FakeProvider:
    def __init__(self):
        self.requests = []

    async def complete(self, request):
        self.requests.append(request)
        payload = context_payload() if len(self.requests) == 1 else extraction_payload()
        return ProviderResponse(
            content=json.dumps(payload),
            usage=ProviderUsage(prompt_tokens=10, completion_tokens=5),
            model="fake",
            finish_reason="stop",
            payload_sha256=str(len(self.requests)) * 64,
        )


def test_context_output_requires_current_in_envelope_evidence():
    valid = validate_context(json.dumps(context_payload()), analysis_input())
    assert valid.relevant_source_ids == [10]
    bad = context_payload(relevant_source_ids=[999])
    with pytest.raises(ValueError, match="context_source_outside_envelope"):
        validate_context(json.dumps(bad), analysis_input())
    stale = context_payload()
    stale["discussion_facts"][0]["evidence"][0]["revision"] = 2
    with pytest.raises(ValueError, match="context_evidence_outside_envelope"):
        validate_context(json.dumps(stale), analysis_input())


def test_group_routing_defaults_legacy_and_new_requires_gate(client):
    async def run():
        async with AsyncSession(client.app.state.engine) as db:
            group = await db.scalar(select(Group).where(Group.telegram_chat_id == -1001))
            default = await get_route(db, group.id)
            assert default.legacy_writes and not default.shadow_runs
            shadow = await configure_group_mode(
                db,
                group.id,
                "shadow",
                NOW,
                shadow_daily_budget_usd=Decimal("0.01"),
                shadow_sample_percent=100,
            )
            assert shadow.legacy_writes and shadow.shadow_runs and not shadow.new_writes
            allowed, reason = await authorize_shadow_run(
                db, group.id, shadow, NOW, Decimal("0.005"), "batch-1"
            )
            assert allowed and reason == "shadow_authorized"
            denied, reason = await authorize_shadow_run(
                db, group.id, shadow, NOW, Decimal("0.02"), "batch-2"
            )
            assert not denied and reason == "shadow_budget_exhausted"
            with pytest.raises(ValueError, match="new_writer_requires_explicit_gate"):
                await configure_group_mode(db, group.id, "new", NOW)
            new = await configure_group_mode(
                db,
                group.id,
                "new",
                NOW,
                new_writer_gate_approved=True,
            )
            assert new.new_writes and not new.legacy_writes and not new.shadow_runs

    asyncio.run(run())


def test_shadow_two_pass_has_no_product_side_effects_and_persists_summary(client):
    async def run():
        async with AsyncSession(client.app.state.engine) as db:
            group = await db.scalar(select(Group).where(Group.telegram_chat_id == -1001))
            data = analysis_input(group.id)
            provider = FakeProvider()
            result = await run_shadow(provider, data, [])
            persist_shadow_summary(
                db,
                group_id=group.id,
                batch_id=None,
                analysis_input=data,
                result=result,
                metrics={"entity_recall": 1.0},
                charged_usd=Decimal("0.001"),
                now=NOW,
            )
            await db.commit()
            assert len(provider.requests) == 2
            assert len(result.validation.accepted) == 1
            assert result.prompt_tokens == 20
            for model in (Homework, AcademicDeadline, Notification, BotOutbox):
                assert await db.scalar(select(func.count()).select_from(model)) == 0
            assert await db.scalar(select(func.count()).select_from(ShadowEvaluationRun)) == 1

    asyncio.run(run())


def test_evaluation_matches_entities_not_raw_proposal_counts():
    reference = [proposal(), proposal(source_id=11, kind="test")]
    candidate = [proposal(), proposal(source_id=12)]
    metrics = compare_entities(
        reference,
        candidate,
        reference_actions=["create", "update"],
        candidate_actions=["create", "cancel"],
    )
    assert metrics["matched_items"] == 1
    assert metrics["entity_precision"] == 0.5
    assert metrics["entity_recall"] == 0.5
    assert metrics["action_accuracy"] == 1.0
    operations = aggregate_operations(
        [
            {
                "latency_ms": 100,
                "prompt_tokens": 10,
                "completion_tokens": 2,
                "charged_usd": Decimal("0.001"),
            },
            {
                "latency_ms": 300,
                "prompt_tokens": 20,
                "completion_tokens": 4,
                "charged_usd": Decimal("0.002"),
            },
        ]
    )
    assert operations["latency_p50_ms"] == 200
    assert operations["latency_p95_ms"] == 300
    assert operations["charged_usd"] == Decimal("0.003")
    display = aggregate_display_reviews(
        [
            {"factually_consistent": True, "completeness": 4, "readability": 5},
            {"factually_consistent": False, "completeness": 2, "readability": 3},
        ]
    )
    assert display == {
        "reviewed_items": 2,
        "factual_consistency": 0.5,
        "completeness_mean": 3,
        "readability_mean": 4,
    }
