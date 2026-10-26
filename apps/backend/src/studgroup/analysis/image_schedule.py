"""Draft timetable proposals; an explicit authorized confirmation is the only write path."""

import json
from datetime import UTC, datetime
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from studgroup.api import ApiError
from studgroup.headman_domain import guard, lock_decision, save_audit
from studgroup.models import (
    Group,
    ImageAnalysisJob,
    ImageScheduleSuggestion,
    Membership,
    MessageAttachment,
    RawMessage,
    SchedulePattern,
)
from studgroup.schedule_audit import schedule_changed


class ScheduleTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")
    row_index: int = Field(ge=0)
    lesson_id: str = Field(max_length=100)
    pattern_revision: int = Field(ge=1)
    override_revision: int = Field(ge=0)
    scope: Literal["once", "recurring"] = "once"


class ScheduleConfirmation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1)
    expected_schedule_version: int = Field(ge=0)
    targets: list[ScheduleTarget] = Field(default_factory=list, max_length=100)


async def capture(db, job, reading, now):
    if not reading.schedule or not {"schedule", "schedule_change"}.intersection(reading.semantic_types):
        return
    if await db.scalar(select(ImageScheduleSuggestion.id).where(ImageScheduleSuggestion.image_job_id == job.id)):
        return
    context = json.loads(job.context_json)
    db.add(ImageScheduleSuggestion(
        group_id=job.group_id, image_job_id=job.id, schedule_version=context["schedule_version"],
        rows_json=json.dumps([row.model_dump(mode="json") for row in reading.schedule], ensure_ascii=False),
        state="pending", revision=1, created_at=now, delete_at=job.delete_at,
    ))


async def current_source(db, job_id, group_id, now):
    from studgroup.analysis.image_jobs import utc

    job = await db.get(ImageAnalysisJob, job_id)
    if job is None or job.group_id != group_id or job.state != "ready" or utc(job.delete_at) <= now:
        raise ApiError("stale_source", "Источник уже изменился или недоступен", 409)
    raw, attachment = await db.get(RawMessage, job.raw_message_id), await db.get(MessageAttachment, job.attachment_id)
    if (raw is None or attachment is None or raw.group_id != group_id or attachment.group_id != group_id
            or raw.revision != job.source_revision or not attachment.active or utc(raw.delete_at) <= now):
        raise ApiError("stale_source", "Источник уже изменился или недоступен", 409)
    from studgroup.analysis.image_jobs import context_snapshot, digest

    current = await context_snapshot(db, raw)
    saved = json.loads(job.context_json)
    current["pass_a_sha256"] = saved["pass_a_sha256"]
    if digest({k: v for k, v in current.items() if k not in {"existing_items", "existing_events"}}) != job.context_sha256:
        raise ApiError("stale_context", "Обсуждение изменилось; требуется новое чтение", 409)
    return job


