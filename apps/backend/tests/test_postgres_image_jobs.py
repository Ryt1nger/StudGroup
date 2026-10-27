"""Real PostgreSQL uniqueness, claim isolation and cascade checks."""

import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

from studgroup.models import Group, ImageAnalysisJob, MessageAttachment, RawMessage

pytestmark = pytest.mark.skipif(
    not os.environ.get("POSTGRES_TEST_URL"), reason="PostgreSQL not configured"
)


async def seed(engine):
    now = datetime.now(UTC)
    async with AsyncSession(engine) as db:
        group = Group(
            telegram_chat_id=-uuid.uuid4().int % 10**14,
            name="Disposable image jobs",
            timezone="Europe/Moscow",
        )
        db.add(group)
        await db.flush()
        raw = RawMessage(
            group_id=group.id,
            telegram_message_id=1,
            text="",
            message_date=now,
            version_date=now,
            delete_at=now + timedelta(days=1),
        )
        db.add(raw)
        await db.flush()
        attachment = MessageAttachment(
            raw_message_id=raw.id,
            group_id=group.id,
            source_revision=1,
            kind="photo",
            stable_file_id="unique",
            telegram_file_id="file",
            created_at=now,
            delete_at=raw.delete_at,
        )
        db.add(attachment)
        await db.flush()
        values = {
            "group_id": group.id,
            "raw_message_id": raw.id,
            "attachment_id": attachment.id,
            "source_revision": 1,
            "context_sha256": "0" * 64,
            "context_json": "{}",
            "version": "image-analysis-1",
            "pass_number": 1,
            "state": "pending",
            "attempts": 0,
            "available_at": now,
            "created_at": now,
            "delete_at": raw.delete_at,
        }
        job = ImageAnalysisJob(**values)
        db.add(job)
        await db.flush()
        ident, group_id = job.id, group.id
        await db.commit()
    return ident, group_id, values


def test_image_job_unique_source_pass_and_skip_locked():
    async def run():
        engine = create_async_engine(os.environ["POSTGRES_TEST_URL"], poolclass=NullPool)
        ident, group_id, values = await seed(engine)
        try:
            async with AsyncSession(engine) as db:
                db.add(ImageAnalysisJob(**values))
                with pytest.raises(IntegrityError):
                    await db.commit()
            async with AsyncSession(engine) as first, AsyncSession(engine) as second:
                job = await first.scalar(
                    select(ImageAnalysisJob)
                    .where(ImageAnalysisJob.id == ident)
                    .with_for_update(skip_locked=True)
                )
                assert job is not None
                assert (
                    await second.scalar(
                        select(ImageAnalysisJob)
                        .where(ImageAnalysisJob.id == ident)
                        .with_for_update(skip_locked=True)
                    )
                    is None
                )
                await first.rollback()
                assert (
                    await second.scalar(
                        select(ImageAnalysisJob)
                        .where(ImageAnalysisJob.id == ident)
                        .with_for_update(skip_locked=True)
                    )
                    is not None
                )
        finally:
            async with AsyncSession(engine) as db:
                await db.delete(await db.get(Group, group_id))
                await db.commit()
                assert await db.get(ImageAnalysisJob, ident) is None
            await engine.dispose()

    asyncio.run(run())


def test_concurrent_image_workers_make_only_one_paid_attempt(monkeypatch):
    from decimal import Decimal

    from test_image_jobs import reading
    from test_photo_reading import JPEG, VisionProvider

    from studgroup.analysis import image_jobs
    from studgroup.main import Settings
    from studgroup.models import GroupAnalysisSettings, ImageAnalysisCall

    async def run():
        engine = create_async_engine(os.environ["POSTGRES_TEST_URL"], poolclass=NullPool)
        ident, group_id, _ = await seed(engine)
        now = datetime.now(UTC)
        async with AsyncSession(engine) as db:
            # seed()'s placeholder job is unrelated to the production scheduling fingerprint.
            await db.delete(await db.get(ImageAnalysisJob, ident))
            group = await db.get(Group, group_id)
            group.pilot_authorized = True
            db.add(
                GroupAnalysisSettings(
                    group_id=group_id,
                    analysis_mode="shadow",
                    shadow_daily_budget_usd=Decimal("0.50"),
                    updated_at=now,
                )
            )
            await db.commit()

        async def fake(*args, **kwargs):
            await asyncio.sleep(0.05)
            return JPEG, "image/jpeg"

        monkeypatch.setattr(image_jobs, "download", fake)
        provider = VisionProvider(reading())
        config = Settings(
            _env_file=None,
            image_analysis_enabled=True,
            image_analysis_since=now - timedelta(days=1),
            image_analysis_groups=[str(group_id)],
        )
        try:
            results = await asyncio.gather(
                *[image_jobs.process_images_next(engine, config, provider, now) for _ in range(2)]
            )
            assert sorted(results) == ["idle", "ready"]
            assert len(provider.vision) == 1
            async with AsyncSession(engine) as db:
                calls = (
                    await db.scalars(
                        select(ImageAnalysisCall).where(ImageAnalysisCall.group_id == group_id)
                    )
                ).all()
                assert len(calls) == 1 and calls[0].reserved_usd == 0
        finally:
            async with AsyncSession(engine) as db:
                await db.delete(await db.get(Group, group_id))
                await db.commit()
            await engine.dispose()

    asyncio.run(run())


