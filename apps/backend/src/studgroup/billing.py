"""Telegram Stars group checkout, settlement and trial reminders.

Digital access sold *inside* Telegram must use XTR. No bank/P2P links or
client-reported payment statuses are accepted. All monetary effects are frozen
on a group order and applied only after Telegram's authenticated successful_payment.
"""

import asyncio
import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from studgroup.billing_plans import (
    extended_paid_until,
    price_quote,
    trial_end,
)
from studgroup.bot_admin import enqueue_once, telegram_request
from studgroup.models import Group, GroupPaymentOrder, GroupSubscription, Membership, User

LOGGER = logging.getLogger(__name__)
GRACE_DAYS = 2
INVOICE_TTL = timedelta(hours=6)
PAYLOAD_PREFIX = "sg-group:"
OPEN_CHECKOUT_TEXT = "Откройте бот StudGroup → «Доступ группы»."
PRICE_NOTE = (
    "800 ₽ за 30 дней для всей группы, а не для каждого студента.\n"
    "Если вас 30–35 человек, это примерно 23–27 ₽ с человека; "
    "25–30 человек — 27–32 ₽; при 20 участниках — 40 ₽."
)


def payment_ready(settings) -> bool:
    return bool(
        settings.billing_enabled
        and settings.billing_payment_mode == "stars"
        and settings.billing_stars_per_month > 0
        and settings.telegram_bot_token
        and not settings.cutover_read_only
    )


