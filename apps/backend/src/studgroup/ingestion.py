"""Durable text inbox; acknowledgement follows commit, with no queue-only data."""

import hmac
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from studgroup.api import ApiError, database
from studgroup.models import Group, GroupAIProfile, GroupTopic, RawMessage, ReceivedUpdate

router = APIRouter(prefix="/v1/telegram", include_in_schema=False)


class Chat(BaseModel):
    id: int
    type: str


class Sender(BaseModel):
    id: int


class ChatMember(BaseModel):
    id: int
    is_bot: bool = False


class ReplyReference(BaseModel):
    message_id: int


class SharedUser(BaseModel):
    user_id: int = Field(gt=0, lt=2**63)


class UsersShared(BaseModel):
    request_id: int
    users: list[SharedUser] = Field(max_length=10)


class ForwardOrigin(BaseModel):
    type: str
    date: int = Field(ge=0)


class ForumTopicCreated(BaseModel):
    name: str = Field(max_length=255)


class ForumTopicEdited(BaseModel):
    name: str | None = Field(default=None, max_length=255)


class Message(BaseModel):
    message_id: int
    date: int = Field(ge=0)
    chat: Chat
    text: str | None = Field(default=None, max_length=65536)
    caption: str | None = Field(default=None, max_length=65536)
    edit_date: int | None = Field(default=None, ge=0)
    sender: Sender | None = Field(default=None, alias="from")
    reply_to_message: ReplyReference | None = None
    message_thread_id: int | None = None
    is_topic_message: bool = False
    forum_topic_created: ForumTopicCreated | None = None
    forum_topic_edited: ForumTopicEdited | None = None
    new_chat_members: list[ChatMember] = Field(default_factory=list, max_length=100)
    users_shared: UsersShared | None = None
    forward_origin: ForwardOrigin | None = None


class CallbackQuery(BaseModel):
    id: str = Field(max_length=256)
    sender: Sender = Field(alias="from")
    message: Message | None = None
    data: str | None = Field(default=None, max_length=64)


class Update(BaseModel):
    update_id: int
    message: Message | None = None
    edited_message: Message | None = None
    callback_query: CallbackQuery | None = None


async def authenticate_webhook(
    request: Request,
    secret: str | None = Header(None, alias="X-Telegram-Bot-Api-Secret-Token"),
):
    expected = request.app.state.settings.telegram_webhook_secret
    if not expected:
        raise ApiError("service_unavailable", "Webhook не настроен", 503)
    if secret is None or not hmac.compare_digest(secret, expected):
        raise ApiError("unauthenticated", "Неверный секрет webhook")


@router.post("/webhook", dependencies=[Depends(authenticate_webhook)])
async def webhook(update: Update, request: Request, db: Annotated[AsyncSession, Depends(database)]):
    # A savepoint lets concurrent duplicate deliveries acknowledge without losing
    # the transaction containing the authoritative inbox update.
    try:
        async with db.begin_nested():
            db.add(ReceivedUpdate(update_id=update.update_id, received_at=datetime.now(UTC)))
            await db.flush()
    except IntegrityError:
        return {"ok": True}

    from studgroup.bot_panels import dispatch

    if await dispatch(db, update, request.app.state.settings):
        await db.commit()
        return {"ok": True}
    message = update.edited_message or update.message
    if message is None or message.chat.type not in {"group", "supergroup"}:
        await db.commit()
        return {"ok": True}
    group = await db.scalar(
        select(Group).where(
            Group.telegram_chat_id == message.chat.id,
            Group.pilot_authorized.is_(True),
            Group.status == "active",
        )
    )
    if group is None:
        await db.commit()
        return {"ok": True}

    # Persist the exact paid-history boundary when Telegram tells this bot that it
    # joined. Older installations are backfilled by migration from the first live
    # message the bot actually received.
    try:
        own_bot_id = int(request.app.state.settings.telegram_bot_token.split(":", 1)[0])
    except (TypeError, ValueError):
        own_bot_id = None
    if own_bot_id is not None and any(
        member.is_bot and member.id == own_bot_id for member in message.new_chat_members
    ):
        joined_at = datetime.fromtimestamp(message.date, UTC)
        saved_joined_at = group.bot_added_at
        if saved_joined_at is not None and saved_joined_at.tzinfo is None:
            saved_joined_at = saved_joined_at.replace(tzinfo=UTC)
        if saved_joined_at is None or joined_at < saved_joined_at:
            group.bot_added_at = joined_at

    from studgroup.group_structure import ensure_profile

    await ensure_profile(db, group.id, datetime.now(UTC))
    if message.message_thread_id is not None:
        await store_topic(db, group.id, message, datetime.now(UTC))

    if message.text is None and message.caption:
        message.text = message.caption

    if message.text is None and update.edited_message is not None:
        previous = await db.scalar(
            select(RawMessage.id).where(
                RawMessage.group_id == group.id,
                RawMessage.telegram_message_id == message.message_id,
            )
        )
        if previous:
            message.text = ""  # Removed attachment caption revokes its old joining URL.
    if message.text is None:
        from studgroup.ai_schedule import signal

        await signal(db, group.id)
        await db.commit()
        return {"ok": True}

    await store_message(db, group, message)
    from studgroup.online_lessons import discover

    stored = await db.scalar(
        select(RawMessage).where(
            RawMessage.group_id == group.id, RawMessage.telegram_message_id == message.message_id
        )
    )
    await discover(db, stored)
    try:
        await db.commit()
    except IntegrityError:
        # Never acknowledge an edit that was not durably committed.
        await db.rollback()
        raise ApiError("service_unavailable", "Повторите доставку", 503) from None
    return {"ok": True}


