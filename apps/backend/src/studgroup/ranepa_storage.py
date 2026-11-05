"""Isolated RANEPA source setup and validated snapshots, never live schedule writes."""

import json
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import delete, select

from studgroup.models import Group, GroupScheduleSnapshot, GroupScheduleSource
from studgroup.ranepa_schedule import WeekSnapshot, group_code, normalized


async def configure_source(db, group_id, code: str, upstream_uuid: UUID | None = None):
    """Store one canonical university code on Group; never enable sync implicitly."""
    group = await db.scalar(select(Group).where(Group.id == group_id).with_for_update())
    if group is None:
        raise ValueError("unknown_group")
    official_code = group_code(code)
    if len(official_code) > 80:
        raise ValueError("invalid_group_code")
    source = await db.scalar(
        select(GroupScheduleSource)
        .where(GroupScheduleSource.group_id == group_id)
        .with_for_update()
    )
    changed = group.academic_group_code is not None and normalized(
        group.academic_group_code
    ) != normalized(official_code)
    if source is None:
        source = GroupScheduleSource(
            group_id=group_id,
            upstream_group_uuid=upstream_uuid,
            collection_mode="public",
            sync_enabled=False,
            updated_at=datetime.now(UTC),
        )
        db.add(source)
    elif changed:
        source.upstream_group_uuid = upstream_uuid
        source.sync_enabled = False
        source.last_success_at = None
        source.last_checked_at = None
        source.last_error = None
    elif upstream_uuid is not None:
        source.upstream_group_uuid = upstream_uuid
    if changed:
        # Old source metadata and snapshots must never migrate to a new cohort.
        await db.execute(
            delete(GroupScheduleSnapshot).where(GroupScheduleSnapshot.group_id == group_id)
        )
    group.academic_group_code = official_code
    source.updated_at = datetime.now(UTC)
    await db.flush()
    return source


def encode_rows(snapshot: WeekSnapshot) -> str:
    rows = []
    for item in snapshot.indexed().values():
        rows.append(
            {
                "day": item.day.isoformat(),
                "starts": item.starts.isoformat(),
                "ends": item.ends.isoformat(),
                "subject": item.subject,
                "teacher": item.teacher,
                "room": item.room,
                "lesson_type": item.lesson_type,
                "subgroup_hint": item.subgroup_hint,
                "upstream_id": item.upstream_id,
                "cancelled": item.cancelled,
            }
        )
    rows.sort(
        key=lambda item: (
            item["day"],
            item["starts"],
            item["subject"],
            item["teacher"] or "",
            item["room"] or "",
        )
    )
    return json.dumps(rows, ensure_ascii=False, separators=(",", ":"))


async def remember_snapshot(db, group_id, snapshot: WeekSnapshot, *, checked_at=None):
    """Persist only complete, identity-verified source data in read-only storage.

    This function intentionally cannot mutate SchedulePattern or send notifications.
    """
    checked_at = checked_at or datetime.now(UTC)
    source = await db.scalar(
        select(GroupScheduleSource)
        .where(GroupScheduleSource.group_id == group_id)
        .with_for_update()
    )
    if source is None or source.upstream_group_uuid is None:
        raise ValueError("unverified_group_mapping")
    group = await db.get(Group, group_id)
    if group is None or not group.academic_group_code:
        raise ValueError("missing_group_code")
    if normalized(group_code(snapshot.group_code)) != normalized(group.academic_group_code):
        raise ValueError("mismatched_group_code")
    if snapshot.week_start.weekday() != 0 or (snapshot.week_end - snapshot.week_start).days != 6:
        raise ValueError("invalid_week_coverage")
    # Zero rows can mean a broken source, not an empty university week.
    if not snapshot.indexed():
        raise ValueError("empty_source_requires_review")
    existing = await db.get(GroupScheduleSnapshot, (group_id, snapshot.week_start))
    fingerprint = snapshot.digest()
    if existing is None:
        existing = GroupScheduleSnapshot(
            group_id=group_id,
            week_start=snapshot.week_start,
            week_end=snapshot.week_end,
            data_digest=fingerprint,
            rows_json=encode_rows(snapshot),
            checked_at=checked_at,
        )
        db.add(existing)
        status = "new"
    else:
        status = "unchanged" if existing.data_digest == fingerprint else "changed"
        existing.week_end = snapshot.week_end
        existing.data_digest = fingerprint
        existing.rows_json = encode_rows(snapshot)
        existing.checked_at = checked_at
    source.last_checked_at = checked_at
    source.last_success_at = checked_at
    source.last_error = None
    await db.flush()
    return status
