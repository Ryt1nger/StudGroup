"""Conservative deterministic links used before semantic context understanding."""

import hashlib
import re
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import select

from studgroup.analysis.contracts import SourceMessage
from studgroup.models import ConversationSegment, RawMessage, SegmentSourceLink

CORRECTION = re.compile(r"\b(?:уточнен|исправ|перенес|отмен|не\s+кт|точнее)\w*", re.IGNORECASE)
TOKEN = re.compile(r"[a-zа-яё0-9]+", re.IGNORECASE)
STOP = {"это", "что", "для", "как", "или", "надо", "нужно", "будет", "тоже"}


@dataclass(frozen=True)
class SegmentLink:
    message_id: int
    revision: int
    reason: str
    strength: int


@dataclass(frozen=True)
class SegmentPlan:
    stable_key: str
    links: tuple[SegmentLink, ...]


def meaningful_tokens(text):
    return {
        token for token in TOKEN.findall(text.casefold()) if len(token) >= 4 and token not in STOP
    }


def link_segments(group_id, messages: list[SourceMessage]):
    ordered = sorted(messages, key=lambda row: (row.message_date, row.telegram_message_id))
    by_id = {row.telegram_message_id: row for row in ordered}
    parent = {row.telegram_message_id: row.telegram_message_id for row in ordered}
    reasons = {row.telegram_message_id: ("standalone", 0) for row in ordered}

    def root(message_id):
        while parent[message_id] != message_id:
            parent[message_id] = parent[parent[message_id]]
            message_id = parent[message_id]
        return message_id

    def union(left, right, reason, strength):
        left_root, right_root = root(left), root(right)
        if left_root != right_root:
            winner, loser = sorted((left_root, right_root))
            parent[loser] = winner
        reasons[right] = (reason, max(strength, reasons[right][1]))

    previous = None
    for message in ordered:
        if message.reply_to_message_id in by_id:
            union(message.reply_to_message_id, message.telegram_message_id, "reply", 100)
        elif previous is not None:
            distance = message.message_date - previous.message_date
            shared = meaningful_tokens(message.text) & meaningful_tokens(previous.text)
            if timedelta(0) <= distance <= timedelta(minutes=10) and CORRECTION.search(
                message.text
            ):
                union(previous.telegram_message_id, message.telegram_message_id, "correction", 90)
            elif (
                message.topic_id is not None
                and message.topic_id == previous.topic_id
                and timedelta(0) <= distance <= timedelta(minutes=30)
                and len(shared) >= 2
            ):
                union(
                    previous.telegram_message_id, message.telegram_message_id, "topic_semantic", 70
                )
        previous = message

    groups = {}
    for message in ordered:
        groups.setdefault(root(message.telegram_message_id), []).append(message)
    plans = []
    for root_id, rows in sorted(groups.items()):
        stable_key = hashlib.sha256(f"{group_id}:{root_id}".encode()).hexdigest()
        plans.append(
            SegmentPlan(
                stable_key=stable_key,
                links=tuple(
                    SegmentLink(
                        message_id=row.telegram_message_id,
                        revision=row.revision,
                        reason=reasons[row.telegram_message_id][0],
                        strength=reasons[row.telegram_message_id][1],
                    )
                    for row in rows
                ),
            )
        )
    return plans


async def persist_segment_plans(db, group_id, plans, now):
    """Persist new immutable segment versions; identical replay is a no-op."""
    created = []
    for plan in plans:
        source_ids = [link.message_id for link in plan.links]
        raw_rows = (
            await db.scalars(
                select(RawMessage).where(
                    RawMessage.group_id == group_id,
                    RawMessage.telegram_message_id.in_(source_ids),
                )
            )
        ).all()
        by_telegram_id = {row.telegram_message_id: row for row in raw_rows}
        if set(by_telegram_id) != set(source_ids) or any(
            by_telegram_id[link.message_id].revision != link.revision for link in plan.links
        ):
            raise ValueError("segment_source_revision_unavailable")
        latest = await db.scalar(
            select(ConversationSegment)
            .where(
                ConversationSegment.group_id == group_id,
                ConversationSegment.stable_key == plan.stable_key,
            )
            .order_by(ConversationSegment.version.desc())
            .limit(1)
            .with_for_update()
        )
        desired = {
            (
                by_telegram_id[link.message_id].id,
                link.revision,
                link.reason,
                link.strength,
            )
            for link in plan.links
        }
        if latest is not None:
            existing = {
                (row.raw_message_id, row.source_revision, row.link_reason, row.strength)
                for row in (
                    await db.scalars(
                        select(SegmentSourceLink).where(SegmentSourceLink.segment_id == latest.id)
                    )
                ).all()
            }
            if existing == desired:
                created.append(latest)
                continue
            latest.state = "superseded"
        segment = ConversationSegment(
            group_id=group_id,
            stable_key=plan.stable_key,
            version=(latest.version + 1) if latest else 1,
            state="open",
            started_at=min(by_telegram_id[link.message_id].message_date for link in plan.links),
            last_activity_at=max(
                by_telegram_id[link.message_id].message_date for link in plan.links
            ),
            created_at=now,
        )
        db.add(segment)
        await db.flush()
        for link in plan.links:
            db.add(
                SegmentSourceLink(
                    segment_id=segment.id,
                    raw_message_id=by_telegram_id[link.message_id].id,
                    source_revision=link.revision,
                    link_reason=link.reason,
                    strength=link.strength,
                )
            )
        created.append(segment)
    return created
