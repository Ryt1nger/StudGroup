"""Bind date-only homework to its subject lesson, never an unrelated day's lessons."""

import re
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from studgroup.api import schedule_data


async def lesson_deadline(db, group, subject, at, date_only, source_text):
    if not at or not date_only:
        return at, date_only
    # An explicit end-of-day/week submission window remains independent of classes.
    if re.search(
        r"(?:до|к)\s+конц[ау]\s+(?:этого\s+|текущего\s+|этой\s+)?(?:дня|суток|недели)|23[:.]59",
        source_text,
        re.IGNORECASE,
    ):
        return at, date_only
    if at.tzinfo is None:
        at = at.replace(tzinfo=UTC)
    day = at.astimezone(ZoneInfo(group.timezone)).date()
    calendar = await schedule_data(day, day, group, db)
    if calendar["week_state"] != "ready":
        return at, date_only

    def normalize(name):
        return " ".join(name.casefold().replace("ё", "е").split())

    lessons = [
        lesson
        for lesson in calendar["lessons"]
        if lesson["status"] == "scheduled" and normalize(lesson["subject"]) == normalize(subject)
    ]
    if not lessons:
        return at, date_only
    lesson = min(lessons, key=lambda item: item["starts_at"])
    return datetime.fromisoformat(lesson["ends_at"]).astimezone(UTC), False
