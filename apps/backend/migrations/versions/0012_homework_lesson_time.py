"""Bind existing date-only homework to the first subject lesson's end on that day."""

from alembic import op
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from studgroup.homework_timing import lesson_deadline
from studgroup.models import Group, Homework, RawMessage

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


async def repair(connection):
    changed = 0
    async with AsyncSession(bind=connection) as db:
        rows = (
            await db.scalars(
                select(Homework).where(
                    Homework.deadline_date_only.is_(True), Homework.deadline_at.is_not(None)
                )
            )
        ).all()
        for row in rows:
            group = await db.get(Group, row.group_id)
            raw = await db.get(RawMessage, row.raw_message_id) if row.raw_message_id else None
            # Without retained evidence we cannot distinguish an explicit all-day window.
            if raw is None:
                continue
            at, date_only = await lesson_deadline(
                db, group, row.subject_name, row.deadline_at, True, raw.text
            )
            if not date_only:
                row.deadline_at = at
                row.deadline_date_only = False
                row.revision += 1
                changed += 1
        await db.flush()
        print(f"HOMEWORK_LESSON_TIMING corrected={changed}")


def upgrade():
    op.run_async(repair)


def downgrade():
    pass
