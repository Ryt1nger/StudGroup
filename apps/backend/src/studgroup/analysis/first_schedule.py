"""One-time, source-bound timetable bootstrap for newly connected pilot groups.

A first timetable does not need a staff confirmation. This entry point never updates or
replaces an existing timetable. Unknown recurrence is limited to the source week and never
silently expanded to the whole semester. All writes share the Group row lock and the normal
schedule-change version/audit/deadline-rebuild transaction.
"""

import json
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from studgroup.analysis.image_contracts import VisualReading
from studgroup.models import (
    CabinetScheduleUpload,
    Group,
    GroupScheduleSnapshot,
    ImageAnalysisJob,
    ImageScheduleSuggestion,
    MessageAttachment,
    RawMessage,
    SchedulePattern,
)
from studgroup.schedule_audit import schedule_changed


def _aware(value):
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def _initial_rows(raw_rows, group, source_at, now):
    """Return validated week-bounded rows, or None if publication would require guessing."""
    from studgroup.analysis.image_schedule import ConfirmedScheduleRow, clipped_field

    if not raw_rows or len(raw_rows) > 100:
        return None
    zone = ZoneInfo(group.timezone)
    local_now = _aware(now).astimezone(zone)
    source = _aware(source_at).astimezone(zone)
    if not local_now - timedelta(days=7) <= source <= local_now:
        return None
    monday = source.date() - timedelta(days=source.weekday())
    sunday = monday + timedelta(days=6)
    prepared = []
    for original in raw_rows:
        if not isinstance(original, dict) or original.get("week", "all") != "all":
            return None
        if original.get("subgroup"):
            # Unmapped subgroups are not implicitly made public to all students.
            return None
        if any(clipped_field(original.get(field)) for field in ("subject", "room", "teacher")):
            return None
        patched = dict(original)
        if not patched.get("valid_from") or not patched.get("valid_until"):
            # A photo of one day is not evidence of a permanent recurring timetable.
            # The bounded source week is the only conservative initial default.
            if patched.get("valid_from") and not monday <= _date(patched["valid_from"]) <= sunday:
                return None
            if patched.get("valid_until") and _date(patched["valid_until"]) > sunday:
                return None
            patched["valid_from"] = monday.isoformat()
            patched["valid_until"] = sunday.isoformat()
        try:
            row = ConfirmedScheduleRow.model_validate(patched)
        except (ValueError, ValidationError):
            return None
        if (
            not row.subject.strip()
            or row.valid_until < local_now.date()
            or row.valid_until > local_now.date() + timedelta(days=366)
            or (row.week != "all")
        ):
            return None
        if any(
            prev.weekday == row.weekday
            and prev.valid_from <= row.valid_until
            and prev.valid_until >= row.valid_from
            and prev.starts < row.ends
            and prev.ends > row.starts
            for prev in prepared
        ):
            return None
        prepared.append(row)
    return prepared


def _date(value):
    from datetime import date

    return value if isinstance(value, date) else date.fromisoformat(value)


