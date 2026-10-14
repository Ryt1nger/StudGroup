"""Hybrid AI output adapter: corpus, contract/prompt compatibility, repair and shadow E2E."""

import asyncio
import json
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID
from zoneinfo import ZoneInfo

import pytest
from hybrid_corpus import CASES
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from test_analysis_shadow import (
    HybridProvider,
    context_payload,
    enable_shadow,
    product_snapshot,
)
from test_processing import source

from studgroup.ai import ProviderFailure
from studgroup.analysis.card_editor import edit_card
from studgroup.analysis.context_strategy import CONTEXT_SYSTEM_PROMPT, validate_context
from studgroup.analysis.contract_prompt import (
    contract_block,
    example_output,
    proposal_contract_block,
    render_contract,
)
from studgroup.analysis.contracts import (
    ANALYSIS_ENGINE_VERSION,
    ANALYSIS_SCHEMA_VERSION,
    AnalysisInput,
    ContextUnderstandingOutput,
    ProposedAcademicItem,
    SourceMessage,
)
from studgroup.analysis.dispatch import dispatch_next
from studgroup.analysis.extraction_pipeline import adapt_and_validate
from studgroup.analysis.extraction_strategy import (
    EXTRACTION_PROMPT_VERSION,
    EXTRACTION_SYSTEM_PROMPT,
    prepare_extraction_request,
    validate_extraction_adapted,
)
from studgroup.analysis.provider import ProviderResponse, ProviderUsage
from studgroup.analysis.repair import (
    MAX_REPAIR_ITEMS,
    REPAIR_MARKER,
    REPAIR_PROMPT_VERSION,
    apply_repair,
    repair_rejected,
)
from studgroup.analysis.shadow import (
    SHADOW_PACKAGE_CAP_USD,
    ShadowPassFailure,
    run_shadow,
    usage_cost_usd,
)
from studgroup.analysis.validation import validate_provider_content
from studgroup.main import Settings
from studgroup.models import ShadowEvaluationRun

pytest_plugins = ["test_schedule_api"]
MSK = ZoneInfo("Europe/Moscow")
GROUP_ID = UUID("00000000-0000-0000-0000-000000000001")
NOW = datetime(2026, 10, 12, 12, tzinfo=UTC)


def build_input(case):
    rows = [
        SourceMessage(
            telegram_message_id=mid,
            revision=1,
            message_date=datetime.fromisoformat(when),
            text=text,
        )
        for mid, when, text in case["messages"]
    ]
    return AnalysisInput(
        group_id=GROUP_ID,
        group_timezone="Europe/Moscow",
        source_revision=1,
        target_message_ids=[rows[-1].telegram_message_id],
        messages=rows,
    )


def envelope(proposals, **patch):
    return json.dumps(
        {
            "schema_version": ANALYSIS_SCHEMA_VERSION,
            "engine_version": ANALYSIS_ENGINE_VERSION,
            "source_revision": 1,
            "proposals": proposals,
            "explicit_empty": not proposals,
        }
        | patch
    )


def issue_codes(result):
    codes = []
    for rejected in result.rejected:
        if rejected.schema_issues:
            codes.extend(issue.error_type for issue in rejected.schema_issues)
        else:
            codes.append(rejected.reason_code)
    return codes


