"""No-publication C+ execution and bounded evaluation persistence."""

import hashlib
import json
from collections import Counter
from dataclasses import dataclass, field
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
    validate_extraction_adapted,
)
from studgroup.analysis.provider import ProviderUsage
from studgroup.analysis.repair import apply_repair, repair_rejected
from studgroup.models import ShadowEvaluationRun

# One shadow package (both passes plus an optional repair) never exceeds this shared cap.
SHADOW_PACKAGE_CAP_USD = Decimal("0.04000000")


def usage_cost_usd(usage):
    hit = min(usage.prompt_cache_hit_tokens, usage.prompt_tokens)
    miss = usage.prompt_tokens - hit
    return (
        Decimal(miss) * Decimal("0.30")
        + Decimal(hit) * Decimal("0.006")
        + Decimal(usage.completion_tokens) * Decimal("1.20")
    ) / Decimal(1000000)


@dataclass(frozen=True)
class ShadowResult:
    context: object
    validation: object
    prompt_tokens: int
    completion_tokens: int
    prompt_cache_hit_tokens: int
    latency_ms: int
    provider_payload_hashes: tuple[str, ...]
    repair: dict = field(default_factory=dict)
    # Conservative charge for a repair call whose usage the provider did not report.
    extra_charge_usd: Decimal = Decimal(0)


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


def failure_stage(code):
    """Map stable reason codes to the owner-facing pipeline stage."""
    if code.startswith("preparation_") or code == "shadow_pipeline_error":
        return "context_preparation"
    if code.startswith("context_validation_") or code in {
        "invalid_shadow_context",
        "context_invalid_json",
        "context_output_not_object",
        "context_schema_invalid",
        "context_source_revision_mismatch",
        "context_source_outside_envelope",
        "context_evidence_outside_envelope",
    }:
        return "validation"
    if code.startswith("extraction_validation_") or code == "invalid_shadow_extraction":
        return "validation"
    if code.startswith("context_"):
        return "first_ai_pass"
    if code.startswith("extraction_"):
        return "second_ai_pass"
    if code.startswith("persistence_"):
        return "result_persistence"
    return "unknown"


def _sum_usage(*rows):
    return ProviderUsage(
        prompt_tokens=sum(row.prompt_tokens for row in rows if row is not None),
        completion_tokens=sum(row.completion_tokens for row in rows if row is not None),
        prompt_cache_hit_tokens=sum(row.prompt_cache_hit_tokens for row in rows if row is not None),
    )


def source_snapshot_sha256(analysis_input):
    # The subject catalog is reference data, not source: the snapshot hash stays what it was.
    payload = analysis_input.model_dump(mode="json", exclude={"subject_catalog"})
    if not payload["image_evidence"]:
        payload.pop("image_evidence")
    for message in payload["messages"]:
        if not message["image_facts"]:
            message.pop("image_facts")
        for attachment in message["attachments"]:
            for key in ("attachment_id", "pixel_sha256", "visual_hash", "content_signature",
                        "image_analysis_state", "semantic_types", "width", "height"):
                if not attachment.get(key):
                    attachment.pop(key, None)
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()


async def run_shadow(
    provider,
    analysis_input,
    memory_context,
    *,
    repair_enabled=False,
    existing_items=None,
    lenient_context=False,
):
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
        context = validate_context(
            context_response.content, analysis_input, lenient=lenient_context
        )
    except (ValueError, TypeError) as error:
        code = str(error)
        if code not in {
            "context_invalid_json",
            "context_output_not_object",
            "context_schema_invalid",
            "context_source_revision_mismatch",
            "context_source_outside_envelope",
            "context_evidence_outside_envelope",
        }:
            code = "context_validation_invalid"
        raise ShadowPassFailure(
            code,
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
                existing_items,
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
        adapted = validate_extraction_adapted(
            extraction_response.content, analysis_input, existing_items=existing_items
        )
        validation = adapted.result
    except (ValueError, TypeError) as error:
        code = str(error)
        if code not in {
            "unparseable_provider_json",
            "provider_output_not_object",
            "schema_version_mismatch",
            "engine_version_mismatch",
            "source_revision_mismatch",
            "proposal_list_required",
            "explicit_empty_mismatch",
        }:
            code = "invalid"
        raise ShadowPassFailure(
            f"extraction_validation_{code}",
            False,
            usage=_sum_usage(context_response.usage, extraction_response.usage),
            hashes=(context_response.payload_sha256, extraction_response.payload_sha256),
            raw_results=(context_response.content, extraction_response.content),
            usage_complete=True,
        ) from None
    usage = _sum_usage(context_response.usage, extraction_response.usage)
    hashes = [context_response.payload_sha256, extraction_response.payload_sha256]
    repair_summary = {"enabled": bool(repair_enabled), "candidates": len(adapted.candidates)}
    extra_charge = Decimal(0)
    if repair_enabled and adapted.candidates:
        try:
            repair = await repair_rejected(
                provider,
                analysis_input,
                adapted.candidates,
                budget_remaining_usd=SHADOW_PACKAGE_CAP_USD - usage_cost_usd(usage),
                cost_fn=usage_cost_usd,
                existing_items=_trusted(existing_items),
            )
        except Exception as error:  # noqa: BLE001 - repair is optional; pass-2 results survive
            repair = None
            repair_summary["error"] = "repair_internal_error"
            repair_summary["error_type"] = type(error).__name__
        if repair is not None:
            validation = apply_repair(validation, repair)
            repair_summary.update(
                attempted=repair.attempted,
                applied=len(repair.repaired),
                outcomes=dict(sorted(Counter(repair.outcomes.values()).items())),
            )
            if repair.skipped_reason:
                repair_summary["skipped"] = repair.skipped_reason
            if repair.error_code:
                repair_summary["error"] = repair.error_code
            if repair.usage is not None:
                usage = _sum_usage(usage, repair.usage)
                repair_summary["prompt_tokens"] = repair.usage.prompt_tokens
                repair_summary["completion_tokens"] = repair.usage.completion_tokens
            elif repair.unreported_usage:
                extra_charge = repair.estimated_cost_usd
            if repair.payload_sha256:
                hashes.append(repair.payload_sha256)
    return ShadowResult(
        context=context,
        validation=validation,
        prompt_tokens=usage.prompt_tokens,
        completion_tokens=usage.completion_tokens,
        prompt_cache_hit_tokens=usage.prompt_cache_hit_tokens,
        latency_ms=round((monotonic() - started) * 1000),
        provider_payload_hashes=tuple(hashes),
        repair=repair_summary,
        extra_charge_usd=extra_charge,
    )


def _trusted(existing_items):
    if existing_items is None:
        return None
    from uuid import UUID

    return {UUID(str(item["id"])): int(item["revision"]) for item in existing_items}


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
