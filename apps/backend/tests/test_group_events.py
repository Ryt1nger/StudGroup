import asyncio
import json
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from test_image_jobs import cfg, download_ok, reading, stored, tick
from test_package_worker import make_new
from test_photo_reading import VisionProvider, photo_message
from test_staff_api import AUTH, make

from studgroup.analysis.group_events import capture_event, purge_events
from studgroup.analysis.image_contracts import ImageEvent
from studgroup.models import GroupEvent, GroupEventRevision, GroupEventSource, RawMessage

pytest_plugins = ["test_schedule_api"]


def test_a_lesson_change_cannot_create_a_parallel_event_even_if_model_fills_events(monkeypatch):
    from types import SimpleNamespace

    from studgroup.analysis import group_events
    from studgroup.analysis.image_contracts import VisualReading

    calls = []

    async def capture(*args):
        calls.append(args)

    monkeypatch.setattr(group_events, "capture_event", capture)
    result = VisualReading(
        summary="Lesson move",
        extracted_text="Физика перенесена",
        complete=True,
        semantic_types=["schedule_change"],
        events=[ImageEvent(title="Физика", audience="group")],
    )
    job = SimpleNamespace(group_id="group")
    asyncio.run(group_events.capture_image(None, job, result, None, datetime.now(UTC)))
    assert calls == []
    result = result.model_copy(update={"semantic_types": ["schedule_change", "event"]})
    asyncio.run(group_events.capture_image(None, job, result, None, datetime.now(UTC)))
    assert len(calls) == 1


def prepare(client, monkeypatch, audience="group"):
    download_ok(monkeypatch)
    photo_message(client)
    make_new(client)
    event = {
        "title": "Лекция о космосе",
        "description": "Открытая лекция",
        "when_text": "28 октября 18:00",
        "starts_at": (datetime.now(UTC) + timedelta(days=2)).isoformat(),
        "place": "Актовый зал",
        "audience": audience,
        "conditions": "Вход свободный",
    }
    provider = VisionProvider(
        json.dumps(json.loads(reading()) | {"semantic_types": ["event"], "events": [event]})
    )
    config = cfg(client)
    assert tick(client, provider, config) == "ready"
    assert tick(client, provider, config) == "ready"
    assert tick(client, provider, config) == "idle"
    client.app.state.settings.image_event_review_enabled = True
    client.app.state.settings.image_analysis_groups = config.image_analysis_groups
    return stored(client, GroupEvent)[0], event


def put(client, event, payload, state="draft", revision=None):
    return client.put(
        f"/v1/staff/image-findings/events/{event.id}",
        headers=AUTH,
        json={"expected_revision": revision or event.revision, "payload": payload, "state": state},
    )


def test_poster_stays_draft_and_publishing_has_separate_flag(client, monkeypatch):
    event, payload = prepare(client, monkeypatch)
    assert event.state == "draft"
    assert client.get("/v1/group-events", headers=AUTH).status_code == 404
    assert put(client, event, payload).status_code == 403
    make(client, "headman")
    assert put(client, event, payload, "active").status_code == 404
    client.app.state.settings.image_event_publication_enabled = True
    result = put(client, event, payload, "active")
    assert result.status_code == 200, result.text
    listing = client.get("/v1/group-events", headers=AUTH)
    assert len(listing.json()["events"]) == 1
    assert put(client, event, payload).status_code == 409
    assert put(client, event, payload, "cancelled", result.json()["revision"]).status_code == 200
    assert client.get("/v1/group-events", headers=AUTH).json()["events"] == []


def test_personal_invitation_cannot_be_promoted_to_whole_group(client, monkeypatch):
    event, payload = prepare(client, monkeypatch, "personal")
    make(client, "headman")
    client.app.state.settings.image_event_publication_enabled = True
    result = put(client, event, payload | {"audience": "group"}, "active")
    assert result.status_code == 409
    assert client.get("/v1/group-events", headers=AUTH).json()["events"] == []


def test_manual_lock_preserves_event_and_keeps_model_change_as_held_finding(client, monkeypatch):
    event, payload = prepare(client, monkeypatch)
    make(client, "headman")
    assert put(client, event, payload | {"place": "Ручной зал"}).status_code == 200

    async def change():
        async with AsyncSession(client.app.state.engine) as db:
            raw = await db.scalar(select(RawMessage))
            incoming = ImageEvent.model_validate_json(
                json.dumps(
                    payload
                    | {
                        "place": "Другой зал",
                        "action": "update",
                        "existing_event_id": str(event.id),
                        "expected_revision": 2,
                    }
                )
            )
            await capture_event(db, raw.group_id, raw, incoming, datetime.now(UTC))
            await db.commit()

    asyncio.run(change())
    current = stored(client, GroupEvent)[0]
    assert json.loads(current.payload_json)["place"] == "Ручной зал"
    assert current.revision == 2 and current.manual_lock
    assert any(source.apply_state == "held" for source in stored(client, GroupEventSource))


def test_event_retention_cascades_sources_and_revision_history(client, monkeypatch):
    prepare(client, monkeypatch)
    assert stored(client, GroupEventRevision)

    async def purge():
        async with AsyncSession(client.app.state.engine) as db:
            await purge_events(db, datetime.now(UTC) + timedelta(days=31))
            await db.commit()

    asyncio.run(purge())
    assert stored(client, GroupEvent) == []


def test_public_events_recheck_membership_and_never_cross_group_boundary(client, monkeypatch):
    from studgroup.models import Group

    event, payload = prepare(client, monkeypatch)
    make(client, "headman")
    client.app.state.settings.image_event_publication_enabled = True
    assert put(client, event, payload, "active").status_code == 200
    path = f"/v1/group-events/{event.id}"
    assert client.get(path).status_code == 401
    assert client.get(path, headers={"Authorization": "Bearer expired"}).status_code == 401

    async def move_to_other_group():
        async with AsyncSession(client.app.state.engine) as db:
            other = await db.scalar(select(Group).where(Group.telegram_chat_id == -1002))
            row = await db.get(GroupEvent, event.id)
            row.group_id = other.id
            await db.commit()

    asyncio.run(move_to_other_group())
    assert client.get(path, headers=AUTH).status_code == 404
    assert client.get("/v1/group-events", headers=AUTH).json()["events"] == []
