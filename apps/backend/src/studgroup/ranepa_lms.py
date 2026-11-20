"""Discover all course meeting candidates without guessing a teacher's meeting."""

import re
from dataclasses import dataclass
from datetime import date, time
from urllib.parse import parse_qs, urljoin, urlsplit

from studgroup.online_lessons import joining_url
from studgroup.ranepa_cabinet import Document
from studgroup.ranepa_link_states import LinkState
from studgroup.ranepa_probe import SourceAccessError
from studgroup.ranepa_schedule import normalized

URL_BASE = "https://lms.ranepa.ru"
DATE = re.compile(r"(?<!\d)(\d{1,2})\.(\d{1,2})\.(20\d{2}|\d{2})(?!\d)")
TIME_RANGE = re.compile(r"(?<!\d)(\d{1,2})[.:](\d{2})\s*[-–]\s*\d{1,2}[.:]\d{2}(?!\d)")
# Moodle labels frequently abbreviate a semester into "9, 16, 23.11".
# Such dates are not full DD.MM.YYYY dates and must not be treated as unknown.
DATE_LIST = re.compile(r"(?<!\d)(\d{1,2}(?:\s*,\s*\d{1,2})*)\.(\d{1,2})(?![.\d])")
INLINE_JOIN = re.compile(r"https://[^\s<>\"']+", re.IGNORECASE)
MAX_CANDIDATES = 80
MAX_DETAIL_PAGES = 8
LMS_MEETING_PATHS = frozenset(
    {"/mod/url/view.php", "/mod/mtslinkrnhgs/view.php", "/mod/bigbluebuttonbn/view.php"}
)
LMS_DETAIL_PATHS = frozenset({"/mod/page/view.php", "/mod/book/view.php", "/course/section.php"})


@dataclass(frozen=True)
class LinkCandidate:
    url: str
    label: str


@dataclass(frozen=True)
class LinkChoice:
    state: LinkState
    url: str | None
    candidates: tuple[LinkCandidate, ...]


def _canonical_lms_activity(raw: str, paths: frozenset[str]) -> str | None:
    try:
        parsed = urlsplit(urljoin(URL_BASE, raw))
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme != "https"
        or parsed.hostname != "lms.ranepa.ru"
        or parsed.username
        or parsed.password
        or port not in (None, 443)
        or parsed.path not in paths
    ):
        return None
    ids = parse_qs(parsed.query).get("id", [])
    if len(ids) != 1 or not ids[0].isdigit():
        return None
    # Never persist Moodle session keys, cookies or fragments.
    return f"{URL_BASE}{parsed.path}?id={ids[0]}"


def meeting_url(raw: str) -> str | None:
    """Accept only canonical LMS activities or approved direct conference URLs."""
    if not raw or len(raw) > 2048:
        return None
    lms = _canonical_lms_activity(raw, LMS_MEETING_PATHS)
    if lms:
        return lms
    # The safe host/path allowlist is shared with Telegram message links.
    try:
        safe_external = joining_url(raw) and not urlsplit(raw).fragment
    except ValueError:
        safe_external = False
    if safe_external:
        return raw
    return None


def course_detail_pages(html: str) -> tuple[str, ...]:
    """Only bounded, same-origin Moodle text/book pages; never follow arbitrary links."""
    if len(html.encode("utf-8")) > 3_000_000:
        raise SourceAccessError("lms_page_too_large")
    document = Document()
    document.feed(html)
    pages = {}
    for anchor in document.root.tags("a"):
        url = _canonical_lms_activity(anchor.attrs.get("href", ""), LMS_DETAIL_PATHS)
        if url:
            pages[url] = None
        if len(pages) >= MAX_DETAIL_PAGES:
            break
    return tuple(pages)


def _nodes_with_ancestors(node, ancestors=()):
    yield node, ancestors
    for child in node.children:
        yield from _nodes_with_ancestors(child, (*ancestors, node))


def _nearby_label(node, ancestors, url):
    """Look for the closest single-link activity/paragraph, not a whole teacher list."""
    label = node.text().strip()
    best = label[:500]
    for parent in reversed(ancestors):
        if parent.tag not in {"li", "p", "div", "article", "details", "section"}:
            continue
        text = parent.text()
        if not (12 <= len(text) <= 500):
            continue
        # Do not attach a whole section listing links for different teachers.
        meeting_anchors = [
            a for a in parent.tags("a") if meeting_url(a.attrs.get("href", "")) is not None
        ]
        meeting_frames = [
            frame
            for frame in parent.tags("iframe")
            if meeting_url(frame.attrs.get("src", "")) == url
        ]
        if (
            (
                len(meeting_anchors) == 1
                and meeting_url(meeting_anchors[0].attrs.get("href", "")) == url
            )
            or (not meeting_anchors and len(meeting_frames) == 1)
        ) and len(text) > len(best):
            best = text[:500]
    return best


