"""Measured transport-call counts; local provider only, no claimed dollar savings."""

import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from test_image_jobs import cfg, download_ok, reading, stored, tick
from test_package_worker import make_new
from test_photo_reading import VisionProvider, photo_message

from studgroup.models import ImageAnalysisCall, ImageAnalysisJob, MessageAttachment, RawMessage

pytest_plugins = ["test_schedule_api"]


def prepare(client, monkeypatch):
    download_ok(monkeypatch)
    photo_message(client, mid=31)
    make_new(client)
    config = cfg(client, image_model_revision="pinned-test-v1")
    provider = VisionProvider(reading())
    assert tick(client, provider, config) == "ready"
    assert tick(client, provider, config) == "ready"
    return provider, config


def test_identical_bytes_reuse_content_but_keep_independent_context(client, monkeypatch):
    provider, config = prepare(client, monkeypatch)
    photo_message(client, mid=32)
    assert tick(client, provider, config) == "ready"  # bytes cache; no model request
    assert tick(client, provider, config) == "ready"  # B for the new source
    assert len(provider.vision) == 3
    jobs = stored(client, ImageAnalysisJob)
    assert sum(job.reused_from_job_id is not None for job in jobs) == 1
    calls = stored(client, ImageAnalysisCall)
    cached = next(row for row in calls if row.state == "reused")
    assert cached.charged_usd == 0 and cached.prompt_tokens == 0
    assert tick(client, provider, config) == "idle"
    assert len(provider.vision) == 3


def test_same_telegram_file_reuses_before_reserving_more_money(client, monkeypatch):
    provider, config = prepare(client, monkeypatch)
    photo_message(client, mid=32)

    async def same():
        async with AsyncSession(client.app.state.engine) as db:
            rows = (await db.scalars(select(MessageAttachment))).all()
            for row in rows:
                row.stable_file_id = "same-telegram-file"
            await db.commit()

    asyncio.run(same())
    config = config.model_copy(update={"image_pass_reservation_usd": 1.0})
    assert tick(client, provider, config) == "reused"
    assert len(provider.vision) == 2
    assert len(stored(client, ImageAnalysisCall)) == 2
    assert any(job.reused_from_job_id for job in stored(client, ImageAnalysisJob))


def test_model_revision_change_invalidates_both_passes(client, monkeypatch):
    provider, config = prepare(client, monkeypatch)
    config = config.model_copy(update={"image_model_revision": "pinned-test-v2"})
    assert tick(client, provider, config) == "ready"
    assert tick(client, provider, config) == "ready"
    assert len(provider.vision) == 4
    assert len({job.model_key for job in stored(client, ImageAnalysisJob)}) == 2


def test_expired_or_retracted_source_is_never_a_cache_hit(client, monkeypatch):
    provider, config = prepare(client, monkeypatch)

    async def expire():
        async with AsyncSession(client.app.state.engine) as db:
            source = await db.scalar(select(RawMessage).where(RawMessage.telegram_message_id == 31))
            source.delete_at = datetime.now(UTC) - timedelta(seconds=1)
            await db.commit()

    asyncio.run(expire())
    photo_message(client, mid=32)
    assert tick(client, provider, config) == "ready"
    assert len(provider.vision) == 3
    assert all(job.reused_from_job_id is None for job in stored(client, ImageAnalysisJob))


def test_caption_edit_reuses_unchanged_pixels_but_rechecks_context(client, monkeypatch):
    provider, config = prepare(client, monkeypatch)

    async def edit():
        async with AsyncSession(client.app.state.engine) as db:
            raw = await db.scalar(select(RawMessage).where(RawMessage.telegram_message_id == 31))
            raw.revision += 1
            raw.text = "Доделать дома"
            await db.commit()

    asyncio.run(edit())
    assert tick(client, provider, config) == "reused"
    assert tick(client, provider, config) == "ready"
    assert len(provider.vision) == 3


def test_cache_never_crosses_group_boundary(client, monkeypatch):
    from decimal import Decimal

    from studgroup.analysis.routing import configure_group_mode
    from studgroup.models import Group

    provider, config = prepare(client, monkeypatch)
    photo_message(client, mid=32)

    async def move():
        async with AsyncSession(client.app.state.engine) as db:
            other = await db.scalar(select(Group).where(Group.telegram_chat_id == -1002))
            other.pilot_authorized = True
            other.bot_added_at = datetime.now(UTC) - timedelta(days=25)
            raw = await db.scalar(select(RawMessage).where(RawMessage.telegram_message_id == 32))
            attachment = await db.scalar(
                select(MessageAttachment).where(MessageAttachment.raw_message_id == raw.id)
            )
            raw.group_id = attachment.group_id = other.id
            await configure_group_mode(
                db, other.id, "shadow", datetime.now(UTC), shadow_daily_budget_usd=Decimal(".50")
            )
            ident = str(other.id)
            await db.commit()
            return ident

    other_id = asyncio.run(move())
    config = config.model_copy(update={"image_analysis_groups": [other_id]})
    assert tick(client, provider, config) == "ready"
    assert len(provider.vision) == 3
    assert all(job.reused_from_job_id is None for job in stored(client, ImageAnalysisJob))


def test_return_to_successful_exact_context_reuses_snapshot(client, monkeypatch):
    provider, config = prepare(client, monkeypatch)

    async def caption(value):
        async with AsyncSession(client.app.state.engine) as db:
            # Another retained message changes context, not the image source revision.
            other = await db.scalar(select(RawMessage).where(RawMessage.telegram_message_id == 32))
            other.text = value
            for attachment in (
                await db.scalars(
                    select(MessageAttachment).where(MessageAttachment.raw_message_id == other.id)
                )
            ).all():
                await db.delete(attachment)  # a text clarification, not another image source
            await db.commit()

    photo_message(client, mid=32)
    asyncio.run(caption("Сделать только упражнения 3 и 4"))
    # Process the new source and the changed context of the original.
    for _ in range(4):
        if tick(client, provider, config) == "idle":
            break
    calls = len(provider.vision)
    asyncio.run(caption(""))
    assert tick(client, provider, config) == "idle"
    assert len(provider.vision) == calls
    jobs = stored(client, ImageAnalysisJob)
    originals = [job for job in jobs if job.pass_number == 2 and job.state == "ready"]
    assert len(originals) == 1
