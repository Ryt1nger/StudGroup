"""Timetable-only subject resolution: hashtag hint, running lesson, honest abstention."""

from datetime import UTC, date, datetime, time
from types import SimpleNamespace

from studgroup.analysis.subject_resolver import (
    Lesson,
    hashtag_subject,
    in_session_last_resort,
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


def test_a_midweek_anchor_counts_whole_monday_weeks_like_the_schedule_screen():
    lessons = [Lesson(0, time(9, 0), time(10, 20), "Основы информатики", week="second")]
    anchor = date(2026, 10, 7)  # Wednesday of a "first" week
    next_monday = datetime(2026, 10, 12, 6, 30, tzinfo=UTC)  # 09:30 Moscow, second week
    assert (
        schedule_subject(next_monday, lessons, timezone=TZ, first_week_anchor=anchor)
        == "Основы информатики"
    )


def _source(mid, hour, minute=0, *, day=14):
    return SimpleNamespace(telegram_message_id=mid, message_date=at(hour, minute, day))


def _evidence(mid, paths=("description",)):
    return SimpleNamespace(telegram_message_id=mid, fact_paths=list(paths))


def _calendar(*rows, ready=True):
    return {
        "week_state": "ready" if ready else "needs_clarification",
        "lessons": [
            {
                "subject": subject,
                "status": status,
                "starts_at": datetime(2026, 10, 14, begin - 3, 0, tzinfo=UTC).isoformat(),
                "ends_at": datetime(2026, 10, 14, finish - 3, 0, tzinfo=UTC).isoformat(),
            }
            for subject, begin, finish, status in rows
        ],
    }


def _last_subject(messages, evidence, calendars):
    return in_session_last_resort(messages, evidence, calendars, timezone=TZ).subject


def test_last_resort_current_lesson_is_tentative_only_from_task_evidence():
    cal = {date(2026, 10, 14): _calendar(("Матан", 10, 12, "scheduled"))}
    result = in_session_last_resort([_source(1, 11)], [_evidence(1)], cal, timezone=TZ)
    assert (result.subject, result.source) == ("Матан", "schedule_tentative")
    assert _last_subject([_source(1, 11)], [_evidence(1, ("deadline",))], cal) is None


def test_last_resort_does_not_use_break_after_class_or_other_day():
    cal = {date(2026, 10, 14): _calendar(("Матан", 10, 12, "scheduled"))}
    assert _last_subject([_source(1, 12, 0)], [_evidence(1)], cal) is None
    assert _last_subject([_source(1, 12, 15)], [_evidence(1)], cal) is None
    assert _last_subject([_source(1, 11, day=15)], [_evidence(1)], cal) is None


def test_last_resort_respects_cancellation_reschedule_and_unknown_week():
    day = date(2026, 10, 14)
    messages = [_source(1, 11)]
    refs = [_evidence(1)]
    cancelled = {day: _calendar(("Матан", 10, 12, "cancelled"))}
    assert _last_subject(messages, refs, cancelled) is None
    moved = {day: _calendar(("Физика", 10, 12, "scheduled"))}
    assert _last_subject(messages, refs, moved) == "Физика"
    unresolved = {day: _calendar(("Матан", 10, 12, "scheduled"), ready=False)}
    assert _last_subject(messages, refs, unresolved) is None


def test_last_resort_abstains_on_parallel_subgroups_and_conflicting_messages():
    day = date(2026, 10, 14)
    simultaneous = {
        day: _calendar(
            ("Матан", 10, 12, "scheduled"),
            ("Иностранный язык", 10, 12, "scheduled"),
        )
    }
    assert _last_subject([_source(1, 11)], [_evidence(1)], simultaneous) is None
    distinct = {day: _calendar(("Матан", 10, 12, "scheduled"), ("Физика", 12, 14, "scheduled"))}
    messages = [_source(1, 11), _source(2, 13)]
    assert _last_subject(messages, [_evidence(1), _evidence(2)], distinct) is None
    # A later message giving only the deadline must not vote for its own lesson.
    refs = [_evidence(1), _evidence(2, ("deadline",))]
    assert _last_subject(messages, refs, distinct) == "Матан"
