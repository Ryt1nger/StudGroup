"""Apply validated proposals through the canonical resolver and compatibility projection."""

from dataclasses import dataclass

from studgroup.analysis.card_editor import edit_card
from studgroup.analysis.contracts import ApplicationOutcome
from studgroup.analysis.deadline_application import apply_policy
from studgroup.analysis.projection import project_item
from studgroup.analysis.resolver import resolve_proposal
from studgroup.analysis.stored import effective_validation
from studgroup.analysis.subject_resolver import hashtag_subject, resolve_card_subject
from studgroup.models import Group


@dataclass(frozen=True)
class WriterBatchResult:
    outcomes: tuple[ApplicationOutcome, ...]
    product_writes: int
    notifications_requested: int


async def apply_validated_batch(
    db,
    *,
    group_id,
    validation,
    messages,
    now,
    verified_subjects=(),
    emit_notifications=True,
    deadline_context=None,
    subject_context=None,
):
    # Defence in depth: whatever produced the validation (live adapter or an older stored run),
    # the same source-checked rules decide audience and date origin before anything is written.
    group = await db.get(Group, group_id)
    if group is not None:
        validation = effective_validation(validation, messages, group.timezone)
    outcomes = []
    writes = 0
    notifications = 0
    for rejected in validation.rejected:
        outcomes.append(
            ApplicationOutcome(
                source_revision=validation.source_revision,
                proposal_ordinal=rejected.proposal_ordinal,
                technical_status="rejected",
                application_status="not_applied",
                reason_code=rejected.reason_code,
            )
        )
    for ordinal, proposal in zip(validation.accepted_ordinals, validation.accepted, strict=True):
        card = edit_card(proposal, messages, verified_subjects=verified_subjects)
        if card.subject is None and subject_context is not None:
            # No source-backed subject: take it from the timetable. Always one of the timetable
            # names, never invented; a split vote leaves the subject empty.
            cited = {ref.telegram_message_id for ref in proposal.evidence}
            texts = [(m.message_date, m.text) for m in messages if m.telegram_message_id in cited]
            if proposal.subject:
                # The model NAMED a subject the sources do not support (e.g. a subject that is not
                # in the timetable). The lesson that happened to be running says nothing about it,
                # so only an unambiguous hashtag may replace it; otherwise the subject stays empty.
                tagged = {hashtag_subject(text, subject_context["names"]) for _, text in texts}
                tagged.discard(None)
                found_subject = next(iter(tagged)) if len(tagged) == 1 else None
            else:
                # The model named none: hashtag, or the lesson running when the messages were sent.
                found_subject = resolve_card_subject(texts, **subject_context).subject
            if found_subject:
                card = card.model_copy(update={"subject": found_subject})
        applied = (
            apply_policy(proposal, messages, card.subject, deadline_context)
            if deadline_context is not None
            else None
        )
        resolution = await resolve_proposal(
            db,
            group_id=group_id,
            proposal=proposal,
            edited_card=card,
            now=now,
            deadline=applied,
        )
        if resolution.item is None or resolution.action in {
            "review",
            "stale",
            "blocked_by_lock",
        }:
            outcomes.append(
                ApplicationOutcome(
                    source_revision=validation.source_revision,
                    proposal_ordinal=ordinal,
                    technical_status=("stale" if resolution.action == "stale" else "parsed"),
                    application_status=resolution.action,
                    reason_code=resolution.reason_code,
                )
            )
            continue
        projection = await project_item(
            db,
            resolution.item,
            now,
            writer_mode="new",
            emit_notifications=emit_notifications,
        )
        application_status = {
            "created": "created",
            "updated": "updated",
            "unchanged": "unchanged",
            "blocked_by_lock": "blocked_by_lock",
            "audience_blocked": "review",
            "type_change_blocked": "review",
        }[projection.action]
        writes += projection.action in {"created", "updated"}
        notifications += emit_notifications and projection.action in {"created", "updated"}
        outcomes.append(
            ApplicationOutcome(
                source_revision=validation.source_revision,
                proposal_ordinal=ordinal,
                technical_status="parsed",
                application_status=application_status,
                reason_code=f"projection_{projection.action}",
            )
        )
    return WriterBatchResult(tuple(outcomes), writes, notifications)
