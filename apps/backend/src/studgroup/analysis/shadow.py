"""No-publication C+ execution and bounded evaluation persistence."""

import hashlib
import json
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from time import monotonic

from studgroup.ai import ProviderFailure
from studgroup.analysis.context_strategy import (
    prepare_context_request,
    validate_context,
)
from studgroup.analysis.extraction_strategy import (
    prepare_extraction_request,
    validate_extraction,
)
from studgroup.analysis.provider import ProviderUsage
from studgroup.models import ShadowEvaluationRun


@dataclass(frozen=True)
class ShadowResult:
    context: object
    validation: object
    prompt_tokens: int
    completion_tokens: int
    prompt_cache_hit_tokens: int
    latency_ms: int
    provider_payload_hashes: tuple[str, str]


class ShadowPassFailure(Exception):
    """Sanitized failure plus any usage already reported by completed passes."""

    def __init__(
        self,
        code,
        retryable,
        *,
        usage=None,
        hashes=(),
        raw_results=(),
        usage_complete=False,
        reservation_releasable=False,
    ):
        self.code = code
        self.retryable = retryable
        self.usage = usage or ProviderUsage(prompt_tokens=0, completion_tokens=0)
        self.hashes = tuple(hashes)
        self.raw_results = tuple(raw_results)
        self.usage_complete = usage_complete
        self.reservation_releasable = reservation_releasable
        super().__init__(code)


def _sum_usage(*rows):
    return ProviderUsage(
        prompt_tokens=sum(row.prompt_tokens for row in rows if row is not None),
        completion_tokens=sum(row.completion_tokens for row in rows if row is not None),
        prompt_cache_hit_tokens=sum(
            row.prompt_cache_hit_tokens for row in rows if row is not None
        ),
    )


def source_snapshot_sha256(analysis_input):
    payload = analysis_input.model_dump(mode="json")
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


async def run_shadow(provider, analysis_input, memory_context):
    started = monotonic()
    try:
        context_response = await provider.complete(
            prepare_context_request(analysis_input, memory_context)
        )
    except ProviderFailure as error:
        raise ShadowPassFailure(
            f"context_{error.code}",
            error.retryable,
            usage=error.usage,
            usage_complete=error.usage is not None or error.reservation_releasable,
            reservation_releasable=error.reservation_releasable,
        ) from None
    try:
        context = validate_context(context_response.content, analysis_input)
    except (ValueError, TypeError):
        raise ShadowPassFailure(
            "invalid_shadow_context",
            False,
            usage=context_response.usage,
            hashes=(context_response.payload_sha256,),
            raw_results=(context_response.content,),
            usage_complete=True,
        ) from None
    try:
        extraction_response = await provider.complete(
            prepare_extraction_request(
                analysis_input,
                context.model_dump(mode="json"),
            )
        )
    except ProviderFailure as error:
        raise ShadowPassFailure(
            f"extraction_{error.code}",
            error.retryable,
            usage=_sum_usage(context_response.usage, error.usage),
            hashes=(context_response.payload_sha256,),
            raw_results=(context_response.content,),
            usage_complete=error.usage is not None or error.reservation_releasable,
            reservation_releasable=error.reservation_releasable,
        ) from None
    try:
        validation = validate_extraction(extraction_response.content, analysis_input)
    except (ValueError, TypeError):
        raise ShadowPassFailure(
            "invalid_shadow_extraction",
            False,
            usage=_sum_usage(context_response.usage, extraction_response.usage),
            hashes=(context_response.payload_sha256, extraction_response.payload_sha256),
            raw_results=(context_response.content, extraction_response.content),
            usage_complete=True,
        ) from None
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
        prompt_cache_hit_tokens=(
            context_response.usage.prompt_cache_hit_tokens
            + extraction_response.usage.prompt_cache_hit_tokens
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
        raw_message_id=None,
        analysis_generation=None,
        source_snapshot_sha256=source_snapshot_sha256(analysis_input),
        engine_version=analysis_input.engine_version,
        state="completed",
        source_revision=analysis_input.source_revision,
        input_json=json.dumps(
            analysis_input.model_dump(mode="json"), ensure_ascii=False, sort_keys=True
        ),
        memory_json="[]",
        context_result_json=result.context.model_dump_json(),
        validation_result_json=result.validation.model_dump_json(),
        provider_payload_hashes=json.dumps(result.provider_payload_hashes),
        metrics_json=json.dumps(metrics, ensure_ascii=False, sort_keys=True),
        attempts=1,
        available_at=now,
        prompt_tokens=result.prompt_tokens,
        completion_tokens=result.completion_tokens,
        prompt_cache_hit_tokens=result.prompt_cache_hit_tokens,
        reserved_usd=Decimal(0),
        charged_usd=Decimal(charged_usd),
        latency_ms=result.latency_ms,
        created_at=now,
        completed_at=now,
        delete_at=now + timedelta(days=30),
    )
    db.add(row)
    return row
