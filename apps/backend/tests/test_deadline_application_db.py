"""The deadline policy, run through the canonical resolver against a real (SQLite) database."""

import asyncio
import json
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from studgroup.analysis.card_editor import EditedCard
from studgroup.analysis.contracts import EvidenceRef, ProposedAcademicItem, SourceMessage
from studgroup.analysis.deadline_application import apply_policy
from studgroup.analysis.resolver import resolve_proposal
from studgroup.deadline_context import build_deadline_context
from studgroup.models import (
    CanonicalDeadlineHistory,
    Group,
    GroupNoLessonDay,
    RawMessage,
    SchedulePattern,
)

pytest_plugins = ["test_schedule_api"]
MSK = ZoneInfo("Europe/Moscow")
MATAN = "Математический анализ"
SENT = datetime(2026, 10, 12, 12, tzinfo=MSK)  # Monday of the SECOND week (anchor 5 Oct)


def proposal(kind="homework", text_has_date=False, action="create", existing=None, revision=None):
    return ProposedAcademicItem(
        action=action,
        existing_item_id=existing,
        expected_item_revision=revision,
        destination="task" if kind == "homework" else "deadline",
        semantic_kind=kind,
        subject=MATAN,
        title="Задание",
        description="Задание.",
        deadline={"at": None, "date_only": False, "basis": "unknown"},
        confidence=90,
        evidence=[EvidenceRef(telegram_message_id=1, revision=1, fact_paths=["description"])],
    )


def card(item):
    return EditedCard(
        title=item.title, description=item.description, visibility="normal", subject=MATAN
    )


def message(text):
    return [SourceMessage(telegram_message_id=1, revision=1, message_date=SENT, text=text)]


async def seed(db, text, lesson_weekday=2, week="second"):
    group = await db.scalar(select(Group).where(Group.telegram_chat_id == -1001))
    db.add(
        RawMessage(
            group_id=group.id,
            telegram_message_id=1,
            sender_id=1,
            text=text,
            message_date=SENT.astimezone(UTC),
            version_date=SENT.astimezone(UTC),
            revision=1,
            delete_at=SENT.astimezone(UTC) + timedelta(days=30),
        )
    )
    await db.execute(SchedulePattern.__table__.delete().where(SchedulePattern.group_id == group.id))
    db.add(
        SchedulePattern(
            group_id=group.id,
            subject=MATAN,
            weekday=lesson_weekday,
            starts=time(10, 40),
            ends=time(12, 10),
            valid_from=date(2026, 10, 5),
            valid_until=date(2026, 12, 27),
            week=week,
        )
    )
    await db.flush()
    return group


async def run_one(db, group, text, item, **kwargs):
    context = await build_deadline_context(db, group)
    applied = apply_policy(item, message(text), MATAN, context)
    return await resolve_proposal(
        db,
        group_id=group.id,
        proposal=item,
        edited_card=card(item),
        now=SENT.astimezone(UTC),
        deadline=applied,
        **kwargs,
    )


def test_homework_without_a_date_gets_the_next_lesson_of_the_right_week_and_a_history_row(client):
    async def run():
        async with AsyncSession(client.app.state.engine, expire_on_commit=False) as db:
            group = await seed(db, "номер 5 письменно")
            result = await run_one(db, group, "номер 5 письменно", proposal())
            await db.commit()
            item = result.item
            assert item.deadline_basis == "schedule_inferred"
            assert item.deadline_reason == "next_subject_lesson"
            assert item.deadline_at.astimezone(MSK).date() == date(2026, 10, 14)
            assert "deadline_inferred_confirm" in json.loads(item.attention_reasons)
            rows = (await db.scalars(select(CanonicalDeadlineHistory))).all()
            assert len(rows) == 1 and rows[0].source_telegram_message_id == 1

    asyncio.run(run())


