"""Offline image queue acceptance tests; no network or paid provider."""

import asyncio
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from test_package_worker import BASE, make_new, settings
from test_photo_reading import JPEG, VisionProvider, photo_message

from studgroup.analysis import image_jobs
from studgroup.models import (
    Group,
    ImageAnalysisCall,
    ImageAnalysisJob,
    MessageAttachment,
    RawMessage,
)

pytest_plugins = ["test_schedule_api"]


def reading():
    return '{"summary":"Лист", "extracted_text":"Алгебра упражнения 1–8", "complete":true}'


def cfg(client, **patch):
    async def ident():
        async with AsyncSession(client.app.state.engine) as db:
            return str(await db.scalar(select(Group.id).where(Group.telegram_chat_id == -1001)))

    return settings(
        image_analysis_enabled=True,
        image_analysis_since=BASE - timedelta(days=1),
        image_analysis_groups=[asyncio.run(ident())],
        **patch,
    )


def tick(client, provider, config, now=None):
    return asyncio.run(
        image_jobs.process_images_next(client.app.state.engine, config, provider, now=now)
    )


def stored(client, model):
    async def rows():
        async with AsyncSession(client.app.state.engine) as db:
            return (await db.scalars(select(model).order_by(model.created_at, model.id))).all()

    return asyncio.run(rows())


def download_ok(monkeypatch):
    async def fake(*args, **kwargs):
        return JPEG, "image/jpeg"

    monkeypatch.setattr(image_jobs, "download", fake)


@pytest.mark.parametrize("total", [1, 6, 7, 12])
@pytest.mark.parametrize("document", [False, True])
def test_all_images_receive_attempts_independent_of_package_limit(
    client, monkeypatch, total, document
):
    download_ok(monkeypatch)
    for index in range(total):
        photo_message(client, mid=31 + index)
    if document:

        async def docs():
            async with AsyncSession(client.app.state.engine) as db:
                for row in (await db.scalars(select(MessageAttachment))).all():
                    row.kind, row.mime_type, row.media_group_id = "document", "image/png", "album"
                await db.commit()

        asyncio.run(docs())
    make_new(client)
    config = cfg(client)
    provider = VisionProvider(reading(), propose=False)
    for _ in range(total * 2):
        assert tick(client, provider, config) == "ready"
    assert tick(client, provider, config) == "idle"
    assert len(provider.vision) == total * 2
    assert len(stored(client, ImageAnalysisJob)) == total * 2
    assert all(j.state == "ready" for j in stored(client, ImageAnalysisJob))


def test_zero_budget_never_calls_vision(client, monkeypatch):
    download_ok(monkeypatch)
    photo_message(client)
    make_new(client, budget="0")
    provider = VisionProvider(reading())
    assert tick(client, provider, cfg(client)) == "idle"
    assert provider.vision == []
    assert stored(client, ImageAnalysisJob)[0].state == "budget_deferred"
    assert stored(client, ImageAnalysisCall) == []


def test_telegram_retry_is_bounded_and_releases_reserve(client, monkeypatch):
    async def fail(*args, **kwargs):
        raise image_jobs.DownloadError("telegram_unavailable", True)

    monkeypatch.setattr(image_jobs, "download", fail)
    photo_message(client)
    make_new(client)
    provider = VisionProvider(reading())
    config = cfg(client)
    now = datetime.now(UTC)
    assert tick(client, provider, config, now) == "retry"
    assert tick(client, provider, config, now) == "idle"
    assert tick(client, provider, config, now + timedelta(minutes=1)) == "retry"
    assert tick(client, provider, config, now + timedelta(minutes=2)) == "failed"
    job = stored(client, ImageAnalysisJob)[0]
    assert job.attempts == 3 and job.error == "telegram_unavailable"
    assert provider.vision == []
    assert sum(c.charged_usd + c.reserved_usd for c in stored(client, ImageAnalysisCall)) == 0


def test_edit_invalidates_old_job_without_mutating_its_result(client, monkeypatch):
    download_ok(monkeypatch)
    photo_message(client)
    make_new(client)
    provider = VisionProvider(reading())
    config = cfg(client)
    assert tick(client, provider, config) == "ready"
    old = stored(client, ImageAnalysisJob)[0]

    async def edit():
        async with AsyncSession(client.app.state.engine) as db:
            raw = await db.scalar(select(RawMessage))
            raw.revision += 1
            raw.text = "Доделать дома"
            await db.commit()

    asyncio.run(edit())
    assert tick(client, provider, config) == "ready"
    jobs = stored(client, ImageAnalysisJob)
    previous = next(j for j in jobs if j.id == old.id)
    assert previous.state == "stale" and previous.result_json == old.result_json
    assert {j.source_revision for j in jobs} == {1, 2}


def test_completed_image_is_not_paid_again_after_restart(client, monkeypatch):
    download_ok(monkeypatch)
    photo_message(client)
    make_new(client)
    provider = VisionProvider(reading())
    config = cfg(client)
    assert tick(client, provider, config) == "ready"
    assert tick(client, provider, config) == "ready"
    assert tick(client, provider, config) == "idle"
    assert len(provider.vision) == 2
    calls = stored(client, ImageAnalysisCall)
    assert len(calls) == 2 and calls[0].charged_usd == Decimal("0.00034200")


