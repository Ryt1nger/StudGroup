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
    GroupAnalysisSettings,
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


def test_incomplete_pass_a_is_reviewed_by_pass_b_without_retrying_pass_a(client, monkeypatch):
    download_ok(monkeypatch)
    photo_message(client)
    make_new(client)
    config = cfg(client)
    partial = (
        '{"summary":"Разворот тетради","extracted_text":"Векторы, пункт 1",'
        '"facts":[{"text":"Векторы","certainty":"confirmed","region":"top"}],'
        '"uncertain_fragments":["строка 2 неразборчива"],"complete":false}'
    )
    provider = VisionProvider(partial, propose=False)
    now = datetime.now(UTC)

    assert tick(client, provider, config, now) == "partial"
    first = stored(client, ImageAnalysisJob)[0]
    assert first.pass_number == 1 and first.state == "partial"
    assert first.error == "image_reading_incomplete"
    assert first.result_json is not None
    assert first.attempts == 1

    # The second paid reading checks pixels again instead of discarding the first.
    provider.answer = reading()
    assert tick(client, provider, config, now) == "ready"
    jobs = stored(client, ImageAnalysisJob)
    assert len(jobs) == 2
    assert {job.pass_number: job.state for job in jobs} == {1: "partial", 2: "ready"}
    assert all(job.attempts == 1 for job in jobs)
    assert tick(client, provider, config, now) == "idle"
    assert len(provider.vision) == 2
    assert len(stored(client, ImageAnalysisCall)) == 2
    # The second pass inherits confirmed pixel facts instead of losing them.
    final = next(job for job in jobs if job.pass_number == 2)
    assert "Векторы" in final.result_json


def test_still_unreadable_after_pass_b_is_held_not_published(client, monkeypatch):
    download_ok(monkeypatch)
    photo_message(client)
    make_new(client)
    config = cfg(client)
    partial = (
        '{"summary":"Нечёткое фото","extracted_text":"Задачи",'
        '"uncertain_fragments":["невидимый номер задачи"],"complete":false}'
    )
    provider = VisionProvider(partial, propose=False)
    now = datetime.now(UTC)

    assert tick(client, provider, config, now) == "partial"
    assert tick(client, provider, config, now) == "failed"
    jobs = stored(client, ImageAnalysisJob)
    assert {job.pass_number: job.state for job in jobs} == {1: "partial", 2: "failed"}
    assert next(job for job in jobs if job.pass_number == 2).error == "image_reading_incomplete"
    assert tick(client, provider, config, now) == "idle"
    assert len(provider.vision) == 2
    assert len(stored(client, ImageAnalysisCall)) == 2


def test_legacy_failed_pass_a_does_not_trigger_unapproved_paid_backfill(client, monkeypatch):
    download_ok(monkeypatch)
    photo_message(client)
    make_new(client)
    config = cfg(client)
    partial = '{"summary":"Нечёткое фото","extracted_text":"","complete":false}'
    provider = VisionProvider(partial, propose=False)
    now = datetime.now(UTC)
    assert tick(client, provider, config, now) == "partial"

    async def mark_preexisting_failure():
        async with AsyncSession(client.app.state.engine) as db:
            job = await db.scalar(select(ImageAnalysisJob))
            job.state = "failed"
            await db.commit()

    asyncio.run(mark_preexisting_failure())
    assert tick(client, provider, config, now) == "idle"
    assert len(provider.vision) == 1
    assert len(stored(client, ImageAnalysisCall)) == 1


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


def test_image_funding_pause_retains_job_and_recovers_with_approved_recurring_cap(
    client, monkeypatch
):
    download_ok(monkeypatch)
    photo_message(client)
    make_new(client)
    now = datetime.now(UTC)

    async def expire_release():
        async with AsyncSession(client.app.state.engine) as db:
            group = await db.scalar(select(Group).where(Group.telegram_chat_id == -1001))
            route = await db.get(GroupAnalysisSettings, group.id)
            route.release_budget_usd = Decimal("0.20")
            route.release_budget_since = now - timedelta(days=2)
            route.release_budget_until = now - timedelta(minutes=1)
            route.release_budget_receipt = "a" * 64
            group_id = group.id
            await db.commit()
            return group_id

    group_id = asyncio.run(expire_release())
    denied = cfg(
        client,
        image_analysis_mode="live",
        ai_release_budget_required_groups=[str(group_id)],
    )
    provider = VisionProvider(reading(), propose=False)
    assert tick(client, provider, denied, now) == "idle"
    assert provider.vision == []
    assert stored(client, ImageAnalysisJob)[0].state == "budget_deferred"

    approved = cfg(
        client,
        image_analysis_mode="live",
        ai_release_budget_required_groups=[str(group_id)],
        ai_recurring_budget_groups=[str(group_id)],
        ai_recurring_approved_until=now + timedelta(days=40),
        ai_recurring_approval_receipt="b" * 64,
    )
    assert tick(client, provider, approved, now + timedelta(minutes=6)) == "ready"
    assert tick(client, provider, approved, now + timedelta(minutes=7)) == "ready"
    assert len(provider.vision) == 2
    assert all(job.state == "ready" for job in stored(client, ImageAnalysisJob))


