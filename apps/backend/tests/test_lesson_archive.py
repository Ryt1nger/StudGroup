"""Date-only cards linked to a real lesson leave active lists at lesson end."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from test_homework import seed_card

from studgroup import lesson_archive
from studgroup.academic_deadlines import archived as archived_deadline
from studgroup.homework import archived_condition, completed_lesson_bindings
from studgroup.lesson_archive import lesson_cutoff, lesson_day_archived, today_lesson_cutoffs
from studgroup.models import Homework

pytest_plugins = ["test_schedule_api"]
MSK = ZoneInfo("Europe/Moscow")
TZ = "Europe/Moscow"


def stamp(hour, minute=0):
    return datetime(2026, 11, 2, hour, minute, tzinfo=MSK)


def lesson(subject, start, end, *, subgroup=None, status="scheduled"):
    return {
        "subject": subject,
        "status": status,
        "subgroup_id": subgroup,
        "starts_at": stamp(start).isoformat(),
        "ends_at": stamp(end).isoformat(),
    }


def test_last_subject_lesson_controls_date_only_archive(monkeypatch):
    async def schedule_data(day_start, day_end, group, db):
        assert day_start == day_end == stamp(0).date()
        return {
            "week_state": "ready",
            "lessons": [
                lesson("Математика", 9, 10),
                lesson("Математика", 12, 13),
                lesson("Математика", 14, 15, status="cancelled"),
                lesson("Физика", 9, 11),
            ],
        }

    monkeypatch.setattr(lesson_archive, "schedule_data", schedule_data)
    group = SimpleNamespace(timezone=TZ)
    cutoffs = asyncio.run(today_lesson_cutoffs(None, group, stamp(10, 30)))
    assert lesson_cutoff("МАТЕМАТИКА", None, cutoffs) == stamp(13).astimezone(UTC)
    due = stamp(0)
    assert not lesson_day_archived("Математика", None, due, True, stamp(12, 59), TZ, cutoffs)
    assert lesson_day_archived("математика", None, due, True, stamp(13), TZ, cutoffs)
    assert not lesson_day_archived("Математика", None, due, False, stamp(14), TZ, cutoffs)
    assert not lesson_day_archived("Иностранный язык", None, due, True, stamp(14), TZ, cutoffs)
    assert not lesson_day_archived(
        "Математика", None, due + timedelta(days=1), True, stamp(14), TZ, cutoffs
    )


def test_subgroups_and_missing_schedule_do_not_archive_wrong_cards(monkeypatch):
    async def schedule_data(day_start, day_end, group, db):
        return {
            "week_state": "ready",
            "lessons": [lesson("Иностранный язык", 10, 11, subgroup="subgroup-1")],
        }

    monkeypatch.setattr(lesson_archive, "schedule_data", schedule_data)
    cutoffs = asyncio.run(today_lesson_cutoffs(None, SimpleNamespace(timezone=TZ), stamp(11)))
    assert lesson_cutoff("Иностранный язык", "subgroup-1", cutoffs) == stamp(11).astimezone(UTC)
    assert lesson_cutoff("Иностранный язык", "subgroup-2", cutoffs) is None
    assert lesson_cutoff("Иностранный язык", None, cutoffs) is None

    async def missing(day_start, day_end, group, db):
        return {"week_state": "unknown", "lessons": [lesson("Математика", 9, 10)]}

    monkeypatch.setattr(lesson_archive, "schedule_data", missing)
    assert asyncio.run(today_lesson_cutoffs(None, SimpleNamespace(timezone=TZ), stamp(14))) == {}


def test_date_only_academic_deadline_archives_at_lesson_end():
    due = stamp(0)
    row = SimpleNamespace(
        cancelled_at=None,
        deadline_at=due,
        date_only=True,
        window_end=None,
        subject="Математика",
        subgroup_id=None,
    )
    cutoffs = {("математика", None): stamp(11).astimezone(UTC)}
    assert not archived_deadline(row, stamp(10, 59), TZ, cutoffs)
    assert archived_deadline(row, stamp(11), TZ, cutoffs)
    assert not archived_deadline(row, stamp(11), TZ)
    row.date_only = False
    row.deadline_at = stamp(18)
    assert not archived_deadline(row, stamp(11), TZ, cutoffs)


def test_homework_sql_archive_predicate_uses_subject_and_date_only(client, monkeypatch):
    card_id = uuid.UUID(seed_card(client))

    async def fake_cutoffs(db, group, now):
        return {("математика", None): stamp(11).astimezone(UTC)}

    monkeypatch.setattr("studgroup.homework.today_lesson_cutoffs", fake_cutoffs)

    async def check():
        async with AsyncSession(client.app.state.engine) as db:
            item = await db.get(Homework, card_id)
            item.subject_name = "Математика"
            item.deadline_at = stamp(0).astimezone(UTC)
            item.deadline_date_only = True
            await db.flush()
            before = stamp(10, 59).astimezone(UTC)
            after = stamp(11).astimezone(UTC)
            assert (
                await completed_lesson_bindings(db, SimpleNamespace(id=item.group_id), before) == []
            )
            completed = await completed_lesson_bindings(
                db, SimpleNamespace(id=item.group_id), after
            )
            assert completed == [("Математика", None)]
            before_query = select(Homework.id).where(
                Homework.id == card_id, archived_condition(before, TZ, completed)
            )
            after_query = select(Homework.id).where(
                Homework.id == card_id, archived_condition(after, TZ, completed)
            )
            assert await db.scalar(before_query) is None
            assert await db.scalar(after_query) == card_id
            # A different subject or an explicitly timed later deadline remains active.
            item.subject_name = "Физика"
            await db.flush()
            assert await db.scalar(after_query) is None
            item.subject_name = "Математика"
            item.deadline_date_only = False
            item.deadline_at = stamp(18).astimezone(UTC)
            await db.flush()
            assert await db.scalar(after_query) is None

    asyncio.run(check())