async def store_message(db, group, message, *, imported=False):
    """One inbox path for Telegram delivery and explicitly scoped history replay.

    The caller commits before acknowledgement. History never overwrites a newer
    live edit; every timestamp represents the original source, not import time.
    """
    profile = await db.get(GroupAIProfile, group.id)
    generation = profile.generation if profile is not None else 1
    version = datetime.fromtimestamp(message.edit_date or message.date, UTC)
    row = await db.scalar(
        select(RawMessage)
        .where(
            RawMessage.group_id == group.id, RawMessage.telegram_message_id == message.message_id
        )
        .with_for_update()
    )
    if row is None:
        original_date = datetime.fromtimestamp(message.date, UTC)
        received = datetime.now(UTC) if not imported else None
        if not imported and group.bot_added_at is None:
            # Safe fallback for an installation where the join service update was
            # missed: never infer the boundary from an uploaded archive.
            group.bot_added_at = original_date
        row = RawMessage(
            group_id=group.id,
            telegram_message_id=message.message_id,
            sender_id=message.sender.id if message.sender else None,
            reply_to_message_id=message.reply_to_message.message_id
            if message.reply_to_message
            else None,
            message_thread_id=message.message_thread_id,
            text=message.text,
            message_date=original_date,
            version_date=version,
            revision=1,
            analysis_generation=generation,
            processing_state="pending",
            imported=imported,
            delete_at=original_date + timedelta(days=30),
            live_received_at=received,
        )
        db.add(row)
        if received:
            from studgroup.ai_schedule import signal

            await signal(db, group.id, received)
        return "inserted"
    else:
        saved_version = row.version_date
        if saved_version.tzinfo is None:
            saved_version = saved_version.replace(tzinfo=UTC)
        if version > saved_version:
            reply_id = message.reply_to_message.message_id if message.reply_to_message else None
            if (
                row.text == message.text
                and row.reply_to_message_id == reply_id
                and row.message_thread_id == message.message_thread_id
            ):
                row.version_date = version
                return "unchanged"
            row.text = message.text
            row.reply_to_message_id = (
                message.reply_to_message.message_id if message.reply_to_message else None
            )
            row.message_thread_id = message.message_thread_id
            row.version_date = version
            row.revision += 1
            row.analysis_generation = generation
            row.processing_state = "pending"
            if not imported:
                from studgroup.ai_schedule import signal

                row.imported = False
                row.live_received_at = datetime.now(UTC)
                await signal(db, group.id, row.live_received_at)
            return "updated"
    return "unchanged"


async def store_topic(db, group_id, message, now):
    thread_id = message.message_thread_id
    row = await db.scalar(
        select(GroupTopic)
        .where(
            GroupTopic.group_id == group_id,
            GroupTopic.telegram_thread_id == thread_id,
        )
        .with_for_update()
    )
    supplied_name = None
    if message.forum_topic_created:
        supplied_name = message.forum_topic_created.name
    elif message.forum_topic_edited:
        supplied_name = message.forum_topic_edited.name
    changed = row is None or (supplied_name is not None and row.name != supplied_name)
    if row is None:
        row = GroupTopic(
            group_id=group_id,
            telegram_thread_id=thread_id,
            name=supplied_name,
            first_seen_at=now,
            updated_at=now,
        )
        db.add(row)
    else:
        if supplied_name is not None:
            row.name = supplied_name
        row.updated_at = now
    if changed:
        from studgroup.group_structure import invalidate_map

        await invalidate_map(db, group_id, now)
