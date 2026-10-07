"""Repair existing inferred homework dates without AI calls or new-change announcements."""

from datetime import UTC, timedelta
from zoneinfo import ZoneInfo

import sqlalchemy as sa
from alembic import op

from studgroup.deadlines import ScheduleDeadlineContext, resolve_deadline
from studgroup.migration_calendar import calendar, tables

revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


async def repair(connection):
    schema = await tables(connection)
    hw, raw, groups, patterns = [
        schema[t] for t in ["homework", "raw_messages", "groups", "schedule_patterns"]
    ]
    rows = (
        (
            await connection.execute(
                sa.select(
                    hw,
                    raw.c.message_date,
                    raw.c.text.label("source_text"),
                    groups.c.timezone,
                    groups.c.first_week_anchor,
                )
                .join(raw, raw.c.id == hw.c.raw_message_id)
                .join(groups, groups.c.id == hw.c.group_id)
                .where(hw.c.verification_state == "inferred")
            )
        )
        .mappings()
        .all()
    )
    changed = unknown = 0
    for row in rows:
        sent = row["message_date"]
        if sent.tzinfo is None:
            sent = sent.replace(tzinfo=UTC)
        day = sent.astimezone(ZoneInfo(row["timezone"])).date()
        end = day + timedelta(days=14)
        lessons, ready = await calendar(
            connection,
            patterns,
            row["group_id"],
            day,
            end,
            row["timezone"],
            row["first_week_anchor"],
        )
        resolved = resolve_deadline(
            [(row["source_text"], sent)],
            row["timezone"],
            row["subject_name"],
            schedule=ScheduleDeadlineContext(lessons, day, end, ready),
        )
        previous = row["deadline_at"]
        if previous and previous.tzinfo is None:
            previous = previous.replace(tzinfo=UTC)
        values = {"source_message_at": row["source_message_at"] or sent}
        if previous != resolved.at or row["deadline_date_only"] != resolved.date_only:
            values.update(
                deadline_at=resolved.at.astimezone(UTC) if resolved.at else None,
                deadline_date_only=resolved.date_only,
                revision=row["revision"] + 1,
            )
            changed += 1
        unknown += resolved.at is None
        await connection.execute(hw.update().where(hw.c.id == row["id"]).values(**values))
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
