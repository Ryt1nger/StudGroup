"""Apply validated proposals through the canonical resolver and compatibility projection."""

from dataclasses import dataclass
from zoneinfo import ZoneInfo

from studgroup.analysis.card_editor import edit_card
from studgroup.analysis.contracts import ApplicationOutcome
from studgroup.analysis.deadline_application import apply_policy
from studgroup.analysis.manual_dedupe import possible_manual_match, recent_manual_cards
from studgroup.analysis.projection import project_item
from studgroup.analysis.resolver import resolve_proposal
from studgroup.analysis.stored import effective_validation
from studgroup.analysis.subject_resolver import hashtag_subject, in_session_last_resort
from studgroup.models import Group


@dataclass(frozen=True)
class WriterBatchResult:
    outcomes: tuple[ApplicationOutcome, ...]
    product_writes: int
    notifications_requested: int
    # (item id, item revision, "created" | "updated") of every card this batch wrote; ids and
    # numbers only, so the owner report can diff the revision against the one before it.
    changes: tuple = ()


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
    changes = []
    manual_cards = None  # lazy: only load if a candidate would create a task
    subject_days = {}  # today's actual timetable loaded only for unresolved academic subjects
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
        inferred_from_lesson = False
        if card.subject is None and subject_context is not None:
            cited = {ref.telegram_message_id for ref in proposal.evidence}
            texts = [m.text for m in messages if m.telegram_message_id in cited]
            tagged = {hashtag_subject(text, subject_context["names"]) for text in texts}
            tagged.discard(None)
            if len(tagged) == 1:
                # An unambiguous, source-written hashtag has stronger evidence than the clock.
                card = card.model_copy(update={"subject": next(iter(tagged))})
            elif not tagged and proposal.subject is None and card.visibility != "held" and group:
                # Last resort only. The subject must still be blank AFTER model, catalog,
                # text validation and hashtags. Load the ACTUAL calendar for the source day:
                # cancelled/moved classes and overlapping subgroups cannot silently assign
                # the subject of an unrelated task posted during the lesson.
                topical_ids = {
                    ref.telegram_message_id
                    for ref in proposal.evidence
                    if {"title", "description"} & set(ref.fact_paths)
                }
                zone = ZoneInfo(group.timezone)
                days = {
                    m.message_date.astimezone(zone).date()
                    for m in messages
                    if m.telegram_message_id in topical_ids
                }
                if days:
                    from studgroup.api import schedule_data

                    for day in sorted(days):
                        if day not in subject_days:
                            subject_days[day] = await schedule_data(day, day, group, db)
                    hint = in_session_last_resort(
                        messages, proposal.evidence, subject_days, timezone=group.timezone
                    )
                    if hint.subject and hint.subject in subject_context["names"]:
                        inferred_from_lesson = True
                        card = card.model_copy(
                            update={
                                "subject": hint.subject,
                                "visibility": "headman_attention",
                                "attention_reasons": [
                                    *card.attention_reasons,
                                    "subject_inferred_from_current_lesson",
                                ],
                            }
                        )
        applied = (
            apply_policy(
                proposal,
                messages,
                None if inferred_from_lesson else card.subject,
                deadline_context,
            )
            if deadline_context is not None
            else None
        )
        if (
            proposal.action == "create"
            and proposal.destination == "task"
            and proposal.existing_item_id is None
        ):
            if manual_cards is None:
                manual_cards = await recent_manual_cards(db, group_id, now)
            proposed_deadline = applied.decision.at if applied is not None else proposal.deadline.at
            if any(
                possible_manual_match(
                    old,
                    subject=card.subject,
                    title=card.title,
                    description=card.description,
                    deadline_at=proposed_deadline,
                )
                for old in manual_cards
            ):
                outcomes.append(
                    ApplicationOutcome(
                        source_revision=validation.source_revision,
                        proposal_ordinal=ordinal,
                        technical_status="parsed",
                        application_status="review",
                        reason_code="possible_manual_duplicate",
                    )
                )
                continue
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
        if projection.action in {"created", "updated"}:
            changes.append((str(resolution.item.id), resolution.item.revision, projection.action))
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
    return WriterBatchResult(tuple(outcomes), writes, notifications, tuple(changes))