# --------------------------------------------------------------------------------------------
# Corpus: every card type has a positive scenario and negative scenarios
# --------------------------------------------------------------------------------------------
@pytest.mark.parametrize("case", CASES, ids=[row["id"] for row in CASES])
def test_corpus_case(case):
    data = build_input(case)
    outcome = validate_extraction_adapted(
        envelope(case["proposals"]), data, existing_items=case["existing"]
    )
    result = outcome.result
    assert result.explicit_empty is case["explicit_empty"]
    assert issue_codes(result) == case["rejected"]
    assert len(result.accepted) == len(case["accepted"])
    for index, (proposal, gold) in enumerate(zip(result.accepted, case["accepted"], strict=True)):
        assert proposal.subject == gold["subject"]
        assert proposal.semantic_kind == gold["kind"]
        assert proposal.action == gold["action"]
        assert proposal.deadline.basis == gold["basis"]
        assert proposal.deadline.date_only is gold["date_only"]
        at = proposal.deadline.at
        assert (at.astimezone(MSK).strftime("%Y-%m-%dT%H:%M") if at else None) == gold["at"]
        card = edit_card(proposal, data.messages)
        assert card.visibility == gold["visibility"], (card, proposal.attention_reasons)
        expected_reason = case["reasons"].get(index)
        if expected_reason:
            assert expected_reason in proposal.attention_reasons
    for name in case["actions"]:
        assert result.adapter_actions.get(name, 0) >= 1, (name, result.adapter_actions)


def test_corpus_covers_every_card_type_and_enough_negatives():
    kinds = {gold["kind"] for row in CASES for gold in row["accepted"]}
    assert kinds == {
        "homework", "test", "control_point", "assessment", "credit", "exam",
        "other_academic_deadline",
    }
    actions = {gold["action"] for row in CASES for gold in row["accepted"]}
    assert actions == {"create", "update", "reschedule", "cancel"}
    assert sum(1 for row in CASES if row["rejected"]) >= 15


def test_corpus_totals():
    accepted = rejected = 0
    for case in CASES:
        result = validate_extraction_adapted(
            envelope(case["proposals"]), build_input(case), existing_items=case["existing"]
        ).result
        accepted += len(result.accepted)
        rejected += len(result.rejected)
    # Pinned so a silent change in acceptance behaviour is noticed in review.
    assert (accepted, rejected) == (
        sum(len(row["accepted"]) for row in CASES),
        sum(len(row["rejected"]) for row in CASES),
    )
    assert accepted >= 30


# --------------------------------------------------------------------------------------------
# The reproduced production defect
# --------------------------------------------------------------------------------------------
def test_original_defect_strict_path_rejected_valid_deadline_json():
    """Raw JSON text for an aware datetime failed the strict model (datetime_type)."""
    case = next(row for row in CASES if row["id"] == "homework_iso_datetime_with_offset")
    data = build_input(case)
    strict = validate_provider_content(
        envelope(case["proposals"]),
        allowed_sources={10: 1},
        source_revision=1,
    )
    assert strict.accepted == []
    assert strict.rejected[0].schema_issues[0].error_type == "datetime_type"
    adapted = validate_extraction_adapted(envelope(case["proposals"]), data).result
    assert len(adapted.accepted) == 1


def test_value_error_is_reported_with_our_rule_code_not_provider_text():
    proposal = {
        "destination": "task", "semantic_kind": "homework", "confidence": 90,
        "deadline": {"at": None, "date_only": True, "basis": "unknown"},
        "evidence": [{"telegram_message_id": 10, "revision": 1, "fact_paths": ["title"]}],
        "title": "PRIVATE TITLE",
    }
    result = validate_provider_content(
        envelope([proposal]), allowed_sources={10: 1}, source_revision=1
    )
    assert issue_codes(result) == ["unknown_deadline_cannot_be_date_only"]
    assert "PRIVATE" not in result.model_dump_json()


@pytest.mark.parametrize(
    "deadline",
    [
        {"at": None, "date_only": True, "basis": "unknown"},
        {"at": None, "date_only": False, "basis": "explicit"},
        {"at": None, "date_only": False, "basis": "schedule_inferred"},
    ],
)
def test_each_deadline_value_error_rule_has_a_distinct_code(deadline):
    from studgroup.analysis.contracts import CONTRACT_RULE_CODES

    proposal = {
        "destination": "task", "semantic_kind": "homework", "confidence": 90,
        "deadline": deadline,
        "evidence": [{"telegram_message_id": 10, "revision": 1, "fact_paths": ["title"]}],
    }
    result = validate_provider_content(
        envelope([proposal]), allowed_sources={10: 1}, source_revision=1
    )
    (code,) = issue_codes(result)
    assert code in CONTRACT_RULE_CODES


