"""Replace the group timetable for 12-18 October 2026 from owner evidence.

The screenshot supplied by the product owner is a date-specific timetable, not
an alternating-semester rule.  Apply it to every provisioned group and retain a
small backup table so a downgrade can restore any overlapping patterns exactly.
Teacher names are intentionally left null because they are truncated in the
source image; no missing text is invented.
"""

from __future__ import annotations

import uuid
from datetime import date, time

import sqlalchemy as sa
from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None

START = date(2026, 10, 12)
END = date(2026, 10, 18)
BEFORE = date(2026, 10, 11)
AFTER = date(2026, 10, 19)

# weekday, starts, ends, subject, location
LESSONS = (
    (0, "10:40", "12:00", "История России", "СДО"),
    (0, "12:20", "13:40", "Основы российской государственности", "СДО"),
    (
        0,
        "14:00",
        "15:20",
        "Основы российской государственности",
        "Ауд. 108 · Вернадского, корп. 5",
    ),
    (
        1,
        "09:00",
        "10:20",
        "Культура личности и общества",
        "Ауд. 201 · Вернадского, корп. 5",
    ),
    (
        1,
        "10:40",
        "12:00",
        "Элективные дисциплины по физической культуре и спорту",
        "КФК",
    ),
    (
        1,
        "12:20",
        "13:40",
        "Основы российской государственности",
        "Ауд. 108 · Вернадского, корп. 5",
    ),
    (1, "14:00", "15:20", "История России", "Ауд. 108 · Вернадского, корп. 5"),
    (
        2,
        "09:00",
        "10:20",
        "Основы информатики",
        "Ауд. 416 · Садовники, 4, к.2",
    ),
    (
        2,
        "10:40",
        "12:00",
        "Математический анализ",
        "Ауд. 421 · Садовники, 4, к.2",
    ),
    (
        2,
        "12:20",
        "13:40",
        "Линейная алгебра и аналитическая геометрия",
        "Ауд. 415 · Садовники, 4, к.2",
    ),
    (
        3,
        "09:00",
        "10:20",
        "Линейная алгебра и аналитическая геометрия",
        "Ауд. 402 · Садовники, 4, к.2",
    ),
    (
        3,
        "10:40",
        "12:00",
        "Основы информатики",
        "Ауд. 416 · Садовники, 4, к.2",
    ),
    (
        3,
        "12:20",
        "13:40",
        "Математический анализ",
        "Ауд. 421 · Садовники, 4, к.2",
    ),
    (4, "09:00", "10:20", "Средства цифровой трансформации", "СДО"),
    (4, "10:40", "12:00", "Математический анализ", "СДО"),
    (
        5,
        "10:40",
        "12:00",
        "Иностранный язык",
        "Ауд. 102а · Вернадского, корп. 5",
    ),
    (
        5,
        "12:20",
        "13:40",
        "Иностранный язык",
        "Ауд. 102а · Вернадского, корп. 5",
    ),
    (
        5,
        "14:00",
        "15:20",
        "Русский язык как иностранный",
        "Ауд. 3416 · Вернадского, корп. 1",
    ),
)


def _tables():
    columns = (
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("group_id", sa.Uuid(), nullable=False),
        sa.Column("subject", sa.String(255), nullable=False),
        sa.Column("weekday", sa.Integer(), nullable=False),
        sa.Column("starts", sa.Time(), nullable=False),
        sa.Column("ends", sa.Time(), nullable=False),
        sa.Column("valid_from", sa.Date(), nullable=False),
        sa.Column("valid_until", sa.Date(), nullable=False),
        sa.Column("week", sa.String(8), nullable=False),
        sa.Column("teacher", sa.String(255)),
        sa.Column("location", sa.String(512)),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("online_url", sa.String(2048)),
        sa.Column("cancelled", sa.Boolean(), nullable=False),
    )
    metadata = sa.MetaData()
    schedule = sa.Table("schedule_patterns", metadata, *[column._copy() for column in columns])
    backup = sa.Table("schedule_backup_20261012", metadata, *columns)
    groups = sa.table("groups", sa.column("id", sa.Uuid()))
    return schedule, backup, groups


