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
from studgroup.models import Group, RawMessage, ReceivedUpdate

router = APIRouter(prefix="/v1/telegram", include_in_schema=False)


class Chat(BaseModel):
    id: int
    type: str


class Sender(BaseModel):
    id: int


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


class Message(BaseModel):
    message_id: int
    date: int = Field(ge=0)
    chat: Chat
    text: str | None = Field(default=None, max_length=65536)
    edit_date: int | None = Field(default=None, ge=0)
    sender: Sender | None = Field(default=None, alias="from")
    reply_to_message: ReplyReference | None = None
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
    if message is None or message.text is None or message.chat.type not in {"group", "supergroup"}:
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

    await store_message(db, group, message)
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
        row = RawMessage(
            group_id=group.id,
            telegram_message_id=message.message_id,
            sender_id=message.sender.id if message.sender else None,
            reply_to_message_id=message.reply_to_message.message_id
            if message.reply_to_message
            else None,
            text=message.text,
            message_date=original_date,
            version_date=version,
            revision=1,
            processing_state="pending",
            imported=imported,
            delete_at=original_date + timedelta(days=30),
        )
        db.add(row)
        return "inserted"
    else:
        saved_version = row.version_date
        if saved_version.tzinfo is None:
            saved_version = saved_version.replace(tzinfo=UTC)
        if version > saved_version:
            row.text = message.text
            row.reply_to_message_id = (
                message.reply_to_message.message_id if message.reply_to_message else None
            )
            row.version_date = version
            row.revision += 1
            row.processing_state = "pending"
            return "updated"
    return "unchanged"
