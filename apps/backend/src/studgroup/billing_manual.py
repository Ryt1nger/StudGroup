"""RUB bank payment links with explicit manual verification by the bot owner.

There is deliberately no bank API or inferred payment receipt. A student's
"I paid" click ONLY creates a review request; the owner must check their bank
and explicitly confirm before any group entitlement is extended.
"""

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from studgroup.billing_plans import extended_paid_until, price_quote
from studgroup.bot_admin import enqueue, enqueue_once
from studgroup.models import BillingPaymentOption, Group, GroupBankOrder, StartSession

LOGGER = logging.getLogger(__name__)
SUCCESS_SECONDS = 20


@dataclass(frozen=True)
class CheckoutOption:
    label: str
    months: int
    payment_url: str
    position: int


def owner_id(settings) -> int | None:
    return settings.owner_telegram_user_id or settings.bot_admin_user_id


def payment_link(settings, months: int) -> str | None:
    if months not in (1, 2, 3, 4):
        return None
    return payment_link_value(getattr(settings, f"billing_alfa_url_{months}m", ""))


def ready(settings) -> bool:
    return bool(
        settings.billing_enabled
        and settings.billing_payment_mode == "manual_alfa"
        and not settings.cutover_read_only
        and settings.telegram_bot_token
        and owner_id(settings) is not None
    )


def payment_link_value(value: str) -> str | None:
    try:
        parsed = urlsplit((value or "").strip())
        _ = parsed.port  # Reject malformed ports before saving the link.
    except ValueError:
        return None
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        return None
    return value.strip()


async def payment_options(db, settings) -> list[CheckoutOption]:
    """Owner-managed buttons, with environment links retained as a migration fallback."""
    stored = (
        await db.scalars(
            select(BillingPaymentOption).order_by(
                BillingPaymentOption.position, BillingPaymentOption.months
            )
        )
    ).all()
    if stored:
        return [
            CheckoutOption(row.label, row.months, row.payment_url, row.position)
            for row in stored
            if row.enabled and payment_link_value(row.payment_url)
        ]
    result = []
    for months in (1, 2, 3, 4):
        url = payment_link(settings, months)
        if url:
            quote = price_quote(uuid.UUID(int=0), months)
            result.append(
                CheckoutOption(f"{months} мес. · {quote.amount_rub:,} ₽", months, url, months)
            )
    return result


async def checkout_ready(db, settings) -> bool:
    return ready(settings) and bool(await payment_options(db, settings))


async def create_order(db, settings, *, group_id, buyer_id, months):
    """Create a frozen RUB request. Opening its link does not prove a payment."""
    if not ready(settings):
        return None
    from studgroup.billing import member_group

    member = await member_group(db, buyer_id)
    if not member or member[1].id != group_id:
        return None
    quote = price_quote(group_id, months)
    option = next(
        (row for row in await payment_options(db, settings) if row.months == months), None
    )
    if option is None:
        return None
    now = datetime.now(UTC)
    order = GroupBankOrder(
        id=uuid.uuid4(),
        group_id=group_id,
        payer_telegram_id=buyer_id,
        months=quote.months,
        amount_rub=quote.amount_rub,
        payment_url=option.payment_url,
        status="pending",
        created_at=now,
        cleanup_done=False,
    )
    db.add(order)
    await db.flush()
    return order


async def submit(db, settings, *, order_id, buyer_id, group_id) -> str:
    """Student requests review, never credits the group."""
    if not ready(settings):
        return "unavailable"
    from studgroup.billing import member_group

    member = await member_group(db, buyer_id)
    if not member or member[1].id != group_id:
        return "unauthorized"
    order = await db.scalar(
        select(GroupBankOrder).where(GroupBankOrder.id == order_id).with_for_update()
    )
    if not order or order.group_id != group_id or order.payer_telegram_id != buyer_id:
        return "unauthorized"
    if order.status == "awaiting_review":
        return "already_submitted"
    if order.status != "pending":
        return "invalid_status"
    group = await db.get(Group, group_id)
    if not group:
        return "unavailable"
    order.status = "awaiting_review"
    order.submitted_at = datetime.now(UTC)
    # Short bank reference helps the owner reconcile the correct amount/group.
    reference = str(order.id).split("-")[0].upper()
    await enqueue_once(
        db,
        "sendMessage",
        {
            "chat_id": owner_id(settings),
            "text": (
                "🏦 Заявка на проверку перевода в Альфа-Банк\n\n"
                f"Группа: {group.name}\n"
                f"Период: {order.months} мес. ({30 * order.months} дней)\n"
                f"Сумма: {order.amount_rub:,} ₽\n"
                f"Telegram плательщика: {buyer_id}\n"
                f"Номер заявки: {reference}\n\n"
                "Сверьте реальное поступление в приложении банка. "
                "Нажимайте «Подтвердить» только после проверки."
            ),
            "reply_markup": {
                "inline_keyboard": [
                    [{"text": "✅ Подтвердить получение", "callback_data": f"mb:{order.id}:ok"}],
                    [{"text": "❌ Не поступило", "callback_data": f"mb:{order.id}:no"}],
                ]
            },
        },
        f"billing-bank-review:{order.id}",
    )
    return "submitted"


