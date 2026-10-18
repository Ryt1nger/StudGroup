"""Deadline decision for one card, following the Intelligent Deadline Spec v1.0 (17 Oct 2026).

Pure and offline: no database, no model call, no clock. Everything is derived from the source
messages' own timestamps and the group's timezone, so reprocessing an old message always gives the
same answer. Parsing of dates and clock times is NOT duplicated here: it reuses
``studgroup.deadlines.contextual_deadline``; this module adds only the policy around it.

Basis vocabulary (spec section 4): ``explicit``, ``relative_resolved``, ``schedule_inferred``,
``unknown``. ``reason`` says why (machine code); a decision never invents a date:

* an explicit or relative date in the source always wins over the timetable;
* an ambiguous range, an approximate period or a withdrawn date stays unknown;
* when the text and the model name different calendar days the date from the text always wins (owner
  decision, 18 Oct 2026); the model's date is never used against the text;
* a homework WITHOUT a date may get the next lesson of its subject, strictly after the calendar day
  of the first source message, only when the timetable covers that period; the result is marked
  ``schedule_inferred`` and must be confirmed by the headman;
* a test, control point, assessment, credit or exam is never dated by the next lesson.
"""

import re
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from studgroup.deadlines import ScheduleDeadlineContext, contextual_deadline, deadline_evidence_text
from studgroup.subject_identity import match_key

_EXPLICIT = {"explicit_source_date", "explicit_source_date_inferred_clock"}
_RELATIVE = {
    "relative_message_date",
    "relative_message_date_inferred_clock",
    "source_day_inferred_clock",
}
_NOT_A_DATE = {
    "approximate_deadline",
    "ambiguous_date_range",
    "ambiguous_weekday",
    "invalid_calendar_deadline",
    "withdrawn_deadline",
}
_DATE_SIGNAL = re.compile(
    r"\d{1,2}[.:]\d{2}|январ|феврал|март|апрел|мая|июн|июл|август|сентябр|октябр|ноябр|декабр"
    r"|завтра|сегодня|через|понедельник|вторник|сред[аеуы]|четверг|пятниц|суббот|воскресень",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class DeadlineDecision:
    at: datetime | None
    date_only: bool
    basis: str  # explicit | relative_resolved | schedule_inferred | unknown
    reason: str

    @property
    def inferred(self):
        return self.basis == "schedule_inferred"


def _unknown(reason):
    return DeadlineDecision(None, False, "unknown", reason)


def decide_deadline(
    *,
    kind,
    sources,
    timezone,
    subject=None,
    schedule: ScheduleDeadlineContext | None = None,
    model_deadline=None,
):
    """``sources`` = ``[(text, sent_at), ...]`` of one card; ``model_deadline`` = ``(at, date_only)``."""
    if not sources or any(stamp.utcoffset() is None for _, stamp in sources):
        raise ValueError("source_timestamp_requires_offset")
    ordered = sorted(sources, key=lambda source: source[1])
    found = None
    for text, stamp in ordered:  # a later dated clarification wins
        resolved = contextual_deadline(text, stamp, timezone)
        if resolved is not None:
            found = resolved
    basis = found.basis if found else None
    if basis in _EXPLICIT:
        return DeadlineDecision(found.at, found.date_only, "explicit", basis)
    if basis in _RELATIVE:
        return DeadlineDecision(found.at, found.date_only, "relative_resolved", basis)
    if basis in _NOT_A_DATE:
        return _unknown(basis)
    joined = "\n".join(deadline_evidence_text(text) for text, _ in ordered)
    if model_deadline and model_deadline[0] is not None and _DATE_SIGNAL.search(joined):
        at, date_only = model_deadline
        if at.utcoffset() is None:
            raise ValueError("deadline_requires_offset")
        return DeadlineDecision(at, bool(date_only), "explicit", "model_source_context")
    if basis == "unresolved_explicit_deadline":
        return _unknown(basis)  # the text names a deadline we could not read: never guess
    if kind != "homework":
        return _unknown("not_inferable_for_kind")
    return _next_lesson(ordered, timezone, subject, schedule)


def _next_lesson(ordered, timezone, subject, schedule):
    if not subject:
        return _unknown("subject_unknown")
    if schedule is None or not schedule.ready:
        return _unknown("schedule_unavailable")
    zone = ZoneInfo(timezone)
    reference_day = ordered[0][1].astimezone(zone).date()
    if not schedule.covered_from <= reference_day <= schedule.covered_until:
        return _unknown("schedule_does_not_cover_source_date")
    wanted = match_key(subject)
    candidates = []
    for lesson in schedule.lessons:
        starts = datetime.fromisoformat(lesson["starts_at"])
        local_day = starts.astimezone(zone).date()
        if (
            match_key(lesson["subject"]) == wanted
            and lesson.get("status", "scheduled") == "scheduled"
            and local_day > reference_day  # never the day of the source message
            and schedule.covered_from <= local_day <= schedule.covered_until
        ):
            candidates.append(starts)
    if not candidates:
        return _unknown("next_subject_lesson_unknown")
    return DeadlineDecision(min(candidates), False, "schedule_inferred", "next_subject_lesson")


def student_label(decision):
    """Neutral wording for students (spec 3.4)."""
    if decision.inferred:
        return "К следующему занятию"
    return "Срок уточняется" if decision.at is None else None


def staff_note(decision):
    """What the headman must know: the date was derived from the timetable, not found in chat."""
    if decision.inferred:
        return "Дата не найдена в сообщении, она выведена по расписанию. Уточните срок."
    return None


def archive_instant(decision, timezone):
    """When the card leaves the active list: the shown moment; date-only = end of the group day."""
    if decision.at is None:
        return None  # an unknown deadline never archives a card
    if not decision.date_only:
        return decision.at
    day = decision.at.astimezone(ZoneInfo(timezone)).date() + timedelta(days=1)
    return datetime.combine(day, time.min, ZoneInfo(timezone))