async def confirm(db, group_id, user, member, ident, body, now=None):
    from studgroup.analysis.image_contracts import ScheduleRow
    from studgroup.analysis.image_jobs import utc

    now = now or datetime.now(UTC)
    locked = await db.scalar(select(Membership).where(Membership.id == member.id).with_for_update().execution_options(populate_existing=True))
    if (locked is None or locked.group_id != group_id or locked.user_id != user.id
            or locked.status != "active" or locked.role not in {"headman", "deputy"}):
        raise ApiError("permission_denied", "Требуются права старосты или заместителя", 403)
    group = await db.scalar(select(Group).where(Group.id == group_id).with_for_update().execution_options(populate_existing=True))
    suggestion = await db.scalar(select(ImageScheduleSuggestion).where(
        ImageScheduleSuggestion.id == ident, ImageScheduleSuggestion.group_id == group_id,
        ImageScheduleSuggestion.delete_at > now,
    ).with_for_update())
    if suggestion is None:
        raise ApiError("not_found", "Предложение недоступно", 404)
    if suggestion.state == "confirmed":
        return group.schedule_version  # retry has no repeated writes or notifications
    if (suggestion.state != "pending" or suggestion.revision != body.expected_revision
            or group.schedule_version != body.expected_schedule_version
            or suggestion.schedule_version != group.schedule_version):
        raise ApiError("revision_conflict", "Расписание изменилось; проверьте предложение заново", 409)
    job = await current_source(db, suggestion.image_job_id, group_id, now)
    rows = [ScheduleRow.model_validate_json(json.dumps(value)) for value in json.loads(suggestion.rows_json)]
    today = now.astimezone(ZoneInfo(group.timezone)).date()
    if any(not row.valid_from or not row.valid_until or row.valid_until < today for row in rows):
        raise ApiError("effective_dates_required", "Нужен подтверждённый актуальный период действия", 409)
    if any(row.subgroup for row in rows):
        raise ApiError("subgroup_mapping_required", "Нужно сопоставление подгруппы с расписанием", 409)
    existing = (await db.scalars(select(SchedulePattern).where(
        SchedulePattern.group_id == group_id, SchedulePattern.cancelled.is_(False),
    ).with_for_update())).all()
    targets = {item.row_index: item for item in body.targets}
    if len(targets) != len(body.targets) or any(index >= len(rows) for index in targets):
        raise ApiError("invalid_request", "Неверное сопоставление строк", 422)
    before, changed = {}, []
    for index, row in enumerate(rows):
        target = targets.get(index)
        if target:
            from studgroup import headman_schedule

            pattern, _override, _lesson, day = await headman_schedule.occurrence(db, group, target.lesson_id)
            if pattern.subject != row.subject or not (row.valid_from <= day <= row.valid_until):
                raise ApiError("schedule_mapping_required", "Выбранное занятие не соответствует строке", 409)
            if target.scope == "recurring" and (
                pattern.valid_from != row.valid_from or pattern.valid_until != row.valid_until
                or pattern.week != row.week
            ):
                raise ApiError("schedule_mapping_required", "Период или чётность правила отличаются", 409)
            local = ZoneInfo(group.timezone)
            starts = datetime.combine(day, row.starts, local)
            ends = datetime.combine(day, row.ends, local)
            await headman_schedule.change(db, group, user, locked, target.lesson_id,
                target.pattern_revision, target.override_revision, target.scope,
                {"starts_at": starts, "ends_at": ends, "location": row.room}, method="image_confirm")
            continue
        if row.week != "all" and group.first_week_anchor is None:
            raise ApiError("week_anchor_required", "Нужно подтверждённое начало первой недели", 409)
        # Never cancel/replace an existing official lesson implicitly. Exact slot matches may
        # update room/teacher only after normal revision and headman-lock checks.
        overlaps = [pattern for pattern in existing if pattern.weekday == row.weekday
                    and pattern.valid_from <= row.valid_until and pattern.valid_until >= row.valid_from
                    and (pattern.week == "all" or row.week == "all" or pattern.week == row.week)
                    and pattern.starts < row.ends and pattern.ends > row.starts]
        exact = [pattern for pattern in overlaps if pattern.subject == row.subject
                 and pattern.starts == row.starts and pattern.ends == row.ends
                 and pattern.valid_from == row.valid_from and pattern.valid_until == row.valid_until
                 and pattern.week == row.week]
        if overlaps:
            if len(overlaps) != 1 or len(exact) != 1:
                raise ApiError("schedule_mapping_required", "Нужно явно выбрать заменяемое занятие", 409)
            pattern = exact[0]
            key = f"p:{pattern.id}"
            await guard(db, group, locked, key, pattern, pattern.revision)
            from studgroup.headman_domain import snapshot
            before[key] = await snapshot(db, key, "p", pattern)
            pattern.location = row.room if row.room is not None else pattern.location
            pattern.teacher = row.teacher if row.teacher is not None else pattern.teacher
            pattern.revision += 1
        else:
            pattern = SchedulePattern(group_id=group_id, subject=row.subject, weekday=row.weekday,
                starts=row.starts, ends=row.ends, valid_from=row.valid_from, valid_until=row.valid_until,
                week=row.week, location=row.room, teacher=row.teacher, revision=1)
            db.add(pattern)
            await db.flush()
            key = f"p:{pattern.id}"
            before[key] = None
            existing.append(pattern)
        await lock_decision(db, group, user, locked, key, pattern)
        changed.append((key, "p", pattern))
    if changed:
        await save_audit(db, group, user, "image_schedule", before, changed)
        await schedule_changed(db, group, method="image_confirm", operation="image_schedule",
            user=user, member=locked, now=now)
    suggestion.state, suggestion.decided_by = "confirmed", user.id
    suggestion.revision += 1
    # Context state is retained; official writes are performed only in this caller transaction.
    assert utc(job.delete_at) > now
    return group.schedule_version
