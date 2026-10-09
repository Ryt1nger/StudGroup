from datetime import date, datetime

import pytest

from studgroup.deadlines import ScheduleDeadlineContext, contextual_deadline, resolve_deadline

TZ = "Europe/Moscow"


def test_decimal_exercise_range_is_not_a_date_and_uses_next_original_source_lesson():
    sent = datetime.fromisoformat("2026-10-08T12:34:06+03:00")
    schedule = ScheduleDeadlineContext(
        [
            {
                "subject": "Математический анализ",
                "starts_at": "2026-10-09T10:40:00+03:00",
                "status": "scheduled",
            }
        ],
        date(2026, 10, 5),
        date(2026, 10, 11),
    )
    result = resolve_deadline(
        [("#матан\nстр.69, вопросы 1-8\nрешать: стр.75 номер 5.1-5.4", sent)],
        TZ,
        "Математический анализ",
        schedule=schedule,
    )
    assert result.at.isoformat() == "2026-10-09T10:40:00+03:00"
    assert result.basis == "next_subject_lesson"


def test_exercise_range_does_not_hide_a_real_submission_date_window():
    sent = datetime.fromisoformat("2026-10-08T12:34:06+03:00")
    result = contextual_deadline("Номера 5.1–5.4 сдаём 10.10–15.10", sent, TZ)
    assert result.basis == "ambiguous_date_range"


def test_missing_historical_schedule_never_rebases_to_current_time():
    result = resolve_deadline(
        [("Ст 46 N 10.1", datetime.fromisoformat("2026-09-30T12:00:00+03:00"))],
        TZ,
        "Математический анализ",
        schedule=timetable(),
        fallback_reference=datetime.fromisoformat("2026-10-06T11:00:00+03:00"),
    )
    assert result.at is None
    assert result.basis == "schedule_does_not_cover_source_date"


def test_new_explicit_information_replaces_provisional_lesson_deadline():
    result = resolve_deadline(
        [("Сдать до 10 октября", datetime.fromisoformat("2026-10-06T12:00:00+03:00"))],
        TZ,
        "Математический анализ",
        schedule=timetable(),
        fallback_reference=datetime.fromisoformat("2026-10-06T13:00:00+03:00"),
    )
    assert result.at.isoformat() == "2026-10-10T00:00:00+03:00"
    assert result.basis == "explicit_source_date"


@pytest.mark.parametrize(
    "text,sent,expected,date_only",
    [
        ("тест до конца недели", "2026-09-29T09:03:29+03:00", "2026-10-04T00:00:00+03:00", True),
        (
            "К след среде номера как ДЗ",
            "2026-10-01T12:37:56+03:00",
            "2026-10-07T00:00:00+03:00",
            True,
        ),
        ("к следующей среде", "2026-10-07T10:00:00+03:00", "2026-10-14T00:00:00+03:00", True),
        ("ДЗ завтра", "2026-10-05T22:30:00+00:00", "2026-10-07T00:00:00+03:00", True),
        ("Сдать завтра в 14:30", "2026-10-05T10:00:00+03:00", "2026-10-06T14:30:00+03:00", False),
        (
            "через полчаса будешь рассказывать",
            "2026-09-29T13:32:27+03:00",
            "2026-09-29T14:02:27+03:00",
            False,
        ),
        ("до конца недели", "2026-12-31T10:00:00+03:00", "2027-01-03T00:00:00+03:00", True),
    ],
)
def test_relative_deadline_uses_source_date(text, sent, expected, date_only):
    result = contextual_deadline(text, datetime.fromisoformat(sent), TZ)
    assert result.at.isoformat() == expected
    assert result.date_only is date_only


def timetable(ready=True):
    return ScheduleDeadlineContext(
        [
            {
                "subject": "Математический анализ",
                "starts_at": "2026-10-05T09:00:00+03:00",
                "status": "scheduled",
            },
            {
                "subject": "Математический анализ",
                "starts_at": "2026-10-06T09:00:00+03:00",
                "status": "cancelled",
            },
            {
                "subject": "История России",
                "starts_at": "2026-10-06T10:40:00+03:00",
                "status": "scheduled",
            },
            {
                "subject": "Математический анализ",
                "starts_at": "2026-10-07T10:40:00+03:00",
                "status": "scheduled",
            },
        ],
        date(2026, 10, 5),
        date(2026, 10, 11),
        ready,
    )


