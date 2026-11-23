"""Durable source inbox; acknowledgement follows commit, with no queue-only data."""

import hashlib
import hmac
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from studgroup.api import ApiError, database
from studgroup.models import (
    Group,
    GroupAIProfile,
    GroupTopic,
    MessageAttachment,
    RawMessage,
    ReceivedUpdate,
)

router = APIRouter(prefix="/v1/telegram", include_in_schema=False)


class Chat(BaseModel):
    id: int
    type: str
    title: str | None = Field(default=None, max_length=255)


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


class TelegramFile(BaseModel):
    file_id: str = Field(max_length=512)
    file_unique_id: str | None = Field(default=None, max_length=255)
    file_name: str | None = Field(default=None, max_length=512)
    mime_type: str | None = Field(default=None, max_length=255)
    file_size: int | None = Field(default=None, ge=0)
    width: int | None = Field(default=None, ge=0)
    height: int | None = Field(default=None, ge=0)


class SuccessfulPayment(BaseModel):
    currency: str = Field(max_length=3)
    total_amount: int = Field(ge=1)
    invoice_payload: str = Field(max_length=128)
    telegram_payment_charge_id: str = Field(max_length=255)
    provider_payment_charge_id: str | None = Field(default=None, max_length=255)


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
    successful_payment: SuccessfulPayment | None = None
    forward_origin: ForwardOrigin | None = None
    media_group_id: str | None = Field(default=None, max_length=255)
    photo: list[TelegramFile] = Field(default_factory=list, max_length=20)
    document: TelegramFile | None = None
    video: TelegramFile | None = None
    audio: TelegramFile | None = None
    voice: TelegramFile | None = None
    animation: TelegramFile | None = None


class MemberStatus(BaseModel):
    status: str
    user: Sender


class MyChatMemberUpdated(BaseModel):
    """The bot's own status in a chat changed (added, promoted, removed)."""

    chat: Chat
    sender: Sender = Field(alias="from")
    new_chat_member: MemberStatus


class CallbackQuery(BaseModel):
    id: str = Field(max_length=256)
    sender: Sender = Field(alias="from")
    message: Message | None = None
    data: str | None = Field(default=None, max_length=64)


class PreCheckoutQuery(BaseModel):
    id: str = Field(max_length=256)
    sender: Sender = Field(alias="from")
    currency: str = Field(max_length=3)
    total_amount: int = Field(ge=1)
    invoice_payload: str = Field(max_length=128)