# --------------------------------------------------------------------------------------------
# Prompt <-> model compatibility (no hand-maintained contract copies)
# --------------------------------------------------------------------------------------------
def test_generated_examples_pass_the_production_path():
    data = AnalysisInput(
        group_id=GROUP_ID,
        group_timezone="Europe/Moscow",
        source_revision=1,
        target_message_ids=[101],
        messages=[
            SourceMessage(
                telegram_message_id=mid, revision=1, message_date=NOW, text=f"сообщение {mid}"
            )
            for mid in (101, 102, 103, 104)
        ],
    )
    payload = example_output()
    result = adapt_and_validate(json.dumps(payload), data).result
    assert len(result.accepted) == 3 and not result.rejected
    # The examples are also canonical in JSON mode.
    for raw in payload["proposals"]:
        ProposedAcademicItem.model_validate_json(json.dumps(raw))


def test_prompt_lists_every_contract_field_and_literal():
    signature = render_contract()
    for name in (
        *ProposedAcademicItem.model_fields,
        "at", "date_only", "basis", "scope", "label",
        "telegram_message_id", "revision", "fact_paths",
    ):
        assert name in signature
    for literal in (
        "homework", "test", "control_point", "assessment", "credit", "exam",
        "other_academic_deadline", "explicit", "schedule_inferred", "ambiguous_range", "unknown",
        "group", "subgroup", "personal", "create", "update", "reschedule", "cancel",
    ):
        assert f'"{literal}"' in signature
    assert "REQUIRES a non-null at" in EXTRACTION_SYSTEM_PROMPT
    assert contract_block() in EXTRACTION_SYSTEM_PROMPT
    assert "ONE proposal" in proposal_contract_block()


def test_extraction_request_gives_relative_date_anchors_and_trusted_items():
    case = CASES[0]
    data = build_input(case)
    request = prepare_extraction_request(
        data, {"facts": []}, [{"id": "11111111-1111-4111-8111-111111111111", "revision": 1}]
    )
    assert request.prompt_version == EXTRACTION_PROMPT_VERSION
    message = request.user_payload["messages"][0]
    assert (message["local_date"], message["local_weekday"]) == ("2026-10-12", "Monday")
    assert request.user_payload["existing_items"][0]["revision"] == 1


def test_context_prompt_example_matches_its_model():
    marker = CONTEXT_SYSTEM_PROMPT[CONTEXT_SYSTEM_PROMPT.index("{\n") :]
    example = json.loads(marker[: marker.index("\nCopy schema_version")])
    for key in example:
        assert key in ContextUnderstandingOutput.model_fields
    assert set(ContextUnderstandingOutput.model_fields) <= set(example)
    data = AnalysisInput(
        group_id=GROUP_ID,
        group_timezone="Europe/Moscow",
        source_revision=1,
        target_message_ids=[123],
        messages=[
            SourceMessage(telegram_message_id=123, revision=1, message_date=NOW, text="текст")
        ],
    )
    assert validate_context(json.dumps(example), data).source_revision == 1


# --------------------------------------------------------------------------------------------
# AI Repair (offline fake provider)
# --------------------------------------------------------------------------------------------
class RepairProvider:
    """Answers repair requests with a scripted function; counts calls."""

    def __init__(self, repairs=None, *, fail=None, raw=None, usage=(120, 60)):
        self.repairs = repairs
        self.fail = fail
        self.raw = raw
        self.usage = usage
        self.requests = []

    async def complete(self, request):
        self.requests.append(request)
        if self.fail:
            raise ProviderFailure(self.fail, False, reservation_releasable=True)
        if self.raw is not None:
            content = self.raw
        else:
            content = json.dumps({"repairs": self.repairs(request) if self.repairs else []})
        return ProviderResponse(
            content=content,
            usage=ProviderUsage(prompt_tokens=self.usage[0], completion_tokens=self.usage[1]),
            model="fake",
            finish_reason="stop",
            payload_sha256="r" * 64,
        )


