"""Transactional create/update/cancel resolver with explicit identity and locks."""

import json
from dataclasses import dataclass

from sqlalchemy import select

from studgroup.analysis.card_editor import DESCRIPTION_ATTACHMENT_ONLY, DESCRIPTION_PENDING
from studgroup.analysis.deadline_updates import apply_field_locks
from studgroup.analysis.entities import item_snapshot
from studgroup.analysis.evidence import add_item_evidence, resolve_evidence
from studgroup.analysis.title_builder import INSUFFICIENT
from studgroup.models import (
    CanonicalAcademicItem,
    CanonicalDeadlineHistory,
    CanonicalItemEvidence,
    CanonicalItemRevision,
)


def _kept_texts(item, edited_card):
    """Title and description for an update, never worse than what the item already shows.

    A display field the card editor had to replace (fallback) is a placeholder, a title rebuilt from
    the update's own fragments or a salvaged fragment. It must not overwrite a title or description the item already carries from an earlier
    accepted pass: the previous text stays, and the card keeps the reasons that mark it unconfirmed.
    A previous placeholder is replaced by anything better.
    """
    title, description = edited_card.title, edited_card.description
    fallback = set(edited_card.fallback_fields)
    # An update usually cites only the new clarifying messages, which cannot support the card's
    # whole title, so the editor rebuilds it from those fragments (e.g. "Быть минимум 2 источника
    # указано"). Any rebuilt title keeps the real title the item already has.
    if "title" in fallback and item.title and not item.title.endswith(INSUFFICIENT):
        title = item.title
    if (
        "description" in fallback
        and "description_pending_ai" in edited_card.attention_reasons
        and item.description
        and item.description not in {DESCRIPTION_PENDING, DESCRIPTION_ATTACHMENT_ONLY}
    ):
        description = item.description
    return title, description


def _locked_fields(item):
    try:
        return set(json.loads(item.locked_fields or "[]"))
    except ValueError:
        return set()


def _deadline_tuple(item):
    return (item.deadline_at, item.deadline_date_only, item.deadline_basis, item.deadline_reason)


def _record_deadline_history(db, item, proposal, sources, now):
    """Keep every shown deadline with the message it came from; never overwrite older rows."""
    cited = [(sources[row.telegram_message_id], row.revision) for row in proposal.evidence]
    cited.sort(key=lambda pair: (pair[0].message_date, pair[0].telegram_message_id))
    raw, revision = cited[-1]
    db.add(
        CanonicalDeadlineHistory(
            item_id=item.id,
            item_revision=item.revision,
            deadline_at=item.deadline_at,
            deadline_date_only=item.deadline_date_only,
            deadline_basis=item.deadline_basis,
            deadline_reason=item.deadline_reason,
            timezone_confirmed=item.timezone_confirmed,
            source_telegram_message_id=raw.telegram_message_id,
            source_reply_to_message_id=raw.reply_to_message_id,
            source_revision=revision,
            changed_at=now,
        )
    )


_BASIS_RANK = {"explicit": 3, "relative_resolved": 2, "schedule_inferred": 1, "unknown": 0}
_DEADLINE_REQUESTS = {"deadline_needs_headman", "deadline_inferred_confirm"}


def _keeps_known_deadline(item, shown):
    """An update that knows less about the date than the card never erases or downgrades it.

    A later message that names no date (or only a lesson-derived one) must not wipe a date the card
    already has from the text; only a better-grounded date or an explicit withdrawal changes it.
    """
    if item.deadline_at is None or shown.reason == "withdrawn_deadline":
        return False
    return _BASIS_RANK.get(shown.basis, 0) < _BASIS_RANK.get(item.deadline_basis, 0)


@dataclass(frozen=True)
class _Shown:
    at: object
    date_only: bool
    basis: str
    reason: str


def _shown_deadline(proposal, edited_card, applied):
    """The deadline the card will show: the policy's decision, or the legacy proposal value."""
    if applied is None:
        value = proposal.deadline
        shown = _Shown(value.at, value.date_only, value.basis, "unknown")
        return shown, False, list(edited_card.attention_reasons)
    decision = applied.decision
    shown = _Shown(decision.at, decision.date_only, decision.basis, decision.reason)
    reasons = list(dict.fromkeys([*edited_card.attention_reasons, *applied.reasons]))
    return shown, applied.timezone_confirmed, reasons


async def _record_missing_evidence(db, item, proposal, sources):
    """A cited message that is new to the item is kept as evidence even if no field changed."""
    present = set(
        (
            await db.execute(
                select(CanonicalItemEvidence.raw_message_id, CanonicalItemEvidence.fact_path).where(
                    CanonicalItemEvidence.item_id == item.id,
                    CanonicalItemEvidence.item_revision == item.revision,
                )
            )
        ).all()
    )
    for reference in proposal.evidence:
        for fact_path in reference.fact_paths:
            raw_id = sources[reference.telegram_message_id].id
            if (raw_id, fact_path) not in present:
                db.add(
                    CanonicalItemEvidence(
                        item_id=item.id,
                        item_revision=item.revision,
                        raw_message_id=raw_id,
                        source_revision=reference.revision,
                        fact_path=fact_path,
                    )
                )
                present.add((raw_id, fact_path))


@dataclass(frozen=True)
class Resolution:
    action: str
    item: CanonicalAcademicItem | None
    reason_code: str


