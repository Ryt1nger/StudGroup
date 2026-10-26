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


def distinct(attachments, anchors=()):
    """The attachments without near-duplicate photos; order and non-photos are untouched.

    ``anchors`` are photos that were already analysed in earlier packages: a new photo repeating one
    of them is hidden whatever its quality, because its content is already on a card.
    """
    photos = [a for a in attachments if a.kind == "photo" and a.processing_state == "ready"]
    known = [a for a in anchors if a.kind == "photo" and a.processing_state == "ready"]
    hidden = {p.id for p in photos if any(same_content(k, p) for k in known)}
    for candidate in sorted(photos, key=quality, reverse=True):
        if candidate.id in hidden:
            continue
        for other in photos:
            if other is not candidate and other.id not in hidden and same_content(candidate, other):
                hidden.add(other.id)
    return [a for a in attachments if a.id not in hidden]


def visual_fingerprint(image):
    """Local difference hash plus aspect ratio. A hint, never semantic evidence."""
    import io
    import warnings

    from PIL import Image, UnidentifiedImageError

    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(image)) as picture:
                if picture.width * picture.height > 40_000_000:
                    return None
                aspect = round(picture.width / picture.height, 3)
                small = picture.convert("L").resize((9, 8))
                pixels = [small.getpixel((x, y)) for y in range(8) for x in range(9)]
        bits = sum((pixels[y * 9 + x] > pixels[y * 9 + x + 1]) << (y * 8 + x)
                   for y in range(8) for x in range(8))
        return f"{aspect}:{bits:016x}"
    except (OSError, ValueError, UnidentifiedImageError, Image.DecompressionBombWarning,
            Image.DecompressionBombError):
        return None


def _visually_close(left, right):
    if not left or not right:
        return False
    try:
        aspect_left, bits_left = left.split(":")
        aspect_right, bits_right = right.split(":")
        return (abs(float(aspect_left) - float(aspect_right)) < 0.02
                and (int(bits_left, 16) ^ int(bits_right, 16)).bit_count() <= 2)
    except (ValueError, TypeError):
        return False


def select_evidence_images(items, required_ids=None):
    """Shared display-only selection. Unique content is never suppressed by text overlap.

    Inputs are attachment metadata dictionaries from the same immutable analysis snapshot.
    No source/reading is deleted and no one/two-image ceiling is imposed.
    """
    required = set(required_ids or ())
    selected = []
    for item in items:
        if item.get("image_analysis_state") is None:
            selected.append(item)
            continue
        if required and str(item.get("attachment_id")) not in required:
            continue
        if item.get("image_analysis_state") != "ready":
            continue
        types = item.get("semantic_types") or []
        if not any(kind in types for kind in ("homework", "assessment", "material")):
            continue
        duplicate = None
        for index, earlier in enumerate(selected):
            exact = item.get("pixel_sha256") and item.get("pixel_sha256") == earlier.get("pixel_sha256")
            same_facts = (item.get("content_signature") and
                          item.get("content_signature") == earlier.get("content_signature"))
            if exact or (same_facts and _visually_close(item.get("visual_hash"), earlier.get("visual_hash"))):
                duplicate = index
                break
        if duplicate is None:
            selected.append(item)
        else:
            old = selected[duplicate]
            quality_new = (item.get("width") or 0) * (item.get("height") or 0)
            quality_old = (old.get("width") or 0) * (old.get("height") or 0)
            if quality_new > quality_old:
                selected[duplicate] = item
    return selected
