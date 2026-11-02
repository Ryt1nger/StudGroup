"""Read-only lesson-linked archive cutoffs for date-only academic cards.

No permanent state mutation: a date-only card with a matching scheduled lesson
in its subject/subgroup is archived at that lesson's last end time. Without a
matching lesson it retains the original end-of-day rule. Explicit timed
deadlines are always independent of this projection.
"""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from studgroup.api import schedule_data


def normalized_subject(value):
    return " ".join(value.casefold().replace("ё", "е").split())


async def today_lesson_cutoffs(db, group, now):
    """Map today's scheduled subject/subgroup occurrences to their final end time.

    A cancelled or missing lesson is never treated as a completed lesson.
    Multiple lessons of the same subject use the LAST end: the card stays
    actionable until the subject's lessons are finished for that audience.
    """
    day = now.astimezone(ZoneInfo(group.timezone)).date()
    calendar = await schedule_data(day, day, group, db)
    if calendar["week_state"] != "ready":
        return {}
    cutoffs = {}
    for lesson in calendar["lessons"]:
        if lesson["status"] != "scheduled":
            continue
        key = (
            normalized_subject(lesson["subject"]),
            lesson.get("subgroup_id"),
        )
        end = datetime.fromisoformat(lesson["ends_at"]).astimezone(UTC)
        if end > cutoffs.get(key, datetime.min.replace(tzinfo=UTC)):
            cutoffs[key] = end
    return cutoffs


def lesson_cutoff(subject, subgroup_id, cutoffs):
    """A whole-group occurrence covers the group; subgroup lessons cover only their subgroup."""
    name = normalized_subject(subject)
    subgroup = str(subgroup_id) if subgroup_id is not None else None
    matches = [end for (name_, subgroup_), end in cutoffs.items() if name_ == name and (subgroup_ is None or subgroup_ == subgroup)]
    return max(matches) if matches else None


def lesson_day_archived(subject, subgroup_id, due_at, date_only, now, timezone, cutoffs):
    """Additional archive rule: only a date-only item on the current local lesson day."""
    if not date_only or due_at is None:
        return False
    zone = ZoneInfo(timezone)
    due = (due_at.replace(tzinfo=UTC) if due_at.tzinfo is None else due_at)
    if due.astimezone(zone).date() != now.astimezone(zone).date():
        return False
    cutoff = lesson_cutoff(subject, subgroup_id, cutoffs)
    return cutoff is not None and cutoff <= now
