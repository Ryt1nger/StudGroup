"""Deterministic watchdog for duplicate cards (second line behind the model's cross-package memory).

The model is shown the group's recent cards and must update a card instead of creating the same
assignment again. That is the primary defence; this module is the safety net that notices when it
failed: two ACTIVE cards that cite the same source messages and read alike, or that read very alike,
are reported. It only reports (pairs of item ids and the signal); it never merges or edits a card,
because one message may legitimately hold several tasks. Pure and offline apart from the loader.

Calibration (three full rehearsals on the real chat, 210 proposal pairs each): true duplicates
shared source messages and scored 0.12-0.51 text overlap, distinct tasks scored 0.08-0.21 with no
shared source; one task described "in other words" can score lower, which is why this is a net and
not the only defence.
"""

import json
import re
from dataclasses import dataclass
from itertools import combinations

from sqlalchemy import select

from studgroup.models import CanonicalAcademicItem, CanonicalItemEvidence, RawMessage

SHARED_SOURCE_MIN_OVERLAP = 0.12  # with at least one shared source message
ALIKE_MIN_OVERLAP = 0.35  # no shared source needed
_STOP = frozenset(
    ["который", "которые", "нужно", "будет", "также", "этого", "этой", "очень", "только", "после", "перед", "чтобы", "когда", "если", "между", "через", "более", "менее", "около", "можно", "нужна", "нужен", "всех", "всем", "каждый", "каждая", "задание", "работа", "работы", "группы", "группа", "занятие", "занятия", "следующей", "следующего", "необходимо", "подготовить", "сделать"]
)
_WORD = re.compile(r"[а-яa-z0-9]{4,}")


@dataclass(frozen=True)
class Candidate:
    id: str
    subject: str | None
    title: str
    description: str
    sources: frozenset


def _tokens(candidate):
    text = f"{candidate.title} {candidate.description}".casefold().replace("ё", "е")
    return {word[:6] for word in _WORD.findall(text) if word not in _STOP}


def overlap(left, right):
    a, b = _tokens(left), _tokens(right)
    return len(a & b) / max(1, len(a | b))


def _subject(candidate):
    return " ".join((candidate.subject or "").casefold().replace("ё", "е").split()) or None


def possible_duplicates(candidates, *, focus=None):
    """Pairs ``(id, id, signal, score)`` of probably-the-same cards; ``focus`` limits to new ids."""
    found = []
    for left, right in combinations(candidates, 2):
        if focus is not None and left.id not in focus and right.id not in focus:
            continue
        subject_left, subject_right = _subject(left), _subject(right)
        if subject_left and subject_right and subject_left != subject_right:
            continue  # two different known subjects are never the same assignment
        score = overlap(left, right)
        shared = left.sources & right.sources
        if shared and score >= SHARED_SOURCE_MIN_OVERLAP:
            found.append((left.id, right.id, "shared_sources", round(score, 2)))
        elif score >= ALIKE_MIN_OVERLAP:
            found.append((left.id, right.id, "alike_text", round(score, 2)))
    return found


async def load_candidates(db, group_id):
    """Active cards of the group with the Telegram ids of every message that backs them."""
    items = (
        await db.scalars(
            select(CanonicalAcademicItem).where(
                CanonicalAcademicItem.group_id == group_id,
                CanonicalAcademicItem.state == "active",
            )
        )
    ).all()
    sources = {}
    for item_id, telegram_id in (
        await db.execute(
            select(CanonicalItemEvidence.item_id, RawMessage.telegram_message_id)
            .join(RawMessage, RawMessage.id == CanonicalItemEvidence.raw_message_id)
            .where(CanonicalItemEvidence.item_id.in_([item.id for item in items]))
        )
    ).all():
        sources.setdefault(item_id, set()).add(telegram_id)
    return [
        Candidate(
            id=str(item.id),
            subject=item.subject,
            title=item.title,
            description=item.description or "",
            sources=frozenset(sources.get(item.id, ())),
        )
        for item in items
    ]


async def watch(db, group_id, touched_item_ids):
    """Report probable duplicates involving the cards this package created or updated."""
    candidates = await load_candidates(db, group_id)
    return possible_duplicates(candidates, focus={str(i) for i in touched_item_ids})


def summary(pairs):
    return json.dumps([list(pair) for pair in pairs][:20])
