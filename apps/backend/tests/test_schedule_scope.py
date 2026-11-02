"""No provider requests or production timetable writes are involved in these tests."""

from datetime import date

from studgroup.analysis.image_contracts import ScheduleInterpretation, ScheduleRow, VisualReading
from studgroup.analysis.schedule_scope import suggest_review_rows
from studgroup.schedule import week_kind


ANCHOR = date(2026, 9, 7)
MONDAY = date(2026, 11, 2)
HORIZON = date(2027, 1, 31)


def reading(*, duration="recurring", coverage="full_week", start=MONDAY, row_patch=None):
    row = dict(weekday=2, starts="13:00", ends="14:30", subject="Алгебра")
    row.update(row_patch or {})
    return VisualReading(
        summary="Расписание", extracted_text="Расписание на неделю", complete=True,
        semantic_types=["schedule"],
        schedule=[ScheduleRow.model_validate(row)],
        schedule_interpretation=ScheduleInterpretation(
            duration=duration, coverage=coverage, week_start=start,
            explanation="Контекст переписки", source_message_ids=[31, 32],
        ),
    )


def test_default_weekly_timetable_repeats_only_in_its_anchored_parity():
    result = suggest_review_rows(reading(), ANCHOR, HORIZON)[0]
    assert result["week"] == week_kind(MONDAY, ANCHOR)
    assert result["valid_from"] == "2026-11-02"
    assert result["valid_until"] == "2027-01-31"
    assert result["week"] != week_kind(MONDAY + __import__("datetime").timedelta(days=7), ANCHOR)


def test_temporary_week_ends_that_sunday_and_never_changes_base_parity():
    result = suggest_review_rows(reading(duration="one_week"), ANCHOR, HORIZON)[0]
    assert (result["week"], result["valid_from"], result["valid_until"]) == (
        "all", "2026-11-02", "2026-11-08",
    )


def test_partial_single_day_move_keeps_its_exact_date():
    value = reading(
        duration="one_week", coverage="partial",
        row_patch={"valid_from": "2026-11-04", "valid_until": "2026-11-04"},
    )
    row = suggest_review_rows(value, ANCHOR, HORIZON)[0]
    assert (row["valid_from"], row["valid_until"], row["week"]) == (
        "2026-11-04", "2026-11-04", "all"
    )


def test_conflicts_or_missing_anchor_horizon_or_scope_remain_unresolved():
    cases = [
        (reading(duration="uncertain"), ANCHOR, HORIZON),
        (reading(start=date(2026, 11, 3)), ANCHOR, HORIZON),
        (reading(), None, HORIZON),
        (reading(), ANCHOR, None),
        (reading(), ANCHOR, date(2026, 11, 3)),
        (reading(row_patch={"valid_from": "2026-10-28"}), ANCHOR, HORIZON),
        (reading(row_patch={"week": "first" if week_kind(MONDAY, ANCHOR) == "second" else "second"}), ANCHOR, HORIZON),
    ]
    for value, anchor, horizon in cases:
        row = suggest_review_rows(value, anchor, horizon)[0]
        assert row["valid_from"] is None or row["valid_from"] == "2026-10-28"
        assert row["valid_until"] is None


def test_explicit_extended_period_is_not_cut_to_the_photo_week():
    row = suggest_review_rows(
        reading(row_patch={"valid_from": "2026-11-02", "valid_until": "2027-03-01"}),
        ANCHOR, HORIZON,
    )[0]
    assert row["valid_until"] == "2027-03-01"


def test_explicit_one_week_contradiction_needs_editor_not_an_override():
    row = suggest_review_rows(
        reading(duration="one_week", row_patch={"valid_until": "2026-12-01"}),
        ANCHOR, HORIZON,
    )[0]
    assert row["valid_from"] is None
    assert row["valid_until"] == "2026-12-01"


def test_existing_cached_readings_without_new_field_stay_compatible():
    value = VisualReading(
        summary="", extracted_text="", complete=True, semantic_types=["schedule"],
        schedule=[ScheduleRow(weekday=0, starts="10:00", ends="11:30", subject="Физика")],
    )
    assert suggest_review_rows(value, ANCHOR, HORIZON)[0]["valid_until"] is None
