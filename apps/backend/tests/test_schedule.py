from datetime import date, time

import pytest

from studgroup.schedule import LessonPattern, expand, week_kind


def pattern(**changes):
    fields = {
        "id": "lesson",
        "subject": "Математика",
        "weekday": 0,
        "starts": time(9),
        "ends": time(10, 30),
        "valid_from": date(2026, 9, 1),
        "valid_until": date(2027, 1, 31),
    }
    return LessonPattern(**(fields | changes))


def test_parity_uses_anchor_and_survives_new_year():
    anchor = date(2026, 12, 28)
    assert week_kind(date(2027, 1, 3), anchor) == "first"
    assert week_kind(date(2027, 1, 4), anchor) == "second"
    assert week_kind(date(2026, 12, 21), anchor) == "second"


def test_alternating_weeks_and_timezone():
    result = expand(
        pattern(week="first"),
        date(2026, 10, 5),
        date(2026, 10, 19),
        "Europe/Moscow",
        date(2026, 10, 5),
    )
    assert len(result) == 2
    assert result[0]["starts_at"] == "2026-10-05T09:00:00+03:00"
    assert result[1]["id"] == "lesson:2026-10-19"


def test_never_invents_parity():
    with pytest.raises(ValueError, match="anchor"):
        expand(pattern(week="first"), date(2026, 10, 5), date(2026, 10, 5), "UTC", None)


def test_semester_boundary():
    assert (
        expand(
            pattern(valid_until=date(2026, 10, 4)),
            date(2026, 10, 5),
            date(2026, 10, 12),
            "UTC",
            None,
        )
        == []
    )


def test_invalid_range_and_lesson():
    with pytest.raises(ValueError):
        pattern(ends=time(8))
    with pytest.raises(ValueError):
        expand(pattern(), date(2026, 10, 1), date(2026, 12, 1), "UTC", None)
