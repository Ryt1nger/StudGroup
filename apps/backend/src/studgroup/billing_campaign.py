"""One-time StudGroup billing announcement on 30 November 2026.

Recipients: active Telegram users in active pilot groups, excluding everybody
who is a member of the BI 1.2 group. All payment links must be ready before
queueing anything. The message has a single Telegram deep-link button; it does
not grant access, charge a bank, or create a payment order.
"""

import logging
import re
import uuid
from datetime import UTC, datetime, timedelta
from urllib.parse import urlencode

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from studgroup.bot_admin import enqueue_once
from studgroup.models import Group, Membership, User

LOGGER = logging.getLogger(__name__)
NOTICE_AT = datetime(2026, 11, 30, 9, 0, tzinfo=UTC)  # 12:00 Europe/Moscow
NOTICE_UNTIL = NOTICE_AT + timedelta(days=1)
EXCLUDED_GROUP_ID = uuid.UUID("4449bbe8-1515-5148-b460-ade389d3f656")
NOTICE_KEY = "billing-20261130"
BOT_NAME = re.compile(r"^[a-zA-Z0-9_]{5,32}$")


def notice_ready(settings, *, now: datetime | None = None) -> bool:
    """Fail closed if the owner has not configured real checkout links."""
    from studgroup.billing_manual import ready

    when = now or datetime.now(UTC)
    name = (settings.bot_username or "").removeprefix("@")
    return bool(
        NOTICE_AT <= when < NOTICE_UNTIL
        and settings.billing_announcement_enabled
        and settings.billing_payment_mode == "manual_alfa"
        and BOT_NAME.fullmatch(name)
        and ready(settings)
    )


async def queue_billing_announcement(engine, settings, *, now=None):
    """Schedule once per real Telegram user, transactionally/idempotently.

    Every member of the excluded BI 1.2 group is omitted, even when the user
    happens to belong to another pilot group as well. Only currently active
    groups and memberships are eligible. No messages go to group chats.
    """
    when = now or datetime.now(UTC)
    if not notice_ready(settings, now=when):
        return 0
    bot_name = (settings.bot_username or "").removeprefix("@")
    deep_link = "https://t.me/" + bot_name + "?" + urlencode({"start": "billing"})
    async with AsyncSession(engine, expire_on_commit=False) as db:
        from studgroup.billing_manual import checkout_ready

        if not await checkout_ready(db, settings):
            return 0
        rows = (
            await db.execute(
                select(User.id, User.telegram_user_id, Group.id)
                .join(Membership, Membership.user_id == User.id)
                .join(Group, Group.id == Membership.group_id)
                .where(
                    Membership.status == "active",
                    Group.status == "active",
                    Group.pilot_authorized.is_(True),
                )
            )
        ).all()
        excluded_ids = {
            telegram_id for _, telegram_id, group_id in rows if group_id == EXCLUDED_GROUP_ID
        }
        recipients = {
            telegram_id
            for _, telegram_id, group_id in rows
            if group_id != EXCLUDED_GROUP_ID and telegram_id not in excluded_ids and telegram_id > 0
        }
        queued = 0
        for telegram_id in sorted(recipients):
            sent = await enqueue_once(
                db,
                "sendMessage",
                {
                    "chat_id": telegram_id,
                    "text": (
                        "💳 Подписка StudGroup для вашей учебной группы\n\n"
                        "Первые 7 дней использования бесплатны. "
                        "Далее — 800 ₽ за 30 дней для всей группы, не за одного человека.\n"
                        "Можно оплатить сразу 1–4 месяца: 800, 1 600, 2 400 или 3 200 ₽.\n\n"
                        "Нажмите кнопку, выберите срок и откройте банковскую ссылку. "
                        "После перевода вернитесь в бот и нажмите «Я оплатил». "
                        "После проверки платежа доступ группы будет продлён.\n\n"
                        "Если оплата уже согласована со старостой, второй раз платить не нужно."
                    ),
                    "reply_markup": {
                        "inline_keyboard": [
                            [{"text": "💳 Открыть оплату группы", "url": deep_link}]
                        ]
                    },
                    "link_preview_options": {"is_disabled": True},
                },
                f"{NOTICE_KEY}:{telegram_id}",
            )
            queued += int(sent)
        await db.commit()
        LOGGER.info("billing_notice_queued count=%d", queued)
        return queued
