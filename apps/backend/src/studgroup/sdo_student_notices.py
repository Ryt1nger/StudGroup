"""Approved private SDO notices; never send credentials or infer password failures."""

import json
import uuid
from datetime import UTC
from urllib.parse import urlencode, urlsplit

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from studgroup.bot_admin import enqueue, enqueue_once
from studgroup.legal import (
    VERSION,
    credentials_ready,
    document_hash,
    has_consent,
    latest_consent,
    receipt,
)
from studgroup.models import (
    BotOutbox,
    Group,
    Membership,
    RanepaCrawlJob,
    RanepaCrawlObservation,
    User,
    UserProfile,
)
from studgroup.ranepa_crawl_jobs import REVALIDATED, enqueue_new_group_account

PREFIX = "sdo-student:"
CAMPAIGN = "sdo-student-campaign:20261126"
PURPOSES = ("terms", "service", "sdo")
MESSAGES = {
    "connect": (
        "🎓 Сделай StudGroup ещё полезнее!\n\n"
        "Подключи свой аккаунт СДО, чтобы получать актуальное расписание, "
        "ссылки на дистанционные пары и более точные дедлайны домашних заданий.\n\n"
        "Больше не нужно постоянно переключаться между учебными сервисами — "
        "всё важное будет под рукой.\n\n"
        "Подключение добровольное, пароль хранится в зашифрованном виде."
    ),
    "password": (
        "🔑 Не удалось войти в твоё СДО\n\n"
        "Личный кабинет сообщил, что указанный логин или пароль неверен.\n\n"
        "Обнови данные для входа, чтобы StudGroup снова мог автоматически "
        "получать твоё расписание и ссылки на занятия."
    ),
    "consent": (
        "🔄 Возобнови синхронизацию СДО\n\n"
        "Мы обновили документы StudGroup. Чтобы автоматическое обновление "
        "расписания и ссылок на занятия продолжилось, необходимо подтвердить "
        "актуальные соглашения:\n\n"
        "• Пользовательское соглашение.\n"
        "• Согласие на обработку персональных данных.\n"
        "• Согласие на хранение и использование реквизитов СДО / ЛК.\n\n"
        "Ознакомиться со всеми документами можно на странице подключения СДО.\n\n"
        "Твои данные для входа уже сохранены — вводить их повторно не нужно."
    ),
}


def utc(value):
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def configured(settings):
    url = urlsplit(settings.webapp_origin)
    return bool(
        credentials_ready(settings)
        and settings.ranepa_access_approved
        and url.scheme == "https"
        and url.hostname
    )


async def active_user(db, telegram_id):
    return await db.scalar(
        select(User)
        .join(Membership, Membership.user_id == User.id)
        .join(Group, Group.id == Membership.group_id)
        .where(
            User.telegram_user_id == telegram_id,
            Membership.status == "active",
            Group.status == "active",
        )
        .limit(1)
    )


async def classify(db, user, settings):
    profile = await db.get(UserProfile, user.id)
    if profile and (profile.settings or {}).get("enabled") is False:
        return None
    previous = await latest_consent(db, user.id, "sdo")
    if previous is not None and previous.action == "revoke":
        return None
    if not profile or not (profile.sdu_credential_id or profile.sdu_password_encrypted):
        return "connect", VERSION
    if not profile.sdu_credential_id:
        return None
    missing = [
        purpose for purpose in PURPOSES if not await has_consent(db, user.id, purpose, settings)
    ]
    if missing:
        return "consent", VERSION
    # The newest finalized outcome must prove actual credential rejection.
    job = await db.scalar(
        select(RanepaCrawlJob)
        .where(RanepaCrawlJob.user_id == user.id, RanepaCrawlJob.finished_at.is_not(None))
        .order_by(RanepaCrawlJob.finished_at.desc(), RanepaCrawlJob.id)
        .limit(1)
    )
    if (
        job is None
        or job.state != "failed"
        or job.failure_code != "login_failed"
        or job.retry_reason in REVALIDATED
    ):
        return None
    evidence = await db.get(RanepaCrawlObservation, (job.id, job.attempts))
    if (
        evidence is None
        or evidence.result_code != "login_failed"
        or evidence.failure_detail != "login_rejected"
        or evidence.last_step != "lk_explicit_rejection"
        or utc(profile.updated_at) > utc(evidence.observed_at)
    ):
        return None
    return "password", f"{job.id}:{job.attempts}"


def document_hashes(settings):
    return {purpose: document_hash(purpose, settings) for purpose in PURPOSES}


