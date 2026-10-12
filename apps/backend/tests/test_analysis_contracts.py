import asyncio
import json
from datetime import UTC, datetime
from uuid import UUID

import httpx
import pytest

from studgroup.ai import (
    BatchExtraction,
    BatchResult,
    DeepSeekProvider,
    SourcedExtraction,
    TokenUsage,
)
from studgroup.analysis.contracts import (
    ANALYSIS_ENGINE_VERSION,
    ANALYSIS_SCHEMA_VERSION,
    AnalysisInput,
    SourceAttachment,
    SourceMessage,
)
from studgroup.analysis.legacy_strategy import LegacyStrategy
from studgroup.analysis.provider import PreparedRequest
from studgroup.analysis.replay import ReplayEnvelope, ReplayMode, replay
from studgroup.analysis.validation import validate_provider_content

GROUP_ID = UUID("00000000-0000-0000-0000-000000000001")


def analysis_input():
    return AnalysisInput(
        group_id=GROUP_ID,
        group_timezone="Europe/Moscow",
        source_revision=3,
        target_message_ids=[10],
        messages=[
            SourceMessage(
                telegram_message_id=10,
                revision=3,
                message_date=datetime(2026, 10, 12, 9, tzinfo=UTC),
                text="ДЗ: номера 1–3",
            )
        ],
    )


def proposal(**patch):
    return {
        "destination": "task",
        "semantic_kind": "homework",
        "subject": "Математика",
        "title": "Решить задачи 1–3",
        "description": "Решить номера 1–3.",
        "deadline": {"at": None, "date_only": False, "basis": "unknown"},
        "audience": {"scope": "group", "label": None},
        "confidence": 90,
        "attention_reasons": [],
        "evidence": [
            {
                "telegram_message_id": 10,
                "revision": 3,
                "fact_paths": ["description"],
            }
        ],
    } | patch


def output_payload(items):
    return {
        "schema_version": ANALYSIS_SCHEMA_VERSION,
        "engine_version": ANALYSIS_ENGINE_VERSION,
        "source_revision": 3,
        "proposals": items,
        "explicit_empty": not items,
    }


def test_contract_rejects_target_outside_versioned_source_envelope():
    payload = analysis_input().model_dump()
    payload["target_message_ids"] = [999]
    with pytest.raises(ValueError, match="target_outside_source_envelope"):
        AnalysisInput.model_validate(payload)


def test_contract_accepts_media_only_source_without_invented_text():
    source = SourceMessage(
        telegram_message_id=11,
        revision=1,
        message_date=datetime(2026, 10, 12, 9, tzinfo=UTC),
        text="",
        attachments=[SourceAttachment(kind="photo")],
    )
    assert source.text == ""
    assert source.attachments[0].processing_state == "metadata_only"


def test_validation_preserves_valid_sibling_and_rejects_bad_evidence():
    invalid = proposal(
        evidence=[
            {
                "telegram_message_id": 999,
                "revision": 1,
                "fact_paths": ["description"],
            }
        ]
    )
    result = validate_provider_content(
        json.dumps(output_payload([invalid, proposal()])),
        allowed_sources={10: 3},
        source_revision=3,
    )
    assert len(result.accepted) == 1
    assert result.raw_proposal_count == 2
    assert result.rejected[0].proposal_ordinal == 0
    assert result.rejected[0].reason_code == "evidence_outside_envelope"


def test_valid_empty_is_distinct_from_all_rejected():
    empty = validate_provider_content(
        json.dumps(output_payload([])), allowed_sources={10: 3}, source_revision=3
    )
    rejected = validate_provider_content(
        json.dumps(output_payload([proposal(confidence=101)])),
        allowed_sources={10: 3},
        source_revision=3,
    )
    assert empty.explicit_empty and empty.raw_proposal_count == 0
    assert not rejected.explicit_empty and rejected.raw_proposal_count == 1
    assert rejected.rejected[0].reason_code == "proposal_schema_invalid"


def test_replay_is_deterministic_and_has_no_side_effect_capability():
    envelope = ReplayEnvelope(
        analysis_input=analysis_input(),
        provider_content=json.dumps(output_payload([proposal()]), sort_keys=True),
        prompt_version="test-1",
    )
    first = asyncio.run(replay(envelope, ReplayMode.DRY_RUN))
    second = asyncio.run(replay(envelope, ReplayMode.DRY_RUN))
    assert first == second
    assert first.replay_key == envelope.replay_key
    assert first.product_writes == first.notifications == first.network_calls == 0
    assert first.application_outcomes[0].application_status == "shadow_only"


def test_schema_revision_mismatch_fails_before_item_validation():
    payload = output_payload([proposal()])
    payload["source_revision"] = 2
    with pytest.raises(ValueError, match="source_revision_mismatch"):
        validate_provider_content(
            json.dumps(payload), allowed_sources={10: 3}, source_revision=3
        )


def test_legacy_strategy_adapts_without_product_writes():
    item = SourcedExtraction(
        kind="homework",
        subject="Математика",
        title="Решить задачи 1–3",
        description="Решить номера 1–3.",
        deadline_at=None,
        deadline_date_only=False,
        urgency="normal",
        confidence=90,
        source_message_ids=[10],
    )

    class Provider:
        async def extract_batch(self, messages, timezone, schedule, target_message_id):
            assert target_message_id == 10
            return BatchResult(
                batch=BatchExtraction(assignments=[item]),
                usage=TokenUsage(prompt_tokens=1, completion_tokens=1),
                model="fake",
            )

    result = asyncio.run(LegacyStrategy(Provider()).analyze(analysis_input()))
    assert result.source_revision == 3
    assert result.proposals[0].destination == "task"
    assert result.proposals[0].evidence[0].revision == 3


def test_deepseek_adapter_implements_transport_only_provider_port():
    def handler(request):
        payload = json.loads(request.content)
        assert payload["messages"][1]["content"] == '{"value": 1}'
        return httpx.Response(
            200,
            json={
                "model": "deepseek-flash",
                "choices": [
                    {"finish_reason": "stop", "message": {"content": '{"ok":true}'}}
                ],
                "usage": {
                    "prompt_tokens": 2,
                    "completion_tokens": 1,
                    "prompt_cache_hit_tokens": 0,
                },
            },
        )

    provider = DeepSeekProvider("test", transport=httpx.MockTransport(handler))
    result = asyncio.run(
        provider.complete(
            PreparedRequest(
                system_prompt="Return JSON.",
                user_payload={"value": 1},
                schema_version=ANALYSIS_SCHEMA_VERSION,
                prompt_version="test-1",
                max_output_tokens=10,
            )
        )
    )
    assert result.content == '{"ok":true}'
    assert result.usage.prompt_tokens == 2
    assert len(result.payload_sha256) == 64
