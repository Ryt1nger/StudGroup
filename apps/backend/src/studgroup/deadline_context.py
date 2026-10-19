"""Build the policy input for one group from its stored timetable (one source of truth).

The timetable is read through ``schedule_data`` - the same projection the schedule screen uses - so
the alternating first/second week, exceptions and cancelled lessons are never re-implemented here.
"""

from datetime import date, timedelta
from types import SimpleNamespace

from sqlalchemy import select

from studgroup.analysis.deadline_application import DeadlineContext
from studgroup.analysis.deadline_updates import group_timezone
from studgroup.deadlines import ScheduleDeadlineContext
from studgroup.models import GroupNoLessonDay, SchedulePattern

SEMESTER_END = date(2026, 12, 27)  # owner decision, 18 Oct 2026; later set per group in the cabinet
CHUNK_DAYS = 28  # schedule_data refuses ranges over 32 days


async def build_deadline_context(db, group, *, horizon=SEMESTER_END):
    from studgroup.api import schedule_data  # late: api imports half the application

    zone = group_timezone(group.timezone)
    # a missing or invalid zone is replaced by the default for the whole computation
    view = SimpleNamespace(
        id=group.id, timezone=zone.name, first_week_anchor=group.first_week_anchor
    )
    patterns = (
        await db.scalars(select(SchedulePattern).where(SchedulePattern.group_id == group.id))
    ).all()
    no_lesson_days = frozenset(
        (
            await db.scalars(
                select(GroupNoLessonDay.day).where(GroupNoLessonDay.group_id == group.id)
            )
        ).all()
    )
    if not patterns:
        return DeadlineContext(zone.name, zone.confirmed, None, no_lesson_days)
    start = min(row.valid_from for row in patterns)
    end = min(horizon, max(row.valid_until for row in patterns))
    lessons, ready, day = [], True, start
    while day <= end:
        chunk_end = min(day + timedelta(days=CHUNK_DAYS - 1), end)
        data = await schedule_data(day, chunk_end, view, db)
        lessons.extend(data["lessons"])
        ready = ready and data["week_state"] == "ready"
        day = chunk_end + timedelta(days=1)
    schedule = ScheduleDeadlineContext(lessons, start, end, ready)
    return DeadlineContext(zone.name, zone.confirmed, schedule, no_lesson_days)