def canary_case():
    return next(
        row for row in CASES if row["id"] == "original_canary_shape_date_only_without_value"
    )


def drop_claim(request):
    """A well-behaved repair: keeps every fact, downgrades the contradictory deadline."""
    out = []
    for entry in request.user_payload["items"]:
        proposal = dict(entry["original"])
        proposal["deadline"] = {"at": None, "date_only": False, "basis": "unknown"}
        out.append({"ordinal": entry["ordinal"], "proposal": proposal})
    return out


def run_repair(provider, case, *, budget=Decimal("0.04")):
    data = build_input(case)
    outcome = validate_extraction_adapted(envelope(case["proposals"]), data)
    repair = asyncio.run(
        repair_rejected(
            provider,
            data,
            outcome.candidates,
            budget_remaining_usd=budget,
            cost_fn=usage_cost_usd,
        )
    )
    return data, outcome, repair


def test_repair_fixes_structure_keeps_facts_and_marks_card():
    provider = RepairProvider(drop_claim)
    data, outcome, repair = run_repair(provider, canary_case())
    assert len(provider.requests) == 1
    assert provider.requests[0].prompt_version == REPAIR_PROMPT_VERSION
    assert repair.outcomes == {0: "repair_applied"}
    merged = apply_repair(outcome.result, repair)
    assert merged.accepted_ordinals == [0] and not merged.rejected
    card = merged.accepted[0]
    assert card.deadline.at is None and REPAIR_MARKER in card.attention_reasons
    assert edit_card(card, data.messages).visibility == "headman_attention"


def test_repair_request_contains_only_cited_context_and_no_value_leaks_in_issues():
    provider = RepairProvider(drop_claim)
    case = canary_case()
    case = {**case, "messages": [*case["messages"], (11, case["messages"][0][1], "посторонний")]}
    _data, _outcome, _repair = run_repair(provider, case)
    payload = provider.requests[0].user_payload
    assert [row["telegram_message_id"] for row in payload["sources"]] == [10]
    assert payload["items"][0]["issues"] == [
        {"path": "proposals[0].deadline", "rule": "deadline_basis_requires_value"}
    ]


@pytest.mark.parametrize(
    ("mutate", "outcome"),
    [
        (lambda p: p["deadline"].update(at="2026-10-15T18:00:00+03:00", basis="explicit"),
         "repair_invented_deadline"),
        (lambda p: p.update(title="Совсем другое задание"), "repair_changed_text"),
        (lambda p: p.update(description=None), "repair_changed_text"),
        (lambda p: p.update(evidence=[{"telegram_message_id": 10, "revision": 1,
                                        "fact_paths": ["title"]},
                                       {"telegram_message_id": 11, "revision": 1,
                                        "fact_paths": ["title"]}]),
         "repair_changed_sources"),
        (lambda p: p.update(semantic_kind="exam", destination="deadline"),
         "repair_changed_classification_fact"),
        (lambda p: p.update(audience={"scope": "personal", "label": None}),
         "repair_changed_audience"),
        (lambda p: p.update(confidence=1000), "repair_still_invalid"),
    ],
)
def test_repair_cannot_change_facts(mutate, outcome):
    def tamper(request):
        out = []
        for entry in request.user_payload["items"]:
            proposal = json.loads(json.dumps(entry["original"]))
            proposal["deadline"] = {"at": None, "date_only": False, "basis": "unknown"}
            mutate(proposal)
            out.append({"ordinal": entry["ordinal"], "proposal": proposal})
        return out

    case = canary_case()
    case = {**case, "messages": [*case["messages"], (11, case["messages"][0][1], "другое")]}
    _data, outcome_, repair = run_repair(RepairProvider(tamper), case)
    assert repair.outcomes == {0: outcome} and not repair.repaired
    merged = apply_repair(outcome_.result, repair)
    assert merged.accepted == [] and merged.rejected[0].repair_outcome == outcome


