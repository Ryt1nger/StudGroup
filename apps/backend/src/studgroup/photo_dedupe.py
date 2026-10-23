"""One card shows one photo per distinct content, not every shot of the same thing.

Students often post the same board or page twice: a wide shot, then a close-up. After the vision
pass each photo carries the text read from it (``photo_text``) and a one-line summary. Two photos
of one subject share most of their words (a close-up is a subset of the wide shot), so the one that
reads best stays and the others are hidden from the card. Photos with different content, and
photos that were not read, are always kept. Nothing is deleted: the source message keeps them all.
"""

import re

MIN_WORDS = 4  # fewer words cannot prove two photos show the same thing
SAME_TEXT = 0.6  # share of the shorter text found in the other
SAME_SUMMARY = 0.7  # word overlap (Jaccard) of two summaries when there is no readable text
_WORD = re.compile(r"[\w]{2,}", re.UNICODE)


def words(text):
    return set(_WORD.findall((text or "").casefold().replace("ё", "е")))


def same_content(left, right):
    left_text, right_text = words(left.photo_text), words(right.photo_text)
    if len(left_text) >= MIN_WORDS and len(right_text) >= MIN_WORDS:
        shared = len(left_text & right_text)
        return shared / min(len(left_text), len(right_text)) >= SAME_TEXT
    if len(left_text) >= MIN_WORDS or len(right_text) >= MIN_WORDS:
        return False  # one photo has text the other lacks: different content
    left_summary, right_summary = words(left.photo_summary), words(right.photo_summary)
    if len(left_summary) < MIN_WORDS or len(right_summary) < MIN_WORDS:
        return False
    return len(left_summary & right_summary) / len(left_summary | right_summary) >= SAME_SUMMARY


def quality(attachment):
    """Readable text first (it carries the meaning), then resolution, then file size."""
    return (
        len(words(attachment.photo_text)),
        (attachment.width or 0) * (attachment.height or 0),
        attachment.size_bytes or 0,
    )


def distinct(attachments):
    """The attachments without near-duplicate photos; order and non-photos are untouched."""
    photos = [a for a in attachments if a.kind == "photo" and a.processing_state == "ready"]
    hidden = set()
    for candidate in sorted(photos, key=quality, reverse=True):
        if candidate.id in hidden:
            continue
        for other in photos:
            if other is not candidate and other.id not in hidden and same_content(candidate, other):
                hidden.add(other.id)
    return [a for a in attachments if a.id not in hidden]