class Update(BaseModel):
    update_id: int
    message: Message | None = None
    edited_message: Message | None = None
    callback_query: CallbackQuery | None = None
    pre_checkout_query: PreCheckoutQuery | None = None
    my_chat_member: MyChatMemberUpdated | None = None


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

    # Payment events do not belong to /start panels. Answer pre-checkout in the
    # Telegram-required short window, and only a successful_payment may grant access.
    if update.pre_checkout_query is not None:
        from studgroup.billing import answer_pre_checkout

        await answer_pre_checkout(db, update.pre_checkout_query, request.app.state.settings)
        await db.commit()
        return {"ok": True}
    if update.message is not None and update.message.successful_payment is not None:
        from studgroup.billing import confirm_payment
        from studgroup.bot_admin import enqueue_once

        outcome = await confirm_payment(db, update.message, request.app.state.settings)
        if outcome not in {"applied", "already_applied"}:
            # A paid-but-unmatched Telegram receipt needs human reconciliation,
            # never a guessed group entitlement or an automatic repeat payment.
            payment = update.message.successful_payment
            reference = hashlib.sha256(
                payment.telegram_payment_charge_id.encode("utf-8")
            ).hexdigest()
            await enqueue_once(
                db,
                "sendMessage",
                {
                    "chat_id": update.message.chat.id,
                    "text": (
                        "⚠️ Платёж получен Telegram, но автоматическое "
                        "продление пока не подтверждено. Не платите повторно. "
                        "Напишите в поддержку: @studgroup_admin."
                    ),
                },
                f"billing-review:{reference}",
            )
        await db.commit()
        return {"ok": True}

    from studgroup.bot_panels import dispatch

    if await dispatch(db, update, request.app.state.settings):
        await db.commit()
        return {"ok": True}
    from studgroup import bot_connect

    settings = request.app.state.settings
    if update.my_chat_member is not None:
        await bot_connect.on_my_chat_member(db, settings, update.my_chat_member)
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
        if update.message is not None:
            await bot_connect.on_group_message(db, settings, update.message)
        await db.commit()
        return {"ok": True}
    # When separately enabled, expire only paid academic processing; do not
    # delete past cards or suspend the group's Telegram membership.
    if settings.billing_enforce_access:
        from studgroup.billing import group_limited

        if await group_limited(db, group.id, settings):
            await db.commit()
            return {"ok": True}
    if (
        update.message is not None
        and (update.message.text or "").lstrip().startswith("/start")
        and not await bot_connect.has_headman(db, group.id)
    ):
        # A headman-less group: an administrator typing /start in it can finish a pending claim.
        await bot_connect.on_group_message(db, settings, update.message)

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

    attachments = attachment_metadata(message)
    if message.text is None and update.edited_message is not None:
        previous = await db.scalar(
            select(RawMessage.id).where(
                RawMessage.group_id == group.id,
                RawMessage.telegram_message_id == message.message_id,
            )
        )
        if previous:
            message.text = ""  # Removed attachment caption revokes its old joining URL.
    if message.text is None and attachments:
        # Persist media-only sources without pretending their contents were understood.
        message.text = ""
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
        await db.flush()
        await sync_attachments(db, row, attachment_metadata(message), received or original_date)
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
            supplied_attachments = attachment_metadata(message)
            attachment_changed = await attachments_differ(db, row.id, supplied_attachments)
            if (
                row.text == message.text
                and row.reply_to_message_id == reply_id
                and row.message_thread_id == message.message_thread_id
                and not attachment_changed
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
            if attachment_changed:
                await sync_attachments(db, row, supplied_attachments, datetime.now(UTC))
            row.analysis_generation = generation
            row.processing_state = "pending"
            if not imported:
                from studgroup.ai_schedule import signal

                row.imported = False
                row.live_received_at = datetime.now(UTC)
                await signal(db, group.id, row.live_received_at)
            return "updated"
    return "unchanged"


def attachment_metadata(message):
    files = []
    if message.photo:
        photo = max(
            message.photo,
            key=lambda item: (item.file_size or 0, (item.width or 0) * (item.height or 0)),
        )
        files.append(("photo", photo))
    # Only photos and files are study material. GIFs, stickers, video and voice are skipped
    # entirely; Telegram sends a GIF with BOTH `animation` and `document`, so a document that
    # accompanies an animation (or is a GIF) is not a file either.
    document = message.document
    if (
        document is not None
        and message.animation is None
        and (document.mime_type or "").lower() != "image/gif"
    ):
        files.append(("document", document))
    return [
        {
            "kind": kind,
            "stable_file_id": item.file_unique_id
            or hashlib.sha256(item.file_id.encode("utf-8")).hexdigest(),
            "telegram_file_id": item.file_id,
            "file_name": item.file_name,
            "mime_type": item.mime_type,
            "size_bytes": item.file_size,
            "width": item.width,
            "height": item.height,
            "media_group_id": message.media_group_id,
        }
        for kind, item in files
    ]


def attachment_signature(item):
    return tuple(
        item[key]
        for key in (
            "kind",
            "stable_file_id",
            "telegram_file_id",
            "file_name",
            "mime_type",
            "size_bytes",
            "width",
            "height",
            "media_group_id",
        )
    )


async def active_attachments(db, raw_message_id):
    return (
        await db.scalars(
            select(MessageAttachment).where(
                MessageAttachment.raw_message_id == raw_message_id,
                MessageAttachment.active.is_(True),
            )
        )
    ).all()


async def attachments_differ(db, raw_message_id, supplied):
    existing = await active_attachments(db, raw_message_id)
    existing_signatures = {
        attachment_signature(
            {
                "kind": row.kind,
                "stable_file_id": row.stable_file_id,
                "telegram_file_id": row.telegram_file_id,
                "file_name": row.file_name,
                "mime_type": row.mime_type,
                "size_bytes": row.size_bytes,
                "width": row.width,
                "height": row.height,
                "media_group_id": row.media_group_id,
            }
        )
        for row in existing
    }
    return existing_signatures != {attachment_signature(item) for item in supplied}


async def sync_attachments(db, raw, supplied, now):
    existing = await active_attachments(db, raw.id)
    if not await attachments_differ(db, raw.id, supplied):
        return False
    for row in existing:
        row.active = False
    for item in supplied:
        db.add(
            MessageAttachment(
                raw_message_id=raw.id,
                group_id=raw.group_id,
                source_revision=raw.revision,
                processing_state="metadata_only",
                active=True,
                created_at=now,
                delete_at=raw.delete_at,
                **item,
            )
        )
    return True


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