def course_links(html: str, *, require_course: bool = True) -> tuple[LinkCandidate, ...]:
    """Gather Moodle activities, trusted conference links, inline URLs and hidden DOM links.

    Works on full course HTML or a verified same-origin Moodle text/book page.
    Collapsed <details> links are included; JavaScript-loaded sections are expanded
    by the browser before this parser is called.
    """
    if len(html.encode("utf-8")) > 3_000_000:
        raise SourceAccessError("lms_page_too_large")
    document = Document()
    document.feed(html)
    if require_course and (
        not document.root.find("course-content")
        and not document.root.find("course-format")
        and not any(
            "/course/view.php" in node.attrs.get("href", "") for node in document.root.tags("a")
        )
    ):
        raise SourceAccessError("lms_course_unverified")
    result: dict[str, LinkCandidate] = {}
    for node, ancestors in _nodes_with_ancestors(document.root):
        if node.tag == "a":
            url = meeting_url(node.attrs.get("href", "") or node.attrs.get("data-url", ""))
            if url:
                label = _nearby_label(node, ancestors, url)
                if label and (url not in result or len(label) > len(result[url].label)):
                    result[url] = LinkCandidate(url, label)
        elif node.tag in {"iframe", "button"}:
            url = meeting_url(node.attrs.get("src", "") or node.attrs.get("data-url", ""))
            if url:
                label = _nearby_label(node, ancestors, url)
                if label:
                    result.setdefault(url, LinkCandidate(url, label))
        if node.tag not in {"p", "li", "div"} or not node.parts:
            continue
        # Some Moodle labels show the actual link as plain text, not an anchor.
        text = " ".join(node.parts).strip()
        if len(text) > 500:
            continue
        for match in INLINE_JOIN.finditer(text):
            raw = match.group().rstrip(".,;!)]}")
            url = meeting_url(raw)
            if url and url not in result:
                result[url] = LinkCandidate(url, text)
        if len(result) >= MAX_CANDIDATES:
            break
    return tuple(result.values())[:MAX_CANDIDATES]


def _group_token(label: str) -> str:
    # Tooltip labels append faculty after " / "; match exact stream identity.
    return normalized(label.split(" / ", 1)[0]).replace(" ", "")


def _lesson_dates(label: str, *, year: int) -> set[date]:
    """Recognise both explicit dates and semester day-lists, with validation."""
    found = set()
    for match in DATE.finditer(label):
        try:
            value = int(match[3])
            found.add(date(2000 + value if value < 100 else value, int(match[2]), int(match[1])))
        except ValueError:
            continue
    for match in DATE_LIST.finditer(label):
        month = int(match[2])
        for day_text in match[1].split(","):
            try:
                found.add(date(year, month, int(day_text.strip())))
            except ValueError:
                continue
    return found


def select_lesson_link(
    links: tuple[LinkCandidate, ...],
    *,
    teacher: str,
    group_label: str,
    day: date,
    starts: time | None = None,
) -> LinkChoice:
    """Never publish a guessed teacher/stream URL; prefer exact date, then permanent."""
    if not teacher.strip() or not group_label.strip():
        return LinkChoice(LinkState.IDENTITY_MISSING, None, ())
    surname = normalized(teacher.split()[0])
    token = _group_token(group_label)
    if len(token) < 10:
        return LinkChoice(LinkState.IDENTITY_MISSING, None, ())
    dated = []
    permanent = []
    identity_matches = []
    undated = []
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
        identity_matches.append(link)
        found_dates = _lesson_dates(link.label, year=day.year)
        if found_dates and day in found_dates:
            dated.append(link)
        elif not found_dates and "постоянн" in label:
            permanent.append(link)
        elif not found_dates:
            # A Moodle "Гиперссылка" with exact teacher AND stream identity
            # is a usable meeting when it is the only such candidate. Multiple
            # different links stay unverified rather than choosing at random.
            undated.append(link)
    matches = dated or permanent
    unique = {link.url: link for link in matches}
    if len(unique) == 1:
        return LinkChoice(LinkState.MATCHED, next(iter(unique)), tuple(unique.values()))
    if unique:
        return LinkChoice(LinkState.AMBIGUOUS, None, tuple(unique.values()))
    undated_unique = {link.url: link for link in undated}
    if len(undated_unique) == 1:
        return LinkChoice(
            LinkState.MATCHED, next(iter(undated_unique)), tuple(undated_unique.values())
        )
    if identity_matches:
        return LinkChoice(LinkState.TIMING_UNVERIFIED, None, tuple(identity_matches))
    return LinkChoice(LinkState.NOT_FOUND, None, ())
