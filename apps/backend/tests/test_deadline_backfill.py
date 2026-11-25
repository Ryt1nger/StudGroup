"""Existing homework is repaired once, from its original source date."""

import asyncio
import uuid
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from studgroup.deadline_backfill import repair_all
from studgroup.models import Group, Homework, Membership, PersonalCompletion, RawMessage, SchedulePattern
from studgroup.subject_identity import subject_uuid

pytest_plugins = ["test_schedule_api"]
MSK = ZoneInfo("Europe/Moscow")
SENT = datetime(2026, 10, 12, 12, tzinfo=MSK)
NOW = SENT.astimezone(UTC) + timedelta(days=10)
SUBJECT = "Математический анализ"


def test_existing_homework_uses_original_date_and_is_not_reprocessed_twice(client):
    async def run():
        engine = client.app.state.engine
        async with AsyncSession(engine, expire_on_commit=False) as db:
            group = await db.scalar(select(Group).where(Group.telegram_chat_id == -1001))
            member = await db.scalar(select(Membership).where(Membership.group_id == group.id))
            await db.execute(SchedulePattern.__table__.delete().where(SchedulePattern.group_id == group.id))
            db.add(
                SchedulePattern(
                    group_id=group.id,
                    subject=SUBJECT,
                    weekday=2,
                    starts=time(10, 40),
                    ends=time(12, 10),
                    valid_from=date(2026, 10, 5),
                    valid_until=date(2026, 12, 27),
                    week="second",
                )
            )
            ids = []
            for index, (text, verification) in enumerate(
                [
                    ("номер 5 письменно", "from_group_message"),
                    ("сдать до 22 октября", "from_group_message"),
                    ("сделать упражнения", "manual_confirmed"),
                    ("сдать 12.10-15.10", "from_group_message"),
                ],
                start=1,
            ):
                raw = RawMessage(
                    group_id=group.id,
                    telegram_message_id=100 + index,
                    sender_id=1,
                    text=text,
                    message_date=SENT,
                    version_date=SENT,
                    revision=1,
                    delete_at=NOW + timedelta(days=30),
                )
                db.add(raw)
                await db.flush()
                row = Homework(
                    id=uuid.uuid4(),
                    group_id=group.id,
                    raw_message_id=raw.id,
                    subject_id=subject_uuid(group.id, SUBJECT),
                    subject_name=SUBJECT,
                    title="Задание",
                    description=text,
                    verification_state=verification,
                    status="published",
                    source_message_at=SENT,
                    created_at=SENT,
                    updated_at=SENT,
                    delete_at=NOW + timedelta(days=30),
                    revision=1,
                )
                db.add(row)
                ids.append(row.id)
            await db.flush()
            db.add(
                PersonalCompletion(
                    homework_id=ids[0],
                    user_id=member.user_id,
                    completed_at=SENT,
                    completed_revision=1,
                )
            )
            await db.commit()

        preview = await repair_all(engine, now=NOW)
        assert preview["eligible"] == 1
        assert preview["already_due"] == 1
        assert preview.get("filled", 0) == 0
        result = await repair_all(engine, now=NOW, apply=True)
        assert result["filled"] == 1

        async with AsyncSession(engine) as db:
            first = await db.get(Homework, ids[0])
            assert first.deadline_at.replace(tzinfo=UTC) == datetime(
                2026, 10, 14, 7, 40, tzinfo=UTC
            )
            assert first.revision == 2
            assert first.verification_state == "inferred"
            for ident in ids[1:]:
                assert (await db.get(Homework, ident)).deadline_at is None
            done = await db.scalar(
                select(PersonalCompletion).where(PersonalCompletion.homework_id == ids[0])
            )
            assert done.completed_at is not None
            assert done.completed_revision == 2
        again = await repair_all(engine, now=NOW, apply=True)
        assert again.get("filled", 0) == 0

    asyncio.run(run())