def test_repair_is_single_shot_and_never_recurses():
    provider = RepairProvider(lambda request: [])
    _data, _outcome, repair = run_repair(provider, canary_case())
    assert len(provider.requests) == 1
    assert repair.outcomes == {0: "repair_declined"}


def test_repair_ignores_unknown_ordinals_and_garbage():
    for raw in ("not json", json.dumps({"repairs": "x"}), json.dumps([1])):
        provider = RepairProvider(raw=raw)
        _data, _outcome, repair = run_repair(provider, canary_case())
        assert repair.outcomes == {0: "repair_invalid_response"}
    provider = RepairProvider(lambda request: [{"ordinal": 9, "proposal": {}}])
    _data, _outcome, repair = run_repair(provider, canary_case())
    assert repair.outcomes == {0: "repair_declined"}


def test_repair_skips_when_budget_is_insufficient_without_calling_provider():
    provider = RepairProvider(drop_claim)
    _data, outcome, repair = run_repair(provider, canary_case(), budget=Decimal("0.0001"))
    assert provider.requests == [] and repair.attempted is False
    assert repair.outcomes == {0: "repair_skipped_budget"}
    assert apply_repair(outcome.result, repair).rejected[0].repair_outcome == (
        "repair_skipped_budget"
    )


def test_repair_provider_failure_is_contained():
    _data, _outcome, repair = run_repair(RepairProvider(fail="rate_limited"), canary_case())
    assert repair.error_code == "repair_rate_limited"
    assert repair.outcomes == {0: "repair_provider_failed"}


def test_non_repairable_rejections_are_never_sent_to_the_provider():
    for case_id in (
        "evidence_for_nonexistent_message",
        "mutation_identity_outside_trusted_context",
        "subgroup_without_label_is_rejected",
        "non_object_proposal_is_rejected",
    ):
        case = next(row for row in CASES if row["id"] == case_id)
        outcome = validate_extraction_adapted(
            envelope(case["proposals"]), build_input(case), existing_items=case["existing"]
        )
        assert outcome.candidates == [], case_id


def test_repair_batch_limit():
    base = canary_case()["proposals"][0]
    case = {**canary_case(), "proposals": [base] * (MAX_REPAIR_ITEMS + 2)}
    provider = RepairProvider(lambda request: [])
    _data, _outcome, repair = run_repair(provider, case)
    assert len(provider.requests[0].user_payload["items"]) == MAX_REPAIR_ITEMS
    assert list(repair.outcomes.values()).count("repair_skipped_batch_limit") == 2


def test_repair_reclassification_is_flagged_for_attention():
    case = next(row for row in CASES if row["id"] == "unknown_semantic_kind_is_rejected")

    def fix(request):
        return [
            {"ordinal": entry["ordinal"],
             "proposal": entry["original"] | {"semantic_kind": "test", "destination": "deadline"}}
            for entry in request.user_payload["items"]
        ]

    _data, _outcome, repair = run_repair(RepairProvider(fix), case)
    card = repair.repaired[0]
    assert card.semantic_kind == "test"
    assert {"repair_changed_classification", REPAIR_MARKER} <= set(card.attention_reasons)


# --------------------------------------------------------------------------------------------
# run_shadow orchestration, budget and isolation
# --------------------------------------------------------------------------------------------
class TwoPassProvider:
    def __init__(self, extraction, repair=None, *, extraction_usage=(500, 400)):
        self.extraction = extraction
        self.repair = repair
        self.extraction_usage = extraction_usage
        self.requests = []

    async def complete(self, request):
        self.requests.append(request)
        message_ids = [row["telegram_message_id"] for row in request.user_payload["messages"]] \
            if "messages" in request.user_payload else []
        if request.prompt_version == "cplus-context-1":
            content = json.dumps(context_payload(relevant_source_ids=message_ids[-1:],
                                                 discussion_facts=[]))
            usage = (300, 100)
        elif request.prompt_version == EXTRACTION_PROMPT_VERSION:
            content, usage = self.extraction, self.extraction_usage
        else:
            content, usage = json.dumps({"repairs": self.repair(request)}), (200, 100)
        return ProviderResponse(
            content=content,
            usage=ProviderUsage(prompt_tokens=usage[0], completion_tokens=usage[1]),
            model="fake",
            finish_reason="stop",
            payload_sha256=str(len(self.requests)) * 64,
        )


