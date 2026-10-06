import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from studgroup.chat_export import ExportMessage
from studgroup.group_import import import_group, pack_payload, unpack_payload
from studgroup.models import Group, Membership, RawMessage, User

pytest_plugins = ["test_schedule_api"]


def test_group_import_is_idempotent_and_binds_only_requested_account(client):
    now = datetime.now(UTC)
    messages = [
        ExportMessage(101, now, "ДЗ: задачи", None, False),
        ExportMessage(102, now + timedelta(minutes=1), "К среде", 101, False),
    ]

    async def run():
        async with AsyncSession(client.app.state.engine) as db:
            group_id, _, counts = await import_group(
                db, messages, {}, -1005555555, 123456789, "Тестовая группа"
            )
            await db.commit()
            assert counts["text_messages"] == 2
            await import_group(db, messages, {}, -1005555555, 123456789, "Тестовая группа")
            await db.commit()
            assert (
                await db.scalar(
                    select(func.count(RawMessage.id)).where(RawMessage.group_id == group_id)
                )
                == 2
            )
            account = await db.scalar(select(User).where(User.telegram_user_id == 123456789))
            member = await db.scalar(select(Membership).where(Membership.group_id == group_id))
            assert member.user_id == account.id
            assert member.role == "student"
            raw = await db.scalar(select(RawMessage).where(RawMessage.telegram_message_id == 102))
            assert raw.reply_to_message_id == 101
            assert raw.processing_state == "needs_context"
            assert (await db.get(Group, group_id)).pilot_authorized is True

    asyncio.run(run())


def test_payload_roundtrip_and_integrity():
    import pytest

    messages = [ExportMessage(1, datetime.now(UTC), "ДЗ: задачи", None, False)]
    payload, digest = pack_payload(messages, {})
    restored, reviewed = unpack_payload(payload, digest)
    assert restored == messages
    assert reviewed == {}
    with pytest.raises(ValueError, match="integrity"):
        unpack_payload(payload + b"x", digest)