async def _student_screen(db, order: GroupBankOrder, message: str, *, success: bool):
    """Render a success/denial on the student's existing one-message start panel."""
    from studgroup import bot_panels, bot_start

    session = await db.scalar(
        select(StartSession)
        .where(StartSession.telegram_user_id == order.payer_telegram_id)
        .with_for_update()
    )
    if session is None:
        session = StartSession(
            telegram_user_id=order.payer_telegram_id,
            nonce="",
            step="home",
            payload="{}",
            expires_at=datetime.now(UTC),
        )
        db.add(session)
        await db.flush()
    await bot_panels.activate(db, order.payer_telegram_id, "start")
    token = bot_panels.scope.set((order.payer_telegram_id, "start"))
    try:
        await bot_start.screen(
            db,
            session,
            message,
            [[("Открыть главное меню", "home")]]
            if success
            else [[("Повторить оплату", "billing_pay"), ("Главное меню", "home")]],
            "billing_success" if success else "billing_declined",
            {"order_id": str(order.id)},
        )
    finally:
        bot_panels.scope.reset(token)


async def owner_callback(db, callback, settings):
    """Only the configured Telegram owner may settle a RUB transfer."""
    data = callback.data or ""
    if not data.startswith("mb:"):
        return False

    def answer(message):
        enqueue(
            db,
            "answerCallbackQuery",
            {"callback_query_id": callback.id, "text": message[:180]},
        )

    if (
        not ready(settings)
        or callback.sender.id != owner_id(settings)
        or callback.message is None
        or callback.message.chat.type != "private"
        or callback.message.chat.id != callback.sender.id
    ):
        answer("Недоступно")
        return True
    try:
        prefix, raw_id, action = data.split(":")
        if prefix != "mb" or action not in {"ok", "no"}:
            raise ValueError("invalid_action")
        order_id = uuid.UUID(raw_id)
    except ValueError:
        answer("Неверная заявка")
        return True
    order = await db.scalar(
        select(GroupBankOrder).where(GroupBankOrder.id == order_id).with_for_update()
    )
    if order is None:
        answer("Заявка не найдена")
        return True
    if order.status != "awaiting_review":
        answer("Заявка уже обработана")
        return True
    now = datetime.now(UTC)
    if action == "ok":
        from studgroup.billing import as_utc, ensure_subscription, formatted_date

        subscription = await ensure_subscription(db, order.group_id, now)
        paid_from = as_utc(subscription.paid_until) if subscription.paid_until else None
        trial_deadline = as_utc(subscription.trial_ends_at)
        if trial_deadline > now and (paid_from is None or paid_from < trial_deadline):
            paid_from = trial_deadline
        subscription.paid_until = extended_paid_until(
            now=now, paid_until=paid_from, months=order.months
        )
        subscription.renewal_warning_for = None
        subscription.updated_at = now
        order.status = "paid"
        order.confirmed_at = now
        order.confirmed_by = callback.sender.id
        order.cleanup_at = now + timedelta(seconds=SUCCESS_SECONDS)
        order.cleanup_done = False
        await _student_screen(
            db,
            order,
            (
                "✅ Оплата подтверждена!\n\n"
                f"Доступ всей группы продлён на {order.months} мес.\n"
                f"Действует до: {formatted_date(subscription.paid_until)}.\n\n"
                "Через 20 секунд откроется главное меню."
            ),
            success=True,
        )
        answer("Оплата подтверждена")
    else:
        order.status = "rejected"
        order.confirmed_at = now
        order.confirmed_by = callback.sender.id
        await _student_screen(
            db,
            order,
            "⚠️ Поступление не подтверждено. Доступ группы не продлён. "
            "Проверьте перевод и при необходимости обратитесь в поддержку.",
            success=False,
        )
        answer("Заявка отклонена")
    # Hide approval buttons to prevent accidental secondary confirmations.
    enqueue(
        db,
        "editMessageReplyMarkup",
        {
            "chat_id": callback.sender.id,
            "message_id": callback.message.message_id,
            "reply_markup": {"inline_keyboard": []},
        },
    )
    return True


async def cleanup_due(engine, settings):
    """Replace short-lived success with the normal home panel; restart-safe."""
    if not ready(settings):
        return
    from studgroup import bot_panels, bot_start

    async with AsyncSession(engine, expire_on_commit=False) as db:
        now = datetime.now(UTC)
        orders = (
            await db.scalars(
                select(GroupBankOrder)
                .where(
                    GroupBankOrder.status == "paid",
                    GroupBankOrder.cleanup_at.is_not(None),
                    GroupBankOrder.cleanup_at <= now,
                    GroupBankOrder.cleanup_done.is_(False),
                )
                .order_by(GroupBankOrder.cleanup_at)
                .limit(20)
                .with_for_update(skip_locked=True)
            )
        ).all()
        for order in orders:
            session = await db.scalar(
                select(StartSession)
                .where(StartSession.telegram_user_id == order.payer_telegram_id)
                .with_for_update()
            )
            if session and session.step == "billing_success":
                try:
                    payload = json.loads(session.payload)
                except (TypeError, ValueError):
                    payload = {}
                if payload.get("order_id") == str(order.id):
                    await bot_panels.activate(db, order.payer_telegram_id, "start")
                    token = bot_panels.scope.set((order.payer_telegram_id, "start"))
                    try:
                        await bot_start.home(db, session, settings, order.payer_telegram_id)
                    finally:
                        bot_panels.scope.reset(token)
            order.cleanup_done = True
        await db.commit()


async def cleanup_loop(engine, settings):
    from studgroup.billing_campaign import queue_billing_announcement

    while True:
        try:
            await cleanup_due(engine, settings)
        except Exception as error:  # noqa: BLE001
            LOGGER.warning("bank_payment_cleanup_failed kind=%s", type(error).__name__)
        try:
            await queue_billing_announcement(engine, settings)
        except Exception as error:  # noqa: BLE001
            LOGGER.warning("billing_notice_iteration_failed kind=%s", type(error).__name__)
        await asyncio.sleep(3)
