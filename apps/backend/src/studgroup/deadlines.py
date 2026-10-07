"""Deadline policy anchored to source time, never to the import/reprocessing clock."""

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class DeadlineResolution:
    at: datetime | None
    date_only: bool
    basis: str


@dataclass(frozen=True)
class ScheduleDeadlineContext:
    lessons: list[dict]
    covered_from: date
    covered_until: date
    ready: bool = True


def canonical_subject(subject, schedule):
    if not subject or schedule is None or not schedule.ready:
        return subject
    normalize = lambda value: " ".join(value.casefold().replace("ё", "е").split())
    known = {normalize(lesson["subject"]): lesson["subject"] for lesson in schedule.lessons}
    key = normalize(subject).lstrip("#")
    if key in known:
        return known[key]
    aliases = {
        "матан": "математический анализ",
        "матанализ": "математический анализ",
        "инфа": "основы информатики",
        "информатика": "основы информатики",
        "линал": "линейная алгебра",
        "русский": "русский язык",
        "английский": "иностранный язык",
        "англ": "иностранный язык",
        "история": "история россии",
    }
    wanted = aliases.get(key)
    if not wanted:
        return subject
    matches = [
        value for name, value in known.items() if name == wanted or name.startswith(wanted + " ")
    ]
    return matches[0] if len(matches) == 1 else subject


WEEKDAYS = (
    r"понедельник(?:а|у)?",
    r"вторник(?:а|у)?",
    r"сред(?:а|е|у|ы)",
    r"четверг(?:а|у)?",
    r"пятниц(?:а|е|у|ы)",
    r"суббот(?:а|е|у|ы)",
    r"воскресень(?:е|я|ю)",
)

MONTHS = {
    "января": 1,
    "февраля": 2,
    "марта": 3,
    "апреля": 4,
    "мая": 5,
    "июня": 6,
    "июля": 7,
    "августа": 8,
    "сентября": 9,
    "октября": 10,
    "ноября": 11,
    "декабря": 12,
}


def contextual_deadline(text: str, sent_at: datetime, timezone: str) -> DeadlineResolution | None:
    """None means no recognized deadline. Unknown means explicit ambiguity: no fallback."""
    if sent_at.utcoffset() is None:
        raise ValueError("source_timestamp_requires_offset")
    local = sent_at.astimezone(ZoneInfo(timezone))
    text = text.casefold().replace("ё", "е")
    if re.search(r"(?:это\s+)?не\s+(?:на\s+|к\s+|до\s+)?(?:завтра|послезавтра|сегодня)\b", text):
        return DeadlineResolution(None, False, "withdrawn_deadline")

    def calendar(days):
        clock = re.search(r"\b(\d{1,2}):(\d{2})\b", text)
        if clock and int(clock[1]) < 24 and int(clock[2]) < 60:
            return DeadlineResolution(
                datetime.combine(
                    local.date() + timedelta(days=days),
                    time(int(clock[1]), int(clock[2])),
                    local.tzinfo,
                ),
                False,
                "relative_message_date",
            )
        return DeadlineResolution(
            datetime.combine(local.date() + timedelta(days=days), time.min, local.tzinfo),
            True,
            "relative_message_date",
        )

    # A time window is not an unambiguous submission deadline.
    if re.search(
        r"(?:конц[еау]|начал[еа]|середин[еы])\s+(?:ноябр|декабр|октябр|сентябр|январ)|перед\s+новым\s+годом",
        text,
    ):
        return DeadlineResolution(None, False, "approximate_deadline")
    if re.search(r"\d{1,2}\.\d{1,2}\s*[-–—]\s*\d{1,2}\.\d{1,2}", text):
        return DeadlineResolution(None, False, "ambiguous_date_range")
    explicit = re.search(
        r"(?:дедлайн|сдать|сдача|срок|до|к)\s*(\d{1,2})\s+("
        + "|".join(MONTHS)
        + r")(?:\s+(20\d{2}))?",
        text,
    )
    if explicit:
        year = int(explicit[3]) if explicit[3] else local.year
        month = MONTHS[explicit[2]]
        if not explicit[3] and local.month >= 11 and month <= 2:
            year += 1
        clock = re.search(r"\b(\d{1,2}):(\d{2})\b", text[explicit.end() :])
        try:
            at = datetime(
                year,
                month,
                int(explicit[1]),
                int(clock[1]) if clock else 0,
                int(clock[2]) if clock else 0,
                tzinfo=local.tzinfo,
            )
        except ValueError:
            return DeadlineResolution(None, False, "invalid_calendar_deadline")
        return DeadlineResolution(at, clock is None, "explicit_source_date")
    if re.search(r"(?:к|на|до)\s+(?:след(?:ующ\w*)?\.?\s+)?пар[еу]", text):
        return DeadlineResolution(None, False, "next_subject_lesson_reference")
    if re.search(r"(?:до|к)\s+конц[ау]\s+(?:этой\s+|текущей\s+)?недели", text):
        return calendar(6 - local.weekday())
    if re.search(r"\bпослезавтра\b", text):
        return calendar(2)
    if re.search(r"\bзавтра\b", text):
        return calendar(1)
    if re.search(r"\bсегодня\b", text):
        return calendar(0)
    match = re.search(r"через\s+(полчаса|\d+\s*(?:минут\w*|час\w*|дн\w*))", text)
    if match:
        value = match[1]
        if value == "полчаса":
            return DeadlineResolution(local + timedelta(minutes=30), False, "relative_message_date")
        count = int(re.search(r"\d+", value)[0])
        if "дн" in value:
            return calendar(count)
        delta = timedelta(minutes=count) if "минут" in value else timedelta(hours=count)
        return DeadlineResolution(local + delta, False, "relative_message_date")
    for weekday, pattern in enumerate(WEEKDAYS):
        weekday_match = re.search(
            r"\b(?:к|до|на|в)\s+(?:след(?:ующей|ующую|ующий)?\.?\s+)?" + pattern + r"\b", text
        )
        if weekday_match:
            if "след" in weekday_match[0] and weekday > local.weekday():
                return DeadlineResolution(None, False, "ambiguous_weekday")
            offset = (weekday - local.weekday()) % 7
            return calendar(offset or 7)
    # Explicit dates and unhandled relative formulations must be resolved by the model,
    # not replaced by a timetable assumption. Historical years/page numbers do not match.
    if re.search(r"(?<![\w.])\d{1,2}\.\d{2}(?!\d)|(?:к|до|на)\s+след\w*|срок|дедлайн", text):
        return DeadlineResolution(None, False, "unresolved_explicit_deadline")
    return None