def test_missing_deadline_defaults_to_next_subject_lesson_start():
    result = resolve_deadline(
        [("Ст 46 N 10.1", datetime.fromisoformat("2026-10-05T12:00:00+03:00"))],
        TZ,
        "Математический анализ",
        schedule=timetable(),
    )
    assert result.at.isoformat() == "2026-10-07T10:40:00+03:00"
    assert result.date_only is False
    assert result.basis == "next_subject_lesson"


@pytest.mark.parametrize(
    "subject,sent,ready",
    [
        ("Математический анализ", "2026-09-30T12:00:00+03:00", True),
        ("Математический анализ", "2026-10-05T12:00:00+03:00", False),
        ("Неизвестный предмет", "2026-10-05T12:00:00+03:00", True),
        (None, "2026-10-05T12:00:00+03:00", True),
    ],
)
def test_incomplete_schedule_never_invents_a_next_lesson(subject, sent, ready):
    assert (
        resolve_deadline(
            [("Решить задачи", datetime.fromisoformat(sent))],
            TZ,
            subject,
            schedule=timetable(ready),
        ).at
        is None
    )


def test_explicit_relative_overrides_wrong_model_date_and_schedule():
    result = resolve_deadline(
        [("До конца недели", datetime.fromisoformat("2026-09-29T09:00:00+03:00"))],
        TZ,
        "Математический анализ",
        datetime.fromisoformat("2026-10-11T00:00:00+03:00"),
        True,
        timetable(),
    )
    assert result.at.isoformat() == "2026-10-04T00:00:00+03:00"


def test_ambiguous_range_is_not_replaced_by_next_lesson():
    result = resolve_deadline(
        [("10.10 - 15.10 Задание", datetime.fromisoformat("2026-10-05T12:00:00+03:00"))],
        TZ,
        "Математический анализ",
        datetime.fromisoformat("2026-10-10T00:00:00+03:00"),
        True,
        timetable(),
    )
    assert result.at is None
    assert result.basis == "ambiguous_date_range"


def test_latest_relative_clarification_uses_its_own_date():
    result = resolve_deadline(
        [
            ("завтра", datetime.fromisoformat("2026-10-01T12:00:00+03:00")),
            ("перенесли на завтра", datetime.fromisoformat("2026-10-05T12:00:00+03:00")),
        ],
        TZ,
        "История России",
    )
    assert result.at.isoformat() == "2026-10-06T00:00:00+03:00"


def test_model_date_without_source_evidence_is_not_accepted():
    result = resolve_deadline(
        [("Написать эссе", datetime.fromisoformat("2026-10-05T12:00:00+03:00"))],
        TZ,
        "Математический анализ",
        datetime.fromisoformat("2026-10-10T00:00:00+03:00"),
        True,
    )
    assert result.at is None


def test_approximate_month_is_not_an_exact_day():
    result = resolve_deadline(
        [("КТ в конце ноября", datetime.fromisoformat("2026-10-05T12:00:00+03:00"))],
        TZ,
        "Математический анализ",
        datetime.fromisoformat("2026-11-30T00:00:00+03:00"),
        True,
    )
    assert result.at is None
    assert result.basis == "approximate_deadline"


def test_subject_alias_requires_an_unambiguous_known_calendar_subject():
    from datetime import date

    from studgroup.deadlines import ScheduleDeadlineContext, canonical_subject

    calendar = ScheduleDeadlineContext(
        [{"subject": "Математический анализ"}], date(2026, 10, 5), date(2026, 10, 18)
    )
    assert canonical_subject("Матан", calendar) == "Математический анализ"
    assert canonical_subject("История", calendar) == "История"
    assert canonical_subject("Матан", None) == "Матан"