def as_utc(value: datetime) -> datetime:
    """Normalize database timestamps, including SQLite test drivers."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def state(subscription: GroupSubscription, now: datetime) -> str:
    now = as_utc(now)
    trial_end_at = as_utc(subscription.trial_ends_at)
    paid_end_at = as_utc(subscription.paid_until) if subscription.paid_until else None
    if paid_end_at is not None and paid_end_at > now:
        return "active"
    if trial_end_at > now:
        return "trial"
    latest = max(trial_end_at, paid_end_at or trial_end_at)
    return "grace" if latest + timedelta(days=GRACE_DAYS) > now else "expired"


def formatted_date(value: datetime) -> str:
    return as_utc(value).strftime("%d.%m.%Y %H:%M UTC")


async def member_group(db, telegram_id: int) -> tuple[Membership, Group] | None:
    """Active Telegram identity and active group membership are authoritative."""
    return (
        await db.execute(
            select(Membership, Group)
            .join(User, User.id == Membership.user_id)
            .join(Group, Group.id == Membership.group_id)
            .where(
                User.telegram_user_id == telegram_id,
                Membership.status == "active",
                Group.status == "active",
                Group.pilot_authorized.is_(True),
            )
            .order_by(Group.name)
            .limit(1)
        )
    ).first()


async def ensure_subscription(
    db: AsyncSession,
    group_id: uuid.UUID,
    now: datetime | None = None,
) -> GroupSubscription:
    """A serialized first access starts one trial; existing pilots get a fair first week.

    The lock on Group ensures two students cannot restart the trial concurrently.
    New subscriptions are only created while billing is explicitly enabled.
    """
    now = now or datetime.now(UTC)
    group = await db.scalar(select(Group).where(Group.id == group_id).with_for_update())
    if group is None or group.status != "active" or not group.pilot_authorized:
        raise ValueError("Group is not eligible for billing")
    subscription = await db.get(GroupSubscription, group_id)
    if subscription is None:
        subscription = GroupSubscription(
            group_id=group_id,
            trial_started_at=now,
            trial_ends_at=trial_end(now),
            paid_until=None,
            trial_warning_at=None,
            trial_expired_notice_at=None,
            renewal_warning_for=None,
            updated_at=now,
        )
        db.add(subscription)
        await db.flush()
    return subscription


async def group_limited(db, group_id: uuid.UUID, settings, now=None) -> bool:
    """Check enforcement only when explicitly enabled; never restrict a pilot by default."""
    from studgroup.billing_manual import ready as bank_ready

    if not settings.billing_enforce_access or not (
        payment_ready(settings) or bank_ready(settings)
    ):
        return False
    subscription = await ensure_subscription(db, group_id, now)
    return state(subscription, now or datetime.now(UTC)) == "expired"


def invoice_payload(order_id: uuid.UUID) -> str:
    return PAYLOAD_PREFIX + str(order_id)


def parse_payload(value: str) -> uuid.UUID | None:
    if not isinstance(value, str) or not value.startswith(PAYLOAD_PREFIX):
        return None
    try:
        result = uuid.UUID(value[len(PAYLOAD_PREFIX) :])
    except (ValueError, AttributeError):
        return None
    return result if invoice_payload(result) == value else None


async def create_invoice(
    db: AsyncSession, settings, *, group_id: uuid.UUID, buyer_id: int, months: int
) -> tuple[GroupPaymentOrder, str] | None:
    """Create a Telegram-native checkout link after freezing the price and payer.

    An unusable provider result cannot grant a subscription; the order is failed
    and another attempt creates a fresh order. The bank is never spoofed.
    """
    if not payment_ready(settings):
        return None
    found = await member_group(db, buyer_id)
    if not found or found[1].id != group_id:
        return None
    quote = price_quote(group_id, months)
    stars = settings.billing_stars_per_month * months
    if stars < 1 or stars > 10000:
        return None
    now = datetime.now(UTC)
    await ensure_subscription(db, group_id, now)
    order = GroupPaymentOrder(
        id=uuid.uuid4(),
        group_id=group_id,
        payer_telegram_id=buyer_id,
        months=months,
        amount_rub=quote.amount_rub,
        stars_amount=stars,
        currency="XTR",
        status="pending",
        created_at=now,
    )
    db.add(order)
    await db.flush()
    try:
        invoice = await telegram_request(
            settings,
            "createInvoiceLink",
            {
                "title": f"StudGroup · группа · {months} мес.",
                "description": (
                    f"Доступ для всей учебной группы на {30 * months} дней. "
                    "Одноразовая оплата звёздами Telegram."
                ),
                "payload": invoice_payload(order.id),
                "currency": "XTR",
                "prices": [{"label": f"StudGroup · {months} мес.", "amount": stars}],
            },
        )
    except Exception as error:  # noqa: BLE001 -- private network failures have no payment effect
        LOGGER.warning("billing_create_invoice_failed kind=%s", type(error).__name__)
        order.status = "failed"
        return None
    url = invoice.get("result")
    if (
        invoice.get("ok") is not True
        or not isinstance(url, str)
        or not url.startswith("https://t.me/")
    ):
        order.status = "failed"
        return None
    order.invoice_url = url
    return order, url


async def validate_pre_checkout(db: AsyncSession, query, settings) -> bool:
    """Never confirm a mutated/expired invoice, forged payer, or wrong XTR amount."""
    if not payment_ready(settings):
        return False
    order_id = parse_payload(query.invoice_payload)
    if order_id is None:
        return False
    order = await db.get(GroupPaymentOrder, order_id)
    if (
        order is None
        or order.status != "pending"
        or order.invoice_url is None
        or order.currency != "XTR"
        or query.currency != "XTR"
        or order.stars_amount != query.total_amount
        or order.payer_telegram_id != query.sender.id
        or order.created_at.replace(tzinfo=order.created_at.tzinfo or UTC) + INVOICE_TTL
        < datetime.now(UTC)
    ):
        return False
    found = await member_group(db, query.sender.id)
    return bool(found and found[1].id == order.group_id)


async def answer_pre_checkout(db, query, settings):
    allowed = await validate_pre_checkout(db, query, settings)
    data = {"pre_checkout_query_id": query.id, "ok": allowed}
    if not allowed:
        data["error_message"] = "Счёт устарел или недоступен. Откройте новый через StudGroup."
    result = await telegram_request(settings, "answerPreCheckoutQuery", data)
    if result.get("ok") is not True:
        raise RuntimeError("telegram_precheckout_answer_failed")


async def confirm_payment(db: AsyncSession, message, settings) -> str:
    """Idempotently settle one Telegram successful_payment and extend all group access."""
    receipt = message.successful_payment
    if receipt is None or message.sender is None or message.chat.id != message.sender.id:
        return "invalid_message"
    order_id = parse_payload(receipt.invoice_payload)
    if order_id is None:
        return "invalid_order"
    order = await db.scalar(
        select(GroupPaymentOrder).where(GroupPaymentOrder.id == order_id).with_for_update()
    )
    if order is None:
        return "unknown_order"
    if (
        receipt.currency != "XTR"
        or order.currency != "XTR"
        or order.stars_amount != receipt.total_amount
        or order.payer_telegram_id != message.sender.id
        or not receipt.telegram_payment_charge_id
    ):
        return "mismatched_receipt"
    if order.status == "paid":
        return (
            "already_applied"
            if order.telegram_charge_id == receipt.telegram_payment_charge_id
            else "duplicate_charge"
        )
    if order.status not in {"pending", "failed"}:
        return "invalid_state"
    matching = await db.scalar(
        select(GroupPaymentOrder.id).where(
            GroupPaymentOrder.telegram_charge_id == receipt.telegram_payment_charge_id
        )
    )
    if matching is not None:
        return "already_applied"
    now = datetime.now(UTC)
    subscription = await ensure_subscription(db, order.group_id, now)
    # Protect the free week when someone pays early; no purchased days are lost.
    paid_from = subscription.paid_until
    trial_deadline = as_utc(subscription.trial_ends_at)
    paid_from = as_utc(paid_from) if paid_from is not None else None
    if trial_deadline > now and (paid_from is None or paid_from < trial_deadline):
        paid_from = trial_deadline
    subscription.paid_until = extended_paid_until(
        now=now, paid_until=paid_from, months=order.months
    )
    subscription.renewal_warning_for = None
    subscription.updated_at = now
    order.telegram_charge_id = receipt.telegram_payment_charge_id
    order.status = "paid"
    order.paid_at = now
    await enqueue_once(
        db,
        "sendMessage",
        {
            "chat_id": order.payer_telegram_id,
            "text": (
                "✅ Оплата получена!\n\n"
                f"Доступ всей учебной группы продлён на {order.months} мес.\n"
                f"Действует до: {formatted_date(subscription.paid_until)}.\n\n"
                "Спасибо! Возвращайтесь в StudGroup."
            ),
        },
        f"billing-success:{order.id}",
    )
    return "applied"


async def register_refund(db: AsyncSession, message) -> str:
    """Apply a Telegram-refunded Stars receipt once; never issue a refund request.

    The remaining group entitlement is reduced by the refunded number of days,
    while already consumed time cannot be taken back. Other settled orders stay
    intact and no negative period is possible.
    """
    receipt = message.refunded_payment
    if receipt is None or message.chat.type != "private":
        return "invalid_message"
    order_id = parse_payload(receipt.invoice_payload)
    if order_id is None:
        return "invalid_order"
    order = await db.scalar(
        select(GroupPaymentOrder).where(GroupPaymentOrder.id == order_id).with_for_update()
    )
    if order is None:
        return "unknown_order"
    if (
        receipt.currency != "XTR"
        or receipt.total_amount != order.stars_amount
        or receipt.telegram_payment_charge_id != order.telegram_charge_id
        or message.chat.id != order.payer_telegram_id
    ):
        return "mismatched_refund"
    if order.status == "refunded":
        return "already_applied"
    if order.status != "paid":
        return "invalid_state"
    now = datetime.now(UTC)
    subscription = await ensure_subscription(db, order.group_id, now)
    if subscription.paid_until is not None:
        previous = as_utc(subscription.paid_until)
        end = max(
            as_utc(subscription.trial_ends_at),
            previous - timedelta(days=30 * order.months),
        )
        subscription.paid_until = end
        subscription.renewal_warning_for = None
        subscription.updated_at = now
    order.status = "refunded"
    order.refunded_at = now
    await enqueue_once(
        db,
        "sendMessage",
        {
            "chat_id": order.payer_telegram_id,
            "text": (
                "↩️ Возврат Telegram Stars подтверждён.\n\n"
                "Оплаченный срок доступа всей группы пересчитан. "
                "Если нужна помощь, напишите @studgroup_admin."
            ),
        },
        f"billing-refund:{order.id}",
    )
    return "applied"


def warning_keyboard(settings):
    name = (settings.bot_username or "").lstrip("@")
    return (
        {
            "inline_keyboard": [
                [{"text": "💳 Продлить доступ группы", "url": f"https://t.me/{name}?start=billing"}]
            ]
        }
        if name
        else None
    )


async def tick(engine, settings) -> None:
    """Poll once per 10 minutes; one warning and one expiry notice per group.

    Operational queue dedupe plus persisted timestamps protect against restarts and
    concurrent server instances. Messages are sent only to the active headman.
    """
    from studgroup.billing_manual import ready as bank_ready

    if not (payment_ready(settings) or bank_ready(settings)):
        return
    async with AsyncSession(engine, expire_on_commit=False) as db:
        people = (
            await db.execute(
                select(Group.id, User.telegram_user_id)
                .join(Membership, Membership.group_id == Group.id)
                .join(User, User.id == Membership.user_id)
                .where(
                    Group.status == "active",
                    Group.pilot_authorized.is_(True),
                    Membership.role == "headman",
                    Membership.status == "active",
                )
            )
        ).all()
        now = datetime.now(UTC)
        for group_id, telegram_id in people:
            subscription = await ensure_subscription(db, group_id, now)
            deadline = as_utc(subscription.paid_until or subscription.trial_ends_at)
            is_trial = subscription.paid_until is None
            keyboard = warning_keyboard(settings)
            if now < deadline <= now + timedelta(hours=24):
                if is_trial and subscription.trial_warning_at is None:
                    sent = await enqueue_once(
                        db,
                        "sendMessage",
                        {
                            "chat_id": telegram_id,
                            "text": (
                                "⏳ Бесплатный период StudGroup заканчивается завтра.\n\n"
                                + PRICE_NOTE
                                + "\n\n"
                                + "Продлить доступ может один участник за всю группу.\n"
                                + OPEN_CHECKOUT_TEXT
                            ),
                            **({"reply_markup": keyboard} if keyboard else {}),
                        },
                        f"billing-trial-warning:{group_id}",
                    )
                    if sent:
                        subscription.trial_warning_at = now
                elif not is_trial and (
                    subscription.renewal_warning_for is None
                    or as_utc(subscription.renewal_warning_for) != deadline
                ):
                    sent = await enqueue_once(
                        db,
                        "sendMessage",
                        {
                            "chat_id": telegram_id,
                            "text": (
                                "⏳ Оплаченный доступ всей группы к StudGroup заканчивается завтра.\n\n"
                                "Продление — от 800 ₽ за 30 дней для группы.\n" + OPEN_CHECKOUT_TEXT
                            ),
                            **({"reply_markup": keyboard} if keyboard else {}),
                        },
                        f"billing-renewal-warning:{group_id}:{int(deadline.timestamp())}",
                    )
                    if sent:
                        subscription.renewal_warning_for = deadline
            elif now >= deadline and is_trial and subscription.trial_expired_notice_at is None:
                sent = await enqueue_once(
                    db,
                    "sendMessage",
                    {
                        "chat_id": telegram_id,
                        "text": (
                            "🎓 Бесплатная неделя StudGroup завершилась.\n\n"
                            + PRICE_NOTE
                            + "\n\n"
                            + "Выберите срок — 1, 2, 3 или 4 месяца. "
                            + OPEN_CHECKOUT_TEXT
                        ),
                        **({"reply_markup": keyboard} if keyboard else {}),
                    },
                    f"billing-trial-expired:{group_id}",
                )
                if sent:
                    subscription.trial_expired_notice_at = now
            elif now >= deadline and not is_trial:
                # The unique key makes paid-expiry notices safe to replay without
                # mutating the paid period or sending daily reminders.
                await enqueue_once(
                    db,
                    "sendMessage",
                    {
                        "chat_id": telegram_id,
                        "text": (
                            "⏳ Оплаченный период StudGroup завершился.\n\n"
                            "В течение двух дней доступ всей группы сохраняется. "
                            "Для продолжения выберите 1–4 месяца.\n" + OPEN_CHECKOUT_TEXT
                        ),
                        **({"reply_markup": keyboard} if keyboard else {}),
                    },
                    f"billing-paid-expired:{group_id}:{int(deadline.timestamp())}",
                )
        await db.commit()


async def loop(engine, settings) -> None:
    while True:
        try:
            await tick(engine, settings)
        except Exception as error:  # noqa: BLE001 -- notification failure must never kill API
            LOGGER.warning("billing_notification_iteration_failed kind=%s", type(error).__name__)
        await asyncio.sleep(600)
