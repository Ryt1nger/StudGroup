"""Adapter -> strict validation for Academic Extraction output, with per-proposal outcomes.

``validation.validate_provider_content`` stays the strict, adapter-free reference gate. This
module runs the deterministic adapter first, then the very same strict Pydantic models, so
nothing the adapter produces can bypass validation.
"""

import json
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from studgroup.analysis.candidates import MAX_CANDIDATES, parse_model_candidates, salvage_rejected
from studgroup.analysis.contracts import (
    ANALYSIS_ENGINE_VERSION,
    ANALYSIS_SCHEMA_VERSION,
    NON_REPAIRABLE_RULE_CODES,
    AnalysisInput,
    ProposalSchemaIssue,
    ProposedAcademicItem,
    RejectedItem,
    ValidationResult,
)
from studgroup.analysis.linking import reconcile_validation
from studgroup.analysis.output_adapter import adapt_proposal
from studgroup.analysis.subject_catalog import usable_catalog
from studgroup.analysis.validation import _safe_schema_issues

MAX_PROPOSALS = 40


@dataclass
class RepairCandidate:
    """In-memory only: the raw provider item and value-free reasons it was rejected."""

    ordinal: int
    raw: Any = field(repr=False)
    issues: list[ProposalSchemaIssue]


@dataclass
class AdaptedValidation:
    result: ValidationResult
    candidates: list[RepairCandidate]


def source_maps(analysis_input: AnalysisInput):
    messages = {row.telegram_message_id: row for row in analysis_input.messages}
    allowed = {row.telegram_message_id: row.revision for row in analysis_input.messages}
    return messages, allowed


def validate_single(ordinal, raw, analysis_input, *, existing_items=None):
    """Return (accepted_item|None, rejected|None, repairable, actions)."""
    messages, allowed = source_maps(analysis_input)
    catalog, _why = usable_catalog(analysis_input.subject_catalog, analysis_input.group_id)
    adapted = adapt_proposal(
        ordinal,
        raw,
        group_timezone=analysis_input.group_timezone,
        messages=messages,
        allowed_sources=allowed,
        existing_items=existing_items,
        subject_catalog=catalog,
    )
    if not adapted.ok:
        rejected = RejectedItem(
            proposal_ordinal=ordinal,
            reason_code="proposal_schema_invalid",
            schema_issues=adapted.issues,
        )
        return None, rejected, adapted.repairable, adapted.actions
    try:
        proposal = ProposedAcademicItem.model_validate(adapted.value)
    except ValidationError as error:
        issues, truncated = _safe_schema_issues(error, ordinal)
        repairable = not any(issue.error_type in NON_REPAIRABLE_RULE_CODES for issue in issues)
        rejected = RejectedItem(
            proposal_ordinal=ordinal,
            reason_code="proposal_schema_invalid",
            schema_issues=issues,
            schema_issues_truncated=truncated,
        )
        return None, rejected, repairable, adapted.actions
    if any(
        evidence.telegram_message_id not in allowed
        or allowed[evidence.telegram_message_id] != evidence.revision
        for evidence in proposal.evidence
    ):
        return (
            None,
            RejectedItem(proposal_ordinal=ordinal, reason_code="evidence_outside_envelope"),
            False,
            adapted.actions,
        )
    return proposal, None, False, adapted.actions


def parse_envelope(content, analysis_input: AnalysisInput, actions: Counter):
    """Same fatal envelope errors as the strict path, except absent version fields."""
    try:
        payload = json.loads(content)
    except (TypeError, ValueError):
        raise ValueError("unparseable_provider_json") from None
    if not isinstance(payload, dict):
        raise TypeError("provider_output_not_object")
    for key, expected in (
        ("schema_version", ANALYSIS_SCHEMA_VERSION),
        ("engine_version", ANALYSIS_ENGINE_VERSION),
        ("source_revision", analysis_input.source_revision),
    ):
        if key not in payload:
            payload[key] = expected
            actions["envelope_field_copied_from_request"] += 1
    if payload["schema_version"] != ANALYSIS_SCHEMA_VERSION:
        raise ValueError("schema_version_mismatch")
    if payload["engine_version"] != ANALYSIS_ENGINE_VERSION:
        raise ValueError("engine_version_mismatch")
    if payload["source_revision"] != analysis_input.source_revision:
        raise ValueError("source_revision_mismatch")
    raw = payload.get("proposals")
    if not isinstance(raw, list):
        raise TypeError("proposal_list_required")
    return payload, raw


