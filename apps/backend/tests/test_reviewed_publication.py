import asyncio
from datetime import date, datetime, time

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from studgroup.main import Settings
from studgroup.models import Group, SchedulePattern
from studgroup.reviewed_publication import apply, unpack

pytest_plugins = ["test_schedule_api"]


def test_reviewed_export_publishes_future_homework_and_keeps_expired_task_archived(client):
    async def run():
        async with AsyncSession(client.app.state.engine) as db:
            group = await db.scalar(select(Group).where(Group.telegram_chat_id == -1001))
            for subject, weekday, starts, ends in [
                ("Основы российской государственности", 0, time(12, 20), time(13, 40)),
                ("Основы российской государственности", 0, time(14), time(15, 20)),
                ("Математический анализ", 4, time(10, 40), time(12)),
            ]:
                db.add(
                    SchedulePattern(
                        group_id=group.id,
                        subject=subject,
                        weekday=weekday,
                        starts=starts,
                        ends=ends,
                        valid_from=date(2026, 10, 5),
                        valid_until=date(2026, 10, 11),
                        week="all",
                    )
                )
            await db.commit()
        payload = {
            "version": 1,
            "owner_id": 1,
            "chat_id": -1001,
            "repeat_schedule": {
                "from": "2026-10-05",
                "until": "2026-10-11",
                "next_until": "2026-10-18",
            },
            "sources": [
                {
                    "message_id": 3632,
                    "message_date": "2026-10-05T14:07:08+03:00",
                    "text": "Составить сравнительную таблицу, в дз",
                },
                {
                    "message_id": 3845,
                    "message_date": "2026-10-08T12:34:06+03:00",
                    "text": "#матан стр.69 вопросы 1–8, стр.75 номер 5.1–5.4",
                },
            ],
            "entries": [],
        }
        for mid, subject in [
            (3632, "Основы российской государственности"),
            (3845, "Математический анализ"),
        ]:
            payload["entries"].append(
                {
                    "evidence_reviewed": True,
                    "assignment": {
                        "kind": "homework",
                        "subject": subject,
                        "title": "Задание",
                        "description": "Решить",
                        "deadline_at": None,
                        "deadline_date_only": False,
                        "urgency": "normal",
                        "confidence": 75,
                        "source_message_ids": [mid],
                    },
                }
            )
        cfg = Settings(_env_file=None, owner_telegram_user_id=1)
        now = datetime.fromisoformat("2026-10-09T08:00:00+00:00")
        async with AsyncSession(client.app.state.engine) as db:
            result = await apply(db, cfg, payload, "test-evidence", now)
            await db.commit()
            assert result["published"] == 2
            assert result["schedule_patterns_extended"] == 3
            assert len(result["active_homework"]) == 1
            assert result["active_homework"][0]["subject"] == "Основы российской государственности"
            assert result["active_homework"][0]["deadline"] == "2026-10-12T09:20:00+00:00"
        async with AsyncSession(client.app.state.engine) as db:
            assert await apply(db, cfg, payload, "test-evidence", now) == {
                "already_committed": True
            }
        payload["owner_id"] = 2
        async with AsyncSession(client.app.state.engine) as db:
            with pytest.raises(ValueError, match="owner_or_chat"):
                await apply(db, cfg, payload, "wrong-owner", now)

    asyncio.run(run())


def test_private_payload_requires_matching_integrity_hash():
    with pytest.raises(ValueError, match="integrity"):
        unpack("not a payload", "wrong")
