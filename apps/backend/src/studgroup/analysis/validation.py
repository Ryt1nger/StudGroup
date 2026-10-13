"""Independent strict validation with per-proposal rejection outcomes."""

import json

from pydantic import ValidationError

from studgroup.analysis.contracts import (
    ANALYSIS_ENGINE_VERSION,
    ANALYSIS_SCHEMA_VERSION,
    ProposalSchemaIssue,
    ProposedAcademicItem,
    RejectedItem,
    ValidationResult,
)

MAX_SCHEMA_ISSUES = 12
_SAFE_PATH_SEGMENTS = frozenset(
    {
        "action",
        "existing_item_id",
        "expected_item_revision",
        "destination",
        "semantic_kind",
        "subject",
        "title",
        "description",
        "deadline",
        "at",
        "date_only",
        "basis",
        "audience",
        "scope",
        "label",
        "confidence",
        "attention_reasons",
        "evidence",
        "telegram_message_id",
        "revision",
        "fact_paths",
    }
)
_EXPECTED_BY_ERROR = {
    "missing": "required_field",
    "extra_forbidden": "field_not_allowed",
    "string_type": "string",
    "int_type": "integer",
    "int_parsing": "integer",
    "bool_type": "boolean",
    "bool_parsing": "boolean",
    "list_type": "array",
    "dict_type": "object",
    "model_type": "object",
    "literal_error": "allowed_literal",
    "datetime_type": "timezone_aware_datetime",
    "datetime_parsing": "timezone_aware_datetime",
    "uuid_type": "valid_identifier",
    "uuid_parsing": "valid_identifier",
}


def _safe_error_type(value):
    if not isinstance(value, str) or not value:
        return "unknown_validation_error"
    value = value.lower()
    allowed = "abcdefghijklmnopqrstuvwxyz0123456789_"
    if len(value) > 64 or value[0] not in allowed[:26] or any(c not in allowed for c in value):
        return "unknown_validation_error"
    return value


def _safe_received_type(value):
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, str):
        return "string"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, (list, tuple)):
        return "array"
    if isinstance(value, dict):
        return "object"
    return "other"


def _safe_path(proposal_ordinal, location):
    path = f"proposals[{proposal_ordinal}]"
    for segment in location:
        if isinstance(segment, int):
            path += f"[{segment}]" if 0 <= segment <= 99 else "[*]"
        elif isinstance(segment, str) and segment in _SAFE_PATH_SEGMENTS:
            path += f".{segment}"
        else:
            path += ".<extra>"
        if len(path) > 150:
            return f"proposals[{proposal_ordinal}].<extra>"
    return path


def _safe_schema_issues(error, proposal_ordinal):
    details = error.errors(include_url=False, include_context=False)
    issues = []
    for detail in details[:MAX_SCHEMA_ISSUES]:
        error_type = _safe_error_type(detail.get("type"))
        issues.append(
            ProposalSchemaIssue(
                path=_safe_path(proposal_ordinal, detail.get("loc", ())),
                error_type=error_type,
                expected=_EXPECTED_BY_ERROR.get(error_type, "valid_value"),
                received_type=(
                    "missing"
                    if error_type == "missing"
                    else _safe_received_type(detail.get("input"))
                ),
            )
        )
    return issues, len(details) > MAX_SCHEMA_ISSUES


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
    accepted_ordinals = []
    rejected = []
    for ordinal, item in enumerate(raw[:40]):
        try:
            proposal = ProposedAcademicItem.model_validate(item)
        except ValidationError as error:
            issues, truncated = _safe_schema_issues(error, ordinal)
            rejected.append(
                RejectedItem(
                    proposal_ordinal=ordinal,
                    reason_code="proposal_schema_invalid",
                    schema_issues=issues,
                    schema_issues_truncated=truncated,
                )
            )
            continue
        except (TypeError, ValueError):
            rejected.append(
                RejectedItem(
                    proposal_ordinal=ordinal,
                    reason_code="proposal_schema_invalid",
                    schema_issues=[
                        ProposalSchemaIssue(
                            path=f"proposals[{ordinal}]",
                            error_type="model_type",
                            expected="object",
                            received_type=_safe_received_type(item),
                        )
                    ],
                )
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
        accepted_ordinals.append(ordinal)
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
        accepted_ordinals=accepted_ordinals,
        rejected=rejected,
        raw_proposal_count=len(raw),
        explicit_empty=explicit_empty,
    )
