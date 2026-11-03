"""Isolated cabinet upload tests; model is faked and no provider HTTP is made."""

import asyncio
import io
import json
import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from PIL import Image

from studgroup.ai import ProviderFailure
from sqlalchemy import select
from test_staff_api import AUTH, db_run, make

from studgroup.models import CabinetScheduleUpload, Group, GroupAnalysisSettings, ScheduleChange

pytest_plugins = ["test_schedule_api"]


def picture():
    out = io.BytesIO()
    Image.new("RGB", (220, 160)).save(out, format="PNG")
    return out.getvalue()


class Vision:
    def __init__(self, coverage="full_week"):
        self.calls = []
        self.coverage = coverage

    async def complete_vision(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {
                        "content": json.dumps(
                            {
                                "summary": "Расписание",
                                "extracted_text": "Математика",
                                "complete": True,
                                "semantic_types": ["schedule"],
                                "schedule": [
                                    {
                                        "weekday": 0,
                                        "starts": "09:00",
                                        "ends": "10:20",
                                        "subject": "Математика",
                                        "week": "all",
                                    }
                                ],
                                "schedule_interpretation": {
                                    "coverage": self.coverage,
                                    "duration": "one_week",
                                },
                            },
                            ensure_ascii=False,
                        )
                    },
                }
            ],
            "usage": {"prompt_tokens": 50, "completion_tokens": 100},
        }


def week():
    today = datetime.now(ZoneInfo("Europe/Moscow")).date()
    return today - timedelta(days=today.weekday())


def upload(client, day, data, duration="one_week"):
    return client.post(
        "/v1/staff/schedule-upload",
        headers=AUTH,
        files={"image": ("week.png", data, "image/png")},
        data={"week_start": day.isoformat(), "duration": duration},
    )


def advance(client, monkeypatch, vision):
    from studgroup import cabinet_schedule_worker

    monkeypatch.setattr(cabinet_schedule_worker, "provider_for", lambda *args, **kwargs: vision)
    return asyncio.run(
        cabinet_schedule_worker.process_next(client.app.state.engine, client.app.state.settings)
    )


def get_draft(client, ident):
    response = client.get("/v1/staff/schedule-upload", headers=AUTH)
    assert response.status_code == 200
    return next(x for x in response.json()["suggestions"] if x["id"] == ident)


def test_only_staff_may_upload_or_view_private_schedule_source(client):
    today = week()
    response = upload(client, today, picture())
    assert response.status_code == 403
    assert client.get("/v1/staff/schedule-upload", headers=AUTH).status_code == 403


def authorize_analysis(client):
    async def seed(db):
        group = await db.scalar(select(Group).where(Group.telegram_chat_id == -1001))
        db.add(
            GroupAnalysisSettings(
                group_id=group.id,
                analysis_mode="shadow",
                shadow_daily_budget_usd=1,
                shadow_sample_percent=100,
                release_budget_usd=0.15,
                release_budget_since=datetime.now(ZoneInfo("UTC")) - timedelta(minutes=5),
                release_budget_until=datetime.now(ZoneInfo("UTC")) + timedelta(hours=1),
                release_budget_receipt="f" * 64,
                updated_at=datetime.now(ZoneInfo("UTC")),
            )
        )

    db_run(client, seed)


