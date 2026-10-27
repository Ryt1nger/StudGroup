"""Owner-approved calendar retention: end date + seven complete local calendar days."""

import hashlib
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from studgroup.api import ApiError


def payload_hash(payload_json):
    return hashlib.sha256(payload_json.encode()).hexdigest()


def retention_end(payload, timezone):
    zone = ZoneInfo(timezone)
    start = payload.starts_at.astimezone(zone).date() if payload.starts_at else payload.starts_on
    finish = payload.ends_at.astimezone(zone).date() if payload.ends_at else payload.ends_on
    if start is None:
        raise ApiError("event_date_required", "Уточните дату начала мероприятия", 409)
    if payload.starts_on and payload.starts_at and payload.starts_on != start:
        raise ApiError("event_dates_ambiguous", "Дата и время начала противоречат друг другу", 409)
    if payload.ends_on and payload.ends_at and payload.ends_on != finish:
        raise ApiError("event_dates_ambiguous", "Дата и время окончания противоречат друг другу", 409)
    if payload.multi_day and (finish is None or finish <= start):
        raise ApiError("event_end_required", "Уточните дату окончания многодневного мероприятия", 409)
    finish = finish or start
    if finish < start:
        raise ApiError("event_date_order", "Окончание раньше начала мероприятия", 409)
    # Exclusive next midnight: event on Dec 20 is retained throughout Dec 27, deleted Dec 28.
    expires = datetime.combine(finish + timedelta(days=8), time.min, zone).astimezone(UTC)
    return finish, expires


def approved_snapshot(event):
    return bool(event.published_at and event.event_end_date and event.approved_payload_sha256
                and event.approved_payload_sha256 == payload_hash(event.payload_json)
                and event.manual_lock)
