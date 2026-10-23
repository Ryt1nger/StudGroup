"""Photos of homework: the vision pass reads them before the package is analysed."""

import asyncio
import json
import uuid
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from test_package_worker import (
    BASE,
    PackageProvider,
    count,
    make_new,
    settings,
    states,
    step,
)
from test_processing import source

from studgroup.analysis import photo_reading
from studgroup.models import Homework, MessageAttachment, RawMessage

pytest_plugins = ["test_schedule_api"]

JPEG = b"\xff\xd8\xff\xe0" + b"0" * 32


def reading(relevance="task", text="Алгебра, №12–15, стр. 40", summary="Страница учебника"):
    return json.dumps({"relevance": relevance, "summary": summary, "text": text})


class VisionProvider(PackageProvider):
    def __init__(self, answer, **kwargs):
        super().__init__(**kwargs)
        self.answer = answer
        self.vision = []

    async def complete_vision(self, **kwargs):
        self.vision.append(kwargs)
        if isinstance(self.answer, Exception):
            raise self.answer
        return {
            "choices": [{"message": {"content": self.answer}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 900, "completion_tokens": 60},
        }


def photo_message(client, mid=31, caption=""):
    source(client, mid=mid, text=caption, when=BASE)

    async def attach():
        async with AsyncSession(client.app.state.engine) as db:
            raw = await db.scalar(select(RawMessage).where(RawMessage.telegram_message_id == mid))
            db.add(
                MessageAttachment(
                    raw_message_id=raw.id,
                    group_id=raw.group_id,
                    source_revision=1,
                    kind="photo",
                    stable_file_id=f"u{mid}",
                    telegram_file_id=f"f{mid}",
                    mime_type="image/jpeg",
                    processing_state="metadata_only",
                    active=True,
                    created_at=BASE,
                    delete_at=BASE + timedelta(days=30),
                )
            )
            await db.commit()

    asyncio.run(attach())


def photo_state(client):
    async def read():
        async with AsyncSession(client.app.state.engine) as db:
            row = await db.scalar(select(MessageAttachment))
            return row.processing_state, row.photo_text, row.photo_summary

    return asyncio.run(read())


def allow_download(monkeypatch, image=JPEG):
    async def fake(token, file_id, *, transport=None):
        return image

    monkeypatch.setattr(photo_reading, "download_telegram_file", fake)


def photo_settings(**patch):
    return settings(photo_reading_enabled=True, **patch)


def test_parse_reading_accepts_the_contract_and_rejects_the_rest():
    assert photo_reading.parse_reading(reading()) == (
        "task",
        "Страница учебника",
        "Алгебра, №12–15, стр. 40",
    )
    fenced = "```json\n" + reading("event", "Объявление о КТ", "КТ 25.10") + "\n```"
    assert photo_reading.parse_reading(fenced)[0] == "event"
    # an irrelevant photo never carries text forward
    assert photo_reading.parse_reading(reading("irrelevant", "Мем", "смешной текст"))[2] == ""
    assert photo_reading.parse_reading("не json") is None
    assert photo_reading.parse_reading(reading("study")) is None
    assert photo_reading.parse_reading('["study"]') is None


def test_sniff_mime_uses_content_not_declared_type():
    assert photo_reading.sniff_mime(JPEG) == "image/jpeg"
    assert photo_reading.sniff_mime(b"\x89PNG\r\n\x1a\n....") == "image/png"
    assert photo_reading.sniff_mime(b"<html>") is None


def test_photo_is_read_and_its_text_reaches_the_extraction_pass(client, monkeypatch):
    allow_download(monkeypatch)
    photo_message(client)
    make_new(client)
    provider = VisionProvider(reading(), cited=(31,))
    assert step(client, provider, photo_settings()) == "completed"
    assert len(provider.vision) == 1
    assert provider.vision[0]["image_data_url"].startswith("data:image/jpeg;base64,")
    assert photo_state(client) == ("ready", "Алгебра, №12–15, стр. 40", "Страница учебника")
    seen = json.dumps(provider.requests[-1].user_payload, ensure_ascii=False)
    assert "Текст на фото: Алгебра, №12–15, стр. 40" in seen
    assert "Фото: Страница учебника" in seen
    assert count(client, Homework) == 1

    async def stored_text():
        async with AsyncSession(client.app.state.engine) as db:
            return await db.scalar(select(RawMessage.text))

    # the stored message is untouched: only the analysis input carries the read text
    assert asyncio.run(stored_text()) == ""


def test_photo_reading_is_off_by_default(client, monkeypatch):
    allow_download(monkeypatch)
    photo_message(client)
    make_new(client)
    provider = VisionProvider(reading(), cited=(31,))
    assert step(client, provider) == "completed"
    assert provider.vision == []
    assert photo_state(client)[0] == "metadata_only"


def test_irrelevant_photo_adds_nothing_to_the_message(client, monkeypatch):
    allow_download(monkeypatch)
    photo_message(client, caption="смотрите")
    make_new(client)
    provider = VisionProvider(
        reading("irrelevant", text="ха-ха", summary="Мем с котом"), propose=False
    )
    assert step(client, provider, photo_settings()) == "completed"
    assert photo_state(client) == ("irrelevant", None, "Мем с котом")
    seen = json.dumps(provider.requests[-1].user_payload, ensure_ascii=False)
    assert "Текст на фото" not in seen and "Мем" not in seen
    assert count(client, Homework) == 0


def test_unreadable_photo_is_marked_failed_once_and_the_package_goes_on(client, monkeypatch):
    async def nothing(token, file_id, *, transport=None):
        return None

    monkeypatch.setattr(photo_reading, "download_telegram_file", nothing)
    photo_message(client, caption="по алгебре задачи с фото")
    make_new(client)
    provider = VisionProvider(reading(), cited=(31,))
    assert step(client, provider, photo_settings()) == "completed"
    assert provider.vision == []
    assert photo_state(client)[0] == "failed"


def test_temporary_vision_fault_waits_instead_of_freezing_the_package(client, monkeypatch):
    from studgroup.ai import ProviderFailure

    allow_download(monkeypatch)
    photo_message(client)
    make_new(client)
    provider = VisionProvider(ProviderFailure("rate_limited", True), cited=(31,))
    assert step(client, provider, photo_settings()) == "photo_retry"
    assert provider.requests == []  # no package was analysed without the photo
    assert photo_state(client)[0] == "metadata_only"
    provider.answer = reading()
    assert step(client, provider, photo_settings()) == "completed"
    assert photo_state(client)[0] == "ready"


def test_photo_cost_is_charged_to_the_package_run(client, monkeypatch):
    from studgroup.models import ShadowEvaluationRun

    allow_download(monkeypatch)
    photo_message(client)
    make_new(client)
    provider = VisionProvider(reading(), cited=(31,))
    step(client, provider, photo_settings())

    async def charged():
        async with AsyncSession(client.app.state.engine) as db:
            return await db.scalar(select(ShadowEvaluationRun.charged_usd))

    # 900 prompt + 60 completion tokens of the vision call plus the two text passes
    assert asyncio.run(charged()) > (900 * 0.30 + 60 * 1.20) / 1_000_000


def test_image_endpoint_serves_the_photo_to_a_member_and_hides_unknown_ones(client, monkeypatch):
    photo_message(client)

    async def attachment_id():
        async with AsyncSession(client.app.state.engine) as db:
            return await db.scalar(select(MessageAttachment.id))

    attachment = asyncio.run(attachment_id())
    headers = {"Authorization": "Bearer valid"}
    allow_download(monkeypatch)
    ok = client.get(f"/v1/attachments/{attachment}/image", headers=headers)
    assert ok.status_code == 200
    assert ok.headers["content-type"] == "image/jpeg"
    assert ok.headers["cache-control"].startswith("private")
    assert ok.content == JPEG
    assert client.get(f"/v1/attachments/{uuid.uuid4()}/image", headers=headers).status_code == 404
    assert client.get(f"/v1/attachments/{attachment}/image").status_code == 401
    allow_download(monkeypatch, image=None)
    assert client.get(f"/v1/attachments/{attachment}/image", headers=headers).status_code == 503


def completed_photo_message(client, mid, when):
    source(client, mid=mid, text="", when=when)

    async def attach():
        async with AsyncSession(client.app.state.engine) as db:
            raw = await db.scalar(select(RawMessage).where(RawMessage.telegram_message_id == mid))
            raw.processing_state = "completed"
            db.add(
                MessageAttachment(
                    raw_message_id=raw.id,
                    group_id=raw.group_id,
                    source_revision=1,
                    kind="photo",
                    stable_file_id=f"u{mid}",
                    telegram_file_id=f"f{mid}",
                    mime_type="image/jpeg",
                    processing_state="metadata_only",
                    active=True,
                    created_at=when,
                    delete_at=when + timedelta(days=30),
                )
            )
            await db.commit()

    asyncio.run(attach())


def test_backfill_reopens_unread_photo_messages_once_and_reads_them(client, monkeypatch):
    allow_download(monkeypatch)
    # a text message that was already analysed and has no photo must stay untouched
    source(client, mid=30, text="по алгебре решить задачи", when=BASE - timedelta(minutes=5))
    completed_photo_message(client, 31, BASE)
    completed_photo_message(client, 32, BASE + timedelta(minutes=1))

    async def settle_text():
        async with AsyncSession(client.app.state.engine) as db:
            raw = await db.scalar(select(RawMessage).where(RawMessage.telegram_message_id == 30))
            raw.processing_state = "completed"
            await db.commit()

    asyncio.run(settle_text())
    make_new(client)
    provider = VisionProvider(reading(), cited=(31,))
    since = BASE - timedelta(hours=1)
    cfg = photo_settings(photo_backfill_since=since)
    assert step(client, provider, cfg) == "completed"
    assert len(provider.vision) == 2
    first = states(client)
    assert first == {30: "completed", 31: "completed", 32: "completed"}
    # a second iteration finds nothing to reopen: no new package, no second read
    assert step(client, provider, cfg) == "idle"
    assert len(provider.vision) == 2


def test_backfill_needs_photo_reading_and_respects_the_start(client, monkeypatch):
    allow_download(monkeypatch)
    completed_photo_message(client, 31, BASE)
    make_new(client)
    provider = VisionProvider(reading(), cited=(31,))
    # photo reading off: nothing is reopened even with a start moment
    assert (
        step(client, provider, settings(photo_backfill_since=BASE - timedelta(hours=1))) == "idle"
    )
    assert provider.vision == []
    # a start after the message: it stays settled
    assert (
        step(client, provider, photo_settings(photo_backfill_since=BASE + timedelta(hours=1)))
        == "idle"
    )
    assert provider.vision == []


def test_a_second_shot_of_the_same_board_is_shown_to_the_model_once(client, monkeypatch):
    allow_download(monkeypatch)
    wide = "Доказательство теорем 20/3 ст 69-70 предел функции эквивалентные бесконечно малые"
    close = "Доказательство теорем 20/3 ст 69-70"
    answers = iter([reading(text=wide), reading(text=close)])

    class TwoShots(VisionProvider):
        async def complete_vision(self, **kwargs):
            self.answer = next(answers)
            return await super().complete_vision(**kwargs)

    photo_message(client, mid=31)
    photo_message(client, mid=32)
    make_new(client)
    provider = TwoShots(reading(), cited=(31, 32))
    assert step(client, provider, photo_settings()) == "completed"
    seen = json.dumps(provider.requests[-1].user_payload, ensure_ascii=False)
    assert seen.count("Текст на фото") == 1
    assert seen.count(close) == 1  # only inside the better reading, never as a second one
