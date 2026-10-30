"""One approved event entity feeds both chronological lists and local-date calendar intervals."""

import asyncio
import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from test_group_events import prepare, put
from test_image_jobs import stored
from test_staff_api import AUTH, make

from studgroup.models import Group, ImageAnalysisCall, RawMessage

pytest_plugins = ["test_schedule_api"]


def published(client, monkeypatch, **patch):
    event, payload = prepare(client, monkeypatch)
    make(client, "headman")
    client.app.state.settings.image_event_publication_enabled = True
    payload.update(patch)
    response = put(client, event, payload, "active")
    assert response.status_code == 200, response.text
    return event, payload, response.json()


def listing(client, **params):
    result = client.get("/v1/group-events", headers=AUTH, params=params)
    assert result.status_code == 200, result.text
    return result.json()["events"]


def test_calendar_uses_group_day_and_inclusive_multi_day_overlap(client, monkeypatch):
    start = (datetime.now(UTC) + timedelta(days=4)).date()
    finish = start + timedelta(days=2)
    event, _payload, _ = published(
        client,
        monkeypatch,
        starts_at=f"{start.isoformat()}T23:30:00-02:00",
        ends_at=f"{finish.isoformat()}T01:30:00+03:00",
        multi_day=True,
    )
    # 23:30 at UTC-2 is the following date in Moscow.
    assert listing(client, start=str(start), end=str(start)) == []
    for day in [start + timedelta(days=1), finish]:
        assert [item["id"] for item in listing(client, start=str(day), end=str(day))] == [
            str(event.id)
        ]
    assert (
        listing(client, start=str(finish + timedelta(days=1)), end=str(finish + timedelta(days=1)))
        == []
    )


def test_move_and_cancel_update_same_record_and_history_without_ai(client, monkeypatch):
    first = (datetime.now(UTC) + timedelta(days=5)).date()
    second = first + timedelta(days=2)
    event, payload, current = published(client, monkeypatch, starts_at=None, starts_on=str(first))
    calls = len(stored(client, ImageAnalysisCall))
    assert current["source_url"].endswith("/31")
    assert not current["rescheduled"]
    moved = put(client, event, payload | {"starts_on": str(second)}, "active", current["revision"])
    assert moved.status_code == 200, moved.text
    assert moved.json()["id"] == current["id"] and moved.json()["rescheduled"]
    assert listing(client, start=str(first), end=str(first)) == []
    assert len(listing(client, start=str(second), end=str(second))) == 1
    cancelled = put(
        client, event, payload | {"starts_on": str(second)}, "cancelled", moved.json()["revision"]
    )
    assert cancelled.status_code == 200
    assert listing(client) == []
    assert listing(client, status="cancelled")[0]["id"] == str(event.id)
    assert listing(client, status="all")[0]["state"] == "cancelled"
    assert client.get(f"/v1/group-events/{event.id}", headers=AUTH).status_code == 200
    assert len(stored(client, ImageAnalysisCall)) == calls


def test_expired_source_link_disappears_while_approved_event_survives(client, monkeypatch):
    event, _, _ = published(client, monkeypatch)

    async def expire():
        async with AsyncSession(client.app.state.engine) as db:
            raw = await db.scalar(select(RawMessage).where(RawMessage.telegram_message_id == 31))
            raw.delete_at = datetime.now(UTC) - timedelta(seconds=1)
            await db.commit()

    asyncio.run(expire())
    item = listing(client)[0]
    assert item["id"] == str(event.id)
    assert item["source_url"] is None and item["source_availability"] == "expired"


@pytest.mark.parametrize(
    "params",
    [
        {"start": "2026-10-01"},
        {"end": "2026-10-01"},
        {"start": "2026-10-02", "end": "2026-10-01"},
        {"start": "2026-01-01", "end": "2027-01-02"},
        {"status": "draft"},
    ],
)
def test_calendar_rejects_invalid_interval_or_private_status(client, monkeypatch, params):
    published(client, monkeypatch)
    assert client.get("/v1/group-events", headers=AUTH, params=params).status_code == 422


def test_capabilities_follow_group_flags_and_staff_role(client):
    from studgroup.api import group_features
    from studgroup.models import Membership

    async def check():
        async with AsyncSession(client.app.state.engine) as db:
            group = await db.scalar(select(Group).where(Group.telegram_chat_id == -1001))
            member = await db.scalar(select(Membership).where(Membership.group_id == group.id))
            settings = client.app.state.settings
            assert group_features(settings, group, member) == {
                "events": True,
                "event_review": False,
                "schedule_review": False,
            }
            settings.image_analysis_groups = [str(group.id)]
            settings.image_event_publication_enabled = settings.image_event_review_enabled = True
            settings.image_schedule_review_enabled = True
            assert group_features(settings, group, member) == {
                "events": True,
                "event_review": False,
                "schedule_review": False,
            }
            member.role = "headman"
            assert all(group_features(settings, group, member).values())
            settings.image_analysis_groups = []
            assert group_features(settings, group, member) == {
                "events": True,
                "event_review": False,
                "schedule_review": False,
            }

    asyncio.run(check())


def test_midnight_end_does_not_add_an_empty_extra_calendar_day(client, monkeypatch):
    first = (datetime.now(UTC) + timedelta(days=4)).date()
    last = first + timedelta(days=1)
    event, _, _ = published(
        client,
        monkeypatch,
        starts_at=f"{first}T20:00:00+03:00",
        ends_at=f"{last}T00:00:00+03:00",
        multi_day=True,
    )
    assert listing(client, start=str(first), end=str(first))[0]["id"] == str(event.id)
    assert listing(client, start=str(last), end=str(last)) == []


def test_repeated_poster_does_not_create_another_public_event(client, monkeypatch):
    from test_processing import source

    from studgroup.analysis.group_events import capture_event
    from studgroup.analysis.image_contracts import ImageEvent
    from studgroup.models import GroupEvent, GroupEventSource

    event, payload, current = published(client, monkeypatch)
    source(client, mid=32, text="Повтор афиши")

    async def repeat():
        async with AsyncSession(client.app.state.engine) as db:
            raw = await db.scalar(select(RawMessage).where(RawMessage.telegram_message_id == 32))
            await capture_event(
                db,
                raw.group_id,
                raw,
                ImageEvent.model_validate_json(json.dumps(payload)),
                datetime.now(UTC),
            )
            await db.commit()

    asyncio.run(repeat())
    assert len(stored(client, GroupEvent)) == 1
    assert len(stored(client, GroupEventSource)) == 2
    assert listing(client)[0]["revision"] == current["revision"]
    assert listing(client)[0]["id"] == str(event.id)
