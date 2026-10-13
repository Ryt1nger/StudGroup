import asyncio
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from test_processing import Provider as LegacyProvider
from test_processing import source

from studgroup.ai import ProviderFailure
from studgroup.analysis.context_strategy import diagnose_context_output, validate_context
from studgroup.analysis.contracts import (
    ANALYSIS_ENGINE_VERSION,
    ANALYSIS_SCHEMA_VERSION,
    AnalysisInput,
    ProposedAcademicItem,
    SourceMessage,
)
from studgroup.analysis.dispatch import dispatch_next
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
from studgroup.analysis.shadow_worker import purge_expired_shadow_runs
from studgroup.main import Settings
from studgroup.models import (
    AcademicDeadline,
    BotOutbox,
    Group,
    GroupAnalysisSettings,
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


class HybridProvider(LegacyProvider, FakeProvider):
    def __init__(self, *, shadow_failure=None):
        LegacyProvider.__init__(self)
        self.requests = []
        self.shadow_failure = shadow_failure

    async def complete(self, request):
        if self.shadow_failure and not self.requests:
            self.requests.append(request)
            raise ProviderFailure(self.shadow_failure, True)
        self.requests.append(request)
        revision = request.user_payload["source_revision"]
        message = request.user_payload["messages"][-1]
        message_id = message["telegram_message_id"]
        if request.prompt_version == "cplus-context-1":
            payload = context_payload(
                source_revision=revision,
                relevant_source_ids=[message_id],
                discussion_facts=[],
            )
        else:
            item = proposal(source_id=message_id).model_dump(mode="json")
            item["evidence"][0]["revision"] = message["revision"]
            payload = {
                "schema_version": ANALYSIS_SCHEMA_VERSION,
                "engine_version": ANALYSIS_ENGINE_VERSION,
                "source_revision": revision,
                "proposals": [item],
                "explicit_empty": False,
            }
        return ProviderResponse(
            content=json.dumps(payload),
            usage=ProviderUsage(prompt_tokens=10, completion_tokens=5),
            model="fake",
            finish_reason="stop",
            payload_sha256=str(len(self.requests)) * 64,
        )


def product_snapshot(client):
    async def read():
        async with AsyncSession(client.app.state.engine) as db:
            result = {}
            for model in (Homework, AcademicDeadline, Notification, BotOutbox):
                result[model.__tablename__] = await db.scalar(
                    select(func.count()).select_from(model)
                )
            return result

    return asyncio.run(read())


def enable_shadow(client, budget="0.20"):
    async def configure():
        async with AsyncSession(client.app.state.engine) as db:
            group = await db.scalar(select(Group).where(Group.telegram_chat_id == -1001))
            await configure_group_mode(
                db,
                group.id,
                "shadow",
                datetime.now(UTC),
                shadow_daily_budget_usd=Decimal(budget),
                shadow_sample_percent=100,
            )
            await db.commit()

    asyncio.run(configure())


def dispatch(client, provider, now=None):
    return asyncio.run(
        dispatch_next(
            client.app.state.engine,
            Settings(ai_enabled=True),
            provider=provider,
            now=now,
        )
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


def test_context_diagnostics_report_structure_without_values():
    retained_structure = {
        "relevant_source_ids": [10],
        "discussion_facts": [],
        "conflicts": [],
        "memory_proposals": [],
        "summary": "redacted provider value",
    }
    diagnostic = diagnose_context_output(
        json.dumps(retained_structure),
        analysis_input(),
    )
    assert diagnostic == {
        "reason": "context_schema_invalid",
        "missing_top_level": ["engine_version", "schema_version", "source_revision"],
        "additional_top_level": ["summary"],
        "validation_errors": {
            "source_revision:missing": 1,
            "summary:extra_forbidden": 1,
        },
    }
    assert "redacted provider value" not in json.dumps(diagnostic)


@pytest.mark.parametrize(
    ("content", "reason"),
    [
        ("not-json", "context_invalid_json"),
        ("[]", "context_output_not_object"),
    ],
)
def test_context_validation_exposes_stable_technical_reason(content, reason):
    with pytest.raises((TypeError, ValueError), match=reason):
        validate_context(content, analysis_input())


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


def test_dispatch_runs_durable_shadow_once_without_product_mutation(client):
    enable_shadow(client)
    source(client, text="ДЗ: решить задачи 1–3")
    provider = HybridProvider()
    assert dispatch(client, provider) == "completed"
    before = product_snapshot(client)
    assert before["homework"] == 1
    assert dispatch(client, provider) == "shadow_completed"
    assert product_snapshot(client) == before
    assert dispatch(client, provider) == "idle"
    assert product_snapshot(client) == before
    assert len(provider.requests) == 2

    async def inspect():
        async with AsyncSession(client.app.state.engine) as db:
            row = await db.scalar(select(ShadowEvaluationRun))
            assert row.state == "completed"
            assert row.attempts == 1
            assert row.validation_result_json
            assert row.context_result_json
            assert row.reserved_usd == 0

    asyncio.run(inspect())


def test_shadow_only_kill_switch_runs_cplus_while_legacy_stays_disabled(client):
    enable_shadow(client)
    source(client, text="ДЗ: решить задачи 1–3")
    provider = HybridProvider()
    settings = Settings(ai_enabled=False, ai_shadow_enabled=True)

    assert (
        asyncio.run(dispatch_next(client.app.state.engine, settings, provider, NOW))
        == "shadow_queued"
    )
    assert product_snapshot(client)["homework"] == 0
    assert (
        asyncio.run(dispatch_next(client.app.state.engine, settings, provider, NOW))
        == "shadow_completed"
    )
    assert product_snapshot(client)["homework"] == 0
    assert len(provider.requests) == 2


def test_shadow_only_kill_switch_honors_absolute_cutoff(client):
    enable_shadow(client)
    source(client)
    provider = HybridProvider()
    settings = Settings(
        ai_enabled=False,
        ai_shadow_enabled=True,
        ai_shadow_enabled_until=NOW,
    )

    assert (
        asyncio.run(dispatch_next(client.app.state.engine, settings, provider, NOW))
        == "shadow_disabled"
    )
    assert provider.requests == []
    assert product_snapshot(client)["homework"] == 0


def test_shadow_failure_is_retryable_and_cannot_mutate_products(client):
    enable_shadow(client)
    source(client)
    provider = HybridProvider(shadow_failure="provider_unreachable")
    assert dispatch(client, provider) == "completed"
    before = product_snapshot(client)
    first = datetime.now(UTC)
    assert dispatch(client, provider, first) == "shadow_retry"
    assert product_snapshot(client) == before
    provider.shadow_failure = None
    assert dispatch(client, provider, first + timedelta(minutes=1)) == "shadow_completed"
    assert product_snapshot(client) == before

    async def inspect():
        async with AsyncSession(client.app.state.engine) as db:
            row = await db.scalar(select(ShadowEvaluationRun))
            assert row.state == "completed"
            assert row.attempts == 2
            assert row.charged_usd >= Decimal("0.04")

    asyncio.run(inspect())


def test_shadow_extraction_pass_failure_is_durable_and_read_only(client):
    class ExtractionFailureProvider(HybridProvider):
        async def complete(self, request):
            if request.prompt_version == "cplus-extraction-1":
                raise ProviderFailure("provider_unreachable", True)
            return await super().complete(request)

    enable_shadow(client)
    source(client)
    provider = ExtractionFailureProvider()
    assert dispatch(client, provider) == "completed"
    before = product_snapshot(client)
    assert dispatch(client, provider) == "shadow_retry"
    assert product_snapshot(client) == before

    async def inspect():
        async with AsyncSession(client.app.state.engine) as db:
            row = await db.scalar(select(ShadowEvaluationRun))
            assert row.last_error == "extraction_provider_unreachable"
            assert row.context_result_json
            assert row.prompt_tokens == 10
            assert row.charged_usd == Decimal("0.04")
            assert row.reserved_usd == 0

    asyncio.run(inspect())


def test_shadow_budget_is_reserved_before_provider_call(client):
    enable_shadow(client, budget="0.01")
    source(client)
    provider = HybridProvider()
    assert dispatch(client, provider) == "completed"
    before = product_snapshot(client)
    assert dispatch(client, provider) == "shadow_budget_deferred"
    assert provider.requests == []
    assert product_snapshot(client) == before

    async def inspect():
        async with AsyncSession(client.app.state.engine) as db:
            row = await db.scalar(select(ShadowEvaluationRun))
            assert row.state == "budget_deferred"
            assert row.reserved_usd == 0
            settings = await db.get(GroupAnalysisSettings, row.group_id)
            settings.analysis_mode = "legacy"
            await db.commit()

    asyncio.run(inspect())
    assert dispatch(client, provider, datetime.now(UTC) + timedelta(days=1)) == "idle"
    assert provider.requests == []


def test_shadow_captures_source_even_when_legacy_relevance_filters_it(client):
    enable_shadow(client)
    source(client, text="К следующей встрече прочитать введение и выписать определения")
    provider = HybridProvider()
    assert dispatch(client, provider) == "shadow_queued"
    assert product_snapshot(client)["homework"] == 0
    assert dispatch(client, provider) == "shadow_completed"
    assert product_snapshot(client)["homework"] == 0

    async def inspect():
        async with AsyncSession(client.app.state.engine) as db:
            row = await db.scalar(select(ShadowEvaluationRun))
            assert row.raw_message_id is not None
            assert row.source_revision == 1
            assert row.state == "completed"

    asyncio.run(inspect())


def test_expired_shadow_diagnostics_are_physically_purged(client):
    async def run():
        async with AsyncSession(client.app.state.engine) as db:
            group = await db.scalar(select(Group).where(Group.telegram_chat_id == -1001))
            data = analysis_input(group.id)
            result = await run_shadow(FakeProvider(), data, [])
            persist_shadow_summary(
                db,
                group_id=group.id,
                batch_id=None,
                analysis_input=data,
                result=result,
                metrics={},
                charged_usd=0,
                now=NOW - timedelta(days=31),
            )
            await db.commit()
        assert await purge_expired_shadow_runs(client.app.state.engine, NOW) == 1
        async with AsyncSession(client.app.state.engine) as db:
            assert await db.scalar(select(func.count()).select_from(ShadowEvaluationRun)) == 0

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
