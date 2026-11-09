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
        f"<div>{value}</div>" for value in ["09 : 00 - 10 : 20", room, name, kind, teacher, CODE]
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


def test_calendar_dates_and_weekday_must_agree():
    html = page(
        "<h2>03.11.2026 Вторник</h2>" + card("305", "Алгебра", "Лекционные занятия", "Иванов")
    )
    assert extract_public_schedule(html, CODE, MON, SUN).lessons[0].day == date(2026, 11, 3)
    with pytest.raises(SourceAccessError, match="invalid_source_day"):
        extract_public_schedule(html.replace("Вторник", "Среда"), CODE, MON, SUN)
    with pytest.raises(SourceAccessError, match="invalid_source_day"):
        extract_public_schedule(html.replace("03.11.2026", "31.11.2026"), CODE, MON, SUN)


def test_repeated_card_is_one_lesson_and_parallel_subgroups_survive():
    first = card("305", "Язык", "Практические занятия", "Иванова") + "<div>Подгруппа 1</div>"
    second = card("306", "Язык", "Практические занятия", "Петров") + "<div>Подгруппа 2</div>"
    result = extract_public_schedule(
        page("<h2>11/2/2026 Понедельник</h2>" + first + second + first), CODE, MON, SUN
    )
    assert len(result.lessons) == 2
    assert {row.subgroup_hint for row in result.lessons} == {"Подгруппа 1", "Подгруппа 2"}


def test_partial_card_cannot_borrow_fields_from_next_card():
    partial = "<div>09:00-10:20</div><div>305</div><div>Язык</div><div>Практические занятия</div>"
    complete = card("306", "История", "Лекционные занятия", "Петров")
    with pytest.raises(SourceAccessError, match="incomplete_source_lesson"):
        extract_public_schedule(
            page("<h2>11/2/2026 Понедельник</h2>" + partial + complete), CODE, MON, SUN
        )


def test_card_with_other_group_is_rejected():
    html = page(
        "<h2>11/2/2026 Понедельник</h2>"
        + card("305", "Язык", "Практические занятия", "Иванова").replace(CODE, "Э-ОБ-385-04-26-03")
    )
    with pytest.raises(SourceAccessError, match="group_identity_not_confirmed"):
        extract_public_schedule(html, CODE, MON, SUN)


def test_captcha_and_hidden_script_group_do_not_pass():
    with pytest.raises(SourceAccessError, match="captcha_required"):
        extract_public_schedule(page("<div>reCAPTCHA</div>"), CODE, MON, SUN)
    with pytest.raises(SourceAccessError, match="group_identity_not_confirmed"):
        extract_public_schedule(f"<script>{CODE}</script><body>Вход</body>", CODE, MON, SUN)
