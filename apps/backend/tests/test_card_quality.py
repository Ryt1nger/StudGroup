"""Quality of student-facing cards: clean text, honest titles, materials, neighbour context."""

import re
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import UUID

import pytest

from studgroup.academic_context import build_context
from studgroup.analysis.card_editor import edit_card
from studgroup.analysis.card_materials import derive_materials, link_kind, summary_label
from studgroup.analysis.contracts import (
    AnalysisInput,
    EvidenceRef,
    ProposedAcademicItem,
    SourceAttachment,
    SourceMessage,
)
from studgroup.analysis.extraction_strategy import validate_extraction_adapted
from studgroup.analysis.text_hygiene import clean_text, clean_title, neutral_title

GROUP = UUID("00000000-0000-0000-0000-000000000002")
T0 = datetime(2026, 10, 12, 9, tzinfo=UTC)
CHAT = -1004421204622
REPORT = (
    "#орг Каждый делает доклад-презентацию о выдающихся героях (персоналиях) РФ. "
    "Например, учёные, выдающиеся исторические личности и тд."
)


def msg(mid, text="", *, minutes=0, reply=None, topic=591, files=(), role=None):
    return SourceMessage(
        telegram_message_id=mid,
        revision=1,
        message_date=T0 + timedelta(minutes=minutes),
        text=text,
        reply_to_message_id=reply,
        topic_id=topic,
        author_role=role,
        attachments=[
            SourceAttachment(kind=kind, file_name=name, media_group_id=album)
            for kind, name, album in files
        ],
    )


def proposal(mids, **patch):
    values = {
        "destination": "task",
        "semantic_kind": "homework",
        "subject": None,
        "title": "Подготовить доклад-презентацию о выдающихся личностях России",
        "description": "Каждый готовит доклад-презентацию о выдающихся героях РФ.",
        "deadline": {"at": None, "date_only": False, "basis": "unknown"},
        "audience": {"scope": "group", "label": None},
        "confidence": 90,
        "attention_reasons": [],
        "evidence": [
            EvidenceRef(telegram_message_id=m, revision=1, fact_paths=["title", "description"])
            for m in mids
        ],
    }
    return ProposedAcademicItem.model_validate(values | patch)


# -- titles and descriptions -----------------------------------------------------------------


def test_tagged_message_is_cleaned_and_keeps_every_fact():
    text = "#матан Всем привет! Решить №12 ,№14 на стр. 43-44 до пятницы.  Сдать в PDF @starosta"
    cleaned = clean_text(text)
    assert "#" not in cleaned and "@" not in cleaned and "привет" not in cleaned.casefold()
    assert cleaned.startswith("Решить")
    for fact in ("№12", "№14", "43-44", "пятницы", "PDF"):
        assert fact in cleaned
    assert re.findall(r"\d+", cleaned) == re.findall(r"\d+", re.sub(r"#\w+", "", text))


def test_cleaning_does_not_touch_links_or_numbers():
    text = "Читать https://moodle.example.edu/course/view.php?id=7#sect-3 ,глава 2.5"
    cleaned = clean_text(text)
    assert "https://moodle.example.edu/course/view.php?id=7#sect-3" in cleaned
    assert "глава 2.5" in cleaned and ", глава" in cleaned


def test_bad_ai_title_from_tags_is_replaced_by_a_neutral_source_title_not_invented():
    source = msg(1, REPORT)
    for bad in ("#орг доклад презентация", "Учебное задание по предмету Культура", "Задание"):
        card = edit_card(proposal([1], title=bad), [source])
        assert card.used_fallback and card.visibility == "headman_attention"
        # rebuilt from the evidenced description, not copied from the first sentence
        assert card.title == "Подготовить доклад-презентацию о выдающихся героях РФ"
        assert "#" not in card.description and "Учебное задание" not in card.title


def test_good_ai_title_survives_and_is_scrubbed_of_tags():
    card = edit_card(
        proposal([1], title="#орг Подготовить доклад-презентацию о выдающихся героях РФ"),
        [msg(1, REPORT)],
    )
    assert not card.used_fallback and card.visibility == "normal"
    assert card.title == "Подготовить доклад-презентацию о выдающихся героях РФ"


def test_title_without_reliable_text_is_neutral_not_invented():
    assert neutral_title("#орг", "Задание") == "Задание: содержание в сообщении чата"
    card = edit_card(
        proposal([1], title="Задание", description=None),
        [msg(1, "", files=[("document", "a.pdf", None)])],
    )
    assert card.title == "Задание: уточнить по сообщению в чате"
    assert "title_insufficient_data" in card.attention_reasons
    assert card.description == "Содержание находится в приложенном исходном сообщении."


