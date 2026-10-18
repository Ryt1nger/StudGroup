"""Owner decisions Q4-Q6 (18 Oct 2026) for deadline updates."""

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pytest

from studgroup.analysis.deadline_updates import (
    apply_field_locks,
    detect_reschedule,
    group_timezone,
    is_newer_move,
    says_deadline_moved,
)
from studgroup.deadlines import ScheduleDeadlineContext

TZ = "Europe/Moscow"
MOSCOW = ZoneInfo(TZ)
MATAN = "Математический анализ"


def msk(day, hour=12):
    return datetime(2026, 10, day, hour, 0, tzinfo=MOSCOW).astimezone(UTC)


def timetable(*days, status="scheduled"):
    lessons = [
        {
            "subject": MATAN,
            "starts_at": datetime(2026, 10, day, 10, 40, tzinfo=MOSCOW).isoformat(),
            "status": status,
        }
        for day in days
    ]
    return ScheduleDeadlineContext(lessons, date(2026, 10, 5), date(2026, 12, 27), True)


def test_a_reschedule_with_a_new_date_is_unambiguous():
    move = detect_reschedule(text="Перенесли на 22.10", sent_at=msk(15), timezone=TZ)
    assert move.basis == "explicit"
    assert move.at.astimezone(MOSCOW).date() == date(2026, 10, 22)


def test_a_reschedule_to_the_next_lesson_uses_the_reliable_timetable():
    move = detect_reschedule(
        text="Сдачу перенесли на следующую пару",
        sent_at=msk(15),
        timezone=TZ,
        subject=MATAN,
        schedule=timetable(21),
    )
    assert move.basis == "schedule_inferred"
    assert move.at.astimezone(MOSCOW).date() == date(2026, 10, 21)


@pytest.mark.parametrize("ctx", [None, timetable(21, status="cancelled")])
def test_a_next_lesson_move_without_a_reliable_timetable_goes_to_review(ctx):
    move = detect_reschedule(
        text="Сдачу перенесли на следующую пару",
        sent_at=msk(15),
        timezone=TZ,
        subject=MATAN,
        schedule=ctx,
    )
    assert move is None


@pytest.mark.parametrize(
    "text", ["Перенесли, ждите новостей", "Сдавайте до 22.10", "всем привет", "срок вроде сдвинули"]
)
def test_vague_or_non_move_messages_are_not_unambiguous(text):
    assert detect_reschedule(text=text, sent_at=msk(15), timezone=TZ, subject=MATAN) is None


def test_the_moved_marker_alone_is_detected():
    assert says_deadline_moved("теперь до пятницы")
    assert not says_deadline_moved("сдать до пятницы")


def test_a_move_to_the_same_moment_changes_nothing():
    move = detect_reschedule(text="Перенесли на 22.10", sent_at=msk(15), timezone=TZ)
    assert not is_newer_move(move, move.at)
    assert is_newer_move(move, msk(20))


def test_group_timezone_falls_back_to_moscow_and_says_it_is_unconfirmed():
    assert group_timezone("Asia/Yekaterinburg").confirmed
    for missing in (None, "", "Not/AZone"):
        zone = group_timezone(missing)
        assert (zone.name, zone.confirmed) == ("Europe/Moscow", False)


def test_locked_fields_are_never_overwritten_and_the_conflict_is_reported():
    current = {"deadline": "2026-10-22", "subject": "A", "title": "T"}
    proposed = {"deadline": "2026-10-25", "subject": "A", "title": "T2"}
    merged, blocked = apply_field_locks(current, proposed, {"deadline", "subject"})
    assert merged == {"deadline": "2026-10-22", "subject": "A", "title": "T2"}
    assert blocked == ["deadline"]  # same-value proposals are not conflicts


def test_only_known_fields_can_be_locked():
    with pytest.raises(ValueError, match="field_not_lockable"):
        apply_field_locks({}, {}, {"audience"})