def test_download_rejects_path_escape_and_retries_telegram_outage():
    import httpx

    async def run():
        def escape(request):
            return httpx.Response(200, json={"result": {"file_path": "../secret"}})

        with pytest.raises(image_jobs.DownloadError) as captured:
            await image_jobs.download("fake", "fake", transport=httpx.MockTransport(escape))
        assert not captured.value.retryable and captured.value.code == "telegram_invalid_path"

        def outage(request):
            return httpx.Response(503)

        with pytest.raises(image_jobs.DownloadError) as captured:
            await image_jobs.download("fake", "fake", transport=httpx.MockTransport(outage))
        assert captured.value.retryable

    asyncio.run(run())


def test_pass_b_retry_reuses_a_and_keeps_every_charge(client, monkeypatch):
    from studgroup.ai import ProviderFailure, TokenUsage

    download_ok(monkeypatch)
    photo_message(client)
    make_new(client)
    config = cfg(client)
    provider = VisionProvider(reading())
    now = datetime.now(UTC)
    assert tick(client, provider, config, now) == "ready"
    provider.answer = ProviderFailure(
        "rate_limited", True, usage=TokenUsage(prompt_tokens=3, completion_tokens=0)
    )
    assert tick(client, provider, config, now) == "retry"
    provider.answer = reading()
    assert tick(client, provider, config, now + timedelta(minutes=1)) == "ready"
    jobs = stored(client, ImageAnalysisJob)
    assert next(j for j in jobs if j.pass_number == 1).attempts == 1
    assert next(j for j in jobs if j.pass_number == 2).attempts == 2
    calls = stored(client, ImageAnalysisCall)
    assert len(calls) == 3
    assert sum(c.charged_usd for c in calls) == Decimal("0.00068490")
    assert all(c.reserved_usd == 0 for c in calls)


def test_expired_paid_lease_never_repeats_ambiguous_purchase(client, monkeypatch):
    download_ok(monkeypatch)
    photo_message(client)
    make_new(client)
    config = cfg(client)
    provider = VisionProvider(reading())
    now = datetime.now(UTC)

    class Crash(VisionProvider):
        async def complete_vision(self, **kwargs):
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        tick(client, Crash(reading()), config, now)
    assert tick(client, provider, config, now + timedelta(minutes=4)) == "idle"
    assert provider.vision == []
    assert stored(client, ImageAnalysisJob)[0].error == "ambiguous_paid_attempt"
    call = stored(client, ImageAnalysisCall)[0]
    assert call.state == "unknown" and call.charged_usd == Decimal("0.02")
    assert call.reserved_usd == 0


@pytest.mark.parametrize(
    "patch,code",
    [
        ({"image_hourly_budget_usd": 0}, "image_hourly_budget"),
        ({"image_total_budget_usd": 0}, "image_budget_exhausted"),
        ({"image_package_budget_usd": 0}, "image_package_budget_exhausted"),
    ],
)
def test_image_limits_prevent_paid_calls(client, monkeypatch, patch, code):
    download_ok(monkeypatch)
    photo_message(client)
    make_new(client)
    provider = VisionProvider(reading())
    assert tick(client, provider, cfg(client, **patch)) == "idle"
    assert provider.vision == []
    assert stored(client, ImageAnalysisJob)[0].error == code


def test_edit_during_paid_call_keeps_charge_but_rejects_result(client, monkeypatch):
    download_ok(monkeypatch)
    photo_message(client)
    make_new(client)
    config = cfg(client)

    class Edit(VisionProvider):
        async def complete_vision(self, **kwargs):
            async with AsyncSession(client.app.state.engine) as db:
                raw = await db.scalar(select(RawMessage))
                raw.revision += 1
                await db.commit()
            return await super().complete_vision(**kwargs)

    assert tick(client, Edit(reading()), config) == "stale"
    assert stored(client, ImageAnalysisJob)[0].result_json is None
    assert stored(client, ImageAnalysisCall)[0].charged_usd == Decimal("0.00034200")


def test_no_explicit_start_means_no_history_processing(client, monkeypatch):
    download_ok(monkeypatch)
    photo_message(client)
    make_new(client)
    config = cfg(client).model_copy(update={"image_analysis_since": None})
    provider = VisionProvider(reading())
    assert tick(client, provider, config) == "disabled"
    assert provider.vision == [] and stored(client, ImageAnalysisJob) == []


def test_visual_context_identifies_source_album_and_explicit_reply(client):
    photo_message(client, mid=31)
    photo_message(client, mid=32, caption="Всё это сделать дома")

    async def run():
        async with AsyncSession(client.app.state.engine) as db:
            for row in (await db.scalars(select(MessageAttachment))).all():
                row.media_group_id = "same-album"
            followup = await db.scalar(
                select(RawMessage).where(RawMessage.telegram_message_id == 32)
            )
            followup.reply_to_message_id = 31
            await db.commit()
            source = await db.scalar(select(RawMessage).where(RawMessage.telegram_message_id == 31))
            context = await image_jobs.context_snapshot(db, source)
            assert context["image_source_message_id"] == 31
            by_id = {row["id"]: row for row in context["messages"]}
            assert by_id[31]["media_group_ids"] == ["same-album"]
            assert by_id[32]["reply_to"] == 31
            assert by_id[32]["media_group_ids"] == ["same-album"]

    asyncio.run(run())