async def queue_notice(db, user, settings, kind, key):
    ident = uuid.uuid4()
    url = settings.webapp_origin.rstrip("/") + "/profile/sdo?" + urlencode({"notice": str(ident)})
    labels = {
        "connect": "Подключить СДО",
        "password": "Обновить данные",
        "consent": "Перейти к подключению СДО",
    }
    keyboard = [[{"text": labels[kind], "web_app": {"url": url}}]]
    if kind == "consent":
        keyboard.insert(0, [{"text": "Согласиться со всем", "callback_data": f"sdo:{ident.hex}"}])
    return await enqueue_once(
        db,
        "sendMessage",
        {
            "chat_id": user.telegram_user_id,
            "text": MESSAGES[kind],
            "reply_markup": {"inline_keyboard": keyboard},
            "_sdo_notice": {
                "kind": kind,
                "key": key,
                "version": VERSION,
                "hashes": document_hashes(settings),
            },
        },
        f"{PREFIX}{kind}:{user.id}:{key}",
        ident=ident,
    )


async def queue_student_notices(engine, settings):
    if not configured(settings):
        return 0
    async with AsyncSession(engine) as db:
        # Only the approved one-time outreach includes unconnected accounts.
        # Subsequent scans deliver new confirmed rejection notices, not invitations.
        initial = await enqueue_once(db, "sdoNoticeCampaign", {}, CAMPAIGN)
        if initial:
            await db.execute(
                update(BotOutbox).where(BotOutbox.dedup_key == CAMPAIGN).values(state="sent")
            )
        users = (
            await db.scalars(
                select(User)
                .join(Membership, Membership.user_id == User.id)
                .join(Group, Group.id == Membership.group_id)
                .where(
                    User.telegram_user_id > 0,
                    Membership.status == "active",
                    Group.status == "active",
                )
                .distinct()
            )
        ).all()
        queued = 0
        for user in users:
            state = await classify(db, user, settings)
            if state and (initial or state[0] == "password"):
                queued += int(await queue_notice(db, user, settings, *state))
        await db.commit()
        return queued


async def delivery_eligible(db, row, settings):
    if not configured(settings):
        return False
    data = json.loads(row.payload)
    notice = data.get("_sdo_notice", {})
    user = await active_user(db, data.get("chat_id"))
    return bool(
        user
        and notice.get("version") == VERSION
        and notice.get("hashes") == document_hashes(settings)
        and await classify(db, user, settings) == (notice.get("kind"), notice.get("key"))
    )


async def own_notice(db, ident, chat):
    row = await db.get(BotOutbox, ident, with_for_update=True)
    if row is None or row.state != "sent" or not (row.dedup_key or "").startswith(PREFIX):
        return None
    data = json.loads(row.payload)
    if data.get("chat_id") != chat or not isinstance(
        data.get("_sdo_notice", {}).get("message_id"), int
    ):
        return None
    return row, data["_sdo_notice"]


async def remove_notice(db, row, chat, message_id):
    await enqueue_once(
        db,
        "deleteMessage",
        {"chat_id": chat, "message_id": message_id},
        f"sdo-student-delete:{row.id}",
    )


async def acknowledge_open(db, ident, chat):
    owned = await own_notice(db, ident, chat)
    if owned is None:
        return False
    row, notice = owned
    await remove_notice(db, row, chat, notice["message_id"])
    return True


async def accept_callback(db, settings, callback, chat, message_id):
    answer = "Уведомление устарело. Откройте страницу подключения СДО."
    try:
        ident = uuid.UUID(hex=callback.data.split(":", 1)[1])
    except (ValueError, IndexError):
        ident = None
    owned = await own_notice(db, ident, chat) if ident else None
    if owned is not None and configured(settings):
        row, notice = owned
        user = await active_user(db, chat)
        if (
            user is not None
            and notice.get("kind") == "consent"
            and notice.get("message_id") == message_id
            and notice.get("version") == VERSION
            and notice.get("hashes") == document_hashes(settings)
        ):
            state = await classify(db, user, settings)
            if state == ("consent", VERSION):
                # An explicit affirmative button lists the three documents.
                # Store independent current-version receipts, never pre-ticked boxes.
                for purpose in PURPOSES:
                    if not await has_consent(db, user.id, purpose, settings):
                        receipt(
                            db,
                            user.id,
                            purpose,
                            "accept" if purpose == "terms" else "grant",
                            "telegram",
                            settings,
                        )
                await db.flush()
                await enqueue_new_group_account(db, user.id, settings, consent_revalidated=True)
                await remove_notice(db, row, chat, message_id)
                answer = "Согласия подтверждены. Синхронизация возобновится."
            elif all([await has_consent(db, user.id, purpose, settings) for purpose in PURPOSES]):
                await remove_notice(db, row, chat, message_id)
                answer = "Согласия уже подтверждены."
    enqueue(db, "answerCallbackQuery", {"callback_query_id": callback.id, "text": answer})
