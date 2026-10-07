"""Repair existing inferred homework dates without AI calls or new-change announcements."""

from datetime import UTC, timedelta
from zoneinfo import ZoneInfo

import sqlalchemy as sa
from alembic import op
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from studgroup.api import schedule_data
from studgroup.deadlines import ScheduleDeadlineContext, resolve_deadline
from studgroup.models import Group, Homework, RawMessage

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


async def repair(connection):
    changed = 0
    unknown = 0
    async with AsyncSession(bind=connection) as db:
        rows = (
            await db.scalars(select(Homework).where(Homework.verification_state == "inferred"))
        ).all()
        for row in rows:
            raw = await db.get(RawMessage, row.raw_message_id) if row.raw_message_id else None
            if raw is None:
                continue
            group = await db.get(Group, row.group_id)
            sent = raw.message_date
            if sent.tzinfo is None:
                sent = sent.replace(tzinfo=UTC)
            if row.source_message_at is None:
                row.source_message_at = sent
            day = sent.astimezone(ZoneInfo(group.timezone)).date()
            end = day + timedelta(days=14)
            calendar = await schedule_data(day, end, group, db)
            schedule = ScheduleDeadlineContext(
                calendar["lessons"], day, end, calendar["week_state"] == "ready"
            )
            resolved = resolve_deadline(
                [(raw.text, sent)], group.timezone, row.subject_name, schedule=schedule
            )
            previous = row.deadline_at
            if previous and previous.tzinfo is None:
                previous = previous.replace(tzinfo=UTC)
            if previous != resolved.at or row.deadline_date_only != resolved.date_only:
                row.deadline_at = resolved.at.astimezone(UTC) if resolved.at else None
                row.deadline_date_only = resolved.date_only
                row.revision += 1
                changed += 1
            if resolved.at is None:
                unknown += 1
            # Keep inferred provenance even when historical timetable coverage is absent.
            # Do not reset source age, personal completion, updated/significant timestamps.
        await db.flush()
        print(
            f"HOMEWORK_SOURCE_REPAIR inferred={len(rows)} corrected={changed} historical_unknown={unknown}"
        )


def upgrade():
    op.add_column(
        "homework", sa.Column("source_message_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.execute(
        "UPDATE homework SET source_message_at = (SELECT message_date FROM raw_messages WHERE raw_messages.id = homework.raw_message_id) WHERE raw_message_id IS NOT NULL"
    )
    op.run_async(repair)


def downgrade():
    # Incorrect guessed dates must not be fabricated again on downgrade.
    op.drop_column("homework", "source_message_at")
