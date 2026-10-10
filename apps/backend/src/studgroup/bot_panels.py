"""Durable private panels. /admin and /st are independent; /start never joins a panel."""

import json
import secrets
import uuid
from contextvars import ContextVar
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.dialects.postgresql import insert

from studgroup.models import (
    BotAdminSession,
    BotDialogRoute,
    BotOutbox,
    BotPanel,
    BotPanelMessage,
    HeadmanSession,
)

scope = ContextVar("bot_panel_scope", default=None)


async def panel(db, chat, mode):
    assert mode in {"admin", "st"} and chat > 0
    await db.execute(
        insert(BotPanel)
        .values(id=uuid.uuid4(), chat_id=chat, mode=mode, generation=1, revision=0)
        .on_conflict_do_nothing(index_elements=[BotPanel.chat_id, BotPanel.mode])
    )
    return await db.scalar(
        select(BotPanel)
        .where(BotPanel.chat_id == chat, BotPanel.mode == mode)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


def job(db, method, data, owner, revision=None):
    db.add(
        BotOutbox(
            method=method,
            payload=json.dumps(data, ensure_ascii=False),
            available_at=datetime.now(UTC),
            panel_id=owner.id,
            panel_generation=owner.generation,
            panel_revision=revision,
        )
    )


async def track(db, owner, message_id, kind):
    await db.execute(
        insert(BotPanelMessage)
        .values(
            id=uuid.uuid4(),
            panel_id=owner.id,
            generation=owner.generation,
            message_id=message_id,
            kind=kind,
        )
        .on_conflict_do_nothing(
            index_elements=[BotPanelMessage.panel_id, BotPanelMessage.message_id]
        )
    )


async def reset(db, owner):
    # Pending work for the old invocation must never resurrect its panel after restart.
    await db.execute(
        update(BotOutbox)
        .where(
            BotOutbox.panel_id == owner.id,
            BotOutbox.state == "pending",
            BotOutbox.method.in_(["renderPanel", "panelAux"]),
        )
        .values(state="superseded")
    )
    rows = (
        await db.scalars(select(BotPanelMessage).where(BotPanelMessage.panel_id == owner.id))
    ).all()
    for row in rows:
        job(
            db,
            "deleteMessage",
            {
                "chat_id": owner.chat_id,
                "message_id": row.message_id,
                "_outgoing": row.kind in {"panel", "aux"},
            },
            owner,
        )
        await db.delete(row)
    owner.generation += 1
    owner.revision = 0
    owner.message_id = None
    await db.flush()


async def queue_frame(db, chat, text, markup=None):
    active = scope.get()
    if not active or active[0] != chat:
        return False
    owner = await panel(db, chat, active[1])
    owner.revision += 1
    # Obsolete screen contents can be skipped, but Telegram callback acknowledgements cannot.
    await db.execute(
        update(BotOutbox)
        .where(
            BotOutbox.panel_id == owner.id,
            BotOutbox.state == "pending",
            BotOutbox.method.in_(["renderPanel", "panelAux"]),
        )
        .values(state="superseded")
    )
    keyboard = bool(markup and ("keyboard" in markup or "remove_keyboard" in markup))
    data = {
        "chat_id": chat,
        "text": text[:4000],
        "link_preview_options": {"is_disabled": True},
        "reply_markup": {"inline_keyboard": []} if keyboard or markup is None else markup,
    }
    job(db, "renderPanel", data, owner, owner.revision)
    # Reply-keyboard/user-picker controls cannot be attached by editMessageText.
    if keyboard:
        job(
            db,
            "panelAux",
            {
                "chat_id": chat,
                "text": "Выбор пользователя" if "keyboard" in markup else "Клавиатура закрыта",
                "reply_markup": markup,
            },
            owner,
            owner.revision,
        )
    else:
        for row in (
            await db.scalars(
                select(BotPanelMessage).where(
                    BotPanelMessage.panel_id == owner.id,
                    BotPanelMessage.generation == owner.generation,
                    BotPanelMessage.kind == "aux",
                )
            )
        ).all():
            job(
                db,
                "deleteMessage",
                {"chat_id": chat, "message_id": row.message_id, "_outgoing": True},
                owner,
            )
            await db.delete(row)
    await db.flush()
    return True


async def activate(db, chat, mode):
    await db.execute(
        insert(BotDialogRoute)
        .values(chat_id=chat, mode=mode)
        .on_conflict_do_update(index_elements=[BotDialogRoute.chat_id], set_={"mode": mode})
    )


def utc(stamp):
    return stamp.replace(tzinfo=UTC) if stamp.tzinfo is None else stamp


async def execute(db, row, settings, request):
    owner = await db.scalar(
        select(BotPanel)
        .where(BotPanel.id == row.panel_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if not owner:
        return "superseded"
    data = json.loads(row.payload)
    if data.get("chat_id") != owner.chat_id:
        return "failed"
    if row.method == "deleteMessage":
        outgoing = data.pop("_outgoing", False)
        result = await request(settings, "deleteMessage", data)
        if result.get("ok") or result.get("error_code") == 400:
            if not result.get("ok") and outgoing:
                await request(
                    settings,
                    "editMessageReplyMarkup",
                    {
                        "chat_id": owner.chat_id,
                        "message_id": data["message_id"],
                        "reply_markup": {"inline_keyboard": []},
                    },
                )
            return "sent"
        return "failed"
    if (
        row.method not in {"renderPanel", "panelAux"}
        or row.panel_generation != owner.generation
        or row.panel_revision != owner.revision
    ):
        return "superseded"
    if row.method == "renderPanel" and owner.message_id:
        data["message_id"] = owner.message_id
        result = await request(settings, "editMessageText", data)
        if (
            result.get("ok")
            or "message is not modified" in result.get("description", "").casefold()
        ):
            return "sent"
        description = result.get("description", "").casefold()
        if result.get("error_code") != 400 or not (
            "message to edit not found" in description or "message can't be edited" in description
        ):
            return "failed"
        # A deleted/uneditable message is replaced only within its own current panel.
        owner.message_id = None
        data.pop("message_id", None)
    result = await request(settings, "sendMessage", data)
    message = result.get("result", {})
    if (
        not result.get("ok")
        or not isinstance(message, dict)
        or not isinstance(message.get("message_id"), int)
    ):
        return "failed"
    ident = message["message_id"]
    if row.method == "renderPanel":
        owner.message_id = ident
        await track(db, owner, ident, "panel")
    elif data.get("reply_markup", {}).get("remove_keyboard"):
        await track(db, owner, ident, "aux")
        job(
            db,
            "deleteMessage",
            {"chat_id": owner.chat_id, "message_id": ident, "_outgoing": True},
            owner,
        )
    else:
        await track(db, owner, ident, "aux")
    return "sent"


async def dispatch(db, update_value, settings):
    from studgroup import bot_admin, headman_bot

    callback = update_value.callback_query
    message = callback.message if callback else update_value.message
    sender = callback.sender if callback else message.sender if message else None
    if not message or not sender or message.chat.type != "private" or message.chat.id != sender.id:
        return False
    chat = sender.id
    text = (message.text or "").strip()
    command = text.split()[0].split("@")[0] if text else ""
    route = await db.get(BotDialogRoute, chat)
    mode = None
    start = not callback and command == "/start"
    if callback:
        parts = (callback.data or "").split(":", 2)
        mode = "admin" if parts[0] == "a" else "st" if parts[0] == "s" else None
        if mode:
            session = await db.get(BotAdminSession if mode == "admin" else HeadmanSession, chat)
            if (
                not session
                or len(parts) != 3
                or not secrets.compare_digest(parts[1], session.nonce)
                or utc(session.expires_at) <= datetime.now(UTC)
            ):
                bot_admin.enqueue(
                    db,
                    "answerCallbackQuery",
                    {
                        "callback_query_id": callback.id,
                        "text": "Кнопка устарела. Открой команду заново.",
                    },
                )
                return True
            owner = await panel(db, chat, mode)
            claimed = await db.scalar(
                select(BotPanelMessage)
                .join(BotPanel, BotPanel.id == BotPanelMessage.panel_id)
                .where(BotPanel.chat_id == chat, BotPanelMessage.message_id == message.message_id)
            )
            if claimed and claimed.panel_id != owner.id:
                bot_admin.enqueue(
                    db,
                    "answerCallbackQuery",
                    {"callback_query_id": callback.id, "text": "Кнопка относится к другой панели."},
                )
                return True
            if owner.message_id is None:
                owner.message_id = message.message_id
                await track(db, owner, message.message_id, "panel")
            elif owner.message_id != message.message_id:
                bot_admin.enqueue(
                    db,
                    "answerCallbackQuery",
                    {
                        "callback_query_id": callback.id,
                        "text": "Это предыдущая панель. Открой новую команду.",
                    },
                )
                return True
    elif not start:
        if command in {"/admin", "/st"}:
            mode = command[1:]
            owner = await panel(db, chat, mode)
            await reset(db, owner)
            await track(db, owner, message.message_id, "command")
            update_value = update_value.model_copy(deep=True)
            update_value.message.text = command
        elif command == "/resetrun":
            # Owner-only reset lives in the admin panel; never let a headman dialog
            # (or the last used /st route) swallow it.
            mode = "admin"
            owner = await panel(db, chat, mode)
            await reset(db, owner)
            await track(db, owner, message.message_id, "command")
        elif command == "/st_cancel":
            mode = "st"
        elif command == "/cancel" or text == "Отмена":
            mode = route.mode if route else "admin"
            if mode == "st":
                update_value = update_value.model_copy(deep=True)
                update_value.message.text = "/st_cancel"
        elif message.users_shared:
            admin = await db.get(BotAdminSession, chat)
            mode = (
                "admin"
                if admin
                and admin.step == "pick"
                and admin.request_id == message.users_shared.request_id
                else route.mode
                if route
                else None
            )
        else:
            mode = route.mode if route else "st" if message.forward_origin else None
        if message.reply_to_message:
            recorded = await db.scalar(
                select(BotPanelMessage)
                .join(BotPanel, BotPanel.id == BotPanelMessage.panel_id)
                .where(
                    BotPanel.chat_id == chat,
                    BotPanelMessage.message_id == message.reply_to_message.message_id,
                    BotPanelMessage.generation == BotPanel.generation,
                )
            )
            if recorded:
                owner = await db.get(BotPanel, recorded.panel_id)
                mode = owner.mode
    if mode:
        await activate(db, chat, mode)
    token = scope.set((chat, mode) if mode else None)
    try:
        if mode == "admin":
            handled = await bot_admin.handle(db, update_value, settings)
        elif mode == "st":
            handled = await headman_bot.handle(db, update_value, settings)
        else:
            handled = await headman_bot.handle(db, update_value, settings)
            if not handled:
                handled = await bot_admin.handle(db, update_value, settings)
        if handled and mode and not callback and not start:
            owner = await panel(db, chat, mode)
            await track(db, owner, message.message_id, "input")
        return True
    finally:
        scope.reset(token)
