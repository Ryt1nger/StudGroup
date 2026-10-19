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
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from studgroup.analysis.output_adapter import explicit_source_dates
from studgroup.deadlines import ScheduleDeadlineContext, contextual_deadline, deadline_evidence_text
from studgroup.subject_identity import match_key

# The stored/API vocabulary of ``deadline_basis`` (spec section 4, owner decision 18 Oct 2026). The
# model-facing contract (``DeadlineValue``) is unchanged: the model still says ``explicit`` and the
# backend derives ``relative_resolved`` itself. ``ambiguous_range`` survives only in rows written
# before this vocabulary and is read as "needs clarification".
DEADLINE_BASES = frozenset({"explicit", "relative_resolved", "schedule_inferred", "unknown"})

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
    no_lesson_days=frozenset(),
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
    literal = _literal_date(ordered, timezone, model_deadline)
    if literal is not None:
        return literal
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
    return _next_lesson(ordered, timezone, subject, schedule, no_lesson_days)


def _literal_date(ordered, timezone, model_deadline):
    """A day and month written in the text ("22.10", "22 октября"): the text wins (Q3).

    ``contextual_deadline`` does not read such dates, so they are resolved here. Exactly one
    distinct date is accepted; with several, only the one the model also names; otherwise unknown.
    """
    zone = ZoneInfo(timezone)
    first_day = ordered[0][1].astimezone(zone).date()
    found = set()
    for text, _ in ordered:
        for day, month in explicit_source_dates(deadline_evidence_text(text)):
            for year in (first_day.year, first_day.year + 1):
                try:
                    candidate = date(year, month, day)
                except ValueError:
                    continue
                if candidate >= first_day - timedelta(days=60):
                    found.add(candidate)
                    break
    if not found:
        return None
    if len(found) > 1 and model_deadline and model_deadline[0] is not None:
        named = model_deadline[0].astimezone(zone).date()
        found = {day for day in found if day == named} or found
    if len(found) != 1:
        return _unknown("several_dates_in_text")
    (day,) = found
    return DeadlineDecision(
        datetime.combine(day, time.min, zone), True, "explicit", "explicit_source_date_literal"
    )


def _next_lesson(ordered, timezone, subject, schedule, no_lesson_days):
    if not subject:
        return _unknown("subject_unknown")
    if schedule is None or not schedule.ready:
        return _unknown("schedule_unavailable")
    zone = ZoneInfo(timezone)
    reference_day = ordered[0][1].astimezone(zone).date()
    if not schedule.covered_from <= reference_day <= schedule.covered_until:
        return _unknown("schedule_does_not_cover_source_date")
    wanted = match_key(subject)
    upcoming = []
    for lesson in schedule.lessons:
        starts = datetime.fromisoformat(lesson["starts_at"])
        local_day = starts.astimezone(zone).date()
        if (
            match_key(lesson["subject"]) == wanted
            and local_day > reference_day  # never the day of the source message
            and schedule.covered_from <= local_day <= schedule.covered_until
        ):
            ends = datetime.fromisoformat(lesson["ends_at"]) if lesson.get("ends_at") else starts
            upcoming.append((starts, local_day, lesson.get("status", "scheduled"), ends))
    if not upcoming:
        return _unknown("next_subject_lesson_unknown")
    starts, local_day, status, ends = min(upcoming)
    # Owner decision (Q2): the timetable is trusted only while the window from the source day to
    # the chosen lesson holds no cancelled lesson and no day without lessons.
    if status != "scheduled":
        return _unknown("cancelled_lesson_in_window")
    if any(reference_day < day <= local_day for day in no_lesson_days):
        return _unknown("no_lesson_day_in_window")
    # Owner decision (19.10): a deadline tied to a lesson ends with that lesson, so the stored
    # moment is the lesson end and the card leaves the active list when the lesson is over.
    return DeadlineDecision(ends, False, "schedule_inferred", "next_subject_lesson")


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


def headman_request(decision, kind):
    """What the headman is asked to do about the deadline; the card itself is always published.

    * control point / event without a found date: published at once and sent to the headman to set
      the deadline (owner decision, 18 Oct 2026); such kinds are never dated by the timetable;
    * homework dated by the timetable: the headman is asked to confirm it (spec 3.4);
    * everything else: no request.
    """
    if decision.at is None and kind != "homework":
        return "set_deadline"
    if decision.inferred:
        return "confirm_inferred_deadline"
    return None
