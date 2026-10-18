"""Timetable-only subject resolution: hashtag hint, running lesson, honest abstention."""

from datetime import UTC, date, datetime, time

from studgroup.analysis.subject_resolver import (
    Lesson,
    hashtag_subject,
    resolve_card_subject,
    schedule_subject,
)

TZ = "Europe/Moscow"
NAMES = [
    "Математический анализ",
    "Линейная алгебра и аналитическая геометрия",
    "История России",
    "Основы информатики",
    "Основы российской государственности",
    "Иностранный язык",
]
LESSONS = [
    Lesson(2, time(10, 40), time(12, 0), "Математический анализ"),  # Wednesday
    Lesson(2, time(12, 20), time(13, 40), "Линейная алгебра и аналитическая геометрия"),
]


def at(hour, minute, day=14):  # 2026-10-14 is a Wednesday; Moscow = UTC+3
    return datetime(2026, 10, day, hour - 3, minute, tzinfo=UTC)


def test_hashtag_abbreviations_map_to_exactly_one_timetable_subject():
    assert hashtag_subject("дз #матан стр 3", NAMES) == "Математический анализ"
    assert hashtag_subject("#линал номер 5", NAMES) == "Линейная алгебра и аналитическая геометрия"
    assert hashtag_subject("#история повторить", NAMES) == "История России"
    assert hashtag_subject("#информатика", NAMES) == "Основы информатики"
    assert hashtag_subject("#дз всем", NAMES) is None
    assert hashtag_subject("#неизвестное", NAMES) is None


def test_two_different_hashtags_are_ambiguous():
    assert hashtag_subject("#матан #история", NAMES) is None


def test_schedule_picks_running_lesson_or_one_that_just_ended():
    assert schedule_subject(at(11, 0), LESSONS, timezone=TZ) == "Математический анализ"
    assert schedule_subject(at(12, 10), LESSONS, timezone=TZ) == "Математический анализ"  # break
    assert (
        schedule_subject(at(14, 0), LESSONS, timezone=TZ)
        == "Линейная алгебра и аналитическая геометрия"
    )
    assert schedule_subject(at(20, 0), LESSONS, timezone=TZ) is None  # evening: no guess
    assert schedule_subject(at(11, 0, day=15), LESSONS, timezone=TZ) is None  # Thursday: none


def test_hashtag_wins_over_schedule_and_never_leaves_the_catalog():
    result = resolve_card_subject([(at(11, 0), "#история прочитать")], LESSONS, NAMES, timezone=TZ)
    assert (result.subject, result.source) == ("История России", "hashtag")


def test_card_with_split_schedule_votes_gets_no_subject():
    result = resolve_card_subject([(at(11, 0), "a"), (at(13, 0), "b")], LESSONS, NAMES, timezone=TZ)
    assert result.subject is None


def test_card_majority_by_schedule_and_coverage_window():
    messages = [(at(11, 0), "a"), (at(11, 30), "b"), (at(20, 0), "c")]
    won = resolve_card_subject(messages, LESSONS, NAMES, timezone=TZ)
    assert (won.subject, won.source) == ("Математический анализ", "schedule")
    outside = resolve_card_subject(
        messages, LESSONS, NAMES, timezone=TZ, covered_from=date(2026, 10, 19)
    )
    assert outside.subject is None


def test_alternating_weeks_use_the_matching_week_pattern():
    lessons = [
        Lesson(2, time(9, 0), time(10, 20), "Основы информатики", week="first"),
        Lesson(2, time(10, 40), time(12, 0), "Математический анализ", week="second"),
    ]
    anchor = date(2026, 10, 5)  # Monday of a "first" week
    first_wed = datetime(2026, 10, 7, 6, 30, tzinfo=UTC)  # 09:30 Moscow, first week
    second_wed = datetime(2026, 10, 14, 8, 0, tzinfo=UTC)  # 11:00 Moscow, second week
    third_wed = datetime(2026, 10, 21, 6, 30, tzinfo=UTC)  # 09:30 Moscow, first week again
    kwargs = {"timezone": TZ, "first_week_anchor": anchor}
    assert schedule_subject(first_wed, lessons, **kwargs) == "Основы информатики"
    assert schedule_subject(second_wed, lessons, **kwargs) == "Математический анализ"
    assert schedule_subject(third_wed, lessons, **kwargs) == "Основы информатики"
    assert (
        schedule_subject(second_wed, lessons, timezone=TZ, first_week_anchor=date(2026, 10, 12))
        is None
    )
