"""Owner-only private dialogs with expiring callbacks and durable Telegram replies."""

import asyncio
import json
import logging
import secrets
import uuid
from datetime import UTC, datetime, timedelta

import httpx
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from studgroup.models import BotAdminAudit, BotAdminSession, BotOutbox, Group, Membership, User


def enqueue(db, method, payload, dedup_key=None):
    db.add(
        BotOutbox(
            method=method,
            payload=json.dumps(payload, ensure_ascii=False),
            available_at=datetime.now(UTC),
            dedup_key=dedup_key,
        )
    )


async def say(db, chat, text, markup=None):
    from studgroup.bot_panels import queue_frame

    if await queue_frame(db, chat, text, markup):
        return
    payload = {"chat_id": chat, "text": text, "link_preview_options": {"is_disabled": True}}
    if markup:
        payload["reply_markup"] = markup
    enqueue(db, "sendMessage", payload)


def buttons(session, rows):
    return {
        "inline_keyboard": [
            [
                {"text": label, "callback_data": f"a:{session.nonce}:{action}"}
                for label, action in row
            ]
            for row in rows
        ]
    }


async def handle(db, update, settings):
    callback = update.callback_query
    message = callback.message if callback else update.message
    sender = callback.sender if callback else message.sender if message else None
    if not message or message.chat.type != "private" or not sender:
        return False
    chat = message.chat.id
    if callback:
        enqueue(db, "answerCallbackQuery", {"callback_query_id": callback.id})
    text = message.text or ""
    if not callback and text.split("@")[0].strip() == "/start":
        await say(
            db,
            chat,
            "Открой StudGroup через кнопку ниже.",
            {
                "inline_keyboard": [
                    [{"text": "Открыть StudGroup", "web_app": {"url": settings.webapp_origin}}]
                ]
            },
        )
        return True
    if sender.id != settings.bot_admin_user_id or chat != sender.id:
        if text.startswith("/admin"):
            await say(db, chat, "Админ-панель недоступна для этого аккаунта.")
        return True
    now = datetime.now(UTC)
    session = await db.scalar(
        select(BotAdminSession).where(BotAdminSession.owner_id == sender.id).with_for_update()
    )
    if not callback and text.strip().split("@")[0] in ["/admin", "/cancel", "Отмена"]:
        if session is None:
            session = BotAdminSession(owner_id=sender.id, nonce="", step="groups", expires_at=now)
            db.add(session)
        session.nonce = secrets.token_hex(6)
        session.step = "groups"
        session.group_id = None
        session.candidate_id = None
        session.candidate_role = "student"
        session.expires_at = now + timedelta(minutes=15)
        groups = (
            await db.scalars(
                select(Group)
                .where(Group.pilot_authorized.is_(True), Group.status == "active")
                .order_by(Group.name)
                .limit(40)
            )
        ).all()
        await say(
            db,
            chat,
            "Выбери группу:" if groups else "Нет подключённых групп.",
            buttons(session, [[(g.name[:50], "g" + g.id.hex)] for g in groups]),
        )
        return True
    if not callback and text.strip().split("@")[0] == "/resetrun":
        from studgroup import first_run

        groups = (
            await db.scalars(
                select(Group).where(Group.pilot_authorized.is_(True), Group.status == "active")
            )
        ).all()
        if len(groups) != 1:
            await say(db, chat, "Сброс первого прогона: нужна ровно одна подключённая группа.")
            return True
        group = groups[0]
        if session is None:
            session = BotAdminSession(owner_id=sender.id, nonce="", step="groups", expires_at=now)
            db.add(session)
        session.nonce = secrets.token_hex(6)
        session.step = "reset"
        session.group_id = group.id
        session.expires_at = now + timedelta(minutes=15)
        info = await first_run.summary(db, group.id)
        since = (
            info["since"].astimezone(UTC).strftime("%d.%m.%Y %H:%M UTC") if info["since"] else "—"
        )
        await say(
            db,
            chat,
            f"Сброс первого прогона для «{group.name}».\n"
            f"Будет удалено: прогонов {info['runs']}, задач {info['jobs']}, "
            f"попыток {info['attempts']}, кандидатов {info['candidates']}.\n"
            "Не удаляется: карточки (архив), сообщения, счётчик расходов.\n"
            f"Анализ начнётся с {since}; к разбору будет подготовлено сообщений: {info['to_analyze']}.",
            buttons(session, [[("Подтвердить сброс", "reset")]]),
        )
        return True
    expiry = session.expires_at if session else now
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=UTC)
    if not session or expiry <= now:
        if callback or message.users_shared:
            await say(db, chat, "Сессия истекла. Отправь /admin заново.")
        return True
    if callback:
        parts = (callback.data or "").split(":", 2)
        if (
            len(parts) != 3
            or parts[0] != "a"
            or not secrets.compare_digest(parts[1], session.nonce)
        ):
            await say(db, chat, "Кнопка устарела. Отправь /admin заново.")
            return True
        action = parts[2]
        if action == "reset" and session.step == "reset" and session.group_id:
            from studgroup import first_run

            done = await first_run.reset_first_run(db, session.group_id, now)
            session.step = "done"
            session.nonce = secrets.token_hex(6)
            if done is None:
                await say(db, chat, "Сброс невозможен: у группы нет даты установки бота.")
            else:
                await say(
                    db,
                    chat,
                    "Готово. Старый прогон удалён: "
                    f"прогонов {done['runs']}, задач {done['jobs']}, "
                    f"попыток {done['attempts']}, кандидатов {done['candidates']}. "
                    "Первый прогон начнётся после включения ИИ.",
                )
            return True
        if action.startswith("g") and session.step == "groups":
            try:
                group = await db.get(Group, uuid.UUID(hex=action[1:]))
            except ValueError:
                group = None
            if not group or not group.pilot_authorized or group.status != "active":
                return True
            session.group_id = group.id
            session.step = "method"
            await say(
                db,
                chat,
                f"{group.name}\nКак выбрать участника?",
                buttons(
                    session,
                    [
                        [("Выбрать пользователя", "pick")],
                        [("Добавить по ID", "id")],
                        [("Назначить старосту по ID", "headman")],
                        [("Назначить помощника по ID", "deputy")],
                    ],
                ),
            )
        elif action in ["pick", "id", "headman", "deputy"] and session.step == "method":
            session.candidate_role = action if action in {"headman", "deputy"} else "student"
            session.step = "id" if action in {"headman", "deputy"} else action
            if action != "pick":
                await say(db, chat, "Отправь числовой Telegram ID участника. Для отмены — /cancel.")
            else:
                session.request_id = secrets.randbelow(2**31 - 1) + 1
                await say(
                    db,
                    chat,
                    "Нажми кнопку ниже и выбери пользователя Telegram.",
                    {
                        "keyboard": [
                            [
                                {
                                    "text": "Выбрать пользователя",
                                    "request_users": {
                                        "request_id": session.request_id,
                                        "user_is_bot": False,
                                        "max_quantity": 1,
                                        "request_name": True,
                                        "request_username": True,
                                    },
                                }
                            ],
                            [{"text": "Отмена"}],
                        ],
                        "resize_keyboard": True,
                        "one_time_keyboard": True,
                    },
                )
        elif action == "yes" and session.step == "confirm" and session.candidate_id:
            group = await db.get(Group, session.group_id)
            if not group or not group.pilot_authorized or group.status != "active":
                return True
            target = session.candidate_id
            await db.execute(
                insert(User)
                .values(id=uuid.uuid4(), telegram_user_id=target, display_name="Участник")
                .on_conflict_do_nothing(index_elements=[User.telegram_user_id])
            )
            user = await db.scalar(select(User).where(User.telegram_user_id == target))
            await db.execute(
                insert(Membership)
                .values(
                    id=uuid.uuid4(),
                    user_id=user.id,
                    group_id=group.id,
                    role="student",
                    status="active",
                )
                .on_conflict_do_nothing(index_elements=[Membership.user_id, Membership.group_id])
            )
            member = await db.scalar(
                select(Membership)
                .where(Membership.user_id == user.id, Membership.group_id == group.id)
                .with_for_update()
            )
            member.status = "active"
            if session.candidate_role in {"headman", "deputy"}:
                member.role = session.candidate_role
            db.add(
                BotAdminAudit(
                    id=uuid.uuid5(uuid.NAMESPACE_URL, f"bot-admin:{update.update_id}"),
                    owner_id=sender.id,
                    target_id=target,
                    group_id=group.id,
                    created_at=now,
                )
            )
            session.step = "done"
            session.nonce = secrets.token_hex(6)
            await say(
                db,
                chat,
                f"ID {target} подключён к «{group.name}». Роль: {member.role}. Попроси участника заново открыть Mini App. Для старосты/помощника доступен /st.",
                {"remove_keyboard": True},
            )
        return True
    target = None
    if session.step == "id":
        if text.isdecimal() and 0 < int(text) < 2**63:
            target = int(text)
        else:
            await say(db, chat, "Нужен положительный числовой ID. Пример: 123456789.")
            return True
    elif session.step == "pick" and message.users_shared:
        shared = message.users_shared
        if shared.request_id != session.request_id or len(shared.users) != 1:
            return True
        target = shared.users[0].user_id
    if target:
        session.candidate_id = target
        session.step = "confirm"
        session.nonce = secrets.token_hex(6)
        group = await db.get(Group, session.group_id)
        if group:
            await say(
                db,
                chat,
                f"Подключить ID {target} к «{group.name}»? Роль: {session.candidate_role}. Назначение роли произойдёт только после подтверждения.",
                buttons(session, [[("Подключить", "yes")]]),
            )
    return True


