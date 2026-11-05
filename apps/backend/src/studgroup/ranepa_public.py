"""Conservative extraction of visible lesson cards from RANEPA public timetable HTML.

Only recognises an explicit date, time range and lesson-type label in order.
A page that does not identify the expected group is never accepted.
"""

import re
from datetime import date, time

from studgroup.ranepa_probe import SourceAccessError, _VisibleText
from studgroup.ranepa_schedule import ExternalLesson, WeekSnapshot, group_code, normalized

DAY_RE = re.compile(
    r"^(\d{1,2}/\d{1,2}/\d{4})\s+"
    r"(?:понедельник|вторник|среда|четверг|пятница|суббота|воскресенье)$"
)
LESSON_TIME_RE = re.compile(
    r"^(\d{1,2})\s*:\s*(\d{2})\s*-\s*(\d{1,2})\s*:\s*(\d{2})$"
)
LESSON_TYPES = {
    "лекционные занятия",
    "практические занятия",
    "лабораторные занятия",
    "семинарские занятия",
}


def extract_public_schedule(
    html: str,
    expected_group_code: str,
    week_start: date,
    week_end: date,
) -> WeekSnapshot:
    """Parse only recognisable cards. A partial extraction must not replace a week."""
    code = group_code(expected_group_code)
    parser = _VisibleText()
    parser.feed(html)
    chunks = [" ".join(value.split()) for value in parser.chunks if value.strip()]
    visible = normalized(" ".join(chunks))
    if "captcha" in visible or "капча" in visible:
        raise SourceAccessError("captcha_required")
    if normalized(code) not in visible:
        raise SourceAccessError("group_identity_not_confirmed")

    today = None
    lessons = []
    for index, chunk in enumerate(chunks):
        heading = DAY_RE.match(normalized(chunk))
        if heading:
            try:
                month, day, year = (int(part) for part in heading.group(1).split("/"))
                today = date(year, month, day)
            except ValueError:
                raise SourceAccessError("invalid_source_day") from None
            continue
        if today is None or not week_start <= today <= week_end:
            continue
        clock = LESSON_TIME_RE.match(chunk)
        if clock is None:
            continue

        # The next fields in the public RANEPA card are room, subject, kind,
        # lecturer, academic group. Require all of these before recording a row.
        end = min(index + 9, len(chunks))
        kind_index = next(
            (
                offset
                for offset in range(index + 2, end)
                if normalized(chunks[offset]) in LESSON_TYPES
            ),
            None,
        )
        if kind_index is None or kind_index < index + 3 or kind_index + 2 >= len(chunks):
            continue
        # The card must start with a room, followed by the discipline and kind.
        if kind_index != index + 3:
            continue
        room, subject, teacher = chunks[index + 1], chunks[index + 2], chunks[index + 4]
        if not all((room, subject, teacher)):
            continue
        try:
            starts = time(int(clock[1]), int(clock[2]))
            ends = time(int(clock[3]), int(clock[4]))
            row = ExternalLesson(
                day=today,
                starts=starts,
                ends=ends,
                subject=subject,
                room=room,
                teacher=teacher,
                lesson_type=chunks[kind_index],
            )
        except (ValueError, TypeError):
            raise SourceAccessError("invalid_source_lesson") from None
        lessons.append(row)

    return WeekSnapshot(code, week_start, week_end, tuple(lessons))
