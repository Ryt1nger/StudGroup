"""One effective view of a stored shadow result, shared by review, preview, promote and writer.

A stored ``ValidationResult`` was produced by the code that ran when the (paid) analysis
happened. Deterministic safeguards added later (audience of collective addresses, origin of an
"explicit" date, service tags) therefore have to be re-applied when the result is read, or old
proposals would flow into cards unchanged. Everything here is source-checked against the frozen
snapshot, idempotent, and never calls the AI.
"""

from collections import Counter

from studgroup.analysis.contracts import (
    Audience,
    DeadlineValue,
    ProposedAcademicItem,
)
from studgroup.analysis.linking import reconcile_validation
from studgroup.analysis.output_adapter import _Normalizer


def _renormalized(ordinal, proposal, messages, timezone, actions):
    by_id = {row.telegram_message_id: row for row in messages}
    normalizer = _Normalizer(
        ordinal,
        group_timezone=timezone,
        messages=by_id,
        allowed_sources={key: row.revision for key, row in by_id.items()},
        existing_items=None,
    )
    value = {
        "deadline": {
            "at": proposal.deadline.at,
            "date_only": proposal.deadline.date_only,
            "basis": proposal.deadline.basis,
        },
        "audience": {"scope": proposal.audience.scope, "label": proposal.audience.label},
        "subject": proposal.subject,
    }
    normalizer.semantic_rules(value, [ref.telegram_message_id for ref in proposal.evidence])
    actions.update(normalizer.actions)
    reasons = list(proposal.attention_reasons)
    for reason in normalizer.extra_reasons:
        if reason not in reasons:
            reasons.append(reason)
    if (
        value["deadline"] == {
            "at": proposal.deadline.at,
            "date_only": proposal.deadline.date_only,
            "basis": proposal.deadline.basis,
        }
        and value["audience"] == {
            "scope": proposal.audience.scope,
            "label": proposal.audience.label,
        }
        and value["subject"] == proposal.subject
        and reasons == proposal.attention_reasons
    ):
        return proposal
    return ProposedAcademicItem.model_validate(
        proposal.model_dump()
        | {
            "deadline": DeadlineValue(**value["deadline"]).model_dump(),
            "audience": Audience(**value["audience"]).model_dump(),
            "subject": value["subject"],
            "attention_reasons": reasons,
        }
    )


def effective_validation(validation, messages, timezone):
    """Stored proposals with today's deterministic rules and supplement links applied."""
    if not validation.accepted:
        return validation
    actions = Counter(validation.adapter_actions)
    accepted = [
        _renormalized(ordinal, proposal, messages, timezone, actions)
        for ordinal, proposal in zip(
            validation.accepted_ordinals, validation.accepted, strict=True
        )
    ]
    updated = validation.model_copy(
        update={"accepted": accepted, "adapter_actions": dict(sorted(actions.items()))}
    )
    return reconcile_validation(updated, messages)
