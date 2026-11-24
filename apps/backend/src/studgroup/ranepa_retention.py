"""Expire imported university timetable weeks only after three calendar months."""

from calendar import monthrange
from datetime import date, timedelta

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from studgroup.models import (
    GroupScheduleSnapshot,
    RanepaMaterializationReceipt,
    RanepaPersonalSnapshot,
)


def expiry_day(week_end: date) -> date:
    """Keep the entire three-month anniversary date, including month-end clipping."""
    year, month_index = divmod(week_end.year * 12 + week_end.month - 1 + 3, 12)
    month = month_index + 1
    return date(year, month, min(week_end.day, monthrange(year, month)[1]))


def expired_week_end(today: date) -> date:
    """Largest week-end date whose three-month anniversary has already passed."""
    cutoff = today - timedelta(days=80)
    while expiry_day(cutoff) >= today:
        cutoff -= timedelta(days=1)
    return cutoff


async def prune_expired_schedule(engine, today: date) -> None:
    """Expire only imported schedule data and its receipts, never manual lessons.

    Jobs, attempt histories, consent, credentials and manual schedules remain.
    """
    cutoff = expired_week_end(today)
    async with AsyncSession(engine) as db:
        await db.execute(
            delete(RanepaMaterializationReceipt).where(
                RanepaMaterializationReceipt.week_start <= cutoff - timedelta(days=6)
            )
        )
        await db.execute(
            delete(RanepaPersonalSnapshot).where(RanepaPersonalSnapshot.week_end <= cutoff)
        )
        await db.execute(
            delete(GroupScheduleSnapshot).where(GroupScheduleSnapshot.week_end <= cutoff)
        )
        await db.commit()
