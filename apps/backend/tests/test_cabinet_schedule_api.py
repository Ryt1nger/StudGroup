"""Isolated cabinet upload tests; model is faked and no provider HTTP is made."""

import asyncio
import io
import json
import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from PIL import Image
from sqlalchemy import select
from test_staff_api import AUTH, db_run, make

from studgroup.ai import ProviderFailure
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


def test_ready_draft_triggers_one_admin_report_without_extra_ai_call(client, monkeypatch):
    from studgroup import cabinet_review_push

    make(client, "headman")
    authorize_analysis(client)
    vision = Vision()
    reports = []

    async def report(engine, settings, ident):
        reports.append(ident)
        return True

    monkeypatch.setattr(cabinet_review_push, "notify_completed", report)
    submitted = upload(client, week(), picture()).json()
    assert advance(client, monkeypatch, vision) == "pass_a_ready"
    assert reports == []
    assert advance(client, monkeypatch, vision) == "ready"
    assert reports == [uuid.UUID(submitted["id"])]
    assert len(vision.calls) == 2
    assert get_draft(client, submitted["id"])["state"] == "pending"


def test_group_subject_catalogue_keeps_new_timetable_subject_after_week_ends(client):
    from datetime import date, time

    from studgroup.models import SchedulePattern

    make(client, "headman")

    async def seed(db):
        group = await db.scalar(select(Group).where(Group.telegram_chat_id == -1001))
        db.add(
            SchedulePattern(
                group_id=group.id,
                subject="Новая дисциплина из расписания",
                weekday=0,
                starts=time(9, 0),
                ends=time(10, 20),
                valid_from=date(2025, 1, 6),
                valid_until=date(2025, 1, 12),
                week="all",
            )
        )

    db_run(client, seed)
    response = client.get("/v1/subjects", headers=AUTH)
    assert response.status_code == 200
    names = [item["name"] for item in response.json()["items"]]
    assert names.count("Новая дисциплина из расписания") == 1


def test_old_cabinet_uploads_are_deleted_without_touching_new_tasks(client):
    from datetime import UTC

    make(client, "headman")
    authorize_analysis(client)
    submitted = upload(client, week(), picture()).json()

    async def age_one(db):
        row = await db.get(CabinetScheduleUpload, uuid.UUID(submitted["id"]))
        row.created_at = datetime(2026, 11, 3, 14, 0, tzinfo=UTC)

    db_run(client, age_one)
    listed = client.get("/v1/staff/schedule-upload", headers=AUTH)
    assert listed.status_code == 200
    assert listed.json()["suggestions"] == []
    assert (
        client.get(f"/v1/staff/schedule-upload/{submitted['id']}/image", headers=AUTH).status_code
        == 404
    )

    newer = upload(client, week(), picture()).json()
    assert newer["id"] != submitted["id"]
    assert get_draft(client, newer["id"])["state"] == "queued"


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


def test_exhausted_release_budget_rejects_upload_before_queue(client, monkeypatch):
    from decimal import Decimal

    from studgroup.analysis import release_budget

    make(client, "headman")
    authorize_analysis(client)

    async def exhausted(*args, **kwargs):
        return Decimal("0.15")

    monkeypatch.setattr(release_budget, "spent", exhausted)
    response = upload(client, week(), picture())
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "analysis_budget_closed"
    assert client.get("/v1/staff/schedule-upload", headers=AUTH).json()["suggestions"] == []


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


