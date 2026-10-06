import asyncio
from datetime import date, time

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from studgroup.local_preview import repeat_next_week
from studgroup.models import Group, SchedulePattern

pytest_plugins = ["test_schedule_api"]


def test_explicit_test_copy_is_scoped_and_idempotent(client):
    async def run():
        async with AsyncSession(client.app.state.engine) as db:
            group = await db.scalar(select(Group).where(Group.telegram_chat_id == -1001))
            db.add(
                SchedulePattern(
                    group_id=group.id,
                    subject="Тестовая пара",
                    weekday=1,
                    starts=time(9),
                    ends=time(10),
                    week="all",
                    valid_from=date(2026, 10, 5),
                    valid_until=date(2026, 10, 11),
                )
            )
            await db.flush()
            assert await repeat_next_week(db, group, date(2026, 10, 5), date(2026, 10, 11)) == 1
            assert await repeat_next_week(db, group, date(2026, 10, 5), date(2026, 10, 11)) == 0
            await db.commit()

    asyncio.run(run())
    headers = {"Authorization": "Bearer valid"}
    response = client.get("/v1/schedule?start=2026-10-12&end=2026-10-18", headers=headers)
    copied = [
        lesson for lesson in response.json()["lessons"] if lesson["subject"] == "Тестовая пара"
    ]
    assert len(copied) == 1
    assert copied[0]["starts_at"].startswith("2026-10-13T09:")
    response = client.get("/v1/schedule?start=2026-10-19&end=2026-10-25", headers=headers)
    assert not any(lesson["subject"] == "Тестовая пара" for lesson in response.json()["lessons"])
