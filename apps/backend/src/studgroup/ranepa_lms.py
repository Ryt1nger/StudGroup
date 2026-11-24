"""Discover all course meeting candidates without guessing a teacher's meeting."""

import re
from dataclasses import dataclass
from datetime import date, time
from urllib.parse import parse_qs, urljoin, urlsplit

from studgroup.online_lessons import joining_url
from studgroup.ranepa_cabinet import MONTHS, Document
from studgroup.ranepa_link_states import LinkState
from studgroup.ranepa_probe import SourceAccessError
from studgroup.ranepa_schedule import normalized
from studgroup.schedule import week_kind

URL_BASE = "https://lms.ranepa.ru"
DATE = re.compile(r"(?<!\d)(\d{1,2})\.(\d{1,2})\.(20\d{2}|\d{2})(?!\d)")
TIME_RANGE = re.compile(r"(?<!\d)(\d{1,2})[.:](\d{2})\s*[-–]\s*\d{1,2}[.:]\d{2}(?!\d)")
# Moodle labels frequently abbreviate a semester into "9, 16, 23.11".
# Such dates are not full DD.MM.YYYY dates and must not be treated as unknown.
DATE_LIST = re.compile(r"(?<!\d)(\d{1,2}(?:\s*,\s*\d{1,2})*)\.(\d{1,2})(?![.\d])")
EXPLICITLY_TEMPORARY = re.compile(
    r"\b(?:одноразов|однократн|разов|временн)\w*|\bна\s+(?:одну|эту)\s+пару"
)
INLINE_JOIN = re.compile(r"https://[^\s<>\"']+", re.IGNORECASE)
MAX_CANDIDATES = 500
WORD_DATE = re.compile(r"(?<!\d)(\d{1,2})\s+(" + "|".join(MONTHS) + r")\b")
MAX_DETAIL_PAGES = 16
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
    may_resolve_duplicates: bool = False


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
            # Relevant text pages may appear BELOW unrelated handouts/books.
            # Prefer their titles without ever crossing the Moodle origin.
            label = normalized(anchor.text())
            priority = any(
                word in label
                for word in ("ссылк", "заняти", "лекц", "вебинар", "конференц", "подключ")
            )
            pages[url] = pages.get(url, False) or priority
    return tuple(sorted(pages, key=lambda url: not pages[url]))[:MAX_DETAIL_PAGES]


def _nodes_with_ancestors(node, ancestors=()):
    yield node, ancestors
    for child in node.children:
        yield from _nodes_with_ancestors(child, (*ancestors, node))


TEACHER_HEADING = re.compile(r"^[А-ЯЁ][а-яё-]+\s+[А-ЯЁ]\.\s*[А-ЯЁ]\.?$")


def _teacher_section(ancestors):
    """A section title applies to its activities, not neighboring teachers."""
    for parent in reversed(ancestors):
        if not (
            parent.tag in {"section", "details", "li", "article"}
            or parent.has("course-section")
            or parent.has("section")
        ):
            continue
        headings = [
            heading
            for tag in ("h2", "h3", "h4", "h5", "summary")
            for heading in parent.tags(tag)
            if TEACHER_HEADING.fullmatch(heading.text().strip())
        ]
        if len(headings) == 1:
            return headings[0].text().strip()
    return ""


def _with_teacher(label, ancestors):
    teacher = _teacher_section(ancestors)
    if teacher:
        return f"{teacher} | {label}"[:500]
    return label[:500]


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
    return _with_teacher(best, ancestors)


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
        if node.tag not in {"p", "li", "div", "span"} or not node.parts:
            continue
        # Some Moodle labels show the actual link as plain text, not an anchor.
        text = " ".join(node.parts).strip()
        if len(text) > 500:
            continue
        inline_matches = tuple(INLINE_JOIN.finditer(text))
        if len(inline_matches) != 1:
            continue
        for match in inline_matches:
            raw = match.group().rstrip(".,;!)]}")
            url = meeting_url(raw)
            if url and url not in result:
                result[url] = LinkCandidate(url, _with_teacher(text, ancestors))
        if len(result) >= MAX_CANDIDATES:
            break
    return tuple(result.values())[:MAX_CANDIDATES]


def dashboard_courses(html: str) -> dict[str, str]:
    """Discover only canonical course links from the authenticated Moodle UI."""
    if len(html.encode("utf-8")) > 3_000_000:
        raise SourceAccessError("lms_page_too_large")
    document = Document()
    document.feed(html)
    result = {}
    for anchor in document.root.tags("a"):
        url = _canonical_lms_activity(anchor.attrs.get("href", ""), frozenset({"/course/view.php"}))
        if url:
            title = anchor.attrs.get("title") or anchor.text()
            if title.strip():
                result[url] = title.strip()[:500]
    return result


