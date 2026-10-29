"""Adapter -> strict validation for Academic Extraction output, with per-proposal outcomes.

``validation.validate_provider_content`` stays the strict, adapter-free reference gate. This
module runs the deterministic adapter first, then the very same strict Pydantic models, so
nothing the adapter produces can bypass validation.
"""

import json
import re
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


def _mutation_has_target_novelty(proposal, analysis_input, existing_item_snapshots):
    """Whether a mutation is grounded in new target evidence, not only old context.

    This is a publication invariant, independent of the prompt. It deliberately uses the generic
    fact-difference checker rather than a list of conversational keywords. A one-word reply may
    still carry a cancel/update when it directly replies to a source already attached to the item.
    """
    # Old frozen shadow/evaluation inputs did not persist the item snapshots required to prove a
    # delta. The live package writer always supplies the list (including an empty one); historical
    # replay remains parse-compatible and publication continues to have its own resolver guards.
    if existing_item_snapshots is None:
        return True
    target_ids = set(analysis_input.target_message_ids)
    cited_targets = target_ids & {ref.telegram_message_id for ref in proposal.evidence}
    if not cited_targets:
        return False
    if proposal.action == "create":
        target_text = "\n".join(
            part
            for row in analysis_input.messages
            if row.telegram_message_id in cited_targets
            for part in [row.text, *row.image_facts]
            if part
        ).strip()
        from studgroup.analysis.card_editor import supplement_adds_information

        return supplement_adds_information("", target_text)
    snapshot = (existing_item_snapshots or {}).get(proposal.existing_item_id, {})
    if not snapshot:
        return False

    from studgroup.analysis.card_editor import normalized, supplement_adds_information

    by_id = {row.telegram_message_id: row for row in analysis_input.messages}
    known = "\n".join(
        str(snapshot.get(key) or "")
        for key in ("subject", "title", "description", "deadline_at")
    )
    target_text = "\n".join(
        part
        for message_id in cited_targets
        for part in [by_id[message_id].text, *by_id[message_id].image_facts]
        if part
    ).strip()
    # A terse cancellation contains no new exercise/date and must not depend on word count.
    # Require both explicit cancellation and a direct reply to this item's persisted source.
    # "Thanks" replying to a card cannot stand in for cancellation evidence.
    if proposal.action == "cancel" and any(
        by_id[message_id].reply_to_message_id in set(snapshot.get("source_message_ids", []))
        and re.fullmatch(
            r"(?:отмена|отменено|отменяется|не надо(?: делать)?|делать не надо)[.!\s]*",
            normalized(by_id[message_id].text),
        )
        for message_id in cited_targets
    ):
        return True
    if not supplement_adds_information(known, target_text):
        return False
    if proposal.action in {"cancel", "reschedule"}:
        return True

    proposed = "\n".join(
        str(value or "")
        for value in (
            proposal.subject,
            proposal.title,
            proposal.description,
            proposal.deadline.at.isoformat() if proposal.deadline.at else None,
        )
    )
    # Publication requires a content-bearing overlap between what changed and what the targets
    # actually say. This is language-agnostic provenance matching, not a conversational keyword
    # allow/deny list. Numbers are kept whole; words use the same conservative stems as the card
    # editor. Old facts may remain in a merged description without being repeated by the target.
    tokens = lambda value: set(re.findall(r"\d+|[a-zа-яё]{4,}", normalized(value)))
    changed = tokens(proposed) - tokens(known)
    removed = tokens(known) - tokens(proposed)
    supported = tokens(target_text)
    if (changed | removed) & supported:
        return True
    # Image facts have already passed their typed provenance validator. A target image may add a
    # fact whose wording is normalized differently by the editor, so do not require stem identity.
    return any(by_id[message_id].image_facts for message_id in cited_targets)


def validate_single(
    ordinal,
    raw,
    analysis_input,
    *,
    existing_items=None,
    existing_item_snapshots=None,
):
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
    if not _mutation_has_target_novelty(
        proposal, analysis_input, existing_item_snapshots
    ):
        return (
            None,
            RejectedItem(
                proposal_ordinal=ordinal,
                reason_code="mutation_not_supported_by_target",
            ),
            False,
            adapted.actions,
        )
    from studgroup.analysis.image_evidence import proposal_image_problem

    image_problem = proposal_image_problem(proposal, analysis_input)
    if image_problem:
        return (
            None,
            RejectedItem(proposal_ordinal=ordinal, reason_code=image_problem),
            False,
            adapted.actions,
        )
    if analysis_input.image_evidence:
        # Persist provenance of the explicitly activated semantic path. Student display must
        # never change just because shadow jobs happen to exist for an older published card.
        first = proposal.evidence[0]
        paths = list(dict.fromkeys([*first.fact_paths, "image_analysis"]))
        if len(paths) > 20:
            return (
                None,
                RejectedItem(proposal_ordinal=ordinal, reason_code="image_evidence_overflow"),
                False,
                adapted.actions,
            )
        proposal = proposal.model_copy(
            update={
                "evidence": [first.model_copy(update={"fact_paths": paths}), *proposal.evidence[1:]]
            }
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
    content,
    analysis_input: AnalysisInput,
    *,
    existing_items=None,
    existing_item_snapshots=None,
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
            ordinal,
            item,
            analysis_input,
            existing_items=existing_items,
            existing_item_snapshots=existing_item_snapshots,
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
    if analysis_input.image_evidence:
        # Reconciliation can add album sources. Recheck the final sources before publication.
        from studgroup.analysis.image_evidence import proposal_image_problem

        kept, kept_ordinals = [], []
        rejected_after = list(result.rejected)
        held_after = list(result.candidates)
        for ordinal, proposal in zip(result.accepted_ordinals, result.accepted):
            problem = proposal_image_problem(proposal, analysis_input)
            if problem is None:
                kept.append(proposal)
                kept_ordinals.append(ordinal)
                continue
            rejected_after.append(RejectedItem(proposal_ordinal=ordinal, reason_code=problem))
            item = salvage_rejected(ordinal, raw[ordinal], problem, [], analysis_input, actions)
            if item is not None:
                held_after.append(item)
        result = result.model_copy(
            update={
                "accepted": kept,
                "accepted_ordinals": kept_ordinals,
                "rejected": rejected_after,
                "candidates": held_after[:MAX_CANDIDATES],
            }
        )
    return AdaptedValidation(result, candidates)
