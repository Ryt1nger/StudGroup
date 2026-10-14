"""Deterministic cleaning of student-facing card text; never adds or removes a fact."""

import re

HASHTAG = re.compile(r"(?<![\w/])#[\w]+", re.UNICODE)
MENTION = re.compile(r"(?<![\w/])@[A-Za-z0-9_]{3,}")
URL = re.compile(r"https?://\S+")
LEADING_ADDRESS = re.compile(
    r"^\s*(?:(?:всем|ребята|народ|коллеги|друзья|одногруппники|добрый\s+(?:день|вечер)|"
    r"доброе\s+утро|привет(?:ствую)?)[\s,!.:–—-]+)+",
    re.IGNORECASE,
)
ABBREVIATIONS = (
    (re.compile(r"(?<![\w.])итд\.?(?!\w)", re.IGNORECASE), "и т. д."),
    (re.compile(r"(?<![\w.])тд\.?(?!\w)", re.IGNORECASE), "т. д."),
    (re.compile(r"(?<![\w.])тп\.?(?!\w)", re.IGNORECASE), "т. п."),
)
# A tag is a service mark, not a discipline, unless group rules say so (never for these).
NON_SUBJECT_WORDS = {
    "орг",
    "организационное",
    "оргвопрос",
    "объявление",
    "важно",
    "всем",
    "дз",
    "hw",
    "общее",
    "info",
}


def _fix_line(line):
    parts = re.split(r"(https?://\S+)", line)
    for index, part in enumerate(parts):
        if index % 2:
            continue
        part = re.sub(r"\s+([,.;:!?)])", r"\1", part)
        part = re.sub(r"([,;:!?])(?=[^\s\d)\"»'])", r"\1 ", part)
        part = re.sub(r"\.(?=[A-Za-zА-Яа-яЁё]{2,})", ". ", part) if " " in part else part
        part = re.sub(r"\(\s+", "(", part)
        for pattern, replacement in ABBREVIATIONS:
            part = pattern.sub(replacement, part)
        parts[index] = part
    return " ".join("".join(parts).split())


def clean_text(text):
    """Strip service tags/@mentions/greetings, normalise spacing and punctuation, keep facts."""
    if not text:
        return ""
    # Hashtags are removed only outside links.
    chunks = re.split(r"(https?://\S+)", text)
    chunks = [
        chunk if index % 2 else MENTION.sub("", HASHTAG.sub("", chunk))
        for index, chunk in enumerate(chunks)
    ]
    text = "".join(chunks)
    lines = [_fix_line(line) for line in text.replace("\r", "").split("\n")]
    lines = [line.strip(" \t-–—,;:") if not re.match(r"^\s*[-–—•]\s*\S", line) else line for line in lines]
    cleaned = [line for line in lines if line]
    if not cleaned:
        return ""
    cleaned[0] = LEADING_ADDRESS.sub("", cleaned[0]) or cleaned[0]
    cleaned[0] = cleaned[0][:1].upper() + cleaned[0][1:] if cleaned[0] else cleaned[0]
    return "\n".join(cleaned)


def clean_title(text):
    """One-line title: cleaned, no trailing punctuation, bounded without cutting a word."""
    line = " ".join(clean_text(text).split())
    line = line.rstrip(" .,;:!–—-")
    if len(line) > 90:
        line = line[:90].rsplit(" ", 1)[0].rstrip(" .,;:–—-") + "…"
    return line


def neutral_title(text, label):
    """Title for a card whose AI title cannot be trusted: the cleaned first clause of the source.

    Nothing is inferred: if the source has no usable text, the title only says what kind of
    item this is and where the content is.
    """
    cleaned = clean_text(text)
    if not cleaned:
        return f"{label}: содержание в сообщении чата"
    first = re.split(r"(?<=[.!?])\s|\n", cleaned, maxsplit=1)[0]
    title = clean_title(first)
    return title or f"{label}: содержание в сообщении чата"


def is_service_subject(subject):
    return bool(subject) and " ".join(subject.casefold().lstrip("#").split()) in NON_SUBJECT_WORDS