NOT_SALVAGED = {"duplicate_proposal", "proposal_overflow"}


def _held_candidates(payload, raw, rejected, accepted, analysis_input, actions):
    held = []
    for item in rejected:
        if item.reason_code in NOT_SALVAGED or item.proposal_ordinal >= len(raw):
            continue
        built = salvage_rejected(
            item.proposal_ordinal,
            raw[item.proposal_ordinal],
            item.schema_issues[0].error_type if item.schema_issues else item.reason_code,
            item.schema_issues,
            analysis_input,
            actions,
        )
        if built is not None:
            held.append(built)
    card_sources = [{ref.telegram_message_id for ref in card.evidence} for card in accepted]
    for candidate in parse_model_candidates(payload.get("candidates"), analysis_input, actions):
        cited = {ref.telegram_message_id for ref in candidate.evidence}
        # The same item reported twice (as a card and as a candidate) stays a card only.
        if candidate.category in {"homework", "check"} and any(
            cited <= ids for ids in card_sources
        ):
            actions["candidate_duplicates_card_discarded"] += 1
            continue
        held.append(candidate)
    return held[:MAX_CANDIDATES]


def adapt_and_validate(
    content, analysis_input: AnalysisInput, *, existing_items=None
) -> AdaptedValidation:
    actions: Counter = Counter()
    payload, raw = parse_envelope(content, analysis_input, actions)
    if analysis_input.subject_catalog is not None:
        _usable, why_not = usable_catalog(analysis_input.subject_catalog, analysis_input.group_id)
        if why_not:
            actions[f"subject_catalog_ignored_{why_not}"] += 1
    accepted, ordinals, rejected, candidates = [], [], [], []
    seen = set()
    for ordinal, item in enumerate(raw[:MAX_PROPOSALS]):
        proposal, reject, repairable, item_actions = validate_single(
            ordinal, item, analysis_input, existing_items=existing_items
        )
        actions.update(item_actions)
        if reject is not None:
            rejected.append(reject)
            if repairable:
                candidates.append(RepairCandidate(ordinal, item, reject.schema_issues))
            continue
        signature = proposal.model_dump_json()
        if signature in seen:
            rejected.append(
                RejectedItem(proposal_ordinal=ordinal, reason_code="duplicate_proposal")
            )
            continue
        seen.add(signature)
        accepted.append(proposal)
        ordinals.append(ordinal)
    rejected.extend(
        RejectedItem(proposal_ordinal=ordinal, reason_code="proposal_overflow")
        for ordinal in range(MAX_PROPOSALS, len(raw))
    )
    # Free discovery is kept apart from publication: what the strict contract rejected and what the
    # model itself flagged as "not a card" stay as stored, non-publishable candidates.
    held = _held_candidates(payload, raw, rejected, accepted, analysis_input, actions)
    rejected = [
        item.model_copy(update={"salvaged": True})
        if any(
            c.proposal_ordinal == item.proposal_ordinal
            for c in held
            if c.origin == "rejected_proposal"
        )
        else item
        for item in rejected
    ]
    # explicit_empty is fully derived from the proposal list; a wrong flag cannot create a card.
    declared = payload.get("explicit_empty")
    explicit_empty = not raw
    if declared != explicit_empty:
        actions["explicit_empty_derived"] += 1
    result = ValidationResult(
        source_revision=analysis_input.source_revision,
        accepted=accepted,
        accepted_ordinals=ordinals,
        rejected=rejected,
        raw_proposal_count=len(raw),
        explicit_empty=explicit_empty,
        adapter_actions=dict(sorted(actions.items())),
        candidates=held,
    )
    result = reconcile_validation(result, analysis_input.messages)
    return AdaptedValidation(result, candidates)
