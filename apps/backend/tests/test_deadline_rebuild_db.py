"""A schedule edit by the headman rebuilds timetable-derived deadlines (two-week cycle aware)."""

import asyncio
import json
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from test_deadline_application_db import MSK, SENT, proposal, run_one, seed

from studgroup.analysis.card_editor import EditedCard  # noqa: F401  (shared helpers use it)
from studgroup.deadline_rebuild import rebuild_after_schedule_change
from studgroup.models import (
    CanonicalDeadlineHistory,
    CanonicalItemEvidence,
    CanonicalItemRevision,
    SchedulePattern,
)

pytest_plugins = ["test_schedule_api"]
NOW = SENT.astimezone(UTC)


async def pattern(db, group):
    return await db.scalar(select(SchedulePattern).where(SchedulePattern.group_id == group.id))


def test_moving_the_second_week_lesson_moves_the_inferred_deadline_and_keeps_history(client):
    async def run():
        async with AsyncSession(client.app.state.engine, expire_on_commit=False) as db:
            group = await seed(db, "номер 5 письменно")
            created = await run_one(db, group, "номер 5 письменно", proposal())
            item = created.item
            assert item.deadline_at.astimezone(MSK).date() == date(2026, 10, 14)
            (await pattern(db, group)).weekday = 3  # Wednesday -> Thursday
            await db.flush()
            changes = await rebuild_after_schedule_change(db, group, NOW + timedelta(minutes=5))
            assert len(changes) == 1
            assert item.deadline_at.astimezone(MSK).date() == date(2026, 10, 15)
            assert item.revision == 2
            history = (await db.scalars(select(CanonicalDeadlineHistory))).all()
            assert sorted(row.item_revision for row in history) == [1, 2]
            actions = (await db.scalars(select(CanonicalItemRevision.action))).all()
            assert "schedule_change" in actions
            evidence = (await db.scalars(select(CanonicalItemEvidence))).all()
            first = [e for e in evidence if e.item_revision == 1]
            second = [e for e in evidence if e.item_revision == 2]
            assert first and len(first) == len(second)  # the card keeps its sources

    asyncio.run(run())


def test_an_unaffected_card_and_a_locked_card_are_left_alone(client):
    async def run():
        async with AsyncSession(client.app.state.engine, expire_on_commit=False) as db:
            group = await seed(db, "номер 5 письменно")
            created = await run_one(db, group, "номер 5 письменно", proposal())
            item = created.item
            before = item.deadline_at
            # the FIRST-week pattern changes; the card waits for a second-week lesson
            db.add(
                SchedulePattern(
                    group_id=group.id,
                    subject="Другой предмет",
                    weekday=1,
                    starts=(await pattern(db, group)).starts,
                    ends=(await pattern(db, group)).ends,
                    valid_from=date(2026, 10, 5),
                    valid_until=date(2026, 12, 27),
                    week="first",
                )
            )
            await db.flush()
            assert await rebuild_after_schedule_change(db, group, NOW) == []
            assert item.deadline_at == before
            item.locked_fields = json.dumps(["deadline"])
            (await pattern(db, group)).weekday = 3
            await db.flush()
            assert await rebuild_after_schedule_change(db, group, NOW) == []
            assert item.deadline_at == before

    asyncio.run(run())


def test_a_cancelled_lesson_makes_the_deadline_unknown_and_asks_the_headman(client):
    async def run():
        async with AsyncSession(client.app.state.engine, expire_on_commit=False) as db:
            group = await seed(db, "номер 5 письменно")
            created = await run_one(db, group, "номер 5 письменно", proposal())
            (await pattern(db, group)).cancelled = True
            await db.flush()
            await rebuild_after_schedule_change(db, group, NOW)
            item = created.item
            assert item.deadline_at is None
            assert item.deadline_reason == "cancelled_lesson_in_window"
            assert "deadline_needs_headman" in json.loads(item.attention_reasons)

    asyncio.run(run())


def test_a_card_whose_deadline_already_passed_is_not_rebuilt(client):
    async def run():
        async with AsyncSession(client.app.state.engine, expire_on_commit=False) as db:
            group = await seed(db, "номер 5 письменно")
            created = await run_one(db, group, "номер 5 письменно", proposal())
            (await pattern(db, group)).weekday = 3
            await db.flush()
            later = datetime(2026, 10, 20, 12, tzinfo=UTC)  # 14 Oct is long past
            assert await rebuild_after_schedule_change(db, group, later) == []
            assert created.item.deadline_at.astimezone(MSK).date() == date(2026, 10, 14)

    asyncio.run(run())


def test_undoing_a_schedule_edit_restores_the_inferred_deadline(client, monkeypatch):
    from studgroup import headman_domain, headman_schedule, schedule_audit
    from studgroup.models import Group, Membership, ScheduleChange, User

    async def at_now(db, group, _now):  # the edit and the undo run at the test's clock
        return await rebuild_after_schedule_change(db, group, NOW + timedelta(minutes=5))

    monkeypatch.setattr(schedule_audit, "rebuild_after_schedule_change", at_now)

    async def run():
        async with AsyncSession(client.app.state.engine, expire_on_commit=False) as db:
            group = await seed(db, "номер 5 письменно")
            item = (await run_one(db, group, "номер 5 письменно", proposal())).item
            assert item.deadline_at.astimezone(MSK).date() == date(2026, 10, 14)
            member = await db.scalar(select(Membership).where(Membership.group_id == group.id))
            member.role = "headman"
            user = await db.get(User, member.user_id)
            lesson = await pattern(db, group)
            aid = await headman_schedule.change(
                db,
                group,
                user,
                member,
                f"{lesson.id}:2026-10-14",
                lesson.revision,
                0,
                "recurring",
                {
                    "starts_at": datetime(2026, 10, 15, 10, 40, tzinfo=MSK),
                    "ends_at": datetime(2026, 10, 15, 12, 10, tzinfo=MSK),
                },
            )
            assert item.deadline_at.astimezone(MSK).date() == date(2026, 10, 15)
            await headman_domain.undo(db, group, user, member, aid)
            assert lesson.weekday == 2
            assert item.deadline_at.astimezone(MSK).date() == date(2026, 10, 14)
            history = (await db.scalars(select(CanonicalDeadlineHistory))).all()
            assert sorted(row.item_revision for row in history) == [1, 2, 3]
            changes = (
                await db.scalars(select(ScheduleChange).order_by(ScheduleChange.schedule_version))
            ).all()
            assert [(c.operation, c.schedule_version, c.actor_role) for c in changes] == [
                ("lesson_recurring", 1, "headman"),
                ("undo", 2, "headman"),
            ]
            assert (await db.get(Group, group.id)).schedule_version == 2

    asyncio.run(run())
