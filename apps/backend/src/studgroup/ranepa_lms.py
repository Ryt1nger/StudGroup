"""Conservative link candidates from full LMS course HTML."""

import re
from dataclasses import dataclass
from datetime import date, time
from urllib.parse import parse_qs, urljoin, urlsplit

from studgroup.ranepa_cabinet import Document
from studgroup.ranepa_probe import SourceAccessError
from studgroup.ranepa_schedule import normalized

URL_BASE = "https://lms.ranepa.ru"
DATE = re.compile(r"(?<!\d)(\d{1,2})\.(\d{1,2})\.(20\d{2}|\d{2})(?!\d)")
TIME_RANGE = re.compile(r"(?<!\d)(\d{1,2})[.:](\d{2})\s*[-–]\s*\d{1,2}[.:]\d{2}(?!\d)")


@dataclass(frozen=True)
class LinkCandidate:
    url: str
    label: str


@dataclass(frozen=True)
class LinkChoice:
    state: str
    url: str | None
    candidates: tuple[LinkCandidate, ...]


def course_links(html: str) -> tuple[LinkCandidate, ...]:
    """Read Moodle meeting activities; skip quizzes, files and external redirects."""
    if len(html.encode("utf-8")) > 3_000_000:
        raise SourceAccessError("lms_page_too_large")
    document = Document()
    document.feed(html)
    if (
        not document.root.find("course-content")
        and not document.root.find("course-format")
        and not any(
            "/course/view.php" in node.attrs.get("href", "") for node in document.root.tags("a")
        )
    ):
        raise SourceAccessError("lms_course_unverified")
    result = {}
    for anchor in document.root.tags("a"):
        url = urljoin(URL_BASE, anchor.attrs.get("href", ""))
        parsed = urlsplit(url)
        if (
            parsed.scheme != "https"
            or parsed.hostname != "lms.ranepa.ru"
            or parsed.path not in {"/mod/url/view.php", "/mod/mtslinkrnhgs/view.php"}
        ):
            continue
        ids = parse_qs(parsed.query).get("id", [])
        if len(ids) != 1 or not ids[0].isdigit():
            continue
        url = f"{URL_BASE}{parsed.path}?id={ids[0]}"
        label = anchor.text()
        if label:
            result[url] = LinkCandidate(url, label)
    return tuple(result.values())


def _group_token(label: str) -> str:
    # Tooltip labels can append a faculty after a slash; only the exact academic
    # stream/group token may match an LMS activity.
    return normalized(label.split(" / ", 1)[0]).replace(" ", "")


def select_lesson_link(
    links: tuple[LinkCandidate, ...],
    *,
    teacher: str,
    group_label: str,
    day: date,
    starts: time | None = None,
) -> LinkChoice:
    """Accept one exact group and teacher link, dated for the lesson or permanent."""
    if not teacher.strip() or not group_label.strip():
        return LinkChoice("identity_missing", None, ())
    surname = normalized(teacher.split()[0])
    token = _group_token(group_label)
    if len(token) < 10:
        return LinkChoice("identity_missing", None, ())
    dated = []
    permanent = []
    for link in links:
        label = normalized(link.label)
        compact = label.replace(" ", "").replace("_", "")
        if not re.search(rf"(?<!\w){re.escape(surname)}(?!\w)", label) or token not in compact:
            continue
        times = {
            time(int(match[1]), int(match[2]))
            for match in TIME_RANGE.finditer(link.label)
            if int(match[1]) < 24 and int(match[2]) < 60
        }
        if starts is not None and times and starts not in times:
            continue
        found_dates = set()
        for match in DATE.finditer(link.label):
            try:
                year = int(match[3])
                found_dates.add(
                    date(2000 + year if year < 100 else year, int(match[2]), int(match[1]))
                )
            except ValueError:
                continue
        if found_dates and day in found_dates:
            dated.append(link)
        elif not found_dates and "постоянн" in label:
            permanent.append(link)
    matches = dated or permanent
    if len(matches) == 1:
        return LinkChoice("matched", matches[0].url, tuple(matches))
    return LinkChoice("ambiguous" if matches else "not_found", None, tuple(matches))
