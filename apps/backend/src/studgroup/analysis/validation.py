"""Independent strict validation with per-proposal rejection outcomes."""

import json

from pydantic import ValidationError

from studgroup.analysis.contracts import (
    ANALYSIS_ENGINE_VERSION,
    ANALYSIS_SCHEMA_VERSION,
    ProposedAcademicItem,
    RejectedItem,
    ValidationResult,
)


def validate_provider_content(
    content: str,
    *,
    allowed_sources: dict[int, int],
    source_revision: int,
) -> ValidationResult:
    try:
        payload = json.loads(content)
    except (TypeError, ValueError):
        raise ValueError("unparseable_provider_json") from None
    if not isinstance(payload, dict):
        raise TypeError("provider_output_not_object")
    if payload.get("schema_version") != ANALYSIS_SCHEMA_VERSION:
        raise ValueError("schema_version_mismatch")
    if payload.get("engine_version") != ANALYSIS_ENGINE_VERSION:
        raise ValueError("engine_version_mismatch")
    if payload.get("source_revision") != source_revision:
        raise ValueError("source_revision_mismatch")
    raw = payload.get("proposals")
    if not isinstance(raw, list):
        raise TypeError("proposal_list_required")

    accepted = []
    rejected = []
    for ordinal, item in enumerate(raw[:40]):
        try:
            proposal = ProposedAcademicItem.model_validate(item)
        except (TypeError, ValueError, ValidationError):
            rejected.append(
                RejectedItem(proposal_ordinal=ordinal, reason_code="proposal_schema_invalid")
            )
            continue
        if any(
            evidence.telegram_message_id not in allowed_sources
            or allowed_sources[evidence.telegram_message_id] != evidence.revision
            for evidence in proposal.evidence
        ):
            rejected.append(
                RejectedItem(proposal_ordinal=ordinal, reason_code="evidence_outside_envelope")
            )
            continue
        accepted.append(proposal)
    rejected.extend(
        RejectedItem(proposal_ordinal=ordinal, reason_code="proposal_overflow")
        for ordinal in range(40, len(raw))
    )
    explicit_empty = payload.get("explicit_empty")
    if not isinstance(explicit_empty, bool) or explicit_empty != (not raw):
        raise ValueError("explicit_empty_mismatch")
    return ValidationResult(
        source_revision=source_revision,
        accepted=accepted,
        rejected=rejected,
        raw_proposal_count=len(raw),
        explicit_empty=explicit_empty,
    )
