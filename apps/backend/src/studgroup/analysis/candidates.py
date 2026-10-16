"""Non-publishable candidates: free discovery kept apart from the publication decision.

The model reports publishable items as ``proposals`` (homework -> task, a check of knowledge ->
deadline) and everything else it finds as ``candidates``: events, organisational instructions,
other useful information, items it cannot confidently categorise. This module also *salvages* a
proposal the strict contract rejected (an unknown kind, a malformed field) instead of dropping its
content: the readable parts are kept as an ``UnpublishedCandidate``.

Nothing here is applied, projected or shown to students. No provider call happens here, and the
module never decides a category from message keywords: the category is the model's own, and the
label vocabulary below only labels what the model already said.
"""

import re

from studgroup.analysis.contracts import (
    AnalysisInput,
    Audience,
    EvidenceRef,
    ProposalSchemaIssue,
    UnpublishedCandidate,
)
from studgroup.analysis.text_hygiene import clean_text

MAX_CANDIDATES = 40
CATEGORIES = frozenset({"homework", "check", "event", "organizational", "information", "other"})
# How a label the model itself used (a semantic_kind it invented, a category spelling) is shown.
# It labels the model's own words; it never classifies message text.
LABEL_CATEGORY = (
    ("check", re.compile(r"test|quiz|exam|credit|control|check|assess|kt|колл?оквиум|зач[её]т")),
    ("event", re.compile(r"event|meeting|seminar|excursion|contest|competition|party|мероприят")),
    ("organizational", re.compile(r"organi[sz]|admin|duty|logistic|орг")),
    ("information", re.compile(r"info|news|announce|notice")),
    ("homework", re.compile(r"homework|essay|project|report|assignment|task|lab|presentation")),
)
DESTINATION_CATEGORY = {"task": "homework", "deadline": "check"}


def _text(value, limit, *, noisy=False):
    if not isinstance(value, str):
        return None
    cleaned = clean_text(value) if noisy else ""
    plain = " ".join(value.split())
    return (cleaned.strip() or plain)[:limit] or None


def label_category(label):
    if not isinstance(label, str):
        return "other"
    folded = "_".join(label.strip().casefold().replace("-", " ").split())
    if folded in CATEGORIES:
        return folded
    for category, pattern in LABEL_CATEGORY:
        if pattern.search(folded):
            return category
    return "other"


def _clean_label(label):
    if not isinstance(label, str):
        return None
    cleaned = re.sub(r"[^\w .:-]", "", label, flags=re.UNICODE).strip()
    return cleaned[:64] or None


def _evidence(raw, analysis_input: AnalysisInput, reasons):
    """Valid cited messages only; otherwise the run's target message (never an invented source)."""
    allowed = {row.telegram_message_id: row.revision for row in analysis_input.messages}
    refs, seen = [], set()
    if isinstance(raw, list):
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            message_id = entry.get("telegram_message_id", entry.get("message_id"))
            if isinstance(message_id, bool) or not isinstance(message_id, int):
                continue
            if message_id not in allowed or message_id in seen:
                continue
            seen.add(message_id)
            refs.append(
                EvidenceRef(
                    telegram_message_id=message_id,
                    revision=allowed[message_id],
                    fact_paths=["description"],
                )
            )
    if refs:
        return refs
    reasons.append("evidence_defaulted_to_target")
    return [
        EvidenceRef(
            telegram_message_id=message_id,
            revision=allowed[message_id],
            fact_paths=["description"],
        )
        for message_id in analysis_input.target_message_ids
        if message_id in allowed
    ]


def _audience(raw, reasons):
    try:
        return Audience.model_validate(raw) if isinstance(raw, dict) else Audience()
    except ValueError:
        reasons.append("audience_unclear")
        return Audience(scope="unknown")


def _confidence(raw):
    if isinstance(raw, bool) or not isinstance(raw, int | float):
        return 50
    value = raw * 100 if 0 < raw <= 1 and isinstance(raw, float) else raw
    return max(0, min(100, int(value)))


def _reasons(raw):
    values = raw if isinstance(raw, list) else []
    return [v for v in values if isinstance(v, str) and re.fullmatch(r"[a-z][a-z0-9_]{0,63}", v)][
        :10
    ]


def _build(raw, analysis_input, *, category, hold_code, origin, ordinal=None, issues=(), label=None):
    reasons = _reasons(raw.get("attention_reasons"))
    title = _text(raw.get("title"), 255, noisy=True)
    description = _text(raw.get("description"), 8000, noisy=True)
    if not title and not description:
        return None
    evidence = _evidence(raw.get("evidence"), analysis_input, reasons)
    if not evidence:
        return None
    return UnpublishedCandidate(
        category=category,
        title=title,
        description=description,
        subject=_text(raw.get("subject"), 255),
        when_text=_text(raw.get("when_text") or raw.get("when"), 255),
        reason=_text(raw.get("reason"), 300),
        audience=_audience(raw.get("audience"), reasons),
        confidence=_confidence(raw.get("confidence")),
        evidence=evidence,
        hold_code=hold_code,
        origin=origin,
        model_label=label,
        proposal_ordinal=ordinal,
        issues=list(issues)[:12],
        attention_reasons=list(dict.fromkeys(reasons)),
    )


def parse_model_candidates(raw_list, analysis_input, actions):
    """Candidates the model reported itself. Invalid entries are counted, never raised."""
    if raw_list is None:
        return []
    if not isinstance(raw_list, list):
        actions["candidates_not_list"] += 1
        return []
    out = []
    for entry in raw_list[:MAX_CANDIDATES]:
        if not isinstance(entry, dict):
            actions["candidate_not_object_discarded"] += 1
            continue
        category = label_category(entry.get("category"))
        built = _build(
            entry,
            analysis_input,
            category=category,
            hold_code=(
                "doubtful_candidate" if category in {"homework", "check"} else "category_not_published"
            ),
            origin="model_candidate",
            label=_clean_label(entry.get("category")) if entry.get("category") not in CATEGORIES else None,
        )
        if built is None:
            actions["candidate_without_content_discarded"] += 1
            continue
        actions["candidate_from_model"] += 1
        out.append(built)
    return out


def salvage_rejected(ordinal, raw, reason_code, issues: list[ProposalSchemaIssue], analysis_input, actions):
    """Keep the readable content of a rejected proposal as a non-publishable candidate."""
    if not isinstance(raw, dict):
        actions["rejected_without_content"] += 1
        return None
    label = raw.get("semantic_kind")
    category = (
        label_category(label)
        if isinstance(label, str)
        else DESTINATION_CATEGORY.get(raw.get("destination"), "other")
    )
    built = _build(
        raw,
        analysis_input,
        category=category,
        hold_code=f"rejected_{reason_code}"[:64],
        origin="rejected_proposal",
        ordinal=ordinal,
        issues=issues,
        label=_clean_label(label),
    )
    if built is None:
        actions["rejected_without_content"] += 1
        return None
    actions["candidate_salvaged_from_rejection"] += 1
    return built
