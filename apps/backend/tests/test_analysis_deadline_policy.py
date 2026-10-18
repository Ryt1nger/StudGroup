"""Acceptance criteria of the Intelligent Deadline Spec v1.0 for the offline deadline policy."""

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from studgroup.analysis.deadline_policy import (
    archive_instant,
    decide_deadline,
    staff_note,
    student_label,
)
from studgroup.deadlines import ScheduleDeadlineContext

TZ = "Europe/Moscow"
MOSCOW = ZoneInfo(TZ)
MATAN = "Математический анализ"


def msk(day, hour=12, minute=0):
    return datetime(2026, 10, day, hour, minute, tzinfo=MOSCOW).astimezone(UTC)


def lesson(day, hour, subject=MATAN, status="scheduled"):
    return {
        "subject": subject,
        "starts_at": datetime(2026, 10, day, hour, 40, tzinfo=MOSCOW).isoformat(),
        "status": status,
    }


def schedule(*lessons, ready=True, start=date(2026, 10, 5), end=date(2026, 10, 25)):
    return ScheduleDeadlineContext(
        lessons=list(lessons), covered_from=start, covered_until=end, ready=ready
    )


def homework(text, sent, **kwargs):
    return decide_deadline(
        kind="homework", sources=[(text, sent)], timezone=TZ, subject=MATAN, **kwargs
    )


WEEK = schedule(lesson(14, 10), lesson(15, 12), lesson(21, 10))  # 14 and 21 Oct are Wednesdays


def test_explicit_date_is_never_replaced_by_the_timetable():
    result = homework("сдать до 20 октября", msk(13), schedule=WEEK)
    assert (result.basis, result.at.date()) == ("explicit", date(2026, 10, 20))


def test_relative_date_is_computed_from_the_source_timestamp_and_is_repeatable():
    first = homework("сделать к завтра", msk(13), schedule=WEEK)
    again = homework("сделать к завтра", msk(13), schedule=WEEK)
    assert first == again
    assert (first.basis, first.at.date()) == ("relative_resolved", date(2026, 10, 14))
    assert homework(
        "сделать к завтра", msk(13) + timedelta(days=7), schedule=WEEK
    ).at.date() == date(2026, 10, 21)


def test_conversational_clock_and_exercise_numbers():
    thursday = homework("до 12.00 в четверг прислать", msk(13), schedule=WEEK)
    assert (thursday.at.astimezone(MOSCOW).hour, thursday.at.astimezone(MOSCOW).minute) == (12, 0)
    exercise = homework("номер 12.5 на стр 43", msk(13), schedule=schedule(lesson(14, 10)))
    assert exercise.basis == "schedule_inferred"  # 12.5 is an exercise, not a time or a date


def test_ambiguous_range_stays_unknown_even_with_a_timetable():
    result = homework("сдать 12.10-15.10", msk(13), schedule=WEEK)
    assert (result.at, result.basis, result.reason) == (None, "unknown", "ambiguous_date_range")


def test_homework_without_date_gets_the_next_lesson_and_is_marked_inferred():
    result = homework("номер 5 на стр 43", msk(13), schedule=WEEK)
    assert result.basis == "schedule_inferred" and result.reason == "next_subject_lesson"
    assert result.at.astimezone(MOSCOW).date() == date(2026, 10, 14)
    assert student_label(result) == "К следующему занятию"
    assert "выведена по расписанию" in staff_note(result)


def test_a_lesson_on_the_same_calendar_day_is_excluded():
    week = schedule(lesson(13, 15), lesson(14, 10))  # a later lesson the SAME day as the message
    result = homework("номер 5 на стр 43", msk(13, 9), schedule=week)
    assert result.at.astimezone(MOSCOW).date() == date(2026, 10, 14)


@pytest.mark.parametrize(
    ("ctx", "reason"),
    [
        (None, "schedule_unavailable"),
        (schedule(lesson(14, 10), ready=False), "schedule_unavailable"),
        (schedule(lesson(14, 10), start=date(2026, 10, 14)), "schedule_does_not_cover_source_date"),
        (schedule(lesson(14, 10, status="cancelled")), "cancelled_lesson_in_window"),
        (schedule(lesson(15, 10, subject="История России")), "next_subject_lesson_unknown"),
        (schedule(lesson(30, 10), end=date(2026, 10, 25)), "next_subject_lesson_unknown"),
    ],
)
def test_unreliable_or_missing_timetable_leaves_the_deadline_unknown(ctx, reason):
    result = homework("номер 5 на стр 43", msk(13), schedule=ctx)
    assert (result.at, result.basis, result.reason) == (None, "unknown", reason)
    assert student_label(result) == "Срок уточняется"


def test_unknown_subject_never_gets_a_date():
    result = decide_deadline(
        kind="homework", sources=[("номер 5", msk(13))], timezone=TZ, subject=None, schedule=WEEK
    )
    assert (result.at, result.reason) == (None, "subject_unknown")


@pytest.mark.parametrize("kind", ["test", "control_point", "assessment", "credit", "exam"])
def test_events_are_never_dated_by_the_next_lesson(kind):
    result = decide_deadline(
        kind=kind,
        sources=[("будет контрольная", msk(13))],
        timezone=TZ,
        subject=MATAN,
        schedule=WEEK,
    )
    assert (result.at, result.basis, result.reason) == (None, "unknown", "not_inferable_for_kind")