def shadow_case():
    case = {
        **canary_case(),
        "messages": [
            (10, "2026-10-12T12:00:00+03:00", "Физика: подготовить конспект параграфа до 15.10"),
        ],
    }
    good = {
        "semantic_kind": "homework", "subject": "Физика", "title": "Конспект параграфа",
        "description": "Подготовить конспект параграфа.", "confidence": 90,
        "deadline": {"at": "2026-10-15T23:59:00+03:00", "date_only": False, "basis": "explicit"},
        "evidence": [{"telegram_message_id": 10, "revision": 1, "fact_paths": ["description"]}],
    }
    bad = good | {"deadline": {"at": None, "date_only": True, "basis": "explicit"}}
    return case, good, bad


def test_run_shadow_disabled_repair_makes_no_extra_call():
    case, good, bad = shadow_case()
    provider = TwoPassProvider(envelope([good, bad]))
    result = asyncio.run(run_shadow(provider, build_input(case), []))
    assert len(provider.requests) == 2
    assert len(result.validation.accepted) == 1 and len(result.validation.rejected) == 1
    assert result.repair == {"enabled": False, "candidates": 1}
    assert result.validation.rejected[0].repair_outcome is None


def test_run_shadow_repair_enabled_recovers_card_and_charges_within_cap():
    case, good, bad = shadow_case()
    provider = TwoPassProvider(envelope([good, bad]), drop_claim)
    result = asyncio.run(run_shadow(provider, build_input(case), [], repair_enabled=True))
    assert [r.prompt_version for r in provider.requests].count(REPAIR_PROMPT_VERSION) == 1
    assert result.validation.accepted_ordinals == [0, 1]
    assert result.repair["applied"] == 1 and result.repair["outcomes"] == {"repair_applied": 1}
    assert result.prompt_tokens == 300 + 500 + 200 and result.completion_tokens == 100 + 400 + 100
    assert len(result.provider_payload_hashes) == 3
    usage = ProviderUsage(prompt_tokens=result.prompt_tokens,
                          completion_tokens=result.completion_tokens)
    assert usage_cost_usd(usage) < SHADOW_PACKAGE_CAP_USD


def test_run_shadow_repair_skipped_when_pass_costs_leave_no_room():
    case, good, bad = shadow_case()
    provider = TwoPassProvider(envelope([good, bad]), drop_claim,
                               extraction_usage=(10_000, 30_000))
    result = asyncio.run(run_shadow(provider, build_input(case), [], repair_enabled=True))
    assert [r.prompt_version for r in provider.requests].count(REPAIR_PROMPT_VERSION) == 0
    assert result.repair["skipped"] == "budget"


def test_run_shadow_survives_repair_provider_error():
    case, good, bad = shadow_case()

    def explode(request):
        raise RuntimeError("PRIVATE boom")

    provider = TwoPassProvider(envelope([good, bad]), explode)
    result = asyncio.run(run_shadow(provider, build_input(case), [], repair_enabled=True))
    assert len(result.validation.accepted) == 1
    assert result.repair["error"] == "repair_internal_error"
    assert "PRIVATE" not in json.dumps(result.repair)


def test_run_shadow_envelope_errors_keep_stable_codes():
    case, _good, _bad = shadow_case()
    provider = TwoPassProvider("not json")
    with pytest.raises(ShadowPassFailure) as failure:
        asyncio.run(run_shadow(provider, build_input(case), []))
    assert failure.value.code == "extraction_validation_unparseable_provider_json"


