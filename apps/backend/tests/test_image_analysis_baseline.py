"""Stage-0 observations, NOT acceptance tests for the requested image improvements.

All vision answers are synthetic. Passing means the audit reproduces today's behaviour,
including defects. Replace the corresponding observation when its implementation stage lands.
These tests cannot measure OCR accuracy or model classification quality.
"""

import asyncio
import json
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from test_package_worker import make_new, states, step
from test_photo_reading import (
    VisionProvider,
    allow_download,
    photo_message,
    photo_settings,
    reading,
)

from studgroup.analysis.photo_reading import enriched_text, parse_reading
from studgroup.models import MessageAttachment, ShadowEvaluationRun
from studgroup.photo_dedupe import distinct

pytest_plugins = ["test_schedule_api"]


def attachment(ident, text):
    return SimpleNamespace(
        id=ident,
        kind="photo",
        processing_state="ready",
        photo_text=text,
        photo_summary="",
        width=1000,
        height=800,
        size_bytes=100,
    )


@pytest.mark.parametrize("total", [1, 6, 7, 12])
def test_baseline_package_settles_with_only_six_images_read(client, monkeypatch, total):
    allow_download(monkeypatch)
    for index in range(total):
        photo_message(client, mid=31 + index)
    make_new(client)
    provider = VisionProvider(reading(), propose=False)
    assert step(client, provider, photo_settings()) == "completed"
    assert len(provider.vision) == min(total, 6)
    assert set(states(client).values()) == {"completed"}

    async def unread():
        async with AsyncSession(client.app.state.engine) as db:
            rows = (await db.scalars(select(MessageAttachment))).all()
            return sum(row.processing_state == "metadata_only" for row in rows)

    assert asyncio.run(unread()) == max(total - 6, 0)
    # No backfill date configured: the remaining images are not scheduled on the next sweep.
    assert step(client, provider, photo_settings()) == "idle"


def test_baseline_image_document_is_stored_but_not_read(client, monkeypatch):
    allow_download(monkeypatch)
    photo_message(client)

    async def as_document():
        async with AsyncSession(client.app.state.engine) as db:
            row = await db.scalar(select(MessageAttachment))
            row.kind = "document"
            row.mime_type = "image/png"
            row.file_name = "synthetic-schedule.png"
            await db.commit()

    asyncio.run(as_document())
    make_new(client)
    provider = VisionProvider(reading(), propose=False)
    assert step(client, provider, photo_settings()) == "completed"
    assert provider.vision == []


def test_baseline_distinct_slides_lose_their_different_single_digit_exercise_numbers():
    # Target: retain four distinct pages. Today's word set discards single-digit numbers.
    pages = [attachment(i, f"Практическая работа по алгебре упражнение {i}") for i in range(1, 5)]
    assert len(distinct(pages)) == 1


def test_baseline_three_crops_lose_unique_facts_before_semantic_analysis(client, monkeypatch):
    allow_download(monkeypatch)
    common = "Алгебра домашняя работа решить упражнения страницы учебника"
    texts = [f"{common} задачи 1–5", f"{common} задачи 6–8", f"{common} формат PDF"]
    answers = iter(texts)

    class Crops(VisionProvider):
        async def complete_vision(self, **kwargs):
            self.answer = reading(text=next(answers))
            return await super().complete_vision(**kwargs)

    for mid in (31, 32, 33):
        photo_message(client, mid=mid)
    make_new(client)
    provider = Crops(reading(), propose=False)
    assert step(client, provider, photo_settings()) == "completed"
    assert len(provider.vision) == 3
    seen = json.dumps(provider.requests[-1].user_payload, ensure_ascii=False)
    assert sum(text in seen for text in texts) == 1


def test_baseline_reading_and_enrichment_silently_truncate_content():
    assert len(parse_reading(reading(text="я" * 2600))[2]) == 2500
    items = [
        {"kind": "photo", "processing_state": "ready", "photo_text": "я" * 2500} for _ in range(6)
    ]
    assert len(enriched_text("", items)) == 12000


def test_baseline_vision_runs_before_zero_budget_deferral(client, monkeypatch):
    allow_download(monkeypatch)
    photo_message(client)
    make_new(client, budget="0")
    provider = VisionProvider(reading(), propose=False)
    assert step(client, provider, photo_settings()) == "budget_deferred"
    assert len(provider.vision) == 1
    assert provider.requests == []


def test_baseline_retry_loses_already_successful_photo_cost(client, monkeypatch):
    from studgroup.ai import ProviderFailure

    allow_download(monkeypatch)
    photo_message(client, mid=31)
    photo_message(client, mid=32)
    make_new(client)

    class Retry(VisionProvider):
        async def complete_vision(self, **kwargs):
            if len(self.vision) == 1:
                self.vision.append(kwargs)
                raise ProviderFailure("rate_limited", True)
            return await super().complete_vision(**kwargs)

    provider = Retry(reading(), propose=False)
    assert step(client, provider, photo_settings()) == "photo_retry"
    assert step(client, provider, photo_settings()) == "completed"

    async def charge():
        async with AsyncSession(client.app.state.engine) as db:
            return (await db.scalar(select(ShadowEvaluationRun))).charged_usd

    # One vision success costs .000342; both text passes together cost .000018 in this fake.
    # Two successful readings should cost .000702, but only the final iteration is charged.
    from decimal import Decimal

    assert asyncio.run(charge()) == Decimal("0.00036000")
