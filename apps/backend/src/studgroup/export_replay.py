"""Offline collection acceptance: replay an export into an isolated private inbox.

No provider calls, public API, group enrollment or preview-data changes. The same
store_message function is used by the authenticated Telegram webhook.
"""

import argparse
import asyncio
import os
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from studgroup.chat_export import read_html_export
from studgroup.ingestion import Chat, Message, ReplyReference, store_message
from studgroup.models import Base, Group, RawMessage

REPLAY_CHAT_ID = -9000000000001


async def replay(archive: Path, directory: Path, timezone: str):
    messages = read_html_export(archive, timezone=timezone)
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    database = directory / "collection-replay.sqlite"
    engine = create_async_engine(f"sqlite+aiosqlite:///{database}")
    outcomes = Counter()
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        os.chmod(database, 0o600)
        async with AsyncSession(engine, expire_on_commit=False) as db:
            groups = (await db.scalars(select(Group))).all()
            if groups and (len(groups) != 1 or groups[0].telegram_chat_id != REPLAY_CHAT_ID):
                raise ValueError("replay_requires_isolated_database")
            group = (
                groups[0]
                if groups
                else Group(
                    telegram_chat_id=REPLAY_CHAT_ID,
                    name="Offline export collection replay",
                    timezone=timezone,
                    pilot_authorized=False,
                )
            )
            if group.timezone != timezone:
                raise ValueError("replay_timezone_changed")
            if not groups:
                db.add(group)
                await db.flush()
            for row in messages:
                if not row.text:
                    outcomes["no_text"] += 1
                    continue
                message = Message(
                    message_id=row.message_id,
                    date=int(row.message_date.timestamp()),
                    chat=Chat(id=REPLAY_CHAT_ID, type="supergroup"),
                    text=row.text,
                    reply_to_message=ReplyReference(message_id=row.reply_to_message_id)
                    if row.reply_to_message_id is not None
                    else None,
                )
                outcomes[await store_message(db, group, message, imported=True)] += 1
                # Bounded transactions; an interruption can be resumed idempotently.
                if sum(outcomes.values()) % 100 == 0:
                    await db.commit()
            await db.commit()
            outcomes["stored_text_messages"] = await db.scalar(
                select(func.count(RawMessage.id)).where(RawMessage.group_id == group.id)
            )
            outcomes["retained_at_server_time"] = await db.scalar(
                select(func.count(RawMessage.id)).where(
                    RawMessage.group_id == group.id,
                    RawMessage.delete_at > datetime.now(UTC),
                )
            )
        return dict(outcomes)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("archive", type=Path)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--timezone", required=True)
    args = parser.parse_args()
    print(
        "Offline replay (counts only):",
        asyncio.run(replay(args.archive, args.directory, args.timezone)),
    )
