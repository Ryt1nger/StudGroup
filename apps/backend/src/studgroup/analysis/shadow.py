"""No-publication C+ execution and bounded evaluation persistence."""

import hashlib
import json
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from time import monotonic

from studgroup.analysis.context_strategy import (
    prepare_context_request,
    validate_context,
)
from studgroup.analysis.extraction_strategy import (
    prepare_extraction_request,
    validate_extraction,
)
from studgroup.models import ShadowEvaluationRun


@dataclass(frozen=True)
class ShadowResult:
    context: object
    validation: object
    prompt_tokens: int
    completion_tokens: int
    latency_ms: int
    provider_payload_hashes: tuple[str, str]


def source_snapshot_sha256(analysis_input):
    payload = analysis_input.model_dump(mode="json")
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


async def run_shadow(provider, analysis_input, memory_context):
    started = monotonic()
    context_response = await provider.complete(
        prepare_context_request(analysis_input, memory_context)
    )
    context = validate_context(context_response.content, analysis_input)
    extraction_response = await provider.complete(
        prepare_extraction_request(
            analysis_input,
            context.model_dump(mode="json"),
        )
    )
    validation = validate_extraction(extraction_response.content, analysis_input)
    return ShadowResult(
        context=context,
        validation=validation,
        prompt_tokens=(
            context_response.usage.prompt_tokens
            + extraction_response.usage.prompt_tokens
        ),
        completion_tokens=(
            context_response.usage.completion_tokens
            + extraction_response.usage.completion_tokens
        ),
        latency_ms=round((monotonic() - started) * 1000),
        provider_payload_hashes=(
            context_response.payload_sha256,
            extraction_response.payload_sha256,
        ),
    )


def persist_shadow_summary(
    db,
    *,
    group_id,
    batch_id,
    analysis_input,
    result,
    metrics,
    charged_usd,
    now,
):
    row = ShadowEvaluationRun(
        group_id=group_id,
        batch_id=batch_id,
        source_snapshot_sha256=source_snapshot_sha256(analysis_input),
        engine_version=analysis_input.engine_version,
        state="completed",
        metrics_json=json.dumps(metrics, ensure_ascii=False, sort_keys=True),
        prompt_tokens=result.prompt_tokens,
        completion_tokens=result.completion_tokens,
        charged_usd=Decimal(charged_usd),
        latency_ms=result.latency_ms,
        created_at=now,
        completed_at=now,
        delete_at=now + timedelta(days=30),
    )
    db.add(row)
    return row