def test_weekly_replacement_replaces_occurrences_and_preserves_other_weeks(client, monkeypatch):
    from datetime import time

    from studgroup.models import SchedulePattern

    make(client, "headman")
    authorize_analysis(client)
    monday = week()

    async def seed(db):
        group = await db.scalar(select(Group).where(Group.telegram_chat_id == -1001))
        db.add(
            SchedulePattern(
                group_id=group.id,
                subject="Курс, который продолжается",
                weekday=0,
                starts=time(9, 0),
                ends=time(10, 20),
                valid_from=monday,
                valid_until=monday + timedelta(days=20),
                week="all",
                revision=1,
            )
        )

    db_run(client, seed)
    vision = Vision()
    uploaded = upload(client, monday, picture()).json()
    assert advance(client, monkeypatch, vision) == "pass_a_ready"
    assert advance(client, monkeypatch, vision) == "ready"
    draft = get_draft(client, uploaded["id"])
    corrected = [
        {
            **draft["rows"][0],
            "subject": "Новый предмет на одну неделю",
            "starts": "11:00",
            "ends": "12:20",
        }
    ]
    body = {
        "expected_revision": 1,
        "expected_schedule_version": 0,
        "corrected_rows": corrected,
        "targets": [],
        "replace_week": True,
    }
    response = client.post(
        f"/v1/staff/schedule-upload/{uploaded['id']}/confirm", headers=AUTH, json=body
    )
    assert response.status_code == 200, response.text
    assert response.json()["schedule_version"] == 1
    this_monday = client.get(
        f"/v1/staff/schedule?start={monday}&end={monday}", headers=AUTH
    ).json()["schedule"]["lessons"]
    assert any(
        item["subject"] == "Новый предмет на одну неделю" and item["status"] == "scheduled"
        for item in this_monday
    )
    assert not any(
        item["subject"] == "Курс, который продолжается" and item["status"] == "scheduled"
        for item in this_monday
    )
    next_monday = monday + timedelta(days=7)
    following = client.get(
        f"/v1/staff/schedule?start={next_monday}&end={next_monday}", headers=AUTH
    ).json()["schedule"]["lessons"]
    assert any(
        item["subject"] == "Курс, который продолжается" and item["status"] == "scheduled"
        for item in following
    )
    changes = db_run(client, lambda db: db.scalars(select(ScheduleChange)))
    # A confirmed weekly snapshot is one schedule-version change, not one per lesson.
    assert len(changes.all()) == 1
    second = client.post(
        f"/v1/staff/schedule-upload/{uploaded['id']}/confirm", headers=AUTH, json=body
    )
    assert second.status_code == 200
    assert second.json()["schedule_version"] == 1


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


class NoLessonsSecondPassVision(Vision):
    async def complete_vision(self, **kwargs):
        result = await super().complete_vision(**kwargs)
        if len(self.calls) == 2:
            data = json.loads(result["choices"][0]["message"]["content"])
            data["schedule"] = []
            result["choices"][0]["message"]["content"] = json.dumps(data)
        return result


def test_manual_screenshot_uses_focused_timetable_prompt_not_generic_chat_analysis(
    client, monkeypatch
):
    make(client, "headman")
    authorize_analysis(client)
    model = Vision()
    submitted = upload(client, week(), picture()).json()
    assert advance(client, monkeypatch, model) == "pass_a_ready"
    assert advance(client, monkeypatch, model) == "ready"
    assert get_draft(client, submitted["id"])["state"] == "pending"
    assert len(model.calls) == 2
    prompt = model.calls[1]["system_prompt"]
    assert "Прочитай изображение РАСПИСАНИЯ" in prompt
    assert "homework" not in prompt
    assert "ImageEvent" not in prompt
    assert "ScheduleRow" in prompt
    assert model.calls[1]["max_output_tokens"] == 6000


def test_no_lessons_still_fails_without_fabricating_scheduled_classes(client, monkeypatch):
    make(client, "headman")
    authorize_analysis(client)
    model = NoLessonsSecondPassVision()
    submitted = upload(client, week(), picture()).json()
    assert advance(client, monkeypatch, model) == "pass_a_ready"
    assert advance(client, monkeypatch, model) == "failed"
    result = get_draft(client, submitted["id"])
    assert result["progress_stage"] == "failed_no_lessons"
    assert result["rows"] == []
    assert len(model.calls) == 2


