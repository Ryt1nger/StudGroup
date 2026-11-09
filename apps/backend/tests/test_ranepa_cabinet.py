"""Regression cases based on the observed November 2026 personal-cabinet grid."""

from datetime import date

import pytest

from studgroup.ranepa_cabinet import extract_cabinet_month, extract_cabinet_week
from studgroup.ranepa_probe import SourceAccessError

CODE = "Э-ОБ-385-04-26-02"
MONDAY = date(2026, 11, 2)


def grid(*, bad_day=False, broken_card=False, duplicate=False):
    heads = [
        f'<div class="schedule-week__cell schedule-week__day"><span class="schedule-week__day-title">{label}</span><span class="schedule-week__day-subtitle">{number} ноября</span></div>'
        for label, number in zip(
            ("пн", "вт", "ср", "чт", "пт", "сб", "вс"), range(2, 9), strict=True
        )
    ]
    if bad_day:
        heads[2] = heads[2].replace("4 ноября", "5 ноября")
    card = '<div class="schedule-week-card"><p class="schedule-week-card__title">Иностранный язык</p><p class="schedule-week-card__text">Иванова</p><p class="schedule-week-card__text">СДО</p></div>'
    if broken_card:
        card = card.replace("Иностранный язык", "")
    body = [
        '<div class="schedule-week__cell schedule-week__cell--item schedule-week__time-wrapper"><span class="schedule-week__time">09:00</span><span class="schedule-week__time">10:20</span></div>'
    ]
    for index in range(7):
        body.append(
            f'<div class="schedule-week__cell schedule-week__cell--item">{card if index == 0 else ""}</div>'
        )
    if duplicate:
        body[1] = body[1].replace(card, card + card)
    return (
        '<div class="schedule-week"><div class="schedule-week__row"><div class="schedule-week__cell"></div>'
        + "".join(heads)
        + '</div><div class="schedule-week__row">'
        + "".join(body)
        + "</div></div>"
    )


def test_real_layout_extracts_week_and_deduplicates_repeat():
    snapshot = extract_cabinet_week(grid(duplicate=True), CODE, MONDAY)
    assert len(snapshot.indexed()) == 1
    row = next(iter(snapshot.indexed().values()))
    assert row.day == MONDAY and row.subject == "Иностранный язык"
    assert row.room == "СДО"


def test_empty_week_requires_month_confirmation():
    empty = grid().replace(
        '<div class="schedule-week-card"><p class="schedule-week-card__title">Иностранный язык</p><p class="schedule-week-card__text">Иванова</p><p class="schedule-week-card__text">СДО</p></div>',
        "",
    )
    with pytest.raises(SourceAccessError, match="empty_source_requires_review"):
        extract_cabinet_week(empty, CODE, MONDAY)
    assert extract_cabinet_week(empty, CODE, MONDAY, allow_empty=True).lessons == ()


@pytest.mark.parametrize(
    "html,code",
    [
        (grid(bad_day=True), "cabinet_day_mismatch"),
        (grid(broken_card=True), "cabinet_card_incomplete"),
        (
            grid().replace("</div></div>", "<p>Loading...</p></div></div>", 1),
            "cabinet_week_loading",
        ),
        ("<main>Авторизация</main>", "cabinet_week_grid_missing"),
        (
            grid().replace('schedule-week__time">10:20', 'schedule-week__time">XX'),
            "cabinet_time_missing",
        ),
    ],
)
def test_incomplete_or_wrong_week_fails_closed(html, code):
    with pytest.raises(SourceAccessError, match=code):
        extract_cabinet_week(html, CODE, MONDAY)


def test_month_grid_checks_date_and_keeps_only_requested_month():
    from datetime import timedelta

    initial = date(2026, 10, 26)
    cells = []
    for offset in range(42):
        day = initial + timedelta(days=offset)
        card = (
            '<div class="schedule-month-card"><p class="schedule-month-card__time">09:00</p><p class="schedule-month-card__title">История России</p></div>'
            if offset in {0, 7}
            else ""
        )
        cells.append(
            f'<div class="schedule-month__cell schedule-month__cell--item"><span class="schedule-month__cell--day">{day.day}</span>{card}</div>'
        )
    html = (
        '<div class="schedule__header-date">Ноябрь 2026</div><div class="schedule-month">'
        + "".join(cells)
        + "</div>"
    )
    cards = extract_cabinet_month(html, 2026, 11)
    assert len(cards) == 1 and cards[0].day == date(2026, 11, 2)
    with pytest.raises(SourceAccessError, match="cabinet_month_day_mismatch"):
        extract_cabinet_month(html.replace(">2</span>", ">3</span>", 1), 2026, 11)
