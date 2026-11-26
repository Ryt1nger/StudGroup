"""PostgreSQL-backed event range/move/cancel reads use the same approved entity."""

import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool
from test_postgres_image_jobs import seed

from studgroup.analysis.event_lifetime import payload_hash, retention_end
from studgroup.analysis.group_events import capture_event, review_update, snapshot
from studgroup.analysis.image_contracts import ImageEvent
from studgroup.group_event_api import EventUpdate, list_group_events, update_review_event
from studgroup.models import Group, GroupEvent, GroupEventSource, RawMessage
from studgroup.staff_api import Staff

pytestmark = pytest.mark.skipif(
    not os.environ.get("POSTGRES_TEST_URL"), reason="PostgreSQL not configured"
)


def test_postgres_event_move_and_cancel_are_atomic_calendar_changes():
    async def run():
        engine = create_async_engine(os.environ["POSTGRES_TEST_URL"], poolclass=NullPool)
        _, group_id, _ = await seed(engine)
        try:
            async with AsyncSession(engine, expire_on_commit=False) as db:
                group = await db.get(Group, group_id)
                now = datetime.now(UTC)
                first = (now + timedelta(days=5)).date()
                later = first + timedelta(days=2)
                payload = ImageEvent(title="Открытая лекция", starts_on=first, audience="group")
                finish, expires = retention_end(payload, group.timezone)
                event = GroupEvent(
                    group_id=group_id,
                    fingerprint=uuid.uuid4().hex,
                    title_key="лекция",
                    payload_json=payload.model_dump_json(),
                    state="active",
                    manual_lock=True,
                    approved_payload_sha256=payload_hash(payload.model_dump_json()),
                    published_at=now,
                    event_end_date=finish,
                    created_at=now,
                    updated_at=now,
                    delete_at=expires,
                )
                db.add(event)
                await db.flush()
                snapshot(db, event, "manual", now)
                await db.commit()
                request = SimpleNamespace(
                    app=SimpleNamespace(
                        state=SimpleNamespace(
                            settings=SimpleNamespace(
                                image_event_publication_enabled=True,
                                image_analysis_groups=[str(group_id)],
                            )
                        )
                    )
                )

                async def calendar(day, status="active"):
                    result = await list_group_events(
                        request, db, (group, None), day, day, status, False
                    )
                    return result["events"]

                assert len(await calendar(first)) == 1
                moved = payload.model_copy(update={"starts_on": later}).model_dump(mode="json")
                await review_update(db, event, event.revision, moved, "active", None, now)
                await db.commit()
                assert await calendar(first) == []
                assert (await calendar(later))[0]["id"] == event.id
                assert (await calendar(later))[0]["rescheduled"]
                await review_update(db, event, event.revision, moved, "cancelled", None, now)
                await db.commit()
                assert await calendar(later) == []
                assert (await calendar(later, "cancelled"))[0]["id"] == event.id
                await review_update(db, event, event.revision, {}, "deleted", None, now)
                await db.commit()
                assert event.state == "deleted"
                assert await calendar(later, "all") == []
                await db.refresh(event)
                assert event.approved_payload_sha256 is None
                assert event.manual_lock
        finally:
            async with AsyncSession(engine) as db:
                await db.delete(await db.get(Group, group_id))
                await db.commit()
            await engine.dispose()

    asyncio.run(run())


def test_postgres_delete_published_text_event_without_date_and_block_replay():
    async def run():
        engine = create_async_engine(os.environ["POSTGRES_TEST_URL"], poolclass=NullPool)
        _, group_id, _ = await seed(engine)
        try:
            async with AsyncSession(engine, expire_on_commit=False) as db:
                group = await db.get(Group, group_id)
                group.status = "active"
                group.pilot_authorized = True
                raw = await db.scalar(select(RawMessage).where(RawMessage.group_id == group_id))
                raw.text = "Приглашение на встречу без указанной даты"
                now = datetime.now(UTC)
                payload = ImageEvent(
                    title="Встреча для всех желающих",
                    description="Тестовое приглашение",
                    audience="group",
                )
                assert await capture_event(db, group_id, raw, payload, now) == "created"
                await db.commit()
                event = await db.scalar(select(GroupEvent).where(GroupEvent.group_id == group_id))
                assert event.state == "active"
                assert event.event_end_date is None
                settings = SimpleNamespace(
                    owner_funded_pilot=True,
                    image_analysis_groups=[],
                    image_event_review_enabled=False,
                    image_schedule_review_enabled=False,
                )
                request = SimpleNamespace(
                    app=SimpleNamespace(state=SimpleNamespace(settings=settings))
                )
                context = Staff(group, SimpleNamespace(id=None), SimpleNamespace(role="deputy"))
                result = await update_review_event(
                    event.id,
                    EventUpdate(
                        expected_revision=event.revision,
                        payload={"title": payload.title},
                        state="deleted",
                    ),
                    request,
                    context,
                    db,
                )
                assert result["state"] == "deleted"
                listing = await list_group_events(request, db, (group, None), status="all")
                assert listing["events"] == []
                source = await db.scalar(
                    select(GroupEventSource).where(GroupEventSource.event_id == event.id)
                )
                assert source.raw_message_id == raw.id
                assert "Тестовое приглашение" not in source.incoming_json
                assert await capture_event(db, group_id, raw, payload, now) == "held"
                await db.commit()
        finally:
            async with AsyncSession(engine) as db:
                await db.delete(await db.get(Group, group_id))
                await db.commit()
            await engine.dispose()

    asyncio.run(run())