def resolve_deadline(
    sources: list[tuple[str, datetime]],
    timezone: str,
    subject: str | None,
    proposed_at: datetime | None = None,
    proposed_date_only: bool = False,
    schedule: ScheduleDeadlineContext | None = None,
    fallback_reference: datetime | None = None,
) -> DeadlineResolution:
    """Source evidence wins; an approved provisional deadline may use today's timetable."""
    resolved = _resolve_source_deadline(
        sources, timezone, subject, proposed_at, proposed_date_only, schedule
    )
    if resolved.at is not None or fallback_reference is None:
        return resolved
    provisional = _resolve_source_deadline(
        [("", fallback_reference)], timezone, subject, schedule=schedule
    )
    if provisional.at is not None:
        return DeadlineResolution(provisional.at, False, "provisional_next_subject_lesson")
    return resolved


def _resolve_source_deadline(
    sources: list[tuple[str, datetime]],
    timezone: str,
    subject: str | None,
    proposed_at: datetime | None = None,
    proposed_date_only: bool = False,
    schedule: ScheduleDeadlineContext | None = None,
) -> DeadlineResolution:
    """Latest dated clarification wins; absent date defaults to next known subject lesson."""
    if not sources or any(stamp.utcoffset() is None for _, stamp in sources):
        raise ValueError("source_timestamp_requires_offset")
    ordered = sorted(sources, key=lambda source: source[1])
    evidence = None
    for text, stamp in ordered:
        resolved = contextual_deadline(text, stamp, timezone)
        if resolved is not None:
            evidence = resolved
    if evidence is not None and evidence.basis not in {
        "unresolved_explicit_deadline",
        "next_subject_lesson_reference",
    }:
        return evidence
    source_text = "\n".join(text for text, _ in ordered)
    date_evidence = re.search(
        r"\d{1,2}[.:]\d{2}|январ|феврал|март|апрел|мая|июн|июл|август|сентябр|октябр|ноябр|декабр|завтра|сегодня|через|понедельник|вторник|сред[аеуы]|четверг|пятниц|суббот|воскресень",
        source_text,
        re.IGNORECASE,
    )
    if (
        proposed_at is not None
        and date_evidence
        and (evidence is None or evidence.basis != "next_subject_lesson_reference")
    ):
        if proposed_at.utcoffset() is None:
            raise ValueError("deadline_requires_offset")
        return DeadlineResolution(proposed_at, proposed_date_only, "model_source_context")
    if evidence is not None and evidence.basis != "next_subject_lesson_reference":
        return evidence
    if schedule is None or not schedule.ready or not subject:
        return DeadlineResolution(None, False, "schedule_unavailable")
    # Do not skip a missing historical week and pretend the first future known lesson
    # is the actual next lesson. Use the original assignment's first source timestamp.
    reference = ordered[0][1]
    reference_day = reference.astimezone(ZoneInfo(timezone)).date()
    if not schedule.covered_from <= reference_day <= schedule.covered_until:
        return DeadlineResolution(None, False, "schedule_does_not_cover_source_date")

    def normalize(name):
        return " ".join(name.casefold().replace("ё", "е").split())

    candidates = []
    for lesson in schedule.lessons:
        start = datetime.fromisoformat(lesson["starts_at"])
        if (
            normalize(lesson["subject"]) == normalize(subject)
            and lesson["status"] == "scheduled"
            and start > reference
            and schedule.covered_from
            <= start.astimezone(ZoneInfo(timezone)).date()
            <= schedule.covered_until
        ):
            candidates.append(start)
    if candidates:
        return DeadlineResolution(min(candidates), False, "next_subject_lesson")
    return DeadlineResolution(None, False, "next_subject_lesson_unknown")
