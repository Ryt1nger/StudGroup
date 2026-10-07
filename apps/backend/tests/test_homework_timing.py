import asyncio
from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from studgroup.homework_timing import lesson_deadline
from studgroup.models import Group

pytest_plugins = ["test_schedule_api"]


@pytest.mark.parametrize(
    "subject,date_only,text,expected_day,expected_hour,expected_only",
    [
        ("Математика", True, "ДЗ на понедельник", 12, 7, False),
        ("Другой предмет", True, "ДЗ на понедельник", 12, 0, True),
        ("Математика", False, "Сдать в 18:00", 12, 15, False),
        ("Математика", True, "Сдать до конца дня", 12, 0, True),
    ],
)
def test_date_only_homework_uses_subject_lesson_end_without_overriding_explicit_time(
    client, subject, date_only, text, expected_day, expected_hour, expected_only
):
    async def run():
        async with AsyncSession(client.app.state.engine) as db:
            group = await db.scalar(select(Group).where(Group.telegram_chat_id == -1001))
            at = datetime(2026, 10, 12, hour=0 if date_only else 15, tzinfo=UTC)
            result, only = await lesson_deadline(db, group, subject, at, date_only, text)
            assert (result.day, result.hour, only) == (expected_day, expected_hour, expected_only)

    asyncio.run(run())