def test_schedule_confirmation_is_atomic_and_idempotent_in_postgres(monkeypatch):
    import json
    from decimal import Decimal

    from test_image_jobs import reading
    from test_photo_reading import JPEG, VisionProvider

    from studgroup.analysis import image_jobs
    from studgroup.analysis.image_schedule import ScheduleConfirmation, confirm
    from studgroup.main import Settings
    from studgroup.models import (
        GroupAnalysisSettings,
        ImageScheduleSuggestion,
        Membership,
        ScheduleChange,
        User,
    )

    async def run():
        engine = create_async_engine(os.environ["POSTGRES_TEST_URL"], poolclass=NullPool)
        ident, group_id, _ = await seed(engine)
        now = datetime.now(UTC)
        async with AsyncSession(engine, expire_on_commit=False) as db:
            await db.delete(await db.get(ImageAnalysisJob, ident))
            group = await db.get(Group, group_id)
            group.pilot_authorized = True
            user = User(
                telegram_user_id=uuid.uuid4().int % 10**14, display_name="Synthetic reviewer"
            )
            db.add(user)
            await db.flush()
            member = Membership(user_id=user.id, group_id=group_id, role="headman", status="active")
            db.add(member)
            db.add(
                GroupAnalysisSettings(
                    group_id=group_id,
                    analysis_mode="shadow",
                    shadow_daily_budget_usd=Decimal("0.50"),
                    updated_at=now,
                )
            )
            await db.commit()

        async def fake(*args, **kwargs):
            return JPEG, "image/jpeg"

        monkeypatch.setattr(image_jobs, "download", fake)
        day = (now + timedelta(days=7)).date()
        payload = json.loads(reading()) | {
            "semantic_types": ["schedule"],
            "schedule": [
                {
                    "weekday": day.weekday(),
                    "starts": "13:00",
                    "ends": "14:30",
                    "subject": "Алгебра",
                    "valid_from": day.isoformat(),
                    "valid_until": day.isoformat(),
                }
            ],
        }
        config = Settings(
            _env_file=None,
            image_analysis_enabled=True,
            image_analysis_since=now - timedelta(days=1),
            image_analysis_groups=[str(group_id)],
        )
        try:
            provider = VisionProvider(json.dumps(payload))
            assert await image_jobs.process_images_next(engine, config, provider, now) == "ready"
            assert await image_jobs.process_images_next(engine, config, provider, now) == "ready"
            async with AsyncSession(engine) as db:
                suggestion_id = await db.scalar(
                    select(ImageScheduleSuggestion.id).where(
                        ImageScheduleSuggestion.group_id == group_id
                    )
                )

            async def apply():
                async with AsyncSession(engine) as db:
                    result = await confirm(
                        db,
                        group_id,
                        user,
                        member,
                        suggestion_id,
                        ScheduleConfirmation(expected_revision=1, expected_schedule_version=0),
                        now,
                    )
                    await db.commit()
                    return result

            assert await asyncio.gather(apply(), apply()) == [1, 1]
            async with AsyncSession(engine) as db:
                changes = (
                    await db.scalars(
                        select(ScheduleChange).where(ScheduleChange.group_id == group_id)
                    )
                ).all()
                assert len(changes) == 1
        finally:
            async with AsyncSession(engine) as db:
                await db.delete(await db.get(Group, group_id))
                await db.delete(await db.get(User, user.id))
                await db.commit()
            await engine.dispose()

    asyncio.run(run())


