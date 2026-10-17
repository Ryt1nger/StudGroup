"""Transactional create/update/cancel resolver with explicit identity and locks."""

import json
from dataclasses import dataclass

from sqlalchemy import select

from studgroup.analysis.card_editor import DESCRIPTION_ATTACHMENT_ONLY, DESCRIPTION_PENDING
from studgroup.analysis.entities import item_snapshot
from studgroup.analysis.evidence import add_item_evidence, resolve_evidence
from studgroup.analysis.title_builder import INSUFFICIENT
from studgroup.models import CanonicalAcademicItem, CanonicalItemEvidence, CanonicalItemRevision


def _kept_texts(item, edited_card):
    """Title and description for an update, never worse than what the item already shows.

    A display field the card editor had to replace (fallback) is a placeholder or a salvaged
    fragment. It must not overwrite a title or description the item already carries from an earlier
    accepted pass: the previous text stays, and the card keeps the reasons that mark it unconfirmed.
    A previous placeholder is replaced by anything better.
    """
    title, description = edited_card.title, edited_card.description
    fallback = set(edited_card.fallback_fields)
    if (
        "title" in fallback
        and "title_insufficient_data" in edited_card.attention_reasons
        and item.title
        and not item.title.endswith(INSUFFICIENT)
    ):
        title = item.title
    if (
        "description" in fallback
        and "description_pending_ai" in edited_card.attention_reasons
        and item.description
        and item.description not in {DESCRIPTION_PENDING, DESCRIPTION_ATTACHMENT_ONLY}
    ):
        description = item.description
    return title, description


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
    if item is None:
        item = CanonicalAcademicItem(
            group_id=group_id,
            destination=proposal.destination,
            semantic_kind=proposal.semantic_kind,
            # the checked subject, never the raw proposal text: an unconfirmed subject stays out
            subject=edited_card.subject,
            title=edited_card.title,
            description=edited_card.description,
            deadline_at=proposal.deadline.at,
            deadline_date_only=proposal.deadline.date_only,
            deadline_basis=proposal.deadline.basis,
            audience_scope=proposal.audience.scope,
            audience_label=proposal.audience.label,
            state="active",
            attention_reasons=json.dumps(edited_card.attention_reasons, ensure_ascii=False),
            manual_lock=False,
            revision=1,
            created_at=now,
            updated_at=now,
        )
        db.add(item)
        await db.flush()
        resolved_action = "create"
    else:
        before = item_snapshot(item)
        if action == "cancel":
            item.state = "cancelled"
        else:
            item.destination = proposal.destination
            item.semantic_kind = proposal.semantic_kind
            # a confirmed subject replaces the old one; a failed or silent re-analysis never
            # erases the subject the card already has
            if edited_card.subject is not None:
                item.subject = edited_card.subject
            item.title, item.description = _kept_texts(item, edited_card)
            item.deadline_at = proposal.deadline.at
            item.deadline_date_only = proposal.deadline.date_only
            item.deadline_basis = proposal.deadline.basis
            item.audience_scope = proposal.audience.scope
            item.audience_label = proposal.audience.label
            item.attention_reasons = json.dumps(edited_card.attention_reasons, ensure_ascii=False)
        item.updated_at = now
        after_without_revision = item_snapshot(item)
        if before == after_without_revision:
            await _record_missing_evidence(db, item, proposal, sources)
            return Resolution("no_change", item, "canonical_item_unchanged")
        item.revision += 1
        resolved_action = "cancel" if action == "cancel" else "update"
    add_item_evidence(db, item, proposal, sources)
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
