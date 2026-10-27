"""Full offline image -> C+ -> canonical card -> student API and shadow-isolation checks."""

import asyncio
import hashlib
import json

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from test_image_jobs import cfg, download_ok, reading, tick
from test_package_worker import make_new, step
from test_photo_reading import VisionProvider, photo_message
from test_student_card_api import seed_published_card

from studgroup.analysis.shadow import source_snapshot_sha256
from studgroup.models import MessageAttachment

pytest_plugins = ["test_schedule_api"]
AUTH = {"Authorization": "Bearer valid"}


def homework_reading():
    return json.dumps(json.loads(reading()) | {"semantic_types": ["homework"],
        "facts": [{"text": "Математика: решить задачи 1–3", "certainty": "confirmed"}]})


def test_image_through_cplus_and_student_api_keeps_full_description(client, monkeypatch):
    download_ok(monkeypatch)
    photo_message(client)
    make_new(client)
    config = cfg(client, image_analysis_mode="live")

    async def ident():
        async with AsyncSession(client.app.state.engine) as db:
            return str(await db.scalar(select(MessageAttachment.id)))
    attachment_id = asyncio.run(ident())

    class Proof(VisionProvider):
        async def complete(self, request):
            result = await super().complete(request)
            if request.prompt_version.startswith("cplus-extraction"):
                payload = json.loads(result.content)
                payload["proposals"][0]["evidence"][0]["fact_paths"] = ["description", f"image:{attachment_id}:0"]
                result.content = json.dumps(payload)
            return result
    provider = Proof(homework_reading(), cited=(31,))
    assert tick(client, provider, config) == "ready"
    assert tick(client, provider, config) == "ready"
    assert step(client, provider, config) == "completed"
    listed = client.get("/v1/homework", headers=AUTH).json()["items"]
    assert len(listed) == 1
    card = client.get(f"/v1/homework/{listed[0]['id']}", headers=AUTH).json()
    assert card["description"] == "Решить задачи 1–3."
    assert [material["kind"] for material in card["materials"]] == ["photo"]
    assert card["materials"][0]["replaces_description"] is False
    assert step(client, provider, config) == "idle"
    assert len(provider.vision) == 2


def test_shadow_reading_never_changes_legacy_published_card_materials(client, monkeypatch):
    download_ok(monkeypatch)
    ident, _foreign = seed_published_card(client)
    make_new(client)
    config = cfg(client)  # shadow evidence mode, not live
    before = client.get(f"/v1/homework/{ident}", headers=AUTH).json()
    provider = VisionProvider(homework_reading(), propose=False)
    assert tick(client, provider, config) == "ready"
    assert tick(client, provider, config) == "ready"
    after = client.get(f"/v1/homework/{ident}", headers=AUTH).json()
    # Response generation time changes on every read; all product fields must be identical.
    assert after.pop("generated_at")
    assert before.pop("generated_at")
    assert after == before


def test_old_snapshot_hash_is_unchanged_when_new_fields_are_absent():
    from test_analysis_shadow import analysis_input

    data = analysis_input()
    old = data.model_dump(mode="json", exclude={"subject_catalog", "image_evidence"})
    for message in old["messages"]:
        message.pop("image_facts", None)
        for attachment in message["attachments"]:
            for key in ("attachment_id", "pixel_sha256", "visual_hash", "content_signature",
                        "image_analysis_state", "semantic_types", "width", "height"):
                attachment.pop(key, None)
    expected = hashlib.sha256(json.dumps(old, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    assert source_snapshot_sha256(data) == expected


def test_oversized_image_context_is_explicitly_held_without_losing_results(client, monkeypatch):
    from studgroup.models import ImageAnalysisJob, RawMessage

    download_ok(monkeypatch)
    photo_message(client)
    make_new(client)
    config = cfg(client, image_analysis_mode="live")
    content = json.loads(homework_reading())
    content["facts"] = [{"text": f"Условие {i}: " + "я" * 3000, "certainty": "confirmed"} for i in range(20)]
    provider = VisionProvider(json.dumps(content, ensure_ascii=False), cited=(31,))
    assert tick(client, provider, config) == "ready"
    assert tick(client, provider, config) == "ready"
    assert step(client, provider, config) == "image_context_review"
    assert provider.requests == []
    async def inspect():
        async with AsyncSession(client.app.state.engine) as db:
            raw = await db.scalar(select(RawMessage))
            jobs = (await db.scalars(select(ImageAnalysisJob))).all()
            assert raw.processing_state == "needs_context"
            assert all(job.state == "ready" and job.result_json for job in jobs)
    asyncio.run(inspect())