# --------------------------------------------------------------------------------------------
# Durable E2E: RawMessage -> context -> Pass 1 -> Pass 2 -> adapter -> validation -> shadow row
# --------------------------------------------------------------------------------------------
def test_shadow_e2e_accepts_real_shaped_output_and_never_touches_product_data(client):
    enable_shadow(client)
    source(client, text="Физика: подготовить конспект параграфа до 15.10", mid=10)
    _case, good, bad = shadow_case()

    class Provider(TwoPassProvider):
        async def complete(self, request):
            if request.prompt_version == EXTRACTION_PROMPT_VERSION:
                message = request.user_payload["messages"][-1]
                self.extraction = envelope(
                    [
                        good | {"evidence": [{"telegram_message_id": message["telegram_message_id"],
                                              "revision": message["revision"],
                                              "fact_paths": ["description", "deadline"]}]},
                        bad | {"evidence": [{"telegram_message_id": message["telegram_message_id"],
                                             "revision": message["revision"],
                                             "fact_paths": ["description"]}]},
                    ],
                    source_revision=request.user_payload["source_revision"],
                )
            return await super().complete(request)

    provider = Provider(None, drop_claim)
    before = product_snapshot(client)
    settings = Settings(ai_enabled=False, ai_shadow_enabled=True, ai_output_repair_enabled=True)
    engine = client.app.state.engine
    assert asyncio.run(dispatch_next(engine, settings, provider, NOW)) == "shadow_queued"
    assert asyncio.run(dispatch_next(engine, settings, provider, NOW)) == "shadow_completed"
    assert product_snapshot(client) == before

    async def inspect():
        async with AsyncSession(client.app.state.engine) as db:
            row = await db.scalar(select(ShadowEvaluationRun))
            metrics = json.loads(row.metrics_json)
            validation = json.loads(row.validation_result_json)
            assert metrics["accepted_items"] == 2 and metrics["rejected_items"] == 0
            assert metrics["repair"]["applied"] == 1
            assert "adapter_actions" in metrics
            assert [p["deadline"]["basis"] for p in validation["accepted"]] == [
                "explicit", "unknown",
            ]
            assert row.reserved_usd == 0
            assert row.charged_usd > 0 and row.charged_usd < SHADOW_PACKAGE_CAP_USD
            assert row.prompt_tokens == 300 + 500 + 200

    asyncio.run(inspect())


def test_existing_fake_provider_payload_is_accepted_by_adapter_path(client):
    enable_shadow(client)
    source(client, text="ДЗ: решить задачи 1–3")
    provider = HybridProvider()
    settings = Settings(ai_enabled=False, ai_shadow_enabled=True)
    engine = client.app.state.engine
    assert asyncio.run(dispatch_next(engine, settings, provider, NOW)) == "shadow_queued"
    assert asyncio.run(dispatch_next(engine, settings, provider, NOW)) == "shadow_completed"


def test_render_cards_is_bounded_and_links_supergroup_sources():
    from studgroup.analysis.review_text import MAX_CHUNK, render_cards, telegram_link

    assert telegram_link(-1004421204622, 7) == "https://t.me/c/4421204622/7"
    assert telegram_link(-5, 7) is None
    card = {
        "kind": "homework", "subject": None, "title": "T", "description": "d" * 2000,
        "deadline_at": None, "deadline_date_only": False, "deadline_basis": "unknown",
        "audience": {"scope": "group"}, "visibility": "visible", "attention_reasons": [],
        "sources": [{"telegram_message_id": 1, "message_date": datetime(2026, 10, 10, tzinfo=UTC),
                     "text": "x" * 5000, "fact_paths": []}],
    }
    chunks = render_cards([card] * 6, -1004421204622)
    assert chunks and all(len(c) <= MAX_CHUNK for c in chunks)
    assert "срок не указан" in chunks[0] and "без предмета" in chunks[0]
