"""Idempotent, zero-provider repair of existing dateless homework.

Only retained, current source evidence and complete timetable coverage qualify.
Never rewrite an explicit, manually settled, cancelled or ambiguous deadline.
"""

import json
import logging
from collections import Counter
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from studgroup.analysis.deadline_application import REASON_CONFIRM_DEADLINE, REASON_SET_DEADLINE
from studgroup.analysis.deadline_policy import decide_deadline
from studgroup.deadline_context import build_deadline_context
from studgroup.deadline_rebuild import commit_item_revision
from studgroup.models import (
    CanonicalAcademicItem,
    CanonicalItemEvidence,
    CanonicalProjectionLink,
    Group,
    Homework,
    PersonalCompletion,
    RawMessage,
)

LOGGER = logging.getLogger(__name__)
REPAIRABLE_REASONS = {
    "unknown",
    "subject_unknown",
    "schedule_unavailable",
    "schedule_does_not_cover_source_date",
    "next_subject_lesson_unknown",
    "next_subject_lesson_subgroup_unknown",
    "cancelled_lesson_in_window",
    "no_lesson_day_in_window",
    "schedule_gap_before_next_lesson",
}


def utc(value):
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def candidates(now):
    return (
        Homework.deadline_at.is_(None),
        Homework.status.in_(("published", "needs_clarification", "completed")),
        Homework.cancelled_at.is_(None),
        Homework.delete_at > now,
    )


def locked(query, apply):
    return query.with_for_update(skip_locked=True) if apply else query


