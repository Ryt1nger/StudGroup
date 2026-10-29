"""Local pixel/word coverage; ambiguous layouts stay visible. No network or AI."""

import io
import re
import warnings

from PIL import Image, UnidentifiedImageError

VERSION = "visual-word-coverage-1"
_RANGE = re.compile(r"^(\d{1,4})[–—](\d{1,4})$")
_TOKEN = re.compile(r"[^\w–—+*/^=−<>-]", re.UNICODE)


def _runs(flags):
    result, start = [], None
    for index, present in enumerate([*flags, False]):
        if present and start is None:
            start = index
        elif not present and start is not None:
            result.append((start, index))
            start = None
    return result


def _regions(ink, count):
    w, h = ink.size
    runs = _runs([ink.crop((x, 0, x + 1, h)).getbbox() is not None for x in range(w)])
    if not runs or len(runs) < count:
        return None
    gaps = [(runs[i + 1][0] - runs[i][1], i) for i in range(len(runs) - 1)]
    cuts = sorted(gaps, reverse=True)[: count - 1] if count > 1 else []
    remaining = [gap for gap, index in gaps if index not in {index for _, index in cuts}]
    if cuts and (
        min(gap for gap, _ in cuts) < max(3, h * 0.16)
        or remaining
        and min(gap for gap, _ in cuts) < max(remaining) * 1.5
    ):
        return None
    borders = [-1, *sorted(index for _, index in cuts), len(runs) - 1]
    return [(runs[borders[i] + 1][0], runs[borders[i + 1]][1]) for i in range(count)]


def build_coverage(image, reading):
    """Prove all ink belongs to readable transcribed text; otherwise return no proof.

    Non-text blocks and tables need stronger geometric evidence, so are never hidden here.
    Masks are private derivatives retained only with the source image job, never sent to AI.
    """
    if not reading.complete or reading.uncertain_fragments or not reading.blocks:
        return None
    if any(block.kind not in {"text", "list"} for block in reading.blocks):
        return None
    lines = [line.split() for line in reading.extracted_text.splitlines() if line.strip()]
    if not lines or sum(map(len, lines)) > 300:
        return None
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(image)) as original:
                if original.width * original.height > 16_000_000:
                    return None
                gray = original.convert("L")
        if sum(gray.histogram()[230:]) / (gray.width * gray.height) < 0.80:
            return None
        ink = gray.point(lambda value: 255 if value < 210 else 0)
        rows = _runs(
            [ink.crop((0, y, ink.width, y + 1)).getbbox() is not None for y in range(ink.height)]
        )
        if len(rows) != len(lines):
            return None
        result = []
        for words, (top, bottom) in zip(lines, rows):
            stripe = ink.crop((0, top, ink.width, bottom))
            regions = _regions(stripe, len(words))
            if regions is None:
                return None
            visual_words = []
            for text, (left, right) in zip(words, regions):
                patch = stripe.crop((left, 0, right, stripe.height))
                if patch.getbbox() is None:
                    return None
                patch = patch.crop(patch.getbbox())
                token = _TOKEN.sub("", text.casefold().replace("ё", "е")) or text
                mask = (
                    patch.resize((48, 24), Image.Resampling.LANCZOS)
                    .point(lambda value: 255 if value > 128 else 0)
                    .convert("1")
                )
                visual_words.append(
                    {
                        "text": token,
                        "height": patch.height,
                        "width": patch.width,
                        "box": [left, top, right, bottom],
                        "mask": mask.tobytes().hex(),
                    }
                )
            result.append(visual_words)
        return {
            "version": VERSION,
            "width": gray.width,
            "height": gray.height,
            "lines": result,
            "readability": min(word["height"] for line in result for word in line),
        }
    except (
        OSError,
        ValueError,
        Image.DecompressionBombWarning,
        Image.DecompressionBombError,
        UnidentifiedImageError,
    ):
        return None


def _range(token):
    match = _RANGE.fullmatch(token)
    if not match:
        return None
    start, end = map(int, match.groups())
    return (start, end) if start <= end and end - start <= 500 else None


def _word_covers(target, source, *, ordinal_range=False):
    if target["height"] < max(10, source["height"] * 0.85):
        return False
    if target["text"] == source["text"]:
        return target["mask"] == source["mask"]
    if not ordinal_range:
        return False
    a, b = _range(target["text"]), _range(source["text"])
    if not a or not b or not (a[0] <= b[0] and a[1] >= b[1]):
        return False
    ratio = (target["width"] / max(1, len(target["text"]))) / (
        source["width"] / max(1, len(source["text"]))
    )
    return 0.8 <= ratio <= 1.25


def visually_covers(target, source):
    if (
        not target
        or not source
        or target.get("version") != VERSION
        or source.get("version") != VERSION
    ):
        return False
    position = 0
    try:
        for source_line in source["lines"]:
            found = False
            for index in range(position, len(target["lines"])):
                line = target["lines"][index]
                for offset in range(len(line) - len(source_line) + 1):
                    ordinal_labels = {
                        "задачи",
                        "задача",
                        "упражнения",
                        "упражнение",
                        "пункты",
                        "пункт",
                        "задания",
                        "задание",
                        "exercises",
                        "exercise",
                        "tasks",
                        "task",
                        "items",
                    }
                    if all(
                        _word_covers(
                            a,
                            b,
                            ordinal_range=(
                                position > 0
                                and offset + position > 0
                                and source_line[position - 1]["text"] in ordinal_labels
                                and line[offset + position - 1]["text"] in ordinal_labels
                            ),
                        )
                        for position, (a, b) in enumerate(zip(line[offset:], source_line))
                    ):
                        position = index + 1
                        found = True
                        break
                if found:
                    break
            if not found:
                return False
        return True
    except (KeyError, ValueError, TypeError):
        return False


def coverage_metadata(profiles):
    return {
        ident: {
            "display_covers": [
                str(other)
                for other, profile in profiles.items()
                if other != ident and visually_covers(target, profile)
            ],
            "display_readability": target["readability"] if target else None,
        }
        for ident, target in profiles.items()
    }


def minimum_cover(items):
    """Exact minimum up to 16 candidates; conservative greedy cover above that bound."""
    if not items:
        return []
    ids = [str(row["attachment_id"]) for row in items]
    masks = []
    for i, row in enumerate(items):
        covers = set(row.get("display_covers") or ())
        masks.append(
            sum(
                1 << j
                for j, ident in enumerate(ids)
                if j == i
                or ident in covers
                or row.get("pixel_sha256")
                and row.get("pixel_sha256") == items[j].get("pixel_sha256")
            )
        )
    full = (1 << len(items)) - 1
    quality = lambda i: (
        items[i].get("display_readability") or 0,
        (items[i].get("width") or 0) * (items[i].get("height") or 0),
        -i,
    )
    order = sorted(range(len(items)), key=quality, reverse=True)
    chosen = []
    covered = 0
    while covered != full:
        best = max(order, key=lambda i: ((masks[i] & ~covered).bit_count(), quality(i)))
        chosen.append(best)
        covered |= masks[best]
    if len(items) <= 16:
        best = chosen

        def search(picked, mask):
            nonlocal best
            if mask == full:
                if len(picked) < len(best):
                    best = picked
                return
            if len(picked) >= len(best):
                return
            missing = next(j for j in range(len(items)) if not mask & (1 << j))
            for i in order:
                if masks[i] & (1 << missing):
                    search([*picked, i], mask | masks[i])

        search([], 0)
        chosen = best
    return [row for i, row in enumerate(items) if i in set(chosen)]
