"""Archiving follows the SHOWN deadline: exact time at that moment, date-only at the end of the
group's calendar day (spec section 7; a midnight timestamp with the date-only flag is not early)."""

import asyncio
import uuid
from datetime import UTC, datetime, time, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession
from test_homework import seed_card

from studgroup.academic_deadlines import archived
from studgroup.models import Homework

pytest_plugins = ["test_schedule_api"]
TZ = "Europe/Moscow"
MSK = ZoneInfo(TZ)


def row(at, date_only):
    return SimpleNamespace(cancelled_at=None, deadline_at=at, date_only=date_only, window_end=None)


def test_date_only_deadline_is_archived_only_after_its_calendar_day_ends():
    midnight = datetime(2026, 10, 14, 0, 0, tzinfo=MSK)
    assert not archived(row(midnight, True), datetime(2026, 10, 14, 0, 0, tzinfo=MSK), TZ)
    assert not archived(row(midnight, True), datetime(2026, 10, 14, 23, 59, tzinfo=MSK), TZ)
    assert archived(row(midnight, True), datetime(2026, 10, 15, 0, 0, tzinfo=MSK), TZ)


def test_exact_time_deadline_is_archived_at_that_moment_not_before():
    at = datetime(2026, 10, 14, 10, 40, tzinfo=MSK)
    assert not archived(row(at, False), at - timedelta(seconds=1), TZ)
    assert archived(row(at, False), at, TZ)


def test_homework_list_uses_the_same_boundaries(client):
    today = datetime.now(UTC).astimezone(MSK).date()
    start_today = datetime.combine(today, time.min, MSK)
    card_id = seed_card(client)
    headers = {"Authorization": "Bearer valid"}

    def set_deadline(at):
        async def update():
            async with AsyncSession(client.app.state.engine) as db:
                row_ = await db.get(Homework, uuid.UUID(card_id))
                row_.deadline_at, row_.deadline_date_only = at, True
                await db.commit()

        asyncio.run(update())

    def in_archive():
        items = client.get("/v1/homework?filter=archive", headers=headers).json()["items"]
        return card_id in {r["id"] for r in items}

    set_deadline(start_today)  # today, date only: active until the day ends
    assert not in_archive()
    set_deadline(start_today - timedelta(days=1))  # yesterday: the day has ended
    assert in_archive()
