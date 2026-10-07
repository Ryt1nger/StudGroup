"""Bind existing date-only homework to the first subject lesson's end on that day."""

import re
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import sqlalchemy as sa
from alembic import op

from studgroup.migration_calendar import calendar, tables

revision = "0012"
down_revision = "0011"
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
                    raw.c.text.label("source_text"),
                    groups.c.timezone,
                    groups.c.first_week_anchor,
                )
                .join(raw, raw.c.id == hw.c.raw_message_id)
                .join(groups, groups.c.id == hw.c.group_id)
                .where(hw.c.deadline_date_only.is_(True), hw.c.deadline_at.is_not(None))
            )
        )
        .mappings()
        .all()
    )
    changed = 0
    for row in rows:
        if re.search(
            r"(?:до|к)\s+конц[ау]\s+(?:этого\s+|текущего\s+|этой\s+)?(?:дня|суток|недели)|23[:.]59",
            row["source_text"],
            re.IGNORECASE,
        ):
            continue
        at = row["deadline_at"]
        if at.tzinfo is None:
            at = at.replace(tzinfo=UTC)
        day = at.astimezone(ZoneInfo(row["timezone"])).date()
        lessons, ready = await calendar(
            connection,
            patterns,
            row["group_id"],
            day,
            day,
            row["timezone"],
            row["first_week_anchor"],
        )

        def normalize(text):
            return " ".join(text.casefold().replace("ё", "е").split())

        matching = [
            l
            for l in lessons
            if normalize(l["subject"]) == normalize(row["subject_name"])
            and l["status"] == "scheduled"
        ]
        if not ready or not matching:
            continue
        lesson = min(matching, key=lambda l: l["starts_at"])
        await connection.execute(
            hw.update()
            .where(hw.c.id == row["id"])
            .values(
                deadline_at=datetime.fromisoformat(lesson["ends_at"]).astimezone(UTC),
                deadline_date_only=False,
                revision=row["revision"] + 1,
            )
        )
        changed += 1
    print(f"HOMEWORK_LESSON_TIMING corrected={changed}")


def upgrade():
    op.run_async(repair)


def downgrade():
    pass
