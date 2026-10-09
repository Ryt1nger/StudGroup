"""One-time, insert-only local preview transfer; target migrations must already exist."""

import argparse
import asyncio
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine

from studgroup.main import Settings
from studgroup.models import Base, Group


def utc_values(row):
    # SQLite drops tzinfo. The application treats its persisted naive timestamps
    # as UTC; asyncpg otherwise interprets them in the host's local timezone.
    return {
        key: value.replace(tzinfo=UTC)
        if isinstance(value, datetime) and value.tzinfo is None
        else value
        for key, value in row.items()
    }


async def transfer(path, target_url):
    if not target_url.startswith("postgresql+asyncpg://"):
        raise ValueError("target_must_be_postgresql")
    source = create_async_engine(f"sqlite+aiosqlite:///{path}")
    target = create_async_engine(target_url)
    try:
        async with source.connect() as read, target.begin() as write:
            groups = (await read.execute(select(Group.__table__))).mappings().all()
            for group in groups:
                if await write.scalar(
                    select(Group.id).where(Group.telegram_chat_id == group["telegram_chat_id"])
                ):
                    raise ValueError("target_group_already_exists_no_overwrite")
            copied = {}
            for table in Base.metadata.sorted_tables:
                if table.name in {
                    "web_sessions",
                    "ai_control",
                    "ai_jobs",
                    "ai_attempts",
                    "ai_candidates",
                    "owner_incidents",
                    "owner_incident_episodes",
                    "received_updates",
                }:
                    continue
                rows = (await read.execute(select(table))).mappings().all()
                if rows:
                    await write.execute(table.insert(), [utc_values(row) for row in rows])
                copied[table.name] = len(rows)
        print("Transfer committed; row counts only:", copied)
    finally:
        await source.dispose()
        await target.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("sqlite", type=Path)
    args = parser.parse_args()
    asyncio.run(transfer(args.sqlite, Settings().database_url))
