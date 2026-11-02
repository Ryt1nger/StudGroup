"""Bounded manual-card memory and pre-publication duplicate review.

A headman's Mini App card is a first-class published Homework, not a
CanonicalAcademicItem. The model must see it as *read-only*, and a suspicious
new canonical create must be held for review BEFORE projection. We never
silently merge, delete, or overwrite the headman's card.
"""

import re
from datetime import UTC, timedelta

from sqlalchemy import select

from studgroup.analysis.duplicate_guard import Candidate, overlap
from studgroup.models import Homework

MAX_MANUAL_CARDS = 30
MANUAL_WINDOW = timedelta(days=35)
MAX_DUE_GAP = timedelta(days=2)
TOPIC_OVERLAP_FLOOR = 0.18
NO_DEADLINE_OVERLAP_FLOOR = 0.35
_TASK_RANGE = re.compile(r"(?<!\\d)(\\d{1,3})\\s*[–—-]\\s*(\\d{1,3})(?!\\d)")


def numbered_ranges(title):
    return [set(range(min(int(a), int(b)), max(int(a), int(b)) + 1)) for a, b in _TASK_RANGE.findall(title)]




def utc(value):
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def topic_tokens(value):
    """Use the duplicate watchdog's coarse topic stems, but never as proof of identity."""
    from studgroup.analysis.duplicate_guard import _tokens

    return _tokens(value)


def possible_manual_match(manual, *, subject, title, description, deadline_at):
    """Weak positive => REVIEW, not merge. Different dates far apart are different work."""
    if not subject or not manual["subject"]:
        return False
    normal = lambda value: " ".join(value.casefold().replace("ё", "е").split())
    if normal(subject) != normal(manual["subject"]):
        return False
    # "Tasks 1–3" and "tasks 4–6" are different homework despite identical
    # generic vocabulary. Do not hold a genuinely new assignment as a duplicate.
    old_ranges, new_ranges = numbered_ranges(manual["title"]), numbered_ranges(title)
    if old_ranges and new_ranges and not any(a & b for a in old_ranges for b in new_ranges):
        return False
    left = Candidate(
        "manual", manual["subject"], manual["title"], manual["description"] or "", frozenset()
    )
    right = Candidate("model", subject, title, description or "", frozenset())
    shared = topic_tokens(left) & topic_tokens(right)
    if len(shared) < 2:
        return False
    score = overlap(left, right)
    old, new = manual["deadline_at"], deadline_at
    if old is not None and new is not None:
        if abs(utc(old) - utc(new)) > MAX_DUE_GAP:
            return False
        return score >= TOPIC_OVERLAP_FLOOR
    return score >= NO_DEADLINE_OVERLAP_FLOOR


async def recent_manual_cards(db, group_id, now):
    """Published and non-expired manual homework only; no extra model calls."""
    rows = (
        await db.scalars(
            select(Homework)
            .where(
                Homework.group_id == group_id,
                Homework.verification_state == "manual_confirmed",
                Homework.status.in_(("published", "completed")),
                Homework.delete_at > now,
                Homework.created_at >= now - MANUAL_WINDOW,
            )
            .order_by(Homework.created_at.desc(), Homework.id)
            .limit(MAX_MANUAL_CARDS)
        )
    ).all()
    return [
        {
            "id": str(row.id),
            "subject": row.subject_name,
            "title": row.title[:255],
            "description": (row.description or "")[:500],
            "deadline_at": row.deadline_at,
        }
        for row in rows
    ]


def prompt_memory(cards):
    """Safe concise readonly cards; do not offer IDs as trusted canonical update targets."""
    return [
        {
            "subject": row["subject"],
            "title": row["title"],
            "description": row["description"],
            "deadline_at": utc(row["deadline_at"]).isoformat() if row["deadline_at"] else None,
        }
        for row in cards
    ]