def test_recurring_image_cap_blocks_pixels_without_clearing_image_job(client, monkeypatch):
    download_ok(monkeypatch)
    photo_message(client)
    make_new(client)
    now = datetime.now(UTC)

    async def id():
        async with AsyncSession(client.app.state.engine) as db:
            return str(await db.scalar(select(Group.id).where(Group.telegram_chat_id == -1001)))

    group_id = asyncio.run(id())
    config = cfg(
        client,
        image_analysis_mode="live",
        ai_recurring_budget_groups=[group_id],
        ai_recurring_approved_until=now + timedelta(days=40),
        ai_recurring_approval_receipt="b" * 64,
        ai_recurring_image_daily_budget_usd=0.0001,
        ai_recurring_image_monthly_budget_usd=0.0001,
    )
    provider = VisionProvider(reading(), propose=False)
    assert tick(client, provider, config, now) == "budget_deferred"
    assert provider.vision == []
    assert stored(client, ImageAnalysisJob)[0].state == "budget_deferred"
    assert stored(client, ImageAnalysisJob)[0].error.startswith("recurring_")


def test_production_vision_reads_both_passes_with_zero_pilot_budgets(client, monkeypatch):
    download_ok(monkeypatch)
    photo_message(client)
    make_new(client, budget="0")

    async def group_id():
        async with AsyncSession(client.app.state.engine) as db:
            return str(await db.scalar(select(Group.id).where(Group.telegram_chat_id == -1001)))

    config = cfg(
        client,
        image_analysis_mode="live",
        ai_production_groups=[asyncio.run(group_id())],
        image_hourly_budget_usd=0,
        image_total_budget_usd=0,
        image_package_budget_usd=0,
    )
    provider = VisionProvider(reading(), propose=False)
    assert tick(client, provider, config) == "ready"
    assert tick(client, provider, config) == "ready"
    assert tick(client, provider, config) == "idle"
    assert len(provider.vision) == 2
    assert all(job.state == "ready" for job in stored(client, ImageAnalysisJob))


def test_production_picture_is_not_failed_by_old_reservation_size(client, monkeypatch):
    download_ok(monkeypatch)
    photo_message(client)
    make_new(client)

    async def group_id():
        async with AsyncSession(client.app.state.engine) as db:
            return str(await db.scalar(select(Group.id).where(Group.telegram_chat_id == -1001)))

    config = cfg(
        client,
        ai_production_groups=[asyncio.run(group_id())],
        image_analysis_mode="live",
        image_pass_reservation_usd=0.000001,
    )
    provider = VisionProvider(reading(), propose=False)
    assert tick(client, provider, config) == "ready"
    assert tick(client, provider, config) == "ready"
    assert all(job.state == "ready" for job in stored(client, ImageAnalysisJob))
    assert all(call.reserved_usd == 0 for call in stored(client, ImageAnalysisCall))


def test_production_expired_vision_lease_isolated_while_next_photo_advances(client, monkeypatch):
    download_ok(monkeypatch)
    photo_message(client, mid=31)
    photo_message(client, mid=32)
    make_new(client)

    async def id():
        async with AsyncSession(client.app.state.engine) as db:
            return str(await db.scalar(select(Group.id).where(Group.telegram_chat_id == -1001)))

    config = cfg(client, image_analysis_mode="live", ai_production_groups=[asyncio.run(id())])
    now = datetime.now(UTC)

    class Crash(VisionProvider):
        async def complete_vision(self, **kwargs):
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        tick(client, Crash(reading()), config, now)
    good = VisionProvider(reading())
    # The expired attempt is quarantined. It is not replayed, and the other photo
    # is still processed by the same worker.
    assert tick(client, good, config, now + timedelta(minutes=4)) == "idle"
    assert tick(client, good, config, now + timedelta(minutes=4, seconds=1)) == "ready"
    assert len(good.vision) == 1
    jobs = stored(client, ImageAnalysisJob)
    assert {job.state for job in jobs} == {"failed", "ready"}
    assert next(job.error for job in jobs if job.state == "failed") == "ambiguous_paid_attempt"
    assert any(call.state == "unknown" for call in stored(client, ImageAnalysisCall))

    async def no_hold():
        async with AsyncSession(client.app.state.engine) as db:
            group = await db.scalar(select(Group).where(Group.telegram_chat_id == -1001))
            return (await db.get(GroupAnalysisSettings, group.id)).paid_outcome_hold

    assert asyncio.run(no_hold()) is None


def test_pass_b_debounces_fresh_reply_context_without_delaying_old_images():
    now = datetime(2026, 11, 7, 7, 22, tzinfo=UTC)
    recent = {
        "messages": [
            {"sent_at": (now - timedelta(seconds=10)).isoformat()},
            {"sent_at": (now - timedelta(minutes=3)).isoformat()},
        ]
    }
    assert image_jobs.pass_b_available_at(recent, now) == now + timedelta(seconds=50)
    old = {"messages": [{"sent_at": (now - timedelta(hours=2)).isoformat()}]}
    assert image_jobs.pass_b_available_at(old, now) == now
    future = {"messages": [{"sent_at": (now + timedelta(days=1)).isoformat()}]}
    assert image_jobs.pass_b_available_at(future, now) == now + timedelta(seconds=60)
