"""RANEPA sync core: source precedence, identity, subgroup safety."""
from datetime import date, time
from uuid import uuid4

import pytest

from studgroup.models import Subgroup
from studgroup.ranepa_schedule import (
    ExternalLesson,
    WeekSnapshot,
    compare_snapshots,
    detect_subgroup,
    group_code,
    screenshot_action,
)

MON = date(2026, 11, 2)
SUN = date(2026, 11, 8)
CODE = "Э-ОБ-385-04-26-02"


def lesson(**overrides):
    values = {
        "day": MON,
        "starts": time(9),
        "ends": time(10, 20),
        "subject": "Иностранный язык",
        "teacher": "Иванова",
        "room": "305",
    }
    values.update(overrides)
    return ExternalLesson(**values)


def snapshot(*rows):
    return WeekSnapshot(CODE, MON, SUN, tuple(rows))


def test_duplicates_are_idempotent():
    row = lesson()
    assert len(snapshot(row, row).indexed()) == 1
    assert snapshot(row, row).digest() == snapshot(row).digest()
    assert compare_snapshots(snapshot(row), snapshot(row, row)).unchanged


def test_parallel_subgroups_survive():
    rows = snapshot(lesson(), lesson(teacher="Петров", room="306"))
    assert len(rows.indexed()) == 2


def test_stable_id_detects_teacher_or_room_change():
    a = snapshot(lesson(upstream_id="42", room="305"))
    b = snapshot(lesson(upstream_id="42", room="409"))
    diff = compare_snapshots(a, b)
    assert len(diff.changed) == 1
    assert not diff.added and not diff.removed


def test_cross_group_week_comparison_is_rejected():
    a = snapshot(lesson())
    b = WeekSnapshot("Э-ОБ-385-04-26-03", MON, SUN, (lesson(),))
    with pytest.raises(ValueError, match="mismatched_group_or_week"):
        compare_snapshots(a, b)


def test_invalid_and_out_of_range_lessons_fail_closed():
    with pytest.raises(ValueError, match="invalid_lesson_time"):
        lesson(ends=time(8))
    with pytest.raises(ValueError, match="lesson_outside_snapshot_window"):
        snapshot(lesson(day=date(2026, 11, 10)))


def test_conflicting_upstream_id_rejected_not_overwritten():
    rows = snapshot(lesson(upstream_id="42"), lesson(upstream_id="42", room="407"))
    with pytest.raises(ValueError, match="conflicting_upstream_lesson"):
        rows.indexed()


def test_fingerprint_does_not_depend_on_input_order():
    one, two = lesson(), lesson(teacher="Петров", room="306")
    assert snapshot(one, two).digest() == snapshot(two, one).digest()


def test_group_code_normalizes_dashes_and_preserves_full_value():
    assert group_code(" Э‐ОБ‐385‐04‐26‐02 ") == CODE


def test_teacher_recognition_but_room_alone_does_not_select_subgroup():
    first = Subgroup(id=uuid4(), position=1, name="1 · Иванова", signals='["Иванова", "1 группа"]')
    second = Subgroup(id=uuid4(), position=2, name="2 · Петров", signals='["Петров", "2 группа"]')
    rows = [first, second]
    assert detect_subgroup(lesson(teacher="Иванова Е. А."), rows).id == first.id
    assert detect_subgroup(lesson(teacher="Петров И. А."), rows).id == second.id
    assert detect_subgroup(lesson(teacher=None, room="305"), rows) is None
    assert detect_subgroup(lesson(teacher="Иванова", subgroup_hint="2 группа"), rows) is None
    assert detect_subgroup(lesson(teacher=None, subgroup_hint="Э-ОБ-385-04-26-02"), rows) is None


def test_screenshot_never_overrides_available_official_source():
    assert screenshot_action(official_configured=True, official_request_succeeded=True) == "compare_with_official"
    assert screenshot_action(official_configured=True, official_request_succeeded=False) == "fallback_review"
    assert screenshot_action(official_configured=False, official_request_succeeded=False) == "fallback_review"
