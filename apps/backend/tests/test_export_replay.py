import asyncio
from datetime import UTC, datetime
from zipfile import ZipFile

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from studgroup.export_replay import replay
from studgroup.ingestion import Chat, Message, store_message
from studgroup.models import Group, RawMessage


def archive(tmp_path):
    path = tmp_path / "export.zip"
    with ZipFile(path, "w") as zip_file:
        zip_file.writestr(
            "messages.html",
            """<html><body>
<div class="message default" id="message1">
<div class="date" title="5 октября 2026, 09:00:00"></div>
<div class="text">ДЗ: задачи</div></div>
<div class="message default" id="message2">
<div class="date" title="5 октября 2026, 09:01:00"></div>
<div class="reply_to"><a href="#go_to_message1">Reply</a></div>
<div class="text">Сдать завтра</div></div>
</body></html>""",
        )
    return path


def test_replay_preserves_dates_replies_and_is_resumable(tmp_path):
    path = archive(tmp_path)
    directory = tmp_path / "replay"
    first = asyncio.run(replay(path, directory, "Europe/Moscow"))
    second = asyncio.run(replay(path, directory, "Europe/Moscow"))
    assert first["inserted"] == 2
    assert second["unchanged"] == 2
    assert second["stored_text_messages"] == 2

    async def check():
        engine = create_async_engine(
            f"sqlite+aiosqlite:///{directory / 'collection-replay.sqlite'}"
        )
        async with AsyncSession(engine) as db:
            group = await db.scalar(select(Group))
            assert group.pilot_authorized is False
            row = await db.scalar(select(RawMessage).where(RawMessage.telegram_message_id == 2))
            assert row.reply_to_message_id == 1
            assert row.message_date.hour == 6  # original Moscow time, stored UTC
            assert row.imported is True
        await engine.dispose()

    asyncio.run(check())


def test_history_replay_cannot_overwrite_a_newer_live_edit(tmp_path):
    path = archive(tmp_path)
    directory = tmp_path / "replay"
    asyncio.run(replay(path, directory, "Europe/Moscow"))

    async def edit():
        engine = create_async_engine(
            f"sqlite+aiosqlite:///{directory / 'collection-replay.sqlite'}"
        )
        async with AsyncSession(engine) as db:
            group = await db.scalar(select(Group))
            message = Message(
                message_id=1,
                date=1791180000,
                edit_date=int(datetime(2026, 10, 6, 10, tzinfo=UTC).timestamp()),
                chat=Chat(id=group.telegram_chat_id, type="supergroup"),
                text="Уточнённое ДЗ",
            )
            assert await store_message(db, group, message) == "updated"
            await db.commit()
        await engine.dispose()

    asyncio.run(edit())
    assert asyncio.run(replay(path, directory, "Europe/Moscow"))["unchanged"] == 2

    async def check():
        engine = create_async_engine(
            f"sqlite+aiosqlite:///{directory / 'collection-replay.sqlite'}"
        )
        async with AsyncSession(engine) as db:
            row = await db.scalar(select(RawMessage).where(RawMessage.telegram_message_id == 1))
            assert row.text == "Уточнённое ДЗ"
            assert row.revision == 2
        await engine.dispose()

    asyncio.run(check())
