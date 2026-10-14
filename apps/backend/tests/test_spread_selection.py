"""Spread selection: whole window, every day, rotating topics, deterministic, no keywords."""

from datetime import UTC, datetime, timedelta
from itertools import pairwise
from types import SimpleNamespace

from studgroup.analysis.backfill import select_spread

START = datetime(2026, 10, 8, 16, 0, tzinfo=UTC)


def message(number, day, minute, thread=None):
    stamp = START + timedelta(days=day, minutes=minute)
    return SimpleNamespace(
        telegram_message_id=number, message_date=stamp, message_thread_id=thread, text=f"t{number}"
    )


def window():
    rows, number = [], 1
    volume = {0: 60, 1: 30, 2: 10, 3: 100, 4: 5, 5: 45, 6: 50}
    for day, count in volume.items():
        for index in range(count):
            thread = (None, 241, 2365, 591)[index % 4]
            rows.append(message(number, day, index * 3, thread))
            number += 1
    return rows


def test_every_day_is_covered_and_volume_weighted():
    rows = window()
    picked = select_spread(rows, 25)
    assert len(picked) == len({row.telegram_message_id for row in picked}) == 25
    days = {row.message_date.date() for row in picked}
    assert len(days) == 7  # all days, including the quiet ones
    per_day = {day: sum(1 for row in picked if row.message_date.date() == day) for day in days}
    busiest = max(per_day, key=per_day.get)
    assert busiest == (START + timedelta(days=3)).date()  # the 100-message day gets most slots
    assert per_day[(START + timedelta(days=4)).date()] >= 1  # even the 5-message day


def test_topics_rotate_and_neighbours_are_avoided():
    picked = select_spread(window(), 25)
    assert len({row.message_thread_id for row in picked}) == 4
    ordered = sorted(row.telegram_message_id for row in picked)
    gaps = [b - a for a, b in pairwise(ordered)]
    assert min(gaps) >= 2  # no two adjacent messages while the window is large


def test_selection_is_deterministic_and_not_keyword_driven():
    rows = window()
    assert [r.telegram_message_id for r in select_spread(rows, 25)] == [
        r.telegram_message_id for r in select_spread(list(reversed(rows)), 25)
    ]
    for row in rows:  # texts carry no task words at all, yet the sample is full
        row.text = "ок"
    assert len(select_spread(rows, 25)) == 25


def test_small_windows_and_fewer_days_than_slots():
    rows = window()
    assert len(select_spread(rows[:10], 25)) == 10  # fewer messages than slots: all of them
    assert select_spread(rows, 0) == []
    few = select_spread(rows, 3)  # fewer slots than days: evenly spaced days, one each
    assert len(few) == 3 and len({row.message_date.date() for row in few}) == 3
    one_day = [row for row in rows if row.message_date.date() == START.date()]
    assert len(select_spread(one_day, 5)) == 5
