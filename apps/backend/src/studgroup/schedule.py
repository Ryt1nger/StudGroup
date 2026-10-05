"""Calendar expansion: week parity uses a group's explicit anchor, never ISO parity."""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class LessonPattern:
    id: str
    subject: str
    weekday: int
    starts: time
    ends: time
    valid_from: date
    valid_until: date
    week: Literal["all", "first", "second"] = "all"
    teacher: str | None = None
    location: str | None = None

    def __post_init__(self):
        if not 0 <= self.weekday <= 6 or self.ends <= self.starts:
            raise ValueError("Invalid lesson weekday or time interval")
        if self.valid_until < self.valid_from or self.week not in {"all", "first", "second"}:
            raise ValueError("Invalid lesson validity or week")


def week_kind(day: date, first_week_anchor: date) -> str:
    monday = day - timedelta(days=day.weekday())
    anchor = first_week_anchor - timedelta(days=first_week_anchor.weekday())
    return "first" if ((monday - anchor).days // 7) % 2 == 0 else "second"


def expand(pattern: LessonPattern, start: date, end: date, timezone: str, anchor: date | None):
    if end < start or (end - start).days > 31:
        raise ValueError("Schedule range must be between 1 and 32 days")
    if pattern.week != "all" and anchor is None:
        raise ValueError("Alternating schedule requires an explicit first-week anchor")
    zone = ZoneInfo(timezone)
    day = max(start, pattern.valid_from)
    result = []
    while day <= min(end, pattern.valid_until):
        matches_week = pattern.week == "all" or week_kind(day, anchor) == pattern.week
        if day.weekday() == pattern.weekday and matches_week:
            result.append(
                {
                    "id": f"{pattern.id}:{day.isoformat()}",
                    "subject": pattern.subject,
                    "starts_at": datetime.combine(day, pattern.starts, zone).isoformat(),
                    "ends_at": datetime.combine(day, pattern.ends, zone).isoformat(),
                    "teacher": pattern.teacher,
                    "location": pattern.location,
                    "status": "scheduled",
                }
            )
        day += timedelta(days=1)
    return result
