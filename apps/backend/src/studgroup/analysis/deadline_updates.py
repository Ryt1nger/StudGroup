"""Pure rules around a deadline that already exists: reschedules, group timezone, manual locks.

Follows the Intelligent Deadline Spec v1.0 and the owner's decisions of 18 Oct 2026. No database,
no model call, no clock: the caller passes the source message, its timestamp and the timetable.

* Q4 - an unambiguous reschedule is a message that says the deadline moved AND carries either a
  date that ``contextual_deadline`` can read (basis ``explicit`` / ``relative_resolved``) or the
  words "next lesson" (date from the reliable timetable, basis ``schedule_inferred``). Anything
  else is left to the headman.
* Q5 - the group's own timezone, otherwise Europe/Moscow marked as not confirmed.
* Q6 - the headman may lock deadline, subject, kind, title and description; the AI never
  overwrites a locked field.
"""

import re
from dataclasses import dataclass
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from studgroup.analysis.deadline_policy import DeadlineDecision, decide_deadline

DEFAULT_TIMEZONE = "Europe/Moscow"
LOCKABLE_FIELDS = frozenset({"deadline", "subject", "kind", "title", "description"})

_MOVED = re.compile(
    r"(перенес(?:ли|ен[аоы]?|ён|ена|ены)|перенос\w*|сдвинул\w*|сдвинут\w*|"
    r"срок\s+(?:теперь|изменил\w*)|теперь\s+(?:до|к|на))",
    re.IGNORECASE,
)
_NEXT_LESSON = re.compile(
    r"на\s+(?:следующ\w+|ближайш\w+)\s+(?:пару|занятие|лекцию|семинар)", re.IGNORECASE
)


@dataclass(frozen=True)
class GroupTimezone:
    name: str
    confirmed: bool


def group_timezone(configured):
    """Q5: the configured IANA zone, else Europe/Moscow flagged as not confirmed."""
    if configured:
        try:
            ZoneInfo(configured)
        except (ZoneInfoNotFoundError, ValueError):
            return GroupTimezone(DEFAULT_TIMEZONE, False)
        return GroupTimezone(configured, True)
    return GroupTimezone(DEFAULT_TIMEZONE, False)


def says_deadline_moved(text):
    return bool(_MOVED.search(text or ""))


def detect_reschedule(*, text, sent_at, timezone, subject=None, schedule=None, no_lesson_days=()):
    """Return a ``DeadlineDecision`` for an unambiguous reschedule, otherwise ``None`` (= review)."""
    if sent_at.utcoffset() is None:
        raise ValueError("source_timestamp_requires_offset")
    if not says_deadline_moved(text):
        return None
    dated = decide_deadline(
        kind="homework", sources=[(text, sent_at)], timezone=timezone, subject=subject
    )
    if dated.basis in {"explicit", "relative_resolved"} and dated.at is not None:
        return dated
    if _NEXT_LESSON.search(text):
        decision = decide_deadline(
            kind="homework",
            sources=[(text, sent_at)],
            timezone=timezone,
            subject=subject,
            schedule=schedule,
            no_lesson_days=frozenset(no_lesson_days),
        )
        return decision if decision.basis == "schedule_inferred" else None
    return None


def is_newer_move(decision, current_at):
    """A reschedule changes the card only when it really names a different moment."""
    return (
        isinstance(decision, DeadlineDecision)
        and decision.at is not None
        and decision.at != current_at
    )


def apply_field_locks(current, proposed, locked):
    """Merge ``proposed`` into ``current`` except locked fields; return ``(merged, blocked)``.

    ``blocked`` lists locked fields the AI wanted to change to a different value, so the caller can
    keep the conflict for the headman instead of dropping it silently.
    """
    unknown = set(locked) - LOCKABLE_FIELDS
    if unknown:
        raise ValueError(f"field_not_lockable:{min(unknown)}")
    merged = dict(current)
    blocked = []
    for field, value in proposed.items():
        if field in locked:
            if current.get(field) != value:
                blocked.append(field)
            continue
        merged[field] = value
    return merged, sorted(blocked)
