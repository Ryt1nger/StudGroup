"""One entry point after any group timetable change: audit row, version bump, deadline rebuild.

Every writer (headman bot, cabinet API, undo, reviewed publication) calls ``schedule_changed`` in
the same transaction as its edit, so ``schedule_changes`` and the derived deadlines never lag the
timetable. Personal (per-student) changes never touch group deadlines.
"""

from datetime import UTC, datetime

from sqlalchemy import update
from sqlalchemy.orm.attributes import set_committed_value

from studgroup.deadline_rebuild import rebuild_after_schedule_change
from studgroup.models import Group, ScheduleChange


async def schedule_changed(
    db, group, *, method, operation, user=None, member=None, scope="group", now=None
):
    """Record the change and return the rebuilt deadline changes (empty for personal scope)."""
    now = now or datetime.now(UTC)
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
