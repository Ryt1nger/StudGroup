"""Parse the observed personal-cabinet week grid as a private preview.

The grid does not prove the academic group identity. Callers must obtain and
verify that separately before promoting any row to group storage.
"""

import re
from dataclasses import dataclass, field
from datetime import date, time, timedelta
from html.parser import HTMLParser

from studgroup.ranepa_probe import SourceAccessError
from studgroup.ranepa_schedule import ExternalLesson, WeekSnapshot, group_code

MONTHS = {
    "января": 1,
    "февраля": 2,
    "марта": 3,
    "апреля": 4,
    "мая": 5,
    "июня": 6,
    "июля": 7,
    "августа": 8,
    "сентября": 9,
    "октября": 10,
    "ноября": 11,
    "декабря": 12,
}
WEEKDAYS = ("пн", "вт", "ср", "чт", "пт", "сб", "вс")
TIME = re.compile(r"^\d{1,2}:\d{2}$")
DAY = re.compile(r"^(\d{1,2})\s+([а-яё]+)$", re.IGNORECASE)


@dataclass
class Element:
    tag: str
    attrs: dict[str, str]
    children: list["Element"] = field(default_factory=list)
    parts: list[str] = field(default_factory=list)

    def has(self, name: str) -> bool:
        return name in self.attrs.get("class", "").split()

    def text(self) -> str:
        return " ".join(" ".join(self.parts + [child.text() for child in self.children]).split())

    def find(self, name: str) -> list["Element"]:
        found = [self] if self.has(name) else []
        for child in self.children:
            found.extend(child.find(name))
        return found

    def tags(self, name: str) -> list["Element"]:
        found = [self] if self.tag == name else []
        for child in self.children:
            found.extend(child.tags(name))
        return found


