"""Scheduled November 30 payment announcement is private, one-time, and excludes BI 1.2."""

import asyncio
import json
import uuid
from datetime import timedelta

from sqlalchemy import select
from test_headman import run

from studgroup import billing_campaign
from studgroup.models import BotOutbox, Group, Membership, User

pytest_plugins = ["test_schedule_api"]

ON_DATE = billing_campaign.NOTICE_AT + timedelta(minutes=1)


def configure(client):
    s = client.app.state.settings
    s.billing_enabled = True
    s.billing_payment_mode = "manual_alfa"
    s.billing_announcement_enabled = True
    s.owner_telegram_user_id = 777
    s.telegram_bot_token = "999:test"
    s.bot_username = "studgroup_rf_bot"
    for n in (1, 2, 3, 4):
        setattr(s, f"billing_alfa_url_{n}m", f"https://pay.example.test/amount/{n * 800}")
    return s


def messages(client):
    async def read(db):
        rows = (
            await db.scalars(
                select(BotOutbox).where(BotOutbox.dedup_key.like("billing-20261130:%"))
            )
        ).all()
        return [json.loads(row.payload) for row in rows]

    return run(client, read)


def test_one_time_notice_excludes_bi12_members_even_when_in_other_groups(client):
    configure(client)

    async def seed(db):
        current_group = await db.scalar(select(Group).where(Group.pilot_authorized.is_(True)))
        excluded = Group(
            id=billing_campaign.EXCLUDED_GROUP_ID,
            telegram_chat_id=-1009001,
            name="БИ 1.2",
            timezone="Europe/Moscow",
            pilot_authorized=True,
        )
        db.add(excluded)
        for uid in (2, 3, 4):
            db.add(User(id=uuid.uuid4(), telegram_user_id=uid, display_name=f"Student {uid}"))
        await db.flush()
        users = (await db.scalars(select(User).where(User.telegram_user_id.in_([2, 3, 4])))).all()
        by_telegram = {user.telegram_user_id: user for user in users}
        for uid, group_id in (
            (2, current_group.id),
            (3, excluded.id),
            (4, current_group.id),
            (4, excluded.id),
        ):
            db.add(
                Membership(
                    id=uuid.uuid4(),
                    user_id=by_telegram[uid].id,
                    group_id=group_id,
                    role="student",
                    status="active",
                )
            )

    run(client, seed)
    worker = billing_campaign.queue_billing_announcement
    assert asyncio.run(worker(client.app.state.engine, client.app.state.settings, now=ON_DATE)) == 2
    assert {m["chat_id"] for m in messages(client)} == {1, 2}
    for notification in messages(client):
        assert notification["reply_markup"]["inline_keyboard"][0][0]["url"] == (
            "https://t.me/studgroup_rf_bot?start=billing"
        )
    assert asyncio.run(worker(client.app.state.engine, client.app.state.settings, now=ON_DATE)) == 0
    assert len(messages(client)) == 2


def test_no_early_late_or_unconfigured_billing_notice(client):
    settings = configure(client)
    worker = billing_campaign.queue_billing_announcement
    engine = client.app.state.engine
    assert asyncio.run(worker(engine, settings, now=ON_DATE - timedelta(days=1))) == 0
    assert asyncio.run(worker(engine, settings, now=ON_DATE + timedelta(days=2))) == 0
    settings.billing_alfa_url_4m = ""
    assert asyncio.run(worker(engine, settings, now=ON_DATE)) == 0
    settings.billing_alfa_url_4m = "https://pay.example.test/amount/3200"
    settings.bot_username = ""
    assert asyncio.run(worker(engine, settings, now=ON_DATE)) == 0
    settings.bot_username = "studgroup_rf_bot"
    settings.billing_announcement_enabled = False
    assert asyncio.run(worker(engine, settings, now=ON_DATE)) == 0
    assert messages(client) == []
