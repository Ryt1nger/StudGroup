"""One entry point after any group timetable change: audit row, version bump, deadline rebuild.

Every writer (headman bot, cabinet API, undo, reviewed publication) calls ``schedule_changed`` in
the same transaction as its edit, so ``schedule_changes`` and the derived deadlines never lag the
timetable. Personal (per-student) changes never touch group deadlines.
"""

from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, update
from sqlalchemy.orm.attributes import set_committed_value

from studgroup.deadline_rebuild import rebuild_after_schedule_change
from studgroup.models import Group, ScheduleChange


# A schedule change is one committed, group-level audit/version entry, not one
# screenshot, lesson row, recognition attempt, or notification.
# Human-confirmed Telegram, cabinet and image-review changes are intentionally excluded.
AUTOMATIC_SCHEDULE_METHODS = frozenset({"ai_auto", "image_auto", "schedule_auto"})
AUTOMATIC_SCHEDULE_WEEKLY_LIMIT = 2


class AutomaticScheduleLimitReached(ValueError):
    """The group's two automatic publication slots are already used this week."""


async def require_auto_schedule_slot(db, group, *, now):
    """Serialize automatic writes by group; week starts Monday in group's own timezone.

    Must run in the SAME transaction as the schedule mutation and audit, before
    incrementing the schedule version. PostgreSQL row locking makes two workers
    unable to consume the same final slot simultaneously.
    """
    zone = ZoneInfo(group.timezone)
    local = now.astimezone(zone)
    monday = local.date() - timedelta(days=local.weekday())
    begin = datetime.combine(monday, time.min, zone).astimezone(UTC)
    end = datetime.combine(monday + timedelta(days=7), time.min, zone).astimezone(UTC)
    await db.scalar(select(Group.id).where(Group.id == group.id).with_for_update())
    count = await db.scalar(
        select(func.count(ScheduleChange.id)).where(
            ScheduleChange.group_id == group.id,
            ScheduleChange.scope == "group",
            ScheduleChange.input_method.in_(AUTOMATIC_SCHEDULE_METHODS),
            ScheduleChange.created_at >= begin,
            ScheduleChange.created_at < end,
        )
    )
    if (count or 0) >= AUTOMATIC_SCHEDULE_WEEKLY_LIMIT:
        raise AutomaticScheduleLimitReached(
            "Автоматические обновления расписания на этой неделе исчерпаны "
            "(2 из 2). Ручные изменения старосты доступны без ограничений."
        )


async def schedule_changed(
    db, group, *, method, operation, user=None, member=None, scope="group", now=None
):
    """Record the change and return the rebuilt deadline changes (empty for personal scope)."""
    now = now or datetime.now(UTC)
    if method in AUTOMATIC_SCHEDULE_METHODS and scope == "group":
        await require_auto_schedule_slot(db, group, now=now)
    version = (
        await db.execute(
            update(Group)
            .where(Group.id == group.id)
            .values(schedule_version=Group.schedule_version + 1)
            .returning(Group.schedule_version)
        )
    ).scalar_one()
    set_committed_value(group, "schedule_version", version)
    db.add(
        ScheduleChange(
            group_id=group.id,
            actor_id=user.id if user else None,
            actor_role=member.role if member else None,
            input_method=method,
            scope=scope,
            schedule_version=version,
            operation=operation,
            created_at=now,
        )
    )
    await db.flush()
    if scope != "group":
        return []
    return await rebuild_after_schedule_change(db, group, now)