def test_event_lifecycle_deduplicates_concurrent_findings_and_retention_cascades():
    from sqlalchemy import func

    from studgroup.analysis.group_events import capture_event, purge_events
    from studgroup.analysis.image_contracts import ImageEvent
    from studgroup.models import GroupEvent, GroupEventRevision, GroupEventSource

    async def run():
        engine = create_async_engine(os.environ["POSTGRES_TEST_URL"], poolclass=NullPool)
        _ident, group_id, values = await seed(engine)
        now = datetime.now(UTC)
        incoming = ImageEvent(
            title="Synthetic lecture", starts_at=now + timedelta(days=1), audience="group"
        )

        async def add():
            async with AsyncSession(engine) as db:
                raw = await db.get(RawMessage, values["raw_message_id"])
                await capture_event(db, group_id, raw, incoming, now)
                await db.commit()

        try:
            await asyncio.gather(add(), add())
            async with AsyncSession(engine) as db:
                rows = (
                    await db.scalars(select(GroupEvent).where(GroupEvent.group_id == group_id))
                ).all()
                assert len(rows) == 1 and rows[0].revision == 1
                event_id = rows[0].id
                assert (
                    await db.scalar(
                        select(func.count())
                        .select_from(GroupEventSource)
                        .where(GroupEventSource.event_id == event_id)
                    )
                    == 1
                )
                raw = await db.get(RawMessage, values["raw_message_id"])
                updated = incoming.model_copy(
                    update={
                        "action": "update",
                        "existing_event_id": event_id,
                        "expected_revision": 1,
                        "place": "New hall",
                    }
                )
                await capture_event(db, group_id, raw, updated, now)
                await db.commit()
                assert (await db.get(GroupEvent, event_id)).revision == 2
                raw = await db.get(RawMessage, values["raw_message_id"])
                await capture_event(
                    db,
                    group_id,
                    raw,
                    updated.model_copy(update={"action": "cancel", "expected_revision": 2}),
                    now,
                )
                await db.commit()
                assert (await db.get(GroupEvent, event_id)).state == "cancelled"
                assert (
                    await db.scalar(
                        select(func.count())
                        .select_from(GroupEventRevision)
                        .where(GroupEventRevision.event_id == event_id)
                    )
                    == 3
                )
                await purge_events(db, now + timedelta(days=2))
                await db.commit()
                assert (
                    await db.scalar(
                        select(func.count())
                        .select_from(GroupEventSource)
                        .where(GroupEventSource.event_id == event_id)
                    )
                    == 0
                )
                assert (
                    await db.scalar(
                        select(func.count())
                        .select_from(GroupEventRevision)
                        .where(GroupEventRevision.event_id == event_id)
                    )
                    == 0
                )
        finally:
            async with AsyncSession(engine) as db:
                await db.delete(await db.get(Group, group_id))
                await db.commit()
            await engine.dispose()

    asyncio.run(run())


def test_approved_event_is_independent_of_expired_and_deleted_private_sources():
    from studgroup.analysis.group_events import capture_event, purge_events, review_update
    from studgroup.analysis.image_contracts import ImageEvent
    from studgroup.models import GroupEvent, GroupEventSource, User

    async def run():
        engine = create_async_engine(os.environ["POSTGRES_TEST_URL"], poolclass=NullPool)
        _ident, group_id, values = await seed(engine)
        now = datetime.now(UTC)
        try:
            async with AsyncSession(engine, expire_on_commit=False) as db:
                raw = await db.get(RawMessage, values["raw_message_id"])
                user = User(telegram_user_id=uuid.uuid4().int % 10**14, display_name="Event editor")
                db.add(user)
                await db.flush()
                user_id = user.id
                incoming = ImageEvent.model_validate_json(
                    '{"title":"Synthetic future event","starts_on":"2026-12-20","audience":"group"}'
                )
                await capture_event(db, group_id, raw, incoming, now)
                await db.flush()
                event = await db.scalar(select(GroupEvent).where(GroupEvent.group_id == group_id))
                event_id = event.id
                await review_update(
                    db, event, 1, incoming.model_dump(mode="json"), "active", user.id, now
                )
                await db.commit()
                assert event.delete_at == datetime(2026, 12, 27, 21, tzinfo=UTC)
                raw.delete_at = now - timedelta(minutes=1)
                await db.flush()
                assert await purge_events(db, now) == 0
                await db.delete(raw)
                await db.commit()
            async with AsyncSession(engine) as db:
                event = await db.get(GroupEvent, event_id)
                assert event is not None and event.state == "active"
                assert event.approved_payload_sha256 and event.published_at
                assert not (
                    await db.scalars(
                        select(GroupEventSource).where(GroupEventSource.event_id == event_id)
                    )
                ).all()
                assert await purge_events(db, datetime(2026, 12, 27, 20, 59, 59, tzinfo=UTC)) == 0
                assert await purge_events(db, datetime(2026, 12, 27, 21, tzinfo=UTC)) == 1
                await db.commit()
        finally:
            async with AsyncSession(engine) as db:
                await db.delete(await db.get(Group, group_id))
                if "user_id" in locals():
                    await db.delete(await db.get(User, user_id))
                await db.commit()
            await engine.dispose()

    asyncio.run(run())
