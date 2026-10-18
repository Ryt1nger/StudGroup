"""Pick a card's subject from the group's timetable, with a hashtag as a hint.

Pure and offline: no database, no model call. The answer is always one of the supplied timetable
subject names (never invented), or nothing. Two signals, in this order:

1. a hashtag that unambiguously abbreviates exactly one timetable subject (``#матан``);
2. the lesson in progress when a message was sent, or one that ended a few minutes earlier.

For a card with several source messages the signals are combined by vote; a split vote yields no
subject, because a wrong subject is worse than a missing one.
"""

import re
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from studgroup.subject_identity import match_key

AFTER_LESSON_MINUTES = 30
_HASHTAG = re.compile(r"#([\w]+)", re.UNICODE)
_STOP_WORDS = frozenset({"и", "по", "как", "на", "в", "для", "о", "об"})
_GENERIC_TAGS = frozenset({"дз", "домашка", "домашнее", "задание", "всем", "важно", "срочно"})


@dataclass(frozen=True)
class Lesson:
    weekday: int  # Monday = 0
    starts: time
    ends: time
    subject: str
    week: str | None = None  # "first" | "second" for alternating weeks; None = every week


@dataclass(frozen=True)
class Resolution:
    subject: str | None
    source: str | None  # "hashtag" | "schedule" | None


def _words(name):
    return [word for word in match_key(name).split() if word not in _STOP_WORDS]


def _stem_match(tag, word):
    shared = 0
    for left, right in zip(tag, word, strict=False):
        if left != right:
            break
        shared += 1
    return shared >= max(4, min(len(tag), len(word)) - 2)


def _abbreviation(tag, words):
    """``матан`` = мат + ан: consecutive word beginnings (2+ letters each) from the first word."""
    position = 0
    for index, word in enumerate(words):
        if position >= len(tag):
            return False
        take = 0
        while (
            position + take < len(tag) and take < len(word) and tag[position + take] == word[take]
        ):
            take += 1
        if take < 2:
            return False
        position += take
        if position == len(tag):
            return index >= 1
    return False


def hashtag_subject(text, names):
    """The one timetable subject the message's hashtags point to, else None."""
    found = set()
    for raw in _HASHTAG.findall(text or ""):
        tag = match_key(raw).replace(" ", "")
        if len(tag) < 3 or tag in _GENERIC_TAGS:
            continue
        hits = {
            name
            for name in names
            if any(_stem_match(tag, word) for word in _words(name)[:2])
            or _abbreviation(tag, _words(name))
        }
        if len(hits) == 1:
            found |= hits
    return next(iter(found)) if len(found) == 1 else None


def week_kind(day: date, first_week_anchor: date) -> str:
    """ "first" for the anchor's week and every second week after it, else "second"."""
    return "first" if ((day - first_week_anchor).days // 7) % 2 == 0 else "second"


def schedule_subject(
    sent_at, lessons, *, timezone, after_minutes=AFTER_LESSON_MINUTES, first_week_anchor=None
):
    """Subject of the lesson running at ``sent_at`` or just finished; the running one wins."""
    local = sent_at.astimezone(ZoneInfo(timezone))
    kind = week_kind(local.date(), first_week_anchor) if first_week_anchor else None
    today = [
        lesson
        for lesson in lessons
        if lesson.weekday == local.weekday() and (lesson.week is None or lesson.week == kind)
    ]
    clock = local.time()
    running = [lesson for lesson in today if lesson.starts <= clock <= lesson.ends]
    if running:
        return running[0].subject
    window = timedelta(minutes=after_minutes)
    recent = []
    for lesson in today:
        ended = datetime.combine(local.date(), lesson.ends)
        if timedelta(0) < datetime.combine(local.date(), clock) - ended <= window:
            recent.append((ended, lesson.subject))
    return max(recent)[1] if recent else None


def resolve_card_subject(
    messages,
    lessons,
    names,
    *,
    timezone,
    covered_from=None,
    covered_to=None,
    first_week_anchor=None,
):
    """Combine the card's source messages ``[(sent_at, text), ...]`` into one resolution."""
    if not messages:
        return Resolution(None, None)
    tagged = {hashtag_subject(text, names) for _, text in messages} - {None}
    if len(tagged) == 1:
        return Resolution(next(iter(tagged)), "hashtag")
    if len(tagged) > 1:
        return Resolution(None, None)
    votes = Counter()
    for sent_at, _ in messages:
        day = sent_at.astimezone(ZoneInfo(timezone)).date()
        if (covered_from and day < covered_from) or (covered_to and day > covered_to):
            continue
        subject = schedule_subject(
            sent_at, lessons, timezone=timezone, first_week_anchor=first_week_anchor
        )
        if subject:
            votes[subject] += 1
    if not votes:
        return Resolution(None, None)
    (top, count), *rest = votes.most_common()
    if count * 2 > len(messages) and not (rest and rest[0][1] == count):
        return Resolution(top, "schedule")
    return Resolution(None, None)