def test_personal_manual_retries_allow_multiple_attempts_without_ai_calls(client, monkeypatch):
    from studgroup.models import User

    make(client, "headman")
    authorize_analysis(client)

    async def personal_user_id(db):
        return await db.scalar(
            select(User.telegram_user_id)
            .join(
                CabinetScheduleUpload,
                CabinetScheduleUpload.created_by == User.id,
            )
            .limit(1)
        )

    submitted = upload(client, week(), picture()).json()
    personal_id = db_run(client, personal_user_id)
    assert personal_id is not None
    monkeypatch.setenv("CABINET_UNLIMITED_RETRY_TELEGRAM_ID", str(personal_id))

    async def fail_attempt(db):
        record = await db.get(CabinetScheduleUpload, uuid.UUID(submitted["id"]))
        record.state = "failed"
        record.progress_stage = "failed_unreadable"

    for attempt in range(3):
        db_run(client, fail_attempt)
        failed = get_draft(client, submitted["id"])
        assert failed["can_retry"] is True
        response = client.post(f"/v1/staff/schedule-upload/{submitted['id']}/retry", headers=AUTH)
        assert response.status_code == 202
        assert response.json()["state"] == "queued"
        assert response.json()["can_retry"] is False

    async def retries(db):
        record = await db.get(CabinetScheduleUpload, uuid.UUID(submitted["id"]))
        return record.retry_count

    assert db_run(client, retries) == 3
    monkeypatch.setenv("CABINET_UNLIMITED_RETRY_TELEGRAM_ID", "0")
    db_run(client, fail_attempt)
    assert get_draft(client, submitted["id"])["can_retry"] is False
    response = client.post(f"/v1/staff/schedule-upload/{submitted['id']}/retry", headers=AUTH)
    assert response.status_code == 409


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


class IncompleteFirstPassVision(Vision):
    async def complete_vision(self, **kwargs):
        result = await super().complete_vision(**kwargs)
        if len(self.calls) == 1:
            payload = json.loads(result["choices"][0]["message"]["content"])
            payload["complete"] = False
            payload["schedule"] = []
            payload["semantic_types"] = ["unknown"]
            result["choices"][0]["message"]["content"] = json.dumps(payload)
        return result


class UnreadableFirstPassVision(Vision):
    async def complete_vision(self, **kwargs):
        result = await super().complete_vision(**kwargs)
        payload = json.loads(result["choices"][0]["message"]["content"])
        payload.update(
            complete=False,
            extracted_text="",
            facts=[],
            blocks=[],
            schedule=[],
        )
        result["choices"][0]["message"]["content"] = json.dumps(payload)
        return result


def test_incomplete_but_legible_first_pass_produces_partial_review(client, monkeypatch):
    make(client, "headman")
    authorize_analysis(client)
    model = IncompleteFirstPassVision()
    submitted = upload(client, week(), picture()).json()
    assert advance(client, monkeypatch, model) == "pass_a_ready"
    checkpoint = get_draft(client, submitted["id"])
    assert checkpoint["progress_percent"] == 50
    assert checkpoint["state"] == "queued"
    assert advance(client, monkeypatch, model) == "ready"
    result = get_draft(client, submitted["id"])
    assert result["state"] == "pending"
    assert result["progress_percent"] == 100
    assert result["progress_stage"] == "ready_partial"
    assert len(result["rows"]) == 1
    assert len(model.calls) == 2


def test_unreadable_first_pass_stops_without_second_paid_call(client, monkeypatch):
    make(client, "headman")
    authorize_analysis(client)
    model = UnreadableFirstPassVision()
    submitted = upload(client, week(), picture()).json()
    assert advance(client, monkeypatch, model) == "failed"
    result = get_draft(client, submitted["id"])
    assert result["progress_stage"] == "failed_unreadable"
    assert result["progress_percent"] == 20
    assert len(model.calls) == 1


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
    retry = client.post(f"/v1/staff/schedule-upload/{submitted['id']}/retry", headers=AUTH)
    assert retry.status_code == 202
    assert retry.json()["progress_stage"] == "queued_b"
    # First successful, charged visual pass stays checkpointed: retry B only.
    assert advance(client, monkeypatch, Vision()) == "ready"
    assert get_draft(client, submitted["id"])["state"] == "pending"