def _copy_values(row):
    return {key: row._mapping[key] for key in row._mapping}


def upgrade():
    op.create_table(
        "schedule_backup_20261012",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("group_id", sa.Uuid(), nullable=False),
        sa.Column("subject", sa.String(255), nullable=False),
        sa.Column("weekday", sa.Integer(), nullable=False),
        sa.Column("starts", sa.Time(), nullable=False),
        sa.Column("ends", sa.Time(), nullable=False),
        sa.Column("valid_from", sa.Date(), nullable=False),
        sa.Column("valid_until", sa.Date(), nullable=False),
        sa.Column("week", sa.String(8), nullable=False),
        sa.Column("teacher", sa.String(255)),
        sa.Column("location", sa.String(512)),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("online_url", sa.String(2048)),
        sa.Column("cancelled", sa.Boolean(), nullable=False),
    )
    bind = op.get_bind()
    schedule, backup, groups = _tables()
    overlapping = bind.execute(
        sa.select(schedule).where(
            schedule.c.valid_from <= END,
            schedule.c.valid_until >= START,
        )
    ).all()
    if overlapping:
        bind.execute(backup.insert(), [_copy_values(row) for row in overlapping])

    for row in overlapping:
        values = _copy_values(row)
        if values["valid_from"] < START and values["valid_until"] > END:
            left = dict(values)
            left["id"] = uuid.uuid5(values["id"], "before-2026-10-12")
            left["valid_until"] = BEFORE
            bind.execute(schedule.insert().values(**left))
            bind.execute(
                schedule.update().where(schedule.c.id == values["id"]).values(valid_from=AFTER)
            )
        elif values["valid_from"] < START:
            bind.execute(
                schedule.update().where(schedule.c.id == values["id"]).values(valid_until=BEFORE)
            )
        elif values["valid_until"] > END:
            bind.execute(
                schedule.update().where(schedule.c.id == values["id"]).values(valid_from=AFTER)
            )
        else:
            bind.execute(schedule.delete().where(schedule.c.id == values["id"]))

    group_ids = bind.execute(sa.select(groups.c.id)).scalars().all()
    rows = []
    for group_id in group_ids:
        namespace = group_id if isinstance(group_id, uuid.UUID) else uuid.UUID(str(group_id))
        for weekday, starts, ends, subject, location in LESSONS:
            key = f"2026-10-12|{weekday}|{starts}|{subject}"
            rows.append(
                {
                    "id": uuid.uuid5(namespace, key),
                    "group_id": group_id,
                    "subject": subject,
                    "weekday": weekday,
                    "starts": time.fromisoformat(starts),
                    "ends": time.fromisoformat(ends),
                    "valid_from": START,
                    "valid_until": END,
                    "week": "all",
                    "teacher": None,
                    "location": location,
                    "revision": 1,
                    "online_url": None,
                    "cancelled": False,
                }
            )
    if rows:
        bind.execute(schedule.insert(), rows)


def downgrade():
    bind = op.get_bind()
    schedule, backup, groups = _tables()
    group_ids = bind.execute(sa.select(groups.c.id)).scalars().all()
    imported_ids = []
    for group_id in group_ids:
        namespace = group_id if isinstance(group_id, uuid.UUID) else uuid.UUID(str(group_id))
        for weekday, starts, _ends, subject, _location in LESSONS:
            imported_ids.append(uuid.uuid5(namespace, f"2026-10-12|{weekday}|{starts}|{subject}"))
    if imported_ids:
        bind.execute(schedule.delete().where(schedule.c.id.in_(imported_ids)))

    saved = bind.execute(sa.select(backup)).all()
    for row in saved:
        values = _copy_values(row)
        left_id = uuid.uuid5(values["id"], "before-2026-10-12")
        bind.execute(schedule.delete().where(schedule.c.id == left_id))
        present = bind.execute(
            sa.select(schedule.c.id).where(schedule.c.id == values["id"])
        ).scalar_one_or_none()
        if present is None:
            bind.execute(schedule.insert().values(**values))
        else:
            bind.execute(schedule.update().where(schedule.c.id == values["id"]).values(**values))
    op.drop_table("schedule_backup_20261012")
