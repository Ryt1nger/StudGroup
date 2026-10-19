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


ANCHOR = date(2026, 10, 5)  # Monday of the FIRST week; 12 Oct starts the SECOND week


def expanded(patterns):
    """Expand weekly patterns over the semester with the real two-week cycle, 28 days at a time."""
    from datetime import time, timedelta

    from studgroup.schedule import LessonPattern, expand

    lessons = []
    for index, (week, weekday, hour) in enumerate(patterns):
        pattern = LessonPattern(
            f"p{index}", MATAN, weekday, time(hour, 40), time(hour + 1, 30),
            date(2026, 10, 5), date(2026, 12, 27), week, None, None,
        )  # fmt: skip
        day = date(2026, 10, 5)
        while day <= date(2026, 12, 27):
            lessons += expand(
                pattern, day, min(day + timedelta(days=27), date(2026, 12, 27)), TZ, ANCHOR
            )
            day += timedelta(days=28)
    context = ScheduleDeadlineContext(lessons, date(2026, 10, 5), date(2026, 12, 27), True)
    return context


def card(card_id, sent_day, **extra):
    from studgroup.analysis.deadline_policy import decide_deadline

    sources = [("номер 5 письменно", msk(sent_day))]
    decision = decide_deadline(
        kind="homework",
        sources=sources,
        timezone=TZ,
        subject=MATAN,
        schedule=OLD,
    )
    return {
        "id": card_id,
        "kind": "homework",
        "subject": MATAN,
        "sources": sources,
        "basis": decision.basis,
        "at": decision.at,
        "locked_fields": extra.get("locked", set()),
    }


# Old timetable: Matan on Wednesday of the second week (14 Oct, 28 Oct, ...) and Friday of the first.
OLD = expanded([("second", 2, 10), ("first", 4, 10)])


def test_a_change_in_one_week_of_the_cycle_moves_only_cards_that_land_in_that_week():
    from studgroup.analysis.deadline_updates import rebuild_inferred_deadlines

    sent_before_second_week_lesson = card("a", 12)  # Mon 12 Oct -> Wed 14 Oct (second week)
    sent_before_first_week_lesson = card("b", 20)  # Tue 20 Oct (first week) -> Fri 23 Oct
    assert sent_before_second_week_lesson["at"].astimezone(MOSCOW).date() == date(2026, 10, 14)
    assert sent_before_first_week_lesson["at"].astimezone(MOSCOW).date() == date(2026, 10, 23)
    # The headman moves the SECOND-week lesson from Wednesday to Thursday; the first week is untouched.
    new = expanded([("second", 3, 10), ("first", 4, 10)])
    changes = rebuild_inferred_deadlines(
        [sent_before_second_week_lesson, sent_before_first_week_lesson],
        now=msk(12),
        timezone=TZ,
        schedule=new,
    )
    assert [c.card_id for c in changes] == ["a"]
    assert changes[0].new_at.astimezone(MOSCOW).date() == date(2026, 10, 15)
    assert changes[0].old_at == sent_before_second_week_lesson["at"]  # kept for the history


def test_locked_confirmed_and_archived_cards_are_not_rebuilt():
    from studgroup.analysis.deadline_updates import rebuild_inferred_deadlines

    new = expanded([("second", 3, 10), ("first", 4, 10)])
    locked = card("locked", 12, locked={"deadline"})
    confirmed = {**card("confirmed", 12), "basis": "explicit"}
    archived = card("archived", 12)
    result = rebuild_inferred_deadlines(
        [locked, confirmed, archived], now=msk(14, 23), timezone=TZ, schedule=new
    )
    assert result == []


def test_a_cancelled_lesson_in_the_new_timetable_makes_the_deadline_unknown_not_stale():
    from studgroup.analysis.deadline_updates import rebuild_inferred_deadlines

    base = expanded([("second", 2, 10)])
    for lesson in base.lessons:
        if lesson["starts_at"].startswith("2026-10-14"):
            lesson["status"] = "cancelled"
    changes = rebuild_inferred_deadlines([card("a", 12)], now=msk(12), timezone=TZ, schedule=base)
    assert (changes[0].new_at, changes[0].reason) == (None, "cancelled_lesson_in_window")
