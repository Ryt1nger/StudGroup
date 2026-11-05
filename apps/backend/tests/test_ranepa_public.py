"""Validate visible-card extraction against representative RANEPA page structure."""

from datetime import date

import pytest

from studgroup.ranepa_probe import SourceAccessError
from studgroup.ranepa_public import extract_public_schedule

MON = date(2026, 11, 2)
SUN = date(2026, 11, 8)
CODE = "Э-ОБ-385-04-26-02"


def page(body):
    return f"<html><body><h1>{CODE}</h1>{body}</body></html>"


def card(room, name, kind, teacher):
    return "".join(
        f"<div>{value}</div>"
        for value in ["09 : 00 - 10 : 20", room, name, kind, teacher, CODE]
    )


def test_extract_two_parallel_lessons_and_deduplicate_identical_cards():
    html = page(
        "<h2>11/2/2026 Понедельник</h2>"
        + card("1 - 303", "Иностранный язык", "Практические занятия", "Иванова Мария")
        + card("1 - 304", "Иностранный язык", "Практические занятия", "Петров Иван")
    )
    result = extract_public_schedule(html, CODE, MON, SUN)
    assert len(result.lessons) == 2
    assert len(result.indexed()) == 2
    assert {r.teacher for r in result.lessons} == {"Иванова Мария", "Петров Иван"}
    assert result.lessons[0].room == "1 - 303"
    assert result.lessons[0].day == MON


def test_rejects_foreign_group():
    html = "<html><h2>Чужая группа</h2></html>"
    with pytest.raises(SourceAccessError, match="group_identity_not_confirmed"):
        extract_public_schedule(html, CODE, MON, SUN)


def test_does_not_invent_rows_from_timeline_without_lesson_type():
    html = page("<h2>11/2/2026 Понедельник</h2><div>08:00</div><div>09:00</div>")
    assert not extract_public_schedule(html, CODE, MON, SUN).lessons


def test_rejects_malformed_lesson_time():
    html = page(
        "<h2>11/2/2026 Понедельник</h2>"
        + card("СДО", "Философия", "Лекционные занятия", "Иванов Иван").replace(
            "09 : 00 - 10 : 20", "20 : 00 - 12 : 00"
        )
    )
    with pytest.raises(SourceAccessError, match="invalid_source_lesson"):
        extract_public_schedule(html, CODE, MON, SUN)


def test_out_of_range_day_is_ignored():
    html = page(
        "<h2>11/9/2026 Понедельник</h2>"
        + card("СДО", "Философия", "Лекционные занятия", "Иванов Иван")
    )
    assert not extract_public_schedule(html, CODE, MON, SUN).lessons
