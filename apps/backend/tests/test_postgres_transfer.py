from datetime import UTC, datetime

from studgroup.postgres_transfer import utc_values


def test_sqlite_timestamps_are_explicit_utc_before_asyncpg_encoding():
    stamp = datetime(2026, 10, 7, 0, 0, tzinfo=UTC).replace(tzinfo=None)
    row = utc_values({"deadline_at": stamp, "description": "unchanged", "optional": None})
    assert row["deadline_at"] == stamp.replace(tzinfo=UTC)
    assert row["description"] == "unchanged"
    assert row["optional"] is None
