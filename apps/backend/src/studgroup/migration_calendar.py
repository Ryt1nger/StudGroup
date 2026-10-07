"""Frozen legacy projection for data migrations; never use evolving application ORM."""

import sqlalchemy as sa

from studgroup.schedule import LessonPattern, expand


async def tables(connection):
    metadata = sa.MetaData()

    def reflect(bind):
        for name in ["homework", "groups", "raw_messages", "schedule_patterns"]:
            sa.Table(name, metadata, autoload_with=bind)

    await connection.run_sync(reflect)
    return metadata.tables


async def calendar(connection, patterns, group_id, day, end, timezone, anchor):
    rows = (
        (
            await connection.execute(
                sa.select(patterns).where(
                    patterns.c.group_id == group_id,
                    patterns.c.valid_from <= end,
                    patterns.c.valid_until >= day,
                )
            )
        )
        .mappings()
        .all()
    )
    lessons = []
    ready = True
    for row in rows:
        if row["week"] != "all" and anchor is None:
            ready = False
            continue
        pattern = LessonPattern(
            str(row["id"]),
            row["subject"],
            row["weekday"],
            row["starts"],
            row["ends"],
            row["valid_from"],
            row["valid_until"],
            row["week"],
            row["teacher"],
            row["location"],
        )
        lessons.extend(expand(pattern, day, end, timezone, anchor))
    return lessons, ready