def matching_courses(courses, subject):
    """Accept exact course title or a distinct subject token in a longer course title."""
    needle = normalized(subject)
    exact = [url for url, title in courses.items() if normalized(title) == needle]
    if exact:
        return tuple(exact)
    if not needle:
        return ()
    return tuple(
        url
        for url, title in courses.items()
        if re.search(rf"(?<!\w){re.escape(needle)}(?!\w)", normalized(title))
    )


# The screenshot examples use a stable stream family and a list of group
# numbers; punctuation and order of the list are not meaningful.
STREAM = re.compile(
    r"(?<![а-я0-9])([а-я]{1,4})\s*-\s*([а-я]{2,4})\s*-?\s*(\d{2})"
    r"\s*-\s*сгя\s*[- ]*\s*(лек(?:ци\w*)?|пр(?:акт\w*)?|семинар\w*)?"
    r"\s*[- ]*\s*(?:\(\s*([\d,\s]+)\s*\)|(\d+(?:\s*,\s*\d+)*))"
)
WEEKDAYS = {
    "пн": 0,
    "понедельник": 0,
    "вт": 1,
    "вторник": 1,
    "ср": 2,
    "среда": 2,
    "среду": 2,
    "чт": 3,
    "четверг": 3,
    "пт": 4,
    "пятница": 4,
    "пятницу": 4,
    "сб": 5,
    "суббота": 5,
    "субботу": 5,
}
PAIR = re.compile(r"(?<!\d)([1-8])\s*(?:-\s*[аяй])?\s*пар[аыуе]\b")
# Standard slots observed in the verified LK timetable. Unknown slots
# are not assigned a pair number.
PAIR_STARTS = (time(9), time(10, 40), time(12, 20), time(14), time(15, 40), time(17, 20))


def _kind(label):
    clean = normalized(label).replace("_", " ")
    lecture = bool(re.search(r"лекц|(?:^|[-\s])лек(?:\b|\()", clean))
    practical = bool(re.search(r"практ|семинар|(?:^|[-\s])пр(?:\b|-)", clean))
    return frozenset(
        kind for kind, present in (("lecture", lecture), ("practical", practical)) if present
    )


def _streams(label):
    clean = normalized(label).replace("_", " ")
    result = []
    for match in STREAM.finditer(clean):
        groups = frozenset(int(number) for number in re.findall(r"\d+", match[5] or match[6]))
        types = _kind(match[4] or "") or _kind(clean)
        result.append((match.groups()[:3], types, groups))
    return result


def _same_stream(expected, offered, lesson_type):
    for family, kinds, groups in expected:
        for other_family, other_kinds, other_groups in offered:
            if family != other_family or not groups.issubset(other_groups):
                continue
            if kinds and other_kinds and not kinds.intersection(other_kinds):
                continue
            if lesson_type and other_kinds and not lesson_type.intersection(other_kinds):
                continue
            return True
    return False


def _recurrence_matches(label, day, starts, first_week_anchor):
    weekdays = {
        number
        for word, number in WEEKDAYS.items()
        if re.search(rf"(?<!\w){word}(?!\w)", label)
    }
    if weekdays and day.weekday() not in weekdays:
        return False
    pairs = {int(match[1]) for match in PAIR.finditer(label)}
    if pairs and (starts not in PAIR_STARTS or PAIR_STARTS.index(starts) + 1 not in pairs):
        return False
    odd = bool(re.search(r"не\s*четн\w*\s+недел", label))
    even = bool(re.search(r"(?<!не)(?<!не )четн\w*\s+недел", label))
    if odd or even:
        if first_week_anchor is None or odd == even:
            return False
        if (week_kind(day, first_week_anchor) == "first") != odd:
            return False
    return True


