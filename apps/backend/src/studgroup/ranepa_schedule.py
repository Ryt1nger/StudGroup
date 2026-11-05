"""Source-neutral preparation of university timetable snapshots.

The university remains the source of truth. No network or live DB writes here.
"""
from dataclasses import dataclass
from datetime import date, time
from hashlib import sha256
import json
import re
import unicodedata

from studgroup.subgroups import recognise


def normalized(value: str | None) -> str:
    value = unicodedata.normalize("NFKC", value or "")
    return " ".join(value.casefold().replace("ё", "е").split())


def group_code(value: str) -> str:
    value = " ".join(unicodedata.normalize("NFKC", value).strip().split())
    value = re.sub(r"[‐‑‒–—−]", "-", value)
    if not value or len(value) > 120:
        raise ValueError("invalid_group_code")
    return value


@dataclass(frozen=True)
class ExternalLesson:
    day: date
    starts: time
    ends: time
    subject: str
    teacher: str | None = None
    room: str | None = None
    lesson_type: str | None = None
    subgroup_hint: str | None = None
    upstream_id: str | None = None
    cancelled: bool = False

    def __post_init__(self):
        if self.ends <= self.starts:
            raise ValueError("invalid_lesson_time")
        if not normalized(self.subject):
            raise ValueError("missing_subject")
        if self.upstream_id is not None and not self.upstream_id.strip():
            raise ValueError("invalid_upstream_id")

    def identity(self) -> tuple[str, ...]:
        """Parallel teachers/rooms remain distinct without a trusted upstream ID."""
        if self.upstream_id is not None:
            return ("upstream", self.upstream_id.strip())
        return (
            "content", self.day.isoformat(), self.starts.isoformat(), self.ends.isoformat(),
            normalized(self.subject), normalized(self.teacher), normalized(self.room),
            normalized(self.subgroup_hint),
        )

    def attributes(self) -> tuple:
        return (
            self.day.isoformat(), self.starts.isoformat(), self.ends.isoformat(),
            normalized(self.subject), normalized(self.teacher), normalized(self.room),
            normalized(self.lesson_type), normalized(self.subgroup_hint), self.cancelled,
        )


@dataclass(frozen=True)
class WeekSnapshot:
    group_code: str
    week_start: date
    week_end: date
    lessons: tuple[ExternalLesson, ...]

    def __post_init__(self):
        group_code(self.group_code)
        if self.week_end < self.week_start or (self.week_end - self.week_start).days > 31:
            raise ValueError("invalid_snapshot_window")
        if any(not self.week_start <= row.day <= self.week_end for row in self.lessons):
            raise ValueError("lesson_outside_snapshot_window")

    def indexed(self) -> dict[tuple[str, ...], ExternalLesson]:
        result = {}
        for row in self.lessons:
            key = row.identity()
            old = result.get(key)
            if old is not None and old.attributes() != row.attributes():
                raise ValueError("conflicting_upstream_lesson")
            result[key] = row
        return result

    def digest(self) -> str:
        values = sorted(
            [(*key, *row.attributes()) for key, row in self.indexed().items()], key=str
        )
        payload = json.dumps(
            [normalized(group_code(self.group_code)), self.week_start.isoformat(),
             self.week_end.isoformat(), values],
            ensure_ascii=False, separators=(",", ":"),
        )
        return sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ScheduleDiff:
    added: tuple[ExternalLesson, ...]
    removed: tuple[ExternalLesson, ...]
    changed: tuple[tuple[ExternalLesson, ExternalLesson], ...]

    @property
    def unchanged(self) -> bool:
        return not (self.added or self.removed or self.changed)


def compare_snapshots(previous: WeekSnapshot, current: WeekSnapshot) -> ScheduleDiff:
    if (
        normalized(group_code(previous.group_code)) != normalized(group_code(current.group_code))
        or (previous.week_start, previous.week_end) != (current.week_start, current.week_end)
    ):
        raise ValueError("mismatched_group_or_week")
    old, new = previous.indexed(), current.indexed()
    return ScheduleDiff(
        added=tuple(new[key] for key in sorted(new.keys() - old.keys())),
        removed=tuple(old[key] for key in sorted(old.keys() - new.keys())),
        changed=tuple(
            (old[key], new[key]) for key in sorted(old.keys() & new.keys())
            if old[key].attributes() != new[key].attributes()
        ),
    )


def detect_subgroup(lesson: ExternalLesson, subgroups: list):
    """Only unique teacher/explicit group matches; never infer from room alone."""
    by_teacher = recognise(subgroups, text=lesson.teacher or "") if lesson.teacher else None
    hint = normalized(lesson.subgroup_hint)
    explicit = bool(re.search(
        r"\b(?:подгрупп\w*|гр\.?|групп\w*)\s*№?\s*[1-6]\b|"
        r"\b[1-6]\s*(?:-?я)?\s*(?:подгрупп\w*|гр\.?|групп\w*)", hint
    ))
    by_hint = recognise(subgroups, text=hint) if explicit else None
    if by_teacher and by_hint and by_teacher.id != by_hint.id:
        return None
    return by_teacher or by_hint


def screenshot_action(*, official_configured: bool, official_request_succeeded: bool) -> str:
    """An official response blocks screenshots as an authoring source.

    The caller must actually refresh the official schedule before taking this action.
    Failed/unconfigured official access can use the existing image review fallback.
    """
    return (
        "compare_with_official"
        if official_configured and official_request_succeeded
        else "fallback_review"
    )
