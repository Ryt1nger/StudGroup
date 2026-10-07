from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select

from studgroup.api import schedule_data
from studgroup.headman_domain import (
    StError,
    entity,
    guard,
    lock_decision,
    safe_url,
    save_audit,
    snapshot,
)
from studgroup.models import ScheduleException, SchedulePattern


async def occurrence(db, group, ident):
    try:
        pattern_id, day = ident.split(":")
        day = date.fromisoformat(day)
        _, pattern = await entity(db, group, "p:" + pattern_id)
    except (ValueError, StError):
        raise StError("Пара недоступна.") from None
    override = await db.scalar(
        select(ScheduleException)
        .where(
            ScheduleException.pattern_id == pattern.id,
            ScheduleException.occurrence_date == day,
            ScheduleException.group_id == group.id,
        )
        .with_for_update()
    )
    selected_day = (
        (
            override.starts_at.replace(tzinfo=UTC)
            if override and override.starts_at.tzinfo is None
            else override.starts_at
        )
        .astimezone(ZoneInfo(group.timezone))
        .date()
        if override
        else day
    )
    calendar = await schedule_data(selected_day, selected_day, group, db)
    lesson = next((l for l in calendar["lessons"] if l["id"] == ident), None)
    if not lesson:
        raise StError("Пара уже изменилась. Обнови расписание.")
    return pattern, override, lesson, day


async def change(db, group, user, member, ident, pattern_revision, override_revision, scope, patch):
    pattern, override, lesson, day = await occurrence(db, group, ident)
    await guard(db, group, member, f"p:{pattern.id}", pattern, pattern_revision)
    if (override.revision if override else 0) != override_revision:
        raise StError("Эта пара уже изменилась. Открой её заново.")
    if patch.get("online_url"):
        patch["online_url"] = safe_url(patch["online_url"])
    if scope == "recurring":
        key = f"p:{pattern.id}"
        before = {key: await snapshot(db, key, "p", pattern)}
        if "starts_at" in patch:
            start, end = (
                patch["starts_at"].astimezone(ZoneInfo(group.timezone)),
                patch["ends_at"].astimezone(ZoneInfo(group.timezone)),
            )
            if start.date() != end.date() or end <= start:
                raise StError("Пара должна закончиться позже начала в тот же день.")
            pattern.starts, pattern.ends, pattern.weekday = (
                start.time(),
                end.time(),
                start.weekday(),
            )
        for field in ["location", "online_url", "cancelled"]:
            if field in patch:
                setattr(pattern, field, patch[field])
        pattern.revision += 1
        await db.flush()
        await lock_decision(db, group, user, member, key, pattern)
        return await save_audit(db, group, user, "schedule", before, [(key, "p", pattern)])
    if scope != "once":
        raise StError("Выбери разовую правку или изменение всего правила.")
    if override:
        key = f"x:{override.id}"
        await guard(db, group, member, key, override, override_revision)
        before = {key: await snapshot(db, key, "x", override)}
    else:
        override = ScheduleException(
            group_id=group.id,
            pattern_id=pattern.id,
            occurrence_date=day,
            starts_at=datetime.fromisoformat(lesson["starts_at"]),
            ends_at=datetime.fromisoformat(lesson["ends_at"]),
            location=lesson["location"],
            online_url=lesson.get("online_url"),
            cancelled=lesson["status"] == "cancelled",
            revision=0,
        )
        db.add(override)
        await db.flush()
        key = f"x:{override.id}"
        before = {key: None}
    for field in ["starts_at", "ends_at", "location", "online_url", "cancelled"]:
        if field in patch:
            setattr(override, field, patch[field])
    if override.ends_at <= override.starts_at:
        raise StError("Конец пары должен быть позже начала.")
    override.revision += 1
    await db.flush()
    await lock_decision(db, group, user, member, key, override)
    return await save_audit(db, group, user, "schedule", before, [(key, "x", override)])


async def add(db, group, user, member, subject, starts, ends, location):
    local = starts.astimezone(ZoneInfo(group.timezone))
    end = ends.astimezone(ZoneInfo(group.timezone))
    if end <= local or end.date() != local.date():
        raise StError("Начало и конец пары должны быть в один день, конец позже начала.")
    row = SchedulePattern(
        group_id=group.id,
        subject=subject,
        weekday=local.weekday(),
        starts=local.time(),
        ends=end.time(),
        valid_from=local.date(),
        valid_until=local.date(),
        week="all",
        location=location,
        revision=1,
    )
    db.add(row)
    await db.flush()
    key = f"p:{row.id}"
    await lock_decision(db, group, user, member, key, row)
    return await save_audit(db, group, user, "schedule_add", {key: None}, [(key, "p", row)])
