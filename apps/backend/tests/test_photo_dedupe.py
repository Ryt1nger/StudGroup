"""One card shows one photo per distinct content."""

from types import SimpleNamespace

from studgroup.photo_dedupe import distinct

BOARD = "20/3 ст 69-70 док-во теорем предел функции эквивалентные бесконечно малые"
CLOSE_UP = "20/3 ст 69-70 док-во теорем"
FORMULAS = "при x стремится к 0 sin kx эквивалентно kx tg kx arctg kx arcsin kx cos kx"


def photo(
    ident, text, *, width=1000, height=800, size=100, summary="", state="ready", kind="photo"
):
    return SimpleNamespace(
        id=ident,
        kind=kind,
        processing_state=state,
        photo_text=text,
        photo_summary=summary,
        width=width,
        height=height,
        size_bytes=size,
    )


def ids(items):
    return [item.id for item in items]


def test_two_shots_of_one_board_keep_only_the_one_that_reads_best():
    wide = photo("wide", BOARD, width=1280, height=720)
    close = photo("close", CLOSE_UP, width=1280, height=400)
    assert ids(distinct([wide, close])) == ["wide"]
    assert ids(distinct([close, wide])) == ["wide"]


def test_photos_with_different_content_are_all_kept():
    board, formulas = photo("board", BOARD), photo("formulas", FORMULAS)
    assert ids(distinct([board, formulas])) == ["board", "formulas"]


def test_three_shots_two_of_them_the_same_board():
    items = [photo("zoom", BOARD), photo("crop", CLOSE_UP), photo("other", FORMULAS)]
    assert ids(distinct(items)) == ["zoom", "other"]


def test_equal_text_prefers_the_higher_resolution():
    low = photo("low", BOARD, width=640, height=480)
    high = photo("high", BOARD, width=1920, height=1080)
    assert ids(distinct([low, high])) == ["high"]


def test_unread_photos_and_files_are_never_hidden():
    items = [
        photo("a", BOARD),
        photo("unread", "", state="metadata_only"),
        photo("failed", "", state="failed"),
        photo("doc", BOARD, kind="document"),
    ]
    assert ids(distinct(items)) == ["a", "unread", "failed", "doc"]


def test_a_photo_with_text_is_not_a_duplicate_of_one_without():
    assert ids(distinct([photo("text", BOARD), photo("blank", "")])) == ["text", "blank"]


def test_summaries_decide_only_when_neither_photo_has_readable_text():
    left = photo("l", "", summary="Белая доска с рукописными формулами по математике")
    right = photo("r", "", summary="Доска с рукописными формулами по математике белая")
    other = photo("o", "", summary="Слайд презентации с таблицей результатов опроса")
    assert ids(distinct([left, right, other])) == ["l", "o"] or ids(
        distinct([left, right, other])
    ) == ["r", "o"]