def test_the_first_week_pattern_does_not_serve_a_second_week_message(client):
    async def run():
        async with AsyncSession(client.app.state.engine, expire_on_commit=False) as db:
            group = await seed(db, "номер 5 письменно", week="first")
            result = await run_one(db, group, "номер 5 письменно", proposal())
            # the next first-week Wednesday after Mon 12 Oct is 21 Oct, not 14 Oct
            assert result.item.deadline_at.astimezone(MSK).date() == date(2026, 10, 21)

    asyncio.run(run())


def test_a_day_without_lessons_or_a_cancelled_lesson_leaves_the_deadline_unknown(client):
    async def run():
        async with AsyncSession(client.app.state.engine, expire_on_commit=False) as db:
            group = await seed(db, "номер 5 письменно")
            db.add(
                GroupNoLessonDay(
                    group_id=group.id, day=date(2026, 10, 13), reason="праздник", created_at=SENT
                )
            )
            await db.flush()
            result = await run_one(db, group, "номер 5 письменно", proposal())
            assert result.item.deadline_at is None
            assert result.item.deadline_reason == "no_lesson_day_in_window"

    asyncio.run(run())


def test_a_control_point_without_a_date_is_published_and_the_headman_is_asked(client):
    async def run():
        async with AsyncSession(client.app.state.engine, expire_on_commit=False) as db:
            group = await seed(db, "будет контрольная")
            result = await run_one(db, group, "будет контрольная", proposal("control_point"))
            assert result.action == "create"
            assert result.item.deadline_at is None
            assert "deadline_needs_headman" in json.loads(result.item.attention_reasons)

    asyncio.run(run())


def test_a_date_in_the_text_beats_the_model_and_an_unconfirmed_timezone_is_flagged(client):
    async def run():
        async with AsyncSession(client.app.state.engine, expire_on_commit=False) as db:
            group = await seed(db, "сдать до 20 октября")
            group.timezone = ""
            item = proposal()
            item = item.model_copy(
                update={
                    "deadline": item.deadline.model_copy(
                        update={
                            "at": datetime(2026, 10, 25, tzinfo=MSK),
                            "date_only": True,
                            "basis": "explicit",
                        }
                    )
                }
            )
            result = await run_one(db, group, "сдать до 20 октября", item)
            assert result.item.deadline_at.astimezone(MSK).date() == date(2026, 10, 20)
            assert result.item.timezone_confirmed is False
            assert "timezone_unconfirmed" in json.loads(result.item.attention_reasons)

    asyncio.run(run())


def test_a_locked_deadline_is_kept_and_the_blocked_change_is_flagged(client):
    async def run():
        async with AsyncSession(client.app.state.engine, expire_on_commit=False) as db:
            group = await seed(db, "номер 5 письменно")
            created = await run_one(db, group, "номер 5 письменно", proposal())
            before = created.item.deadline_at
            created.item.locked_fields = json.dumps(["deadline"])
            await db.flush()
            again = proposal(action="update", existing=created.item.id, revision=1)
            updated = await run_one(
                db,
                group,
                "сдать до 30 октября",
                again,
                action="update",
                existing_item_id=created.item.id,
                expected_item_revision=1,
            )
            assert updated.item.deadline_at == before
            assert "ai_change_blocked_by_lock" in json.loads(updated.item.attention_reasons)

    asyncio.run(run())


def test_a_reschedule_without_an_unambiguous_new_date_goes_to_review(client):
    async def run():
        async with AsyncSession(client.app.state.engine, expire_on_commit=False) as db:
            group = await seed(db, "номер 5 письменно")
            created = await run_one(db, group, "номер 5 письменно", proposal("control_point"))
            moved = proposal(
                "control_point", action="reschedule", existing=created.item.id, revision=1
            )
            result = await run_one(
                db,
                group,
                "контрольную перенесли",
                moved,
                action="reschedule",
                existing_item_id=created.item.id,
                expected_item_revision=1,
            )
            assert (result.action, result.reason_code) == ("review", "reschedule_not_unambiguous")

    asyncio.run(run())