def test_long_title_is_bounded_without_cutting_a_word():
    title = clean_title("Подготовить " + "очень длинное описание задания " * 8)
    assert len(title) <= 91 and title.endswith("…") and "  " not in title


def test_service_tag_is_never_a_subject():
    card = edit_card(proposal([1], subject="орг"), [msg(1, REPORT)])
    assert card.subject is None and "service_tag_not_subject" in card.attention_reasons


def test_fact_loss_is_impossible_when_ai_description_invents_numbers():
    card = edit_card(
        proposal([1], description="Сделать 5 слайдов и доклад-презентацию."), [msg(1, REPORT)]
    )
    assert card.used_fallback and "unsupported_numeric_fact" in card.attention_reasons
    assert "5 слайдов" not in card.description


# -- adapter: audience, identity of changes -----------------------------------------------------


def _extraction(proposals, messages):
    from test_analysis_hybrid import envelope

    data = AnalysisInput(
        group_id=GROUP,
        group_timezone="Europe/Moscow",
        source_revision=1,
        target_message_ids=[messages[-1].telegram_message_id],
        messages=messages,
    )
    return validate_extraction_adapted(envelope(proposals), data).result


def _raw(**patch):
    base = {
        "semantic_kind": "homework",
        "subject": None,
        "title": "Перенос",
        "description": "Перенесли на пятницу.",
        "confidence": 80,
        "audience": {"scope": "group", "label": None},
        "deadline": {"at": None, "date_only": False, "basis": "unknown"},
        "evidence": [{"telegram_message_id": 2, "revision": 1, "fact_paths": ["description"]}],
    }
    return base | patch


def test_change_without_trusted_identity_becomes_a_held_proposal_not_a_silent_edit():
    messages = [msg(1, "Контрольная в среду"), msg(2, "Перенесли на пятницу", reply=1, minutes=5)]
    result = _extraction([_raw(action="reschedule")], messages)
    (item,) = result.accepted
    assert item.action == "create" and "unconfirmed_change" in item.attention_reasons
    assert result.adapter_actions["change_without_identity_held"] == 1
    card = edit_card(item, messages)
    assert card.visibility == "held"


def test_cancellation_without_identity_is_rejected_and_reported():
    messages = [msg(1, "Контрольная в среду"), msg(2, "Нет, это уже отменили", minutes=5)]
    result = _extraction([_raw(action="cancel")], messages)
    assert not result.accepted
    from test_analysis_hybrid import issue_codes

    assert len(result.rejected) == 1 and "mutation_identity_missing" in issue_codes(result)


# -- materials ---------------------------------------------------------------------------------


def test_materials_from_replies_albums_links_and_files_without_text():
    task = msg(1, REPORT, role="headman")
    pdf = msg(2, "", minutes=2, reply=1, files=[("document", "Требования.pdf", None)])
    caption = msg(3, "Образец", minutes=3, reply=1, files=[("document", "Образец.docx", None)])
    photos = [
        msg(4, "", minutes=4, files=[("photo", None, "album")]),
        msg(5, "", minutes=4, files=[("photo", None, "album")]),
    ]
    link = msg(
        6, "Лекция: https://disk.yandex.ru/d/abc, сайт https://example.org/x.", minutes=5, reply=1
    )
    unrelated = msg(7, "", minutes=8, files=[("photo", None, None)])
    far = msg(8, "", minutes=600, files=[("document", "other.pdf", None)])
    cited = [task, photos[0]]
    confirmed, candidates = derive_materials(
        cited, [task, pdf, caption, *photos, link, unrelated, far], CHAT
    )
    titles = [(m["group"], m["title"]) for m in confirmed]
    assert ("file", "Требования.pdf") in titles and ("file", "Образец.docx") in titles
    assert sum(1 for g, _ in titles if g == "photo") == 2  # the album travels together
    assert {m["kind"] for m in confirmed if m["group"] == "link"} == {"cloud", "site"}
    assert summary_label(confirmed) == "Фото (2), Файлы (2), Ссылки (2)"
    assert all(m["contents_read"] is False for m in confirmed)
    assert all(m["telegram_link"].startswith("https://t.me/c/4421204622/") for m in confirmed)
    assert [m["message_id"] for m in candidates] == [7]  # nearby but unproven: not attached
    assert all(m["message_id"] != 8 for m in confirmed + candidates)


def test_materials_are_not_duplicated_for_repeated_references():
    task = msg(1, "см. https://example.org/a https://example.org/a")
    confirmed, _ = derive_materials([task, task], [task, task], CHAT)
    assert len(confirmed) == 1