def test_vision_draft_requires_explicit_confirmation_and_deduplicates(client, monkeypatch):
    from studgroup.analysis import release_budget

    make(client, "headman")
    authorize_analysis(client)
    vision = Vision()
    monkeypatch.setattr(release_budget, "provider_for", lambda *args, **kwargs: vision)
    today = week()
    data = picture()
    response = upload(client, today, data)
    assert response.status_code == 201, response.text
    suggestion = response.json()
    assert suggestion["state"] == "queued"
    assert suggestion["progress_percent"] == 5
    assert vision.calls == []
    assert advance(client, monkeypatch, vision) == "pass_a_ready"
    mid = get_draft(client, suggestion["id"])
    assert mid["progress_percent"] == 50
    assert mid["state"] == "queued"
    assert advance(client, monkeypatch, vision) == "ready"
    suggestion = get_draft(client, suggestion["id"])
    assert suggestion["progress_percent"] == 100
    assert suggestion["progress_stage"] == "ready"
    assert suggestion["state"] == "pending"
    assert len(suggestion["rows"]) == 1
    assert suggestion["rows"][0]["valid_from"] == today.isoformat()
    assert suggestion["rows"][0]["valid_until"] == (today + timedelta(days=6)).isoformat()
    assert len(vision.calls) == 2
    assert all("data:image/png;base64," in call["image_data_url"] for call in vision.calls)
    listed = client.get("/v1/staff/schedule-upload", headers=AUTH)
    assert listed.status_code == 200
    assert len(listed.json()["suggestions"]) == 1
    image = client.get(f"/v1/staff/schedule-upload/{suggestion['id']}/image", headers=AUTH)
    assert image.content == data
    assert image.headers["cache-control"] == "private, no-store"

    async def count(db):
        return len((await db.scalars(select(ScheduleChange))).all())

    assert db_run(client, count) == 0
    official = client.get(
        f"/v1/staff/schedule?start={today.isoformat()}&end={today.isoformat()}",
        headers=AUTH,
    ).json()
    before = official["lesson_edits"][0]
    confirmed = client.post(
        f"/v1/staff/schedule-upload/{suggestion['id']}/confirm",
        headers=AUTH,
        json={
            "expected_revision": 1,
            "expected_schedule_version": 0,
            "corrected_rows": suggestion["rows"],
            "targets": [
                {
                    "row_index": 0,
                    "lesson_id": before["lesson_id"],
                    "pattern_revision": before["pattern_revision"],
                    "override_revision": before["override_revision"],
                    "scope": "once",
                }
            ],
        },
    )
    assert confirmed.status_code == 200, confirmed.text
    assert db_run(client, count) == 1
    duplicate = upload(client, today, data)
    assert duplicate.status_code == 201
    assert duplicate.json()["id"] == suggestion["id"]
    assert len(vision.calls) == 2


def test_reject_bad_image_without_spending_or_draft(client, monkeypatch):
    from studgroup.analysis import release_budget

    make(client, "deputy")
    authorize_analysis(client)
    vision = Vision()
    monkeypatch.setattr(release_budget, "provider_for", lambda *args, **kwargs: vision)
    response = upload(client, week(), b"not a valid PNG")
    assert response.status_code == 422
    assert vision.calls == []
    assert client.get("/v1/staff/schedule-upload", headers=AUTH).json()["suggestions"] == []


def test_editor_selected_week_gives_safe_dates_when_vision_coverage_is_unknown(client, monkeypatch):
    """Unknown screenshot coverage must not force a date field on every visible cell."""
    from studgroup.analysis import release_budget

    make(client, "headman")
    authorize_analysis(client)
    vision = Vision(coverage="unknown")
    monkeypatch.setattr(release_budget, "provider_for", lambda *args, **kwargs: vision)
    monday = week()
    response = upload(client, monday, picture())
    assert response.status_code == 201, response.text
    result = response.json()
    assert result["state"] == "queued"
    assert advance(client, monkeypatch, vision) == "pass_a_ready"
    assert advance(client, monkeypatch, vision) == "ready"
    result = get_draft(client, result["id"])
    assert result["state"] == "pending"
    assert result["duration"] == "one_week"
    assert result["rows"][0]["valid_from"] == monday.isoformat()
    assert result["rows"][0]["valid_until"] == (monday + timedelta(days=6)).isoformat()
    assert len(vision.calls) == 2
    assert client.get("/v1/staff/schedule-upload", headers=AUTH).status_code == 200
    # Preview only: no official write before explicit confirmation.
    assert result["confirmation"] is None


def test_recovery_of_queued_second_pass_without_repeating_paid_first_pass(client, monkeypatch):
    make(client, "headman")
    authorize_analysis(client)
    vision = Vision()
    submitted = upload(client, week(), picture()).json()
    assert advance(client, monkeypatch, vision) == "pass_a_ready"
    first = get_draft(client, submitted["id"])
    assert first["progress_stage"] == "queued_b"
    assert len(vision.calls) == 1
    # A fresh worker invocation after a restart reads the SQL checkpoint.
    assert advance(client, monkeypatch, vision) == "ready"
    assert len(vision.calls) == 2
    assert get_draft(client, submitted["id"])["state"] == "pending"


