import asyncio
import json

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from test_image_jobs import cfg, download_ok, reading, stored, tick
from test_package_worker import make_new
from test_photo_reading import VisionProvider, photo_message
from test_staff_api import AUTH, make

from studgroup.models import ImageScheduleSuggestion, ScheduleChange, SchedulePattern

pytest_plugins = ["test_schedule_api"]


def prepare(client, monkeypatch, **row_patch):
    download_ok(monkeypatch)
    photo_message(client)
    make_new(client)
    row = {"weekday": 2, "starts": "13:00", "ends": "14:30", "subject": "Алгебра",
           "room": "201", "teacher": "Иванов", "week": "all", "valid_from": "2026-10-28", "valid_until": "2026-12-31"} | row_patch
    payload = json.loads(reading()) | {"semantic_types": ["schedule"], "schedule": [row]}
    config = cfg(client)
    provider = VisionProvider(json.dumps(payload))
    assert tick(client, provider, config) == "ready"
    assert tick(client, provider, config) == "ready"
    suggestion = stored(client, ImageScheduleSuggestion)[0]
    client.app.state.settings.image_schedule_review_enabled = True
    client.app.state.settings.image_analysis_groups = config.image_analysis_groups
    return suggestion


def test_schedule_is_only_a_suggestion_until_authorized_confirmation(client, monkeypatch):
    suggestion = prepare(client, monkeypatch)
    assert len(stored(client, ScheduleChange)) == 0
    path = f"/v1/staff/image-findings/schedule/{suggestion.id}/confirm"
    body = {"expected_revision": 1, "expected_schedule_version": 0}
    assert client.post(path, json=body, headers=AUTH).status_code == 403
    make(client, "headman")
    result = client.post(path, json=body, headers=AUTH)
    assert result.status_code == 200, result.text
    assert result.json()["schedule_version"] == 1
    assert len(stored(client, ScheduleChange)) == 1
    assert client.post(path, json=body, headers=AUTH).status_code == 200
    assert len(stored(client, ScheduleChange)) == 1
    async def patterns():
        async with AsyncSession(client.app.state.engine) as db:
            return (await db.scalars(select(SchedulePattern).where(SchedulePattern.subject == "Алгебра"))).all()
    assert len(asyncio.run(patterns())) == 1


def test_missing_effective_date_and_unknown_subgroup_cannot_change_schedule(client, monkeypatch):
    suggestion = prepare(client, monkeypatch, valid_from=None, valid_until=None)
    make(client, "headman")
    response = client.post(f"/v1/staff/image-findings/schedule/{suggestion.id}/confirm",
        json={"expected_revision": 1, "expected_schedule_version": 0}, headers=AUTH)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "effective_dates_required"
    assert stored(client, ScheduleChange) == []


def test_schedule_review_is_disabled_by_default(client):
    make(client, "headman")
    assert client.get("/v1/staff/image-findings/schedule", headers=AUTH).status_code == 404


def test_explicit_single_lesson_mapping_uses_existing_audited_change(client, monkeypatch):
    suggestion = prepare(client, monkeypatch, weekday=0, subject="Математика",
        starts="11:00", ends="12:30", valid_from="2026-10-26")
    make(client, "headman")
    data = client.get("/v1/staff/schedule?start=2026-11-02&end=2026-11-02", headers=AUTH).json()
    lesson = data["lesson_edits"][0]
    response = client.post(f"/v1/staff/image-findings/schedule/{suggestion.id}/confirm", headers=AUTH,
        json={"expected_revision": 1, "expected_schedule_version": 0, "targets": [{
            "row_index": 0, "lesson_id": lesson["lesson_id"], "pattern_revision": lesson["pattern_revision"],
            "override_revision": lesson["override_revision"], "scope": "once"}]})
    assert response.status_code == 200, response.text
    assert stored(client, ScheduleChange)[0].operation == "lesson_once"
    assert stored(client, ScheduleChange)[0].input_method == "image_confirm"


def test_subgroup_schedule_requires_explicit_mapping(client, monkeypatch):
    suggestion = prepare(client, monkeypatch, subgroup="Первая")
    make(client, "headman")
    response = client.post(f"/v1/staff/image-findings/schedule/{suggestion.id}/confirm", headers=AUTH,
        json={"expected_revision": 1, "expected_schedule_version": 0})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "subgroup_mapping_required"
    assert stored(client, ScheduleChange) == []