class Document(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = Element("document", {})
        self.stack = [self.root]

    def handle_starttag(self, tag, attrs):
        node = Element(tag, dict(attrs))
        self.stack[-1].children.append(node)
        if tag not in {"br", "img", "input", "meta", "link", "hr", "source"}:
            self.stack.append(node)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                return

    def handle_data(self, data):
        self.stack[-1].parts.append(data)


@dataclass(frozen=True)
class MonthCard:
    day: date
    starts: time
    subject: str


def _day(value: str, expected: date, weekday: int) -> None:
    match = DAY.fullmatch(value.casefold())
    if not match or MONTHS.get(match[2]) != expected.month or int(match[1]) != expected.day:
        raise SourceAccessError("cabinet_day_mismatch")
    if expected.weekday() != weekday:
        raise SourceAccessError("cabinet_weekday_mismatch")


def extract_cabinet_week(
    html: str, expected_group_code: str, monday: date, *, allow_empty: bool = False
) -> WeekSnapshot:
    """Extract all visible week cards; reject loading, missing cells and bad dates."""
    if monday.weekday() != 0:
        raise ValueError("week_start_must_be_monday")
    if len(html.encode("utf-8")) > 12_000_000:
        raise SourceAccessError("page_too_large")
    doc = Document()
    doc.feed(html)
    grids = doc.root.find("schedule-week")
    if len(grids) != 1:
        raise SourceAccessError("cabinet_week_grid_missing")
    if "Loading..." in grids[0].text() or "Загрузка..." in grids[0].text():
        raise SourceAccessError("cabinet_week_loading")
    rows = [child for child in grids[0].children if child.has("schedule-week__row")]
    if len(rows) < 2:
        raise SourceAccessError("cabinet_week_incomplete")
    headers = [child for child in rows[0].children if child.has("schedule-week__cell")]
    if len(headers) != 8:
        raise SourceAccessError("cabinet_week_incomplete")
    for weekday, cell in enumerate(headers[1:]):
        title = cell.find("schedule-week__day-title")
        subtitle = cell.find("schedule-week__day-subtitle")
        if len(title) != 1 or len(subtitle) != 1 or title[0].text().casefold() != WEEKDAYS[weekday]:
            raise SourceAccessError("cabinet_week_incomplete")
        _day(subtitle[0].text(), monday + timedelta(days=weekday), weekday)
    lessons = []
    for row in rows[1:]:
        cells = [child for child in row.children if child.has("schedule-week__cell")]
        if len(cells) != 8:
            raise SourceAccessError("cabinet_week_incomplete")
        clock = cells[0].find("schedule-week__time")
        if len(clock) != 2 or any(not TIME.fullmatch(part.text()) for part in clock):
            raise SourceAccessError("cabinet_time_missing")
        starts, ends = (time.fromisoformat(part.text()) for part in clock)
        for weekday, cell in enumerate(cells[1:]):
            cards = cell.find("schedule-week-card")
            for card in cards:
                title = card.find("schedule-week-card__title")
                if len(title) != 1 or not title[0].text():
                    raise SourceAccessError("cabinet_card_incomplete")
                details = [part.text() for part in card.find("schedule-week-card__text")]
                teacher = details[0] if details else None
                room = details[1] if len(details) > 1 else None
                if room == "Уточняется":
                    room = None
                lessons.append(
                    ExternalLesson(
                        day=monday + timedelta(days=weekday),
                        starts=starts,
                        ends=ends,
                        subject=title[0].text(),
                        teacher=teacher,
                        room=room,
                        lesson_type=None,
                        subgroup_hint=None,
                    )
                )
    if not lessons and not allow_empty:
        raise SourceAccessError("empty_source_requires_review")
    snapshot = WeekSnapshot(
        group_code(expected_group_code), monday, monday + timedelta(days=6), tuple(lessons)
    )
    snapshot.indexed()  # Reject conflicting duplicates before returning a preview.
    return snapshot


def extract_cabinet_month(html: str, year: int, month: int) -> tuple[MonthCard, ...]:
    """Cross-check the month grid against complete weekly card reads."""
    first = date(year, month, 1)
    if len(html.encode("utf-8")) > 12_000_000:
        raise SourceAccessError("page_too_large")
    doc = Document()
    doc.feed(html)
    headings = doc.root.find("schedule__header-date")
    month_names = (
        "январь",
        "февраль",
        "март",
        "апрель",
        "май",
        "июнь",
        "июль",
        "август",
        "сентябрь",
        "октябрь",
        "ноябрь",
        "декабрь",
    )
    if len(headings) != 1 or headings[0].text().casefold() != f"{month_names[month - 1]} {year}":
        raise SourceAccessError("cabinet_month_mismatch")
    grids = doc.root.find("schedule-month")
    if len(grids) != 1 or "Loading..." in grids[0].text():
        raise SourceAccessError("cabinet_month_incomplete")
    cells = grids[0].find("schedule-month__cell--item")
    if len(cells) not in {35, 42}:
        raise SourceAccessError("cabinet_month_incomplete")
    initial = first - timedelta(days=first.weekday())
    cards = []
    for offset, cell in enumerate(cells):
        day = initial + timedelta(days=offset)
        marker = cell.find("schedule-month__cell--day")
        if len(marker) != 1 or not re.match(rf"^{day.day}(?:\D|$)", marker[0].text()):
            raise SourceAccessError("cabinet_month_day_mismatch")
        for item in cell.find("schedule-month-card"):
            clock = item.find("schedule-month-card__time")
            title = item.find("schedule-month-card__title")
            if (
                len(clock) != 1
                or len(title) != 1
                or not TIME.fullmatch(clock[0].text())
                or not title[0].text()
            ):
                raise SourceAccessError("cabinet_month_card_incomplete")
            if day.month == month:
                cards.append(MonthCard(day, time.fromisoformat(clock[0].text()), title[0].text()))
    if not cards:
        raise SourceAccessError("empty_source_requires_review")
    return tuple(cards)
