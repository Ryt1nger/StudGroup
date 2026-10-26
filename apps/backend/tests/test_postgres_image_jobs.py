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

pytestmark = pytest.mark.skipif(not os.environ.get("POSTGRES_TEST_URL"), reason="PostgreSQL not configured")


async def seed(engine):
    now = datetime.now(UTC)
    async with AsyncSession(engine) as db:
        group = Group(telegram_chat_id=-uuid.uuid4().int % 10**14, name="Disposable image jobs", timezone="Europe/Moscow")
        db.add(group)
        await db.flush()
        raw = RawMessage(group_id=group.id, telegram_message_id=1, text="", message_date=now, version_date=now, delete_at=now + timedelta(days=1))
        db.add(raw)
        await db.flush()
        attachment = MessageAttachment(raw_message_id=raw.id, group_id=group.id, source_revision=1, kind="photo", stable_file_id="unique", telegram_file_id="file", created_at=now, delete_at=raw.delete_at)
        db.add(attachment)
        await db.flush()
        values = {"group_id": group.id, "raw_message_id": raw.id, "attachment_id": attachment.id, "source_revision": 1, "context_sha256": "0" * 64, "context_json": "{}", "version": "image-analysis-1", "pass_number": 1, "state": "pending", "attempts": 0, "available_at": now, "created_at": now, "delete_at": raw.delete_at}
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
                job = await first.scalar(select(ImageAnalysisJob).where(ImageAnalysisJob.id == ident).with_for_update(skip_locked=True))
                assert job is not None
                assert await second.scalar(select(ImageAnalysisJob).where(ImageAnalysisJob.id == ident).with_for_update(skip_locked=True)) is None
                await first.rollback()
                assert await second.scalar(select(ImageAnalysisJob).where(ImageAnalysisJob.id == ident).with_for_update(skip_locked=True)) is not None
        finally:
            async with AsyncSession(engine) as db:
                await db.delete(await db.get(Group, group_id))
                await db.commit()
                assert await db.get(ImageAnalysisJob, ident) is None
            await engine.dispose()
    asyncio.run(run())