@pytest.mark.parametrize(
    ("url", "kind"),
    [
        ("https://docs.google.com/document/d/1", "cloud"),
        ("https://moodle.university.ru/mod/assign", "lms"),
        ("https://t.me/c/1/2", "telegram"),
        ("https://example.org", "site"),
    ],
)
def test_link_kinds(url, kind):
    assert link_kind(url) == kind


# -- context: files without caption reach the model --------------------------------------------


def _raw_message(mid, text, minutes, *, reply=None, attachments=(), sender="a", topic=591):
    return SimpleNamespace(
        message_id=mid,
        reply_to_message_id=reply,
        message_date=T0 + timedelta(minutes=minutes),
        text=text,
        message_thread_id=topic,
        topic_name="Организационное",
        author_alias=sender,
        author_role=None,
        attachment_kinds=[a["kind"] for a in attachments],
        attachments=list(attachments),
    )


def test_context_keeps_captionless_reply_files_and_albums_but_not_far_unrelated_ones():
    doc = {"kind": "document", "file_name": "t.pdf", "media_group_id": None}
    album = {"kind": "photo", "file_name": None, "media_group_id": "g"}
    rows = [
        _raw_message(1, REPORT, 0),
        _raw_message(2, "", 1, reply=1, attachments=[doc]),
        _raw_message(3, "", 2, attachments=[album]),
        _raw_message(4, "", 2, attachments=[album]),
        _raw_message(5, "", 60 * 24 * 5, attachments=[doc], topic=7),
    ]
    context = build_context(rows, 1, neighborhood=0)
    ids = {row["message_id"] for row in context}
    assert {1, 2} <= ids and 5 not in ids
    assert all("author_role" in row for row in context)
    file_row = next(row for row in context if row["message_id"] == 2)
    assert file_row["text"] == "" and file_row["attachments"][0]["file_name"] == "t.pdf"


# -- title builder: structured facts first, never a raw first sentence --------------------------


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (
            "Ребят, тут короче по истории нужно сделать презентацию про Петра I, сдать на семинаре",
            "Подготовить презентацию про Петра I",
        ),
        (
            "#орг Всем привет!! Напоминаю, надо решить №12,14 на стр.43, до пятницы",
            "Решить №12,14 на стр.43",
        ),
        (
            "Сделать презинтацию по теме Клетка. Сдать в четверг",
            "Подготовить презентацию по теме Клетка",
        ),
        ("1) конспект §5\n2) задачи 3-7", "Конспект §5 и задачи 3-7"),
        (
            "Нужно написать реферат про Ломоносова и сдать до 20.10",
            "Написать реферат про Ломоносова",
        ),
        (
            "к следующей паре определиться с командами на проект и темой",
            "Определиться с командами на проект и темой",
        ),
        ("Каждый делает доклад о Менделееве. Тема на выбор", "Подготовить доклад о Менделееве"),
        ("ок", "Задание: уточнить по сообщению в чате"),
    ],
)
def test_rebuilt_titles_are_concrete_without_copying_the_first_sentence(source, expected):
    from studgroup.analysis.title_builder import build_title

    assert build_title(None, source, "Задание")[0] == expected


def test_trusted_description_is_the_first_structured_source_for_a_rebuilt_title():
    source = msg(
        1, "Ребят, тут короче по истории нужно сделать презентацию про Петра I, сдать на семинаре"
    )
    card = edit_card(
        proposal(
            [1],
            title="Задание",
            subject=None,
            description="Подготовить презентацию про Петра I. Сдать на семинаре.",
        ),
        [source],
    )
    assert card.title == "Подготовить презентацию про Петра I"
    assert card.used_fallback and "generic_title" in card.attention_reasons


def test_untrusted_description_is_not_used_for_the_title():
    source = msg(1, "Подготовить презентацию про Петра")
    card = edit_card(
        proposal([1], title="Задание", description="Подготовить 12 слайдов про Петра"), [source]
    )
    assert "12" not in card.title and card.title == "Подготовить презентацию про Петра"


def test_good_model_title_is_left_untouched():
    for good in ("Решить задачи 3–7 со страницы 41", "Подготовить доклад про клетку"):
        source = msg(1, f"Нужно {good[0].lower()}{good[1:]}, 3–7 41")
        card = edit_card(proposal([1], title=good, description=good + ".", subject=None), [source])
        assert card.title == good and not card.used_fallback


def test_title_with_preamble_from_the_model_is_rebuilt_not_published():
    source = msg(1, "Ребят, короче надо подготовить доклад про клетку")
    card = edit_card(
        proposal(
            [1],
            title="Ребят, короче надо подготовить доклад про клетку",
            description="Подготовить доклад про клетку.",
            subject=None,
        ),
        [source],
    )
    assert card.title == "Подготовить доклад про клетку" and "noisy_title" in card.attention_reasons