async def publish_first(db, suggestion, source_at, now, *, reading=None):
    """Auto-confirm only the *first* safe schedule; return False for normal review."""
    if suggestion.state != "pending":
        return False
    if reading is not None and (
        not reading.complete
        or "schedule" not in reading.semantic_types
        or reading.uncertain_fragments
    ):
        return False
    group = await db.scalar(
        select(Group)
        .where(Group.id == suggestion.group_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if (
        group is None
        or group.status != "active"
        or not group.pilot_authorized
        or group.schedule_version != 0
        or suggestion.schedule_version != 0
    ):
        return False
    if await db.scalar(select(SchedulePattern.id).where(SchedulePattern.group_id == group.id).limit(1)):
        return False
    if await db.scalar(
        select(GroupScheduleSnapshot.week_start)
        .where(GroupScheduleSnapshot.group_id == group.id)
        .limit(1)
    ):
        return False
    try:
        rows = _initial_rows(json.loads(suggestion.rows_json), group, source_at, now)
    except (ValueError, TypeError, KeyError):
        return False
    if not rows:
        return False

    # ScheduleChanged may fail on an unrelated deadline rebuild. In that case
    # keep the paid recognition successful and the suggestion reviewable.
    try:
        async with db.begin_nested():
            db.add_all(
                SchedulePattern(
                    group_id=group.id,
                    subject=row.subject.strip(),
                    weekday=row.weekday,
                    starts=row.starts,
                    ends=row.ends,
                    valid_from=row.valid_from,
                    valid_until=row.valid_until,
                    week=row.week,
                    location=row.room,
                    teacher=row.teacher,
                    revision=1,
                )
                for row in rows
            )
            await db.flush()
            await schedule_changed(
                db,
                group,
                method="image_auto",
                operation="initial_schedule",
                now=now,
            )
            suggestion.state = "confirmed"
            suggestion.revision += 1
            suggestion.confirmation_json = json.dumps(
                {
                    "automatic": True,
                    "scope": "initial_schedule",
                    "date_policy": "source_week_when_unspecified",
                    "rows": [row.model_dump(mode="json") for row in rows],
                    "schedule_version_before": 0,
                },
                ensure_ascii=False,
            )
            await db.flush()
    except Exception:  # noqa: BLE001 -- retain manual review on unexpected rebuild failure
        return False
    return True


async def publish_image_first(db, job, suggestion, reading, now):
    """Accept only current, stable Telegram photo evidence for the first timetable."""
    if job.state != "ready" or job.pass_number != 2:
        return False
    raw = await db.get(RawMessage, job.raw_message_id)
    attachment = await db.get(MessageAttachment, job.attachment_id)
    if (
        raw is None
        or attachment is None
        or raw.group_id != suggestion.group_id
        or attachment.group_id != suggestion.group_id
        or not attachment.active
        or attachment.source_revision != raw.revision
        or raw.revision != job.source_revision
        or _aware(raw.delete_at) <= now
        or _aware(attachment.delete_at) <= now
        or _aware(job.delete_at) <= now
    ):
        return False
    return await publish_first(db, suggestion, raw.message_date, now, reading=reading)


async def publish_cabinet_first(engine, ident, now=None):
    """Staff-uploaded first timetable also skips the second, redundant approval."""
    now = now or datetime.now(UTC)
    async with AsyncSession(engine) as db:
        row = await db.get(CabinetScheduleUpload, ident)
        if row is None or row.state != "pending" or _aware(row.delete_at) <= now:
            return False
        applied = await publish_first(db, row, row.created_at, now)
        await db.commit()
        return applied


async def recover_pending_first(engine, now=None):
    """Idempotently recover already-recognized first photos after a safe deploy."""
    now = now or datetime.now(UTC)
    async with AsyncSession(engine) as db:
        candidates = (
            await db.scalars(
                select(ImageScheduleSuggestion.id)
                .join(ImageAnalysisJob, ImageAnalysisJob.id == ImageScheduleSuggestion.image_job_id)
                .join(Group, Group.id == ImageScheduleSuggestion.group_id)
                .where(
                    ImageScheduleSuggestion.state == "pending",
                    ImageScheduleSuggestion.delete_at > now,
                    ImageAnalysisJob.state == "ready",
                    Group.status == "active",
                    Group.pilot_authorized.is_(True),
                    Group.schedule_version == 0,
                )
                .order_by(ImageScheduleSuggestion.created_at.desc())
                .limit(100)
            )
        ).all()
    applied = 0
    for ident in candidates:
        async with AsyncSession(engine) as db:
            suggestion = await db.get(ImageScheduleSuggestion, ident)
            if suggestion is None or suggestion.state != "pending":
                continue
            job = await db.get(ImageAnalysisJob, suggestion.image_job_id)
            if job is None or not job.result_json:
                continue
            try:
                reading = VisualReading.model_validate_json(job.result_json)
            except (ValueError, ValidationError):
                continue
            if await publish_image_first(db, job, suggestion, reading, now):
                applied += 1
            await db.commit()
    return applied
