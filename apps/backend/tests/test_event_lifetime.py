import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from test_group_events import prepare, put
from test_image_jobs import stored
from test_staff_api import AUTH, make

from studgroup.analysis.event_lifetime import retention_end
from studgroup.analysis.group_events import purge_events
from studgroup.analysis.image_contracts import ImageEvent
from studgroup.api import ApiError
from studgroup.models import (
    GroupEvent,
    GroupEventRevision,
    GroupEventSource,
    ImageAnalysisJob,
    MessageAttachment,
    RawMessage,
)

pytest_plugins = ["test_schedule_api"]


@pytest.mark.parametrize(
    "values,zone,expected",
    [
        ({"starts_on": "2026-12-20"}, "Europe/Moscow", "2026-12-27T21:00:00+00:00"),
        ({"starts_at": "2026-12-20T18:00:00+03:00"}, "Europe/Moscow", "2026-12-27T21:00:00+00:00"),
        (
            {"starts_on": "2026-12-20", "ends_on": "2026-12-22", "multi_day": True},
            "Europe/Moscow",
            "2026-12-29T21:00:00+00:00",
        ),
        (
            {"starts_at": "2026-12-20T23:00:00Z", "ends_at": "2026-12-22T23:00:00Z"},
            "Europe/Moscow",
            "2026-12-30T21:00:00+00:00",
        ),
        ({"starts_on": "2026-10-31"}, "America/New_York", "2026-11-08T05:00:00+00:00"),
    ],
)
def test_calendar_retention_is_inclusive_and_timezone_aware(values, zone, expected):
    import json

    event = ImageEvent.model_validate_json(json.dumps({"title": "Событие", **values}))
    assert retention_end(event, zone)[1] == datetime.fromisoformat(expected)


def test_unknown_and_ambiguous_dates_never_publish():
    import json

    for data in (
        {},
        {"starts_on": "2026-12-20", "multi_day": True},
        {"starts_at": "2026-12-20T23:00:00Z", "starts_on": "2026-12-20"},
    ):
        event = ImageEvent.model_validate_json(json.dumps({"title": "Событие", **data}))
        with pytest.raises(ApiError):
            retention_end(event, "Europe/Moscow")
    with pytest.raises(ValidationError):
        ImageEvent.model_validate_json(
            '{"title":"Событие","starts_on":"2026-12-20","ends_on":"2026-12-19"}'
        )


def test_published_event_survives_private_source_expiry_and_remains_readable(client, monkeypatch):
    event, payload = prepare(client, monkeypatch)
    make(client, "headman")
    client.app.state.settings.image_event_publication_enabled = True
    payload |= {"starts_at": "2026-12-20T18:00:00+03:00", "when_text": "20 декабря 2026"}
    response = put(client, event, payload, "active")
    assert response.status_code == 200, response.text
    record = stored(client, GroupEvent)[0]
    assert record.delete_at.replace(tzinfo=UTC) == datetime(2026, 12, 27, 21, tzinfo=UTC)

    async def expire_sources():
        async with AsyncSession(client.app.state.engine) as db:
            cutoff = datetime.now(UTC) - timedelta(minutes=1)
            for cls in (RawMessage, MessageAttachment, ImageAnalysisJob):
                for row in (await db.scalars(select(cls))).all():
                    row.delete_at = cutoff
            await purge_events(db, datetime.now(UTC))
            await db.commit()

    asyncio.run(expire_sources())
    assert stored(client, GroupEventSource) == []
    assert stored(client, GroupEventRevision)
    result = client.get(f"/v1/group-events/{event.id}", headers=AUTH)
    assert result.status_code == 200
    assert result.json()["source_availability"] == "expired"
    assert len(client.get("/v1/group-events", headers=AUTH).json()["events"]) == 1
    # An authorized editor can reschedule the approved record without restoring private data.
    result = put(
        client, record, payload | {"starts_at": "2027-02-01T18:00:00+03:00"}, "active", revision=response.json()["revision"]
    )
    assert result.status_code == 200, result.text
    assert stored(client, GroupEvent)[0].delete_at.replace(tzinfo=UTC) == datetime(
        2027, 2, 8, 21, tzinfo=UTC
    )
    assert stored(client, GroupEventSource) == []


def test_date_only_event_and_cancelled_record_are_retained_through_seventh_day(client, monkeypatch):
    event, payload = prepare(client, monkeypatch)
    make(client, "headman")
    client.app.state.settings.image_event_publication_enabled = True
    payload |= {"starts_at": None, "starts_on": "2026-12-20", "when_text": "20 декабря 2026"}
    result = put(client, event, payload, "active")
    assert result.status_code == 200, result.text
    result = put(client, event, payload, "cancelled", revision=result.json()["revision"])
    assert result.status_code == 200
    assert client.get("/v1/group-events", headers=AUTH).json()["events"] == []
    assert client.get(f"/v1/group-events/{event.id}", headers=AUTH).json()["state"] == "cancelled"

    async def boundary():
        async with AsyncSession(client.app.state.engine) as db:
            assert await purge_events(db, datetime(2026, 12, 27, 20, 59, 59, tzinfo=UTC)) == 0
            assert await purge_events(db, datetime(2026, 12, 27, 21, tzinfo=UTC)) == 1
            await db.commit()

    asyncio.run(boundary())
    assert stored(client, GroupEvent) == []