async def repair_group(db, group, now, *, apply=False):
    from studgroup.headman_domain import ai_locked

    counts = Counter()
    ids = (
        await db.scalars(select(Homework.id).where(Homework.group_id == group.id, *candidates(now)))
    ).all()
    if not ids:
        return {}
    context = await build_deadline_context(db, group)
    for ident in ids:
        counts["examined"] += 1
        link = await db.scalar(
            select(CanonicalProjectionLink).where(
                CanonicalProjectionLink.entity_type == "homework",
                CanonicalProjectionLink.entity_id == ident,
            )
        )
        item = None
        if link is not None:
            item = await db.scalar(
                locked(
                    select(CanonicalAcademicItem).where(CanonicalAcademicItem.id == link.item_id),
                    apply,
                )
            )
            if (
                item is None
                or item.group_id != group.id
                or item.semantic_kind != "homework"
                or item.destination != "task"
                or item.state != "active"
                or item.audience_scope not in {"group", "subgroup"}
                or item.deadline_at is not None
                or item.deadline_basis != "unknown"
                or item.deadline_reason not in REPAIRABLE_REASONS
                or link.projected_revision != item.revision
                or item.manual_lock
                or "deadline" in json.loads(item.locked_fields or "[]")
                or "subject_inferred_from_current_lesson"
                in json.loads(item.attention_reasons or "[]")
            ):
                counts["skipped_canonical_not_eligible"] += 1
                continue
        row = await db.scalar(
            locked(
                select(Homework).where(
                    Homework.id == ident, Homework.group_id == group.id, *candidates(now)
                ),
                apply,
            )
        )
        if row is None:
            counts["skipped_changed_or_busy"] += 1
            continue
        if row.verification_state == "manual_confirmed" or row.raw_message_id is None:
            counts["skipped_manual_or_missing_source"] += 1
            continue
        if await ai_locked(db, group.id, row.raw_message_id):
            counts["skipped_manual_lock"] += 1
            continue

        if item is not None:
            refs = (
                await db.scalars(
                    select(CanonicalItemEvidence).where(
                        CanonicalItemEvidence.item_id == item.id,
                        CanonicalItemEvidence.item_revision == item.revision,
                    )
                )
            ).all()
            source_ids = {ref.raw_message_id for ref in refs}
        else:
            refs = []
            source_ids = {row.raw_message_id}
        raws = (
            await db.scalars(
                locked(
                    select(RawMessage).where(
                        RawMessage.id.in_(source_ids),
                        RawMessage.group_id == group.id,
                        RawMessage.delete_at > now,
                    ),
                    apply,
                )
            )
        ).all()
        by_id = {raw.id: raw for raw in raws}
        if (
            not raws
            or set(by_id) != source_ids
            or any(by_id[ref.raw_message_id].revision != ref.source_revision for ref in refs)
            or any(not raw.text for raw in raws)
        ):
            counts["skipped_missing_or_changed_evidence"] += 1
            continue
        manually_locked = False
        for raw in raws:
            if raw.id != row.raw_message_id and await ai_locked(db, group.id, raw.id):
                manually_locked = True
                break
        if manually_locked:
            counts["skipped_manual_lock"] += 1
            continue
        sources = [(raw.text, utc(raw.message_date)) for raw in raws]
        if item is None:
            # Legacy cards may include clarifications from other messages in their text.
            # The stored card must not itself contain an explicit or ambiguous deadline.
            sources.append((row.description or "", min(stamp for _, stamp in sources)))
        decision = decide_deadline(
            kind="homework",
            sources=sources,
            timezone=context.timezone,
            subject=item.subject if item is not None else row.subject_name,
            schedule=context.schedule,
            no_lesson_days=context.no_lesson_days,
            subgroup_id=row.subgroup_id,
        )
        if not decision.inferred or decision.at is None:
            counts["skipped_" + decision.reason] += 1
            continue
        counts["eligible"] += 1
        if decision.at <= now:
            counts["already_due"] += 1
        if not apply:
            continue

        if item is not None:
            item.deadline_at = utc(decision.at)
            item.deadline_date_only = False
            item.deadline_basis = decision.basis
            item.deadline_reason = decision.reason
            item.timezone_confirmed = context.timezone_confirmed
            reasons = set(json.loads(item.attention_reasons or "[]"))
            reasons.discard(REASON_SET_DEADLINE)
            reasons.add(REASON_CONFIRM_DEADLINE)
            if not context.timezone_confirmed:
                reasons.add("timezone_unconfirmed")
            item.attention_reasons = json.dumps(sorted(reasons), ensure_ascii=False)
            await commit_item_revision(
                db,
                item,
                now,
                action="deadline_backfill",
                actor="deadline_backfill",
                timezone_confirmed=context.timezone_confirmed,
                project=False,
            )
            link.projected_revision = item.revision
            link.updated_at = now
        # Update only deadline-related fields. Preserve descriptions, subgroup,
        # materials, retention, status and every student's completion.
        previous = row.revision
        row.deadline_at = utc(decision.at)
        row.deadline_date_only = False
        if row.verification_state != "needs_clarification":
            row.verification_state = "inferred"
        row.updated_at = now
        row.revision += 1
        await db.execute(
            update(PersonalCompletion)
            .where(
                PersonalCompletion.homework_id == row.id,
                PersonalCompletion.completed_at.is_not(None),
                PersonalCompletion.completed_revision == previous,
            )
            .values(completed_revision=row.revision)
        )
        counts["filled"] += 1
    return dict(counts)


async def repair_all(engine, *, apply=False, now=None):
    now = now or datetime.now(UTC)
    async with AsyncSession(engine) as db:
        group_ids = (
            await db.scalars(
                select(Group.id)
                .join(Homework, Homework.group_id == Group.id)
                .where(Group.status == "active", *candidates(now))
                .distinct()
            )
        ).all()
    total = Counter()
    for group_id in group_ids:
        async with AsyncSession(engine) as db:
            group = await db.scalar(
                locked(
                    select(Group).where(Group.id == group_id, Group.status == "active"),
                    apply,
                )
            )
            if group is None:
                total["groups_busy"] += 1
                continue
            total.update(await repair_group(db, group, now, apply=apply))
            if apply:
                await db.commit()
    return dict(total)


async def backfill_tick(engine):
    report = await repair_all(engine, apply=True)
    if report.get("filled"):
        LOGGER.info("homework_deadline_backfill: %s", report)