async def telegram_request(settings, method, payload):
    async with httpx.AsyncClient(timeout=10, trust_env=False, follow_redirects=False) as client:
        response = await client.post(
            f"https://api.telegram.org/bot{settings.telegram_bot_token}/{method}", json=payload
        )
        data = response.json()
        if response.status_code != 200:
            data["ok"] = False
        return data


async def deliver(engine, settings):
    if not settings.telegram_bot_token:
        return
    now = datetime.now(UTC)
    async with AsyncSession(engine, expire_on_commit=False) as db:
        row = await db.scalar(
            select(BotOutbox)
            .where(BotOutbox.state == "pending", BotOutbox.available_at <= now)
            .where((BotOutbox.lease_until.is_(None)) | (BotOutbox.lease_until <= now))
            .order_by(BotOutbox.available_at)
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if not row:
            return
        testing_update = (row.dedup_key or "").startswith("owner-test:")
        if (
            testing_update
            and settings.ai_schedule_enabled
            and any(
                f":{prefix}:" in row.dedup_key
                for prefix in (
                    "run-start",
                    "run-finish",
                    "run-reconciled",
                    "filter-start",
                    "filter-finish",
                )
            )
        ):
            # Also catches legacy notices queued by an overlapping old deployment.
            row.state = "superseded"
            await db.commit()
            return
        if testing_update and (
            not settings.owner_update_notifications_enabled
            or json.loads(row.payload).get("chat_id") != settings.owner_telegram_user_id
        ):
            row.state = "superseded"
            await db.commit()
            return
        row.lease_until = now + timedelta(seconds=30)
        row.attempts += 1
        await db.commit()
        ok = False
        try:
            if row.panel_id:
                from studgroup.bot_panels import execute

                outcome = await execute(db, row, settings, telegram_request)
                if outcome == "superseded":
                    row.state = "superseded"
                    row.lease_until = None
                    await db.commit()
                    return
                ok = outcome == "sent"
            elif row.method in ["sendMessage", "answerCallbackQuery"]:
                result = await telegram_request(settings, row.method, json.loads(row.payload))
                ok = result.get("ok") is True
        except (httpx.HTTPError, ValueError):
            pass
        row.state = (
            "sent" if ok else "failed" if row.attempts >= 5 and not testing_update else "pending"
        )
        row.lease_until = None
        row.available_at = now + timedelta(seconds=min(60, row.attempts * 10))
        await db.commit()


async def delivery_loop(engine, settings):
    iterations = 0
    while True:
        try:
            await deliver(engine, settings)
            if iterations % 10 == 0:
                from studgroup.owner_updates import tick as owner_update_tick

                await owner_update_tick(engine, settings)
            if iterations % 60 == 0:
                from studgroup.headman_digest import tick

                await tick(engine)
            iterations += 1
        except Exception as error:  # noqa: BLE001 -- durable retry; never log sensitive exception text
            logging.getLogger(__name__).warning(
                "bot_delivery_iteration_failed: %s", type(error).__name__
            )
        await asyncio.sleep(0.5)
