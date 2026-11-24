"""Calendar-month retention does not depend on the rolling import window."""

import asyncio
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from studgroup.models import Base, Group, GroupScheduleSnapshot, RanepaPersonalSnapshot, User
from studgroup.ranepa_retention import expired_week_end, expiry_day, prune_expired_schedule


@pytest.mark.parametrize(
    ("week_end", "anniversary"),
    [
        (date(2026, 11, 29), date(2027, 2, 28)),
        (date(2027, 11, 28), date(2028, 2, 28)),
        (date(2026, 10, 25), date(2027, 1, 25)),
        (date(2026, 12, 31), date(2027, 3, 31)),
    ],
)
def test_calendar_month_retention(week_end, anniversary):
    assert expiry_day(week_end) == anniversary
    assert expired_week_end(anniversary) < week_end
    assert expired_week_end(anniversary + timedelta(days=1)) >= week_end


def test_expiry_preserves_recent_and_future_weeks(tmp_path):
    async def scenario():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/retention.db")
        group_id, user_id = uuid4(), uuid4()
        now = datetime(2027, 3, 1, tzinfo=UTC)
        expired = date(2026, 11, 23)
        kept = [date(2026, 11, 30), date(2027, 3, 1), date(2027, 4, 5)]
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            async with AsyncSession(engine) as db:
                db.add_all(
                    [
                        Group(
                            id=group_id,
                            telegram_chat_id=-97111,
                            name="Retention",
                            timezone="Europe/Moscow",
                        ),
                        User(id=user_id, telegram_user_id=97111, display_name="Student"),
                    ]
                )
                await db.flush()
                for monday in [expired, *kept]:
                    fields = {
                        "group_id": group_id,
                        "week_start": monday,
                        "week_end": monday + timedelta(days=6),
                        "data_digest": "a" * 64,
                        "rows_json": "[]",
                        "checked_at": now,
                    }
                    db.add(GroupScheduleSnapshot(**fields))
                    db.add(RanepaPersonalSnapshot(user_id=user_id, **fields))
                await db.commit()
            await prune_expired_schedule(engine, now.date())
            async with AsyncSession(engine) as db:
                for model in (GroupScheduleSnapshot, RanepaPersonalSnapshot):
                    assert list(
                        await db.scalars(select(model.week_start).order_by(model.week_start))
                    ) == kept
        finally:
            await engine.dispose()

    asyncio.run(scenario())