def test_later_dated_clarification_wins_over_an_earlier_one():
    result = decide_deadline(
        kind="homework",
        sources=[("сдать до 20 октября", msk(13)), ("перенос: сдать до 22 октября", msk(15))],
        timezone=TZ,
        subject=MATAN,
        schedule=WEEK,
    )
    assert result.at.date() == date(2026, 10, 22)


def test_model_deadline_is_kept_only_when_the_source_carries_a_date_signal():
    proposed = (msk(22), False)
    kept = decide_deadline(
        kind="homework",
        sources=[("до послезавтра сдать", msk(13))],
        timezone=TZ,
        subject=MATAN,
        schedule=None,
        model_deadline=proposed,
    )
    assert kept.basis == "relative_resolved"
    assert kept.at.astimezone(MOSCOW).date() == date(2026, 10, 15)
    agreeing = decide_deadline(
        kind="homework",
        sources=[("до послезавтра сдать", msk(13))],
        timezone=TZ,
        subject=MATAN,
        schedule=None,
        model_deadline=(msk(15), False),
    )
    assert agreeing.at.date() == date(2026, 10, 15)
    bare = decide_deadline(
        kind="homework",
        sources=[("номер 5", msk(13))],
        timezone=TZ,
        subject=MATAN,
        schedule=None,
        model_deadline=proposed,
    )
    assert bare.at is None  # no date in the text: the model's date is not evidence


def test_naive_timestamps_are_rejected():
    with pytest.raises(ValueError, match="source_timestamp_requires_offset"):
        decide_deadline(kind="homework", sources=[("x", datetime(2026, 10, 13, 12))], timezone=TZ)


def test_archive_is_immediate_for_exact_time_and_end_of_day_for_date_only():
    exact = homework("сдать до 20 октября в 14:30", msk(13), schedule=WEEK)
    assert archive_instant(exact, TZ) == exact.at
    only_date = homework("сдать до 20 октября", msk(13), schedule=WEEK)
    assert only_date.date_only
    assert archive_instant(only_date, TZ) == datetime(2026, 10, 21, 0, 0, tzinfo=MOSCOW)


def test_unknown_deadline_never_archives_and_a_move_moves_the_archive_time():
    unknown = homework("номер 5", msk(13), schedule=None)
    assert archive_instant(unknown, TZ) is None
    moved = decide_deadline(
        kind="homework",
        sources=[
            ("сдать до 20 октября в 10:00", msk(13)),
            ("перенос до 22 октября в 10:00", msk(15)),
        ],
        timezone=TZ,
        subject=MATAN,
        schedule=WEEK,
    )
    assert archive_instant(moved, TZ).astimezone(MOSCOW).date() == date(2026, 10, 22)


def test_text_date_always_wins_over_a_different_model_date():
    result = decide_deadline(
        kind="control_point",
        sources=[("тест сегодня", msk(12))],
        timezone=TZ,
        subject=MATAN,
        schedule=WEEK,
        model_deadline=(msk(15), True),
    )
    assert (result.basis, result.reason) == ("relative_resolved", "relative_message_date")
    assert result.at.astimezone(MOSCOW).date() == date(2026, 10, 12)
    agree = decide_deadline(
        kind="control_point",
        sources=[("тест сегодня", msk(12))],
        timezone=TZ,
        subject=MATAN,
        schedule=WEEK,
        model_deadline=(msk(12), True),
    )
    assert agree.basis == "relative_resolved"


def test_a_cancelled_lesson_in_the_window_makes_the_timetable_unreliable():
    cancelled = schedule(lesson(14, 10, status="cancelled"), lesson(21, 10))
    result = homework("номер 5 письменно", msk(13), schedule=cancelled)
    assert (result.at, result.basis, result.reason) == (
        None,
        "unknown",
        "cancelled_lesson_in_window",
    )


def test_a_day_without_lessons_in_the_window_makes_the_timetable_unreliable():
    holiday = {date(2026, 10, 14)}
    result = homework("номер 5 письменно", msk(13), schedule=WEEK, no_lesson_days=holiday)
    assert result.reason == "no_lesson_day_in_window"
    later = {date(2026, 10, 30)}
    ok = homework("номер 5 письменно", msk(13), schedule=WEEK, no_lesson_days=later)
    assert ok.basis == "schedule_inferred"


@pytest.mark.parametrize("text", ["сдать до 22.10", "сдать до 22 октября", "сдать 22/10"])
def test_a_literal_day_and_month_in_the_text_is_an_explicit_date_only_deadline(text):
    result = homework(text, msk(15), schedule=WEEK, model_deadline=(msk(29), True))
    assert (result.basis, result.date_only) == ("explicit", True)
    assert result.reason in {"explicit_source_date", "explicit_source_date_literal"}
    assert result.at.astimezone(MOSCOW).date() == date(2026, 10, 22)  # the text beats the model


def test_several_literal_dates_need_the_model_to_pick_one_otherwise_unknown():
    text = "лекция 14.10, сдать до 22.10"
    unknown = homework(text, msk(13), schedule=WEEK)
    assert (unknown.at, unknown.reason) == (None, "several_dates_in_text")
    picked = homework(text, msk(13), schedule=WEEK, model_deadline=(msk(22), True))
    assert picked.at.astimezone(MOSCOW).date() == date(2026, 10, 22)


def test_an_impossible_calendar_date_is_ignored():
    assert homework("сдать до 31.02", msk(15), schedule=None).at is None