def test_stale_paid_stage_fails_closed_and_requires_explicit_retry(client, monkeypatch):
    make(client, "deputy")
    authorize_analysis(client)
    vision = Vision()
    submitted = upload(client, week(), picture()).json()

    async def lose_worker(db):
        row = await db.get(CabinetScheduleUpload, uuid.UUID(submitted["id"]))
        row.state = "processing"
        row.progress_stage = "reading_a"
        row.progress_percent = 20
        row.claimed_at = datetime.now(ZoneInfo("UTC")) - timedelta(minutes=20)

    db_run(client, lose_worker)
    assert advance(client, monkeypatch, vision) == "stale"
    error = get_draft(client, submitted["id"])
    assert error["state"] == "failed"
    assert error["can_retry"] is True
    assert not vision.calls
    retry = client.post(f"/v1/staff/schedule-upload/{submitted['id']}/retry", headers=AUTH)
    assert retry.status_code == 202
    assert retry.json()["state"] == "queued"
    assert advance(client, monkeypatch, vision) == "pass_a_ready"
    assert advance(client, monkeypatch, vision) == "ready"
    assert (
        client.post(f"/v1/staff/schedule-upload/{submitted['id']}/retry", headers=AUTH).status_code
        == 409
    )


class IncompleteSecondPassVision(Vision):
    async def complete_vision(self, **kwargs):
        result = await super().complete_vision(**kwargs)
        if len(self.calls) == 2:
            payload = json.loads(result["choices"][0]["message"]["content"])
            payload["complete"] = False
            payload["semantic_types"] = ["unknown"]
            result["choices"][0]["message"]["content"] = json.dumps(payload)
        return result


class FailingSecondPassVision(Vision):
    async def complete_vision(self, **kwargs):
        if self.calls:
            self.calls.append(kwargs)
            raise ProviderFailure(
                "release_budget_exhausted",
                False,
                reservation_releasable=True,
            )
        return await super().complete_vision(**kwargs)


def test_incomplete_second_pass_keeps_editable_partial_draft(client, monkeypatch):
    make(client, "headman")
    authorize_analysis(client)
    model = IncompleteSecondPassVision()
    submitted = upload(client, week(), picture()).json()
    assert advance(client, monkeypatch, model) == "pass_a_ready"
    assert advance(client, monkeypatch, model) == "ready"
    result = get_draft(client, submitted["id"])
    assert result["state"] == "pending"
    assert result["progress_percent"] == 100
    assert result["progress_stage"] == "ready_partial"
    assert len(result["rows"]) == 1
    assert result["rows"][0]["valid_from"] == week().isoformat()
    assert result["confirmation"] is None

    async def count(db):
        return len((await db.scalars(select(ScheduleChange))).all())

    assert db_run(client, count) == 0


def test_failed_second_pass_records_safe_reason_without_hidden_provider_text(client, monkeypatch):
    make(client, "headman")
    authorize_analysis(client)
    model = FailingSecondPassVision()
    submitted = upload(client, week(), picture()).json()
    assert advance(client, monkeypatch, model) == "pass_a_ready"
    assert advance(client, monkeypatch, model) == "failed"
    result = get_draft(client, submitted["id"])
    assert result["state"] == "failed"
    assert result["progress_percent"] == 65
    assert result["progress_stage"] == "failed_budget"
    assert result["can_retry"] is True
    assert len(model.calls) == 2
    retry = client.post(
        f"/v1/staff/schedule-upload/{submitted['id']}/retry", headers=AUTH
    )
    assert retry.status_code == 202
    assert retry.json()["progress_stage"] == "queued_b"
    # First successful, charged visual pass stays checkpointed: retry B only.
    assert advance(client, monkeypatch, Vision()) == "ready"
    assert get_draft(client, submitted["id"])["state"] == "pending"