def _group_token(label: str) -> str:
    # Tooltip labels append faculty after " / "; match exact stream identity.
    return normalized(label.split(" / ", 1)[0]).replace(" ", "").replace("_", "")


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
    for match in WORD_DATE.finditer(normalized(label)):
        try:
            found.add(date(year, MONTHS[match[2]], int(match[1])))
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
    lesson_type: str | None = None,
    first_week_anchor: date | None = None,
) -> LinkChoice:
    """Never publish a guessed teacher/stream URL; prefer exact date, then permanent."""
    if not teacher.strip():
        return LinkChoice(LinkState.IDENTITY_MISSING, None, ())
    surname = normalized(teacher.split()[0])
    expected_streams = _streams(group_label)
    token = _group_token(group_label)
    if len(token) < 10:
        token = ""
    dated = []
    permanent = []
    identity_matches = []
    undated = []
    teacher_section_matches = []
    teacher_section_general = []
    lesson_kinds = _kind(lesson_type)
    for link in links:
        label = normalized(INLINE_JOIN.sub(" ", link.label)).replace("_", " ")
        compact = label.replace(" ", "").replace("_", "")
        if not re.search(rf"(?<!\w){re.escape(surname)}(?!\w)", label):
            continue
        # Scoped teacher headings may contain a general permanent link for the
        # teacher's lectures/practicals, with no explicit stream. Do not accept
        # a different stream or an explicitly conflicting lesson type.
        offered_streams = _streams(label)
        exact_stream = _same_stream(expected_streams, offered_streams, lesson_kinds)
        if not expected_streams or not offered_streams:
            exact_stream = bool(token and token in compact)
        section_teacher = "|" in link.label and TEACHER_HEADING.fullmatch(
            link.label.split("|", 1)[0].strip()
        )
        expected = normalized(teacher).split()
        initials = re.search(
            rf"(?<!\w){re.escape(surname)}\s+([а-я])\.?\s*([а-я])(?:\.|\b)", label
        )
        if (
            len(expected) >= 3
            and initials
            and [part[0] for part in expected[1:3]] != list(initials.groups())
        ):
            continue
        explicit_stream = re.search(r"об-?\d{2}|сгя|\bгрупп\w*|\bпоток\w*", label)
        offered_kinds = _kind(label)
        if lesson_kinds and offered_kinds and not lesson_kinds.intersection(offered_kinds):
            continue
        kind_matches = bool(lesson_kinds.intersection(offered_kinds))
        generic_permanent = not offered_kinds and "постоянн" in label
        if not exact_stream and not (
            section_teacher and not explicit_stream and (kind_matches or generic_permanent)
        ):
            continue
        if not _recurrence_matches(label, day, starts, first_week_anchor):
            continue
        times = {
            time(int(match[1]), int(match[2]))
            for match in TIME_RANGE.finditer(link.label)
            if int(match[1]) < 24 and int(match[2]) < 60
        }
        if starts is not None and times and starts not in times:
            continue
        identity_matches.append(link)
        found_dates = _lesson_dates(label, year=day.year)
        if not exact_stream:
            if day in found_dates or (not found_dates and "постоянн" in label):
                teacher_section_matches.append(link)
            elif not found_dates and not EXPLICITLY_TEMPORARY.search(label):
                teacher_section_general.append(link)
            continue
        if found_dates and day in found_dates:
            dated.append(link)
        elif not found_dates and "постоянн" in label:
            permanent.append(link)
        elif not found_dates and not EXPLICITLY_TEMPORARY.search(label):
            # A Moodle "Гиперссылка" with exact teacher AND stream identity
            # is a usable meeting when it is the only such candidate. Multiple
            # different links stay unverified rather than choosing at random.
            undated.append(link)
    matches = dated or permanent
    unique = {link.url: link for link in matches}
    if len(unique) == 1:
        return LinkChoice(LinkState.MATCHED, next(iter(unique)), tuple(unique.values()))
    if unique:
        return LinkChoice(LinkState.AMBIGUOUS, None, tuple(unique.values()), True)
    undated_unique = {link.url: link for link in undated}
    if len(undated_unique) == 1:
        return LinkChoice(
            LinkState.MATCHED, next(iter(undated_unique)), tuple(undated_unique.values())
        )
    if undated_unique:
        return LinkChoice(LinkState.TIMING_UNVERIFIED, None, tuple(undated_unique.values()), True)
    if teacher_section_matches or teacher_section_general:
        scoped = {link.url: link for link in teacher_section_matches or teacher_section_general}
        if len(scoped) == 1:
            return LinkChoice(LinkState.MATCHED, next(iter(scoped)), tuple(scoped.values()))
        if scoped:
            return LinkChoice(LinkState.AMBIGUOUS, None, tuple(scoped.values()), True)
    if identity_matches:
        return LinkChoice(LinkState.TIMING_UNVERIFIED, None, tuple(identity_matches))
    return LinkChoice(LinkState.NOT_FOUND, None, ())