async def resolve_proposal(
    db,
    *,
    group_id,
    proposal,
    edited_card,
    now,
    action=None,
    existing_item_id=None,
    expected_item_revision=None,
    deadline=None,
):
    action = action or proposal.action
    if existing_item_id is None:
        existing_item_id = proposal.existing_item_id
    if expected_item_revision is None:
        expected_item_revision = proposal.expected_item_revision
    sources = await resolve_evidence(db, group_id, proposal)
    item = None
    if existing_item_id is not None:
        item = await db.get(CanonicalAcademicItem, existing_item_id, with_for_update=True)
        if item is None or item.group_id != group_id:
            return Resolution("review", None, "related_item_unavailable")
        if expected_item_revision is None or item.revision != expected_item_revision:
            return Resolution("stale", item, "stale_item_revision")
        if item.manual_lock:
            return Resolution("blocked_by_lock", item, "manual_lock")
    if edited_card.visibility == "held" or action == "review":
        return Resolution("review", item, "proposal_requires_review")
    if action in {"cancel", "reschedule", "update"} and item is None:
        return Resolution("review", None, "explicit_item_identity_required")
    if item is not None and proposal.destination != item.destination:
        return Resolution("review", item, "destination_change_requires_new_item")
    shown, timezone_confirmed, card_reasons = _shown_deadline(proposal, edited_card, deadline)
    if deadline is not None and action == "reschedule" and shown.at is None:
        # Q4: only an unambiguous move changes a card; anything else waits for the headman.
        return Resolution("review", item, "reschedule_not_unambiguous")
    if item is None:
        item = CanonicalAcademicItem(
            group_id=group_id,
            destination=proposal.destination,
            semantic_kind=proposal.semantic_kind,
            # the checked subject, never the raw proposal text: an unconfirmed subject stays out
            subject=edited_card.subject,
            title=edited_card.title,
            description=edited_card.description,
            deadline_at=shown.at,
            deadline_date_only=shown.date_only,
            deadline_basis=shown.basis,
            deadline_reason=shown.reason,
            timezone_confirmed=timezone_confirmed,
            audience_scope=proposal.audience.scope,
            audience_label=proposal.audience.label,
            state="active",
            attention_reasons=json.dumps(card_reasons, ensure_ascii=False),
            locked_fields="[]",
            manual_lock=False,
            revision=1,
            created_at=now,
            updated_at=now,
        )
        db.add(item)
        await db.flush()
        resolved_action = "create"
        before_deadline = None
    else:
        before = item_snapshot(item)
        before_deadline = _deadline_tuple(item)
        if action == "cancel":
            item.state = "cancelled"
        else:
            item.destination = proposal.destination
            title, description = _kept_texts(item, edited_card)
            # a confirmed subject replaces the old one; a failed or silent re-analysis never
            # erases the subject the card already has
            subject = edited_card.subject if edited_card.subject is not None else item.subject
            current = {
                "deadline": _deadline_tuple(item),
                "subject": item.subject,
                "kind": item.semantic_kind,
                "title": item.title,
                "description": item.description,
            }
            keeps_deadline = action == "update" and _keeps_known_deadline(item, shown)
            if keeps_deadline:
                # The date stays, so the policy's "set/confirm the date" requests for the date-less
                # message do not apply; the card keeps the requests it already carried.
                card_reasons = [
                    *(r for r in card_reasons if r not in _DEADLINE_REQUESTS),
                    *(
                        r
                        for r in json.loads(item.attention_reasons or "[]")
                        if r in _DEADLINE_REQUESTS
                    ),
                ]
            proposed = {
                "deadline": current["deadline"]
                if keeps_deadline
                else (shown.at, shown.date_only, shown.basis, shown.reason),
                "subject": subject,
                "kind": proposal.semantic_kind,
                "title": title,
                "description": description,
            }
            merged, blocked = apply_field_locks(current, proposed, _locked_fields(item))
            if blocked:  # the AI wanted to change a field the headman locked: keep and flag it
                card_reasons = [*card_reasons, "ai_change_blocked_by_lock"]
            item.semantic_kind = merged["kind"]
            item.subject = merged["subject"]
            item.title, item.description = merged["title"], merged["description"]
            (
                item.deadline_at,
                item.deadline_date_only,
                item.deadline_basis,
                item.deadline_reason,
            ) = merged["deadline"]
            if "deadline" not in blocked:
                item.timezone_confirmed = timezone_confirmed
            item.audience_scope = proposal.audience.scope
            item.audience_label = proposal.audience.label
            item.attention_reasons = json.dumps(card_reasons, ensure_ascii=False)
        item.updated_at = now
        after_without_revision = item_snapshot(item)
        if before == after_without_revision:
            await _record_missing_evidence(db, item, proposal, sources)
            return Resolution("no_change", item, "canonical_item_unchanged")
        item.revision += 1
        resolved_action = "cancel" if action == "cancel" else "update"
    add_item_evidence(db, item, proposal, sources)
    if resolved_action in {"create", "update"} and (
        resolved_action == "create" or _deadline_tuple(item) != before_deadline
    ):
        _record_deadline_history(db, item, proposal, sources, now)
    db.add(
        CanonicalItemRevision(
            item_id=item.id,
            item_revision=item.revision,
            action=resolved_action,
            actor="ai_shadow",
            snapshot_json=item_snapshot(item),
            created_at=now,
        )
    )
    return Resolution(resolved_action, item, f"canonical_{resolved_action}")
