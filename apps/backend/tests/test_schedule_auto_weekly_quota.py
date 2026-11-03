"""Automatic timetable publication quota; manual edits remain unlimited.

No provider requests, scheduling worker, or production timetable changes are used.
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from test_staff_api import db_run

from studgroup.models import Group, ScheduleChange
from studgroup.schedule_audit import (
    AUTOMATIC_SCHEDULE_WEEKLY_LIMIT,
    AutomaticScheduleLimitReached,
    schedule_changed,
)

pytest_plugins = ["test_schedule_api"]


def record(client, *, group_chat=-1001, method="ai_auto", at=None, scope="group"):
    async def commit(db):
        group = await db.scalar(select(Group).where(Group.telegram_chat_id == group_chat))
        await schedule_changed(
            db,
            group,
            method=method,
            operation="lesson_once",
            scope=scope,
            now=at or datetime(2026, 11, 3, 12, 0, tzinfo=UTC),
        )
        return group.schedule_version

    return db_run(client, commit)


def audit(client, *, group_chat=-1001):
    async def read(db):
        group = await db.scalar(select(Group).where(Group.telegram_chat_id == group_chat))
        rows = await db.scalars(
            select(ScheduleChange)
            .where(ScheduleChange.group_id == group.id)
            .order_by(ScheduleChange.schedule_version)
        )
        return group.schedule_version, [
            (row.input_method, row.scope, row.schedule_version) for row in rows
        ]

    return db_run(client, read)


def test_automatic_updates_stop_after_two_committed_group_changes_per_week(client):
    assert AUTOMATIC_SCHEDULE_WEEKLY_LIMIT == 2
    assert record(client, method="ai_auto") == 1
    assert record(client, method="image_auto") == 2
    with pytest.raises(AutomaticScheduleLimitReached, match="2 из 2"):
        record(client, method="schedule_auto")
    assert audit(client) == (
        2,
        [("ai_auto", "group", 1), ("image_auto", "group", 2)],
    )


def test_unlimited_manual_headman_cabinet_bot_and_approved_image_changes(client):
    for method in ["ai_auto", "ai_auto"]:
        record(client, method=method)
    for _ in range(4):
        for method in ("cabinet", "bot", "image_confirm"):
            record(client, method=method)
    version, events = audit(client)
    assert version == 14
    assert len(events) == 14
    assert sum(method == "ai_auto" for method, _, _ in events) == 2
    with pytest.raises(AutomaticScheduleLimitReached):
        record(client, method="ai_auto")
    assert audit(client) == (version, events)


def test_weekly_quota_uses_group_local_monday_not_utc_or_iso_week(client):
    # Sunday 1 Nov 20:59 UTC is Sun 23:59 Moscow; 21:00 UTC is Mon 2 Nov there.
    sunday = datetime(2026, 11, 1, 20, 59, tzinfo=UTC)
    monday = sunday + timedelta(minutes=1)
    record(client, at=sunday)
    record(client, at=sunday)
    record(client, at=monday)
    record(client, at=monday)
    with pytest.raises(AutomaticScheduleLimitReached):
        record(client, at=monday)
    assert audit(client)[0] == 4


def test_group_quota_is_isolated(client):
    for _ in range(2):
        record(client, group_chat=-1001)
    assert record(client, group_chat=-1002) == 1
    with pytest.raises(AutomaticScheduleLimitReached):
        record(client, group_chat=-1001)
    assert record(client, group_chat=-1002) == 2


def test_personal_scope_does_not_consume_group_automatic_slots(client):
    for _ in range(5):
        record(client, method="ai_auto", scope="personal")
    assert record(client, method="ai_auto", scope="group") == 6
    assert record(client, method="ai_auto", scope="group") == 7
    with pytest.raises(AutomaticScheduleLimitReached):
        record(client, method="ai_auto", scope="group")
    assert audit(client)[0] == 7
