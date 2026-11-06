"""Conservative extraction of visible RANEPA timetable cards.

This parser produces preview data only. A successful parse does not establish
that the source returned every lesson in the requested week.
"""

import re
from datetime import date, time

from studgroup.ranepa_probe import SourceAccessError, _VisibleText
from studgroup.ranepa_schedule import ExternalLesson, WeekSnapshot, group_code, normalized

DAY_RE = re.compile(
    r"^(\d{1,2}[/.-]\d{1,2}[/.-]\d{4})\s+"
    r"(понедельник|вторник|среда|четверг|пятница|суббота|воскресенье)$"
)
LESSON_TIME_RE = re.compile(r"^(\d{1,2})\s*:\s*(\d{2})\s*[-–—]\s*(\d{1,2})\s*:\s*(\d{2})$")
LESSON_TYPES = {
    "лекционные занятия",
    "практические занятия",
    "лабораторные занятия",
    "семинарские занятия",
}
SUBGROUP_RE = re.compile(r"^(?:подгруппа|группа|гр\.?)\s*№?\s*[1-6]$", re.IGNORECASE)


def _source_day(value: str, weekday: str) -> date:
    first, second, year = (int(part) for part in re.split(r"[/.-]", value))
    try:
        # Slash dates on the public timetable are month/day/year.
        day = date(year, first, second) if "/" in value else date(year, second, first)
    except ValueError:
        raise SourceAccessError("invalid_source_day") from None
    weekdays = ("понедельник", "вторник", "среда", "четверг", "пятница", "суббота", "воскресенье")
    if day.weekday() != weekdays.index(weekday):
        raise SourceAccessError("invalid_source_day")
    return day


def extract_public_schedule(
    html: str, expected_group_code: str, week_start: date, week_end: date
) -> WeekSnapshot:
    """Parse bounded cards; reject ambiguous partial cards rather than silently lose lessons."""
    code = group_code(expected_group_code)
    parser = _VisibleText()
    parser.feed(html)
    chunks = [" ".join(value.split()) for value in parser.chunks if value.strip()]
    visible = normalized(" ".join(chunks))
    if any(marker in visible for marker in ("captcha", "капча", "recaptcha")):
        raise SourceAccessError("captcha_required")
    if normalized(code) not in visible:
        raise SourceAccessError("group_identity_not_confirmed")

    current_day = None
    lessons: dict[tuple[str, ...], ExternalLesson] = {}
    for index, chunk in enumerate(chunks):
        heading = DAY_RE.fullmatch(normalized(chunk))
        if heading:
            current_day = _source_day(*heading.groups())
            continue
        if current_day is None or not week_start <= current_day <= week_end:
            continue
        clock = LESSON_TIME_RE.fullmatch(chunk)
        if clock is None:
            continue
        # Stop at the next card or day. Never borrow missing fields from it.
        following = []
        for value in chunks[index + 1 :]:
            if DAY_RE.fullmatch(normalized(value)) or LESSON_TIME_RE.fullmatch(value):
                break
            following.append(value)
        kinds = [i for i, value in enumerate(following) if normalized(value) in LESSON_TYPES]
        if not kinds:
            continue  # Timeline labels alone are not lesson cards.
        if len(kinds) != 1 or kinds[0] != 2 or len(following) < 5:
            raise SourceAccessError("incomplete_source_lesson")
        room, subject, kind, teacher, card_code = following[:5]
        if normalized(group_code(card_code)) != normalized(code):
            raise SourceAccessError("group_identity_not_confirmed")
        trailing = following[5:]
        if len(trailing) > 1 or (trailing and not SUBGROUP_RE.fullmatch(trailing[0])):
            raise SourceAccessError("incomplete_source_lesson")
        try:
            starts = time(int(clock[1]), int(clock[2]))
            ends = time(int(clock[3]), int(clock[4]))
            row = ExternalLesson(
                day=current_day,
                starts=starts,
                ends=ends,
                subject=subject,
                room=room,
                teacher=teacher,
                lesson_type=kind,
                subgroup_hint=trailing[0] if trailing else None,
            )
        except (ValueError, TypeError):
            raise SourceAccessError("invalid_source_lesson") from None
        old = lessons.get(row.identity())
        if old is not None and old.attributes() != row.attributes():
            raise SourceAccessError("conflicting_source_lesson")
        lessons[row.identity()] = row

    return WeekSnapshot(code, week_start, week_end, tuple(lessons.values()))
