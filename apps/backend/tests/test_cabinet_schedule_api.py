"""Isolated cabinet upload tests; model is faked and no provider HTTP is made."""

import io
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from PIL import Image
from sqlalchemy import select
from test_staff_api import AUTH, db_run, make

from studgroup.models import Group, GroupAnalysisSettings, ScheduleChange

pytest_plugins = ["test_schedule_api"]


def picture():
    out = io.BytesIO()
    Image.new("RGB", (220, 160)).save(out, format="PNG")
    return out.getvalue()


class Vision:
    def __init__(self):
        self.calls = []

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
                                    "coverage": "full_week",
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
