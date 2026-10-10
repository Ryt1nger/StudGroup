"""Deterministic group map that must exist before any paid history analysis."""

import json
import re
from collections import Counter

from sqlalchemy import func, select, update

from studgroup.models import Group, GroupAIProfile, GroupTopic, RawMessage, SchedulePattern


async def ensure_profile(db, group_id, now):
    profile = await db.get(GroupAIProfile, group_id)
    if profile is not None:
        return profile
    latest = await db.scalar(
        select(func.coalesce(func.max(RawMessage.analysis_generation), 1)).where(
            RawMessage.group_id == group_id
        )
    )
    profile = GroupAIProfile(
        group_id=group_id,
        generation=int(latest or 1),
        state="mapping",
        backfill_requested=True,
        source_count=0,
        requested_at=now,
    )
    db.add(profile)
    await db.flush()
    return profile


async def invalidate_map(db, group_id, now):
    """Refresh structure for a changed topic without replaying completed history."""
    profile = await db.get(GroupAIProfile, group_id)
    if profile is None:
        return await ensure_profile(db, group_id, now)
    if profile.state == "live":
        profile.state = "mapping"
        profile.backfill_requested = False
        profile.requested_at = now
        profile.mapped_at = None
    return profile


async def map_next(db, now):
    profile = await db.scalar(
        select(GroupAIProfile)
        .where(GroupAIProfile.state == "mapping")
        .order_by(GroupAIProfile.requested_at, GroupAIProfile.group_id)
        .with_for_update(skip_locked=True)
    )
    if profile is None:
        return None
    group = await db.get(Group, profile.group_id)
    messages = (
        await db.scalars(
            select(RawMessage)
            .where(RawMessage.group_id == profile.group_id)
            .order_by(RawMessage.message_date, RawMessage.telegram_message_id)
        )
    ).all()
    topics = (
        await db.scalars(
            select(GroupTopic)
            .where(GroupTopic.group_id == profile.group_id)
            .order_by(GroupTopic.telegram_thread_id)
        )
    ).all()
    subjects = sorted(
        set(
            (
                await db.scalars(
                    select(SchedulePattern.subject).where(
                        SchedulePattern.group_id == profile.group_id
                    )
                )
            ).all()
        ),
        key=str.casefold,
    )
    topic_counts = Counter(row.message_thread_id for row in messages if row.message_thread_id)
    hashtags = Counter(
        tag.casefold() for row in messages for tag in re.findall(r"#[\w]+", row.text or "")
    )
    structure = {
        "version": 1,
        "mode": "topics"
        if topics
        else "inferred"
        if subjects or hashtags or any(row.reply_to_message_id for row in messages)
        else "unstructured",
        "message_count": len(messages),
        "first_message_at": messages[0].message_date.isoformat() if messages else None,
        "last_message_at": messages[-1].message_date.isoformat() if messages else None,
        "reply_count": sum(row.reply_to_message_id is not None for row in messages),
        "topics": [
            {
                "thread_id": row.telegram_thread_id,
                "name": row.name,
                "message_count": topic_counts[row.telegram_thread_id],
            }
            for row in topics
        ],
        "schedule_subjects": subjects,
        "hashtags": [tag for tag, _count in hashtags.most_common(50)],
        "historic_topic_metadata_complete": not any(
            row.imported and row.message_thread_id is None for row in messages
        ),
    }
    eligible = [
        row
        for row in messages
        if group.bot_added_at is not None and row.message_date >= group.bot_added_at
    ]
    structure["analysis_scope"] = "since_bot_added"
    structure["analysis_start_at"] = (
        group.bot_added_at.isoformat() if group.bot_added_at is not None else None
    )
    structure["analysis_message_count"] = len(eligible)
    profile.structure_json = json.dumps(structure, ensure_ascii=False, separators=(",", ":"))
    profile.source_count = len(eligible)
    profile.mapped_at = now
    if profile.backfill_requested:
        await db.execute(
            update(RawMessage)
            .where(
                RawMessage.group_id == profile.group_id,
                RawMessage.processing_state.in_(["pending", "processing", "failed"]),
                RawMessage.message_date < group.bot_added_at
                if group.bot_added_at is not None
                else True,
            )
            .values(processing_state="completed")
        )
        if group.bot_added_at is not None:
            await db.execute(
                update(RawMessage)
                .where(
                    RawMessage.group_id == profile.group_id,
                    RawMessage.message_date >= group.bot_added_at,
                )
                .values(analysis_generation=profile.generation, processing_state="pending")
            )
        profile.state = "backfill"
    else:
        profile.state = "live"
        profile.completed_at = now
    return profile


async def complete_ready_backfills(db, now):
    profiles = (
        await db.scalars(
            select(GroupAIProfile)
            .where(GroupAIProfile.state == "backfill")
            .with_for_update(skip_locked=True)
        )
    ).all()
    completed = 0
    for profile in profiles:
        remaining = await db.scalar(
            select(func.count(RawMessage.id))
            .join(Group, Group.id == RawMessage.group_id)
            .where(
                RawMessage.group_id == profile.group_id,
                RawMessage.analysis_generation == profile.generation,
                Group.bot_added_at.is_not(None),
                RawMessage.message_date >= Group.bot_added_at,
                RawMessage.processing_state.in_(["pending", "processing"]),
            )
        )
        if remaining:
            continue
        profile.state = "live"
        profile.backfill_requested = False
        profile.completed_at = now
        completed += 1
    return completed
