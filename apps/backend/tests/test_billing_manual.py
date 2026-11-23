"""Bank-link group checkout: student claim is not proof; owner approval is authoritative."""

import asyncio
import json
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from test_bot_start import last, tap
from test_headman import deliver, run
from test_ingestion import send

from studgroup import billing_manual
from studgroup.models import BotOutbox, GroupBankOrder, GroupSubscription, StartSession

pytest_plugins = ["test_schedule_api"]


def enable(client):
    settings = client.app.state.settings
    settings.billing_enabled = True
    settings.billing_payment_mode = "manual_alfa"
    settings.billing_enforce_access = False
    settings.telegram_bot_token = "9999:test"
    settings.owner_telegram_user_id = 777777
    settings.billing_alfa_url_1m = "https://pay.alfabank.example/800"
    settings.billing_alfa_url_2m = "https://pay.alfabank.example/1600"
    settings.billing_alfa_url_3m = "https://pay.alfabank.example/2400"
    settings.billing_alfa_url_4m = "https://pay.alfabank.example/3200"
    assert billing_manual.ready(settings)
    return settings


def order_state(client):
    async def read(db):
        order = await db.scalar(select(GroupBankOrder))
        subscription = await db.get(GroupSubscription, order.group_id)
        return order, subscription

    return run(client, read)


def create_and_submit(client):
    deliver(client, "/start", uid=1)
    tap(client, "Доступ группы", uid=1)
    tap(client, "Продлить доступ", uid=1)
    tap(client, "2 месяца", uid=1)
    frame = last(client)
    assert "1,600 ₽" in frame["text"]
    assert any(
        button.get("url") == "https://pay.alfabank.example/1600"
        for row in frame["reply_markup"]["inline_keyboard"]
        for button in row
    )
    order, subscription = order_state(client)
    assert order.status == "pending" and order.amount_rub == 1600
    assert order.months == 2 and subscription.paid_until is None
    tap(client, "Я оплатил", uid=1)
    order, subscription = order_state(client)
    assert order.status == "awaiting_review" and subscription.paid_until is None
    assert "Заявка отправлена" in last(client)["text"]
    return order


def owner_click(client, order, action="ok", uid=777777, update_id=99011):
    return send(
        client,
        {
            "update_id": update_id,
            "callback_query": {
                "id": f"review-{update_id}",
                "from": {"id": uid},
                "message": {
                    "message_id": 12345,
                    "date": int(datetime.now(UTC).timestamp()),
                    "chat": {"id": uid, "type": "private"},
                    "text": "Ручная проверка",
                },
                "data": f"mb:{order.id}:{action}",
            },
        },
    )


def test_claim_notifies_owner_and_never_extends_entitlement(client):
    enable(client)
    order = create_and_submit(client)

    async def owner_alerts(db):
        return [
            json.loads(row.payload)
            for row in (
                await db.scalars(
                    select(BotOutbox).where(
                        BotOutbox.method == "sendMessage",
                        BotOutbox.dedup_key == f"billing-bank-review:{order.id}",
                    )
                )
            ).all()
        ]

    alerts = run(client, owner_alerts)
    assert len(alerts) == 1
    assert alerts[0]["chat_id"] == 777777
    assert "1,600 ₽" in alerts[0]["text"]
    assert "Подтвердить получение" in str(alerts[0]["reply_markup"])


def test_only_owner_can_confirm_and_replay_never_extends_twice(client):
    enable(client)
    order = create_and_submit(client)
    assert owner_click(client, order, uid=777778, update_id=99011).status_code == 200
    assert order_state(client)[0].status == "awaiting_review"

    assert owner_click(client, order, update_id=99012).status_code == 200
    paid, subscription = order_state(client)
    assert paid.status == "paid" and paid.confirmed_by == 777777
    deadline = subscription.paid_until
    assert billing_manual.owner_id(client.app.state.settings) == 777777
    assert billing_manual.ready(client.app.state.settings)
    assert billing_manual.SUCCESS_SECONDS == 20
    assert deadline is not None

    assert owner_click(client, order, update_id=99013).status_code == 200
    again, subscription = order_state(client)
    assert again.status == "paid" and subscription.paid_until == deadline
    assert "Оплата подтверждена" in last(client)["text"]


def test_rejection_never_extends_group(client):
    enable(client)
    order = create_and_submit(client)
    assert owner_click(client, order, action="no", update_id=99111).status_code == 200
    rejected, subscription = order_state(client)
    assert rejected.status == "rejected" and subscription.paid_until is None
    assert "не подтверждено" in last(client)["text"]


def test_success_screen_returns_to_home_after_20_second_window(client):
    settings = enable(client)
    order = create_and_submit(client)
    assert owner_click(client, order, update_id=99211).status_code == 200

    async def expire(db):
        saved = await db.get(GroupBankOrder, order.id)
        saved.cleanup_at = datetime.now(UTC) - timedelta(seconds=1)

    run(client, expire)
    asyncio.run(billing_manual.cleanup_due(client.app.state.engine, settings))
    assert "Вы подключены" in last(client)["text"]

    async def after(db):
        saved = await db.get(GroupBankOrder, order.id)
        session = await db.get(StartSession, order.payer_telegram_id)
        count = await db.scalar(
            select(func.count(GroupBankOrder.id)).where(GroupBankOrder.status == "paid")
        )
        return saved.cleanup_done, session.step, count

    assert run(client, after) == (True, "home", 1)
    asyncio.run(billing_manual.cleanup_due(client.app.state.engine, settings))


def test_manual_flow_refuses_missing_or_insecure_links(client):
    settings = enable(client)
    settings.billing_alfa_url_3m = ""
    assert not billing_manual.ready(settings)
    settings.billing_alfa_url_3m = "http://not-secure.example/pay"
    assert not billing_manual.ready(settings)
    settings.billing_alfa_url_3m = "https://pay.alfabank.example/2400"
    assert billing_manual.payment_link(settings, 3) == settings.billing_alfa_url_3m
    assert billing_manual.payment_link(settings, 5) is None
