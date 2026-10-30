"""PostgreSQL-backed event range/move/cancel reads use the same approved entity."""

import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool
from test_postgres_image_jobs import seed

from studgroup.analysis.event_lifetime import payload_hash, retention_end
from studgroup.analysis.group_events import review_update, snapshot
from studgroup.analysis.image_contracts import ImageEvent
from studgroup.group_event_api import list_group_events
from studgroup.models import Group, GroupEvent

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
        finally:
            async with AsyncSession(engine) as db:
                await db.delete(await db.get(Group, group_id))
                await db.commit()
            await engine.dispose()

    asyncio.run(run())
