"""The approved campaign uses private, version-bound and deduplicated notices."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from studgroup import bot_admin
from studgroup import sdo_student_notices as notices
from studgroup.legal import has_consent, receipt
from studgroup.models import (
    BotOutbox,
    Group,
    RanepaCrawlJob,
    RanepaCrawlObservation,
    User,
    UserProfile,
)

pytest_plugins = ["test_schedule_api"]


def test_student_notice_is_private_deduplicated_and_removed_on_open(client, monkeypatch):
    settings = client.app.state.settings
    settings.telegram_bot_token = "fake-no-network"
    settings.webapp_origin = "https://example.invalid"
    monkeypatch.setattr(notices, "configured", lambda _: True)
    engine = client.app.state.engine

    async def scenario():
        async with AsyncSession(engine) as db:
            group = await db.scalar(select(Group).where(Group.telegram_chat_id == -1001))
            group.status = "active"
            group.academic_group_code = "TEST"
            await db.commit()
        assert await notices.queue_student_notices(engine, settings) == 1
        assert await notices.queue_student_notices(engine, settings) == 0
        calls = []

        async def request(_settings, method, data):
            calls.append((method, data))
            return {"ok": True, "result": {"message_id": 42}}

        await bot_admin.deliver(engine, settings, request=request)
        async with AsyncSession(engine) as db:
            row = await db.scalar(select(BotOutbox).where(BotOutbox.method == "sendMessage"))
            assert row.state == "sent"
            assert json.loads(row.payload)["_sdo_notice"]["message_id"] == 42
            ident = row.id
        assert "_sdo_notice" not in calls[0][1]
        assert calls[0][1]["chat_id"] == 1
        async with AsyncSession(engine) as db:
            assert not await notices.acknowledge_open(db, ident, 2)
            assert await notices.acknowledge_open(db, ident, 1)
            assert await notices.acknowledge_open(db, ident, 1)
            await db.commit()
        await bot_admin.deliver(engine, settings, request=request)
        assert calls[-1] == ("deleteMessage", {"chat_id": 1, "message_id": 42})

    asyncio.run(scenario())


def test_consent_callback_accepts_current_documents_only_for_recipient(client, monkeypatch):
    settings = client.app.state.settings
    settings.telegram_bot_token = "fake-no-network"
    settings.webapp_origin = "https://example.invalid"
    monkeypatch.setattr(notices, "configured", lambda _: True)
    engine = client.app.state.engine

    async def scenario():
        async with AsyncSession(engine) as db:
            group = await db.scalar(select(Group).where(Group.telegram_chat_id == -1001))
            group.status = "active"
            group.academic_group_code = "TEST"
            user = await db.scalar(select(User).where(User.telegram_user_id == 1))
            uid = user.id
            db.add(UserProfile(user_id=uid, sdu_credential_id=uuid4(), updated_at=datetime.now(UTC)))
            await db.commit()
        assert await notices.queue_student_notices(engine, settings) == 1

        async def request(_settings, method, data):
            return {"ok": True, "result": {"message_id": 43}}

        await bot_admin.deliver(engine, settings, request=request)
        async with AsyncSession(engine) as db:
            row = await db.scalar(select(BotOutbox).where(BotOutbox.method == "sendMessage"))
            data = json.loads(row.payload)
            buttons = data["reply_markup"]["inline_keyboard"]
            assert len(buttons) == 2
            callback = SimpleNamespace(id="callback", data=buttons[0][0]["callback_data"])
            await notices.accept_callback(db, settings, callback, 2, 43)
            await db.flush()
            assert not await has_consent(db, uid, "sdo", settings)
            await notices.accept_callback(db, settings, callback, 1, 43)
            await db.commit()
        async with AsyncSession(engine) as db:
            for purpose in notices.PURPOSES:
                assert await has_consent(db, uid, purpose, settings)
            assert await db.scalar(select(BotOutbox).where(BotOutbox.method == "deleteMessage"))

    asyncio.run(scenario())


def test_only_explicit_current_password_rejection_is_notified(client):
    settings = client.app.state.settings
    engine = client.app.state.engine
    now = datetime.now(UTC)

    async def scenario():
        async with AsyncSession(engine, expire_on_commit=False) as db:
            user = await db.scalar(select(User).where(User.telegram_user_id == 1))
            group = await db.scalar(select(Group).where(Group.telegram_chat_id == -1001))
            profile = UserProfile(
                user_id=user.id, sdu_credential_id=uuid4(), updated_at=now - timedelta(days=1)
            )
            db.add(profile)
            for purpose in notices.PURPOSES:
                receipt(
                    db,
                    user.id,
                    purpose,
                    "accept" if purpose == "terms" else "grant",
                    "webapp",
                    settings,
                )
            job = RanepaCrawlJob(
                user_id=user.id,
                group_id=group.id,
                week_start=now.date() - timedelta(days=now.weekday()),
                priority=0,
                state="failed",
                attempts=1,
                created_at=now,
                finished_at=now,
                failure_code="login_failed",
                retry_reason="credentials_required",
            )
            db.add(job)
            await db.flush()
            assert await notices.classify(db, user, settings) is None
            evidence = RanepaCrawlObservation(
                job_id=job.id,
                attempt=1,
                observed_at=now,
                result_code="login_failed",
                elapsed_ms=1,
                events_json="[]",
                failure_detail="login_rejected",
                last_step="lk_form_still_visible",
            )
            db.add(evidence)
            await db.flush()
            assert await notices.classify(db, user, settings) is None
            evidence.last_step = "lk_explicit_rejection"
            await db.flush()
            assert (await notices.classify(db, user, settings))[0] == "password"
            profile.updated_at = now + timedelta(seconds=1)
            await db.flush()
            assert await notices.classify(db, user, settings) is None

    asyncio.run(scenario())
