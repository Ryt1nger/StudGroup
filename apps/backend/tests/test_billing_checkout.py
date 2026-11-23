"""End-to-end test of group-wide Stars checkout without real Telegram calls."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from test_bot_start import last, tap
from test_headman import deliver, run
from test_ingestion import send

from studgroup import billing
from studgroup.models import BotOutbox, GroupPaymentOrder, GroupSubscription, Membership, RawMessage

pytest_plugins = ["test_schedule_api"]


def enable(client, monkeypatch):
    settings = client.app.state.settings
    settings.billing_enabled = True
    settings.billing_payment_mode = "stars"
    settings.billing_stars_per_month = 100
    settings.billing_enforce_access = False
    settings.telegram_bot_token = "test-token"
    settings.bot_username = "studgroup_test_bot"
    calls = []

    async def telegram(_settings, method, data):
        calls.append((method, data))
        if method == "createInvoiceLink":
            return {"ok": True, "result": "https://t.me/$TEST_STUDGROUP"}
        return {"ok": True, "result": True}

    monkeypatch.setattr(billing, "telegram_request", telegram)
    return calls


def single_order(client):
    async def get(db):
        order = await db.scalar(select(GroupPaymentOrder))
        subscription = await db.get(GroupSubscription, order.group_id)
        return order, subscription

    return run(client, get)


def start_checkout(client):
    deliver(client, "/start", uid=1)
    tap(client, "Доступ группы", uid=1)
    assert "800 ₽" in last(client)["text"]
    tap(client, "Продлить доступ группы", uid=1)
    assert "4 месяца" in last(client)["text"]
    tap(client, "2 месяца", uid=1)
    frame = last(client)
    assert "1 600 ₽" in frame["text"] or "1,600 ₽" in frame["text"]
    assert "⭐ 200" in frame["text"]
    assert any(
        b.get("url") == "https://t.me/$TEST_STUDGROUP"
        for row in frame["reply_markup"]["inline_keyboard"]
        for b in row
    )
    order, subscription = single_order(client)
    return order, subscription


def telegram_checkout(client, order, price=200, query_id=70000):
    return send(
        client,
        {
            "update_id": query_id,
            "pre_checkout_query": {
                "id": f"pc-{query_id}",
                "from": {"id": 1},
                "currency": "XTR",
                "total_amount": price,
                "invoice_payload": billing.invoice_payload(order.id),
            },
        },
    )


def telegram_settlement(client, order, price=200, update_id=70001, charge="test-star-charge"):
    return send(
        client,
        {
            "update_id": update_id,
            "message": {
                "message_id": update_id,
                "date": int(datetime.now(UTC).timestamp()),
                "chat": {"id": 1, "type": "private"},
                "from": {"id": 1},
                "successful_payment": {
                    "currency": "XTR",
                    "total_amount": price,
                    "invoice_payload": billing.invoice_payload(order.id),
                    "telegram_payment_charge_id": charge,
                },
            },
        },
    )


def test_checkout_from_connected_group_settles_once_for_every_member(client, monkeypatch):
    calls = enable(client, monkeypatch)
    order, sub = start_checkout(client)
    assert order.months == 2 and order.amount_rub == 1600
    assert order.stars_amount == 200 and order.currency == "XTR"
    assert sub.paid_until is None
    assert calls[0][0] == "createInvoiceLink"
    assert calls[0][1]["currency"] == "XTR"
    assert "provider_token" not in calls[0][1]
    assert telegram_checkout(client, order).status_code == 200
    assert calls[-1][0] == "answerPreCheckoutQuery" and calls[-1][1]["ok"] is True
    assert telegram_settlement(client, order).status_code == 200
    paid, after = single_order(client)
    assert paid.status == "paid" and paid.telegram_charge_id == "test-star-charge"
    assert billing.as_utc(after.paid_until) > datetime.now(UTC) + timedelta(days=59)
    deadline = after.paid_until
    # Telegram may retry or reissue the same signed receipt. It must never extend twice.
    assert telegram_settlement(client, order, update_id=70002).status_code == 200
    assert single_order(client)[1].paid_until == deadline

    async def count_success(db):
        return await db.scalar(
            select(func.count())
            .select_from(BotOutbox)
            .where(BotOutbox.dedup_key == f"billing-success:{order.id}")
        )

    assert run(client, count_success) == 1


def test_wrong_stars_total_rejected_before_payment_and_cannot_activate(client, monkeypatch):
    calls = enable(client, monkeypatch)
    order, _ = start_checkout(client)
    assert telegram_checkout(client, order, price=199).status_code == 200
    assert calls[-1][1]["ok"] is False
    assert telegram_settlement(client, order, price=199).status_code == 200
    saved, subscription = single_order(client)
    assert saved.status == "pending"
    assert subscription.paid_until is None


def test_telegram_refund_shortens_group_access_only_once(client, monkeypatch):
    enable(client, monkeypatch)
    order, _ = start_checkout(client)
    assert telegram_settlement(client, order).status_code == 200
    _paid, subscription = single_order(client)
    old_deadline = billing.as_utc(subscription.paid_until)

    refund = {
        "update_id": 70090,
        "message": {
            "message_id": 70090,
            "date": int(datetime.now(UTC).timestamp()),
            "chat": {"id": 1, "type": "private"},
            "refunded_payment": {
                "currency": "XTR",
                "total_amount": order.stars_amount,
                "invoice_payload": billing.invoice_payload(order.id),
                "telegram_payment_charge_id": "test-star-charge",
            },
        },
    }
    assert send(client, refund).status_code == 200
    refunded, subscription = single_order(client)
    assert refunded.status == "refunded"
    assert refunded.refunded_at is not None
    assert billing.as_utc(subscription.paid_until) == old_deadline - timedelta(days=60)
    refund["update_id"] += 1
    refund["message"]["message_id"] += 1
    assert send(client, refund).status_code == 200
    assert billing.as_utc(single_order(client)[1].paid_until) == old_deadline - timedelta(days=60)


def test_a_repeat_start_does_not_reset_the_groups_trial(client, monkeypatch):
    enable(client, monkeypatch)
    deliver(client, "/start", uid=1)
    tap(client, "Доступ группы", uid=1)

    async def get_trial(db):
        return await db.scalar(select(GroupSubscription.trial_ends_at))

    before = run(client, get_trial)
    deliver(client, "/start", uid=1)
    tap(client, "Доступ группы", uid=1)
    assert run(client, get_trial) == before


def test_headman_reminder_is_deduplicated(client, monkeypatch):
    enable(client, monkeypatch)

    async def promote_and_seed(db):
        member = await db.scalar(select(Membership))
        member.role = "headman"
        await billing.ensure_subscription(db, member.group_id)
        subscription = await db.get(GroupSubscription, member.group_id)
        subscription.trial_ends_at = datetime.now(UTC) + timedelta(hours=12)

    run(client, promote_and_seed)
    asyncio.run(billing.tick(client.app.state.engine, client.app.state.settings))
    asyncio.run(billing.tick(client.app.state.engine, client.app.state.settings))

    async def count(db):
        return await db.scalar(
            select(func.count())
            .select_from(BotOutbox)
            .where(BotOutbox.dedup_key.like("billing-trial-warning:%"))
        )

    assert run(client, count) == 1


def test_access_enforcement_only_when_explicitly_enabled(client, monkeypatch):
    enable(client, monkeypatch)

    async def expire(db):
        member = await db.scalar(select(Membership))
        subscription = await billing.ensure_subscription(db, member.group_id)
        subscription.trial_ends_at = datetime.now(UTC) - timedelta(days=3)

    run(client, expire)
    settings = client.app.state.settings
    settings.billing_enforce_access = True
    new_message = {
        "update_id": 88881,
        "message": {
            "message_id": 88881,
            "date": int(datetime.now(UTC).timestamp()),
            "chat": {"id": -1001, "type": "supergroup"},
            "from": {"id": 1},
            "text": "ДЗ по математике",
        },
    }
    assert send(client, new_message).status_code == 200

    async def number(db):
        return await db.scalar(select(func.count()).select_from(RawMessage))

    assert run(client, number) == 0
    settings.billing_enforce_access = False
    new_message["update_id"] = 88882
    new_message["message"]["message_id"] = 88882
    assert send(client, new_message).status_code == 200
    assert run(client, number) == 1


def test_invalid_period_or_payload_never_makes_an_order():
    with pytest.raises(ValueError):
        billing.price_quote(uuid.uuid4(), 5)
    assert billing.parse_payload("random") is None
    assert billing.parse_payload("sg-group:not-a-uuid") is None
