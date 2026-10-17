"""Reliable merging of an assignment with its supplements, independent of model citations."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from test_analysis_hybrid import envelope

from studgroup.analysis.card_editor import edit_card
from studgroup.analysis.contracts import AnalysisInput, SourceAttachment, SourceMessage
from studgroup.analysis.extraction_strategy import (
    prepare_extraction_request,
    validate_extraction_adapted,
)
from studgroup.analysis.linking import candidates_payload, reconcile_validation

GROUP = UUID("00000000-0000-0000-0000-000000000003")
T0 = datetime(2026, 10, 12, 9, tzinfo=UTC)
TASK = "Каждый делает доклад-презентацию о выдающихся героях РФ"


def msg(mid, text="", *, minutes=0, reply=None, topic=591, who="a", role="headman", files=()):
    return SourceMessage(
        telegram_message_id=mid,
        revision=1,
        message_date=T0 + timedelta(minutes=minutes),
        text=text,
        reply_to_message_id=reply,
        topic_id=topic,
        author_alias=f"member-{who}",
        author_role=role,
        attachments=[
            SourceAttachment(kind=kind, file_name=name, media_group_id=album)
            for kind, name, album in files
        ],
    )


def raw(mids, title="Подготовить доклад-презентацию о героях РФ", **patch):
    base = {
        "semantic_kind": "homework",
        "subject": None,
        "title": title,
        "description": "Каждый готовит доклад-презентацию о выдающихся героях РФ.",
        "confidence": 90,
        "audience": {"scope": "group", "label": None},
        "deadline": {"at": None, "date_only": False, "basis": "unknown"},
        "evidence": [
            {"telegram_message_id": m, "revision": 1, "fact_paths": ["description"]} for m in mids
        ],
    }
    return base | patch


def run(proposals, messages):
    data = AnalysisInput(
        group_id=GROUP,
        group_timezone="Europe/Moscow",
        source_revision=1,
        target_message_ids=[messages[0].telegram_message_id],
        messages=messages,
    )
    return validate_extraction_adapted(envelope(proposals), data).result, data


def ids(proposal):
    return [ref.telegram_message_id for ref in proposal.evidence]


def test_list_of_topics_in_a_reply_is_attached_even_if_the_model_cited_only_the_task():
    messages = [
        msg(1, TASK),
        msg(2, "1) Исторические деятели 2) Творцы 3) Учёные", minutes=2, reply=1),
    ]
    result, data = run([raw([1])], messages)
    (card,) = result.accepted
    assert ids(card) == [1, 2]
    assert card.description.endswith("Дополнение: 1) Исторические деятели 2) Творцы 3) Учёные")
    assert result.adapter_actions["supplement_text_appended"] == 1
    assert edit_card(card, data.messages).visibility == "normal"


def test_reply_with_extra_requirement_from_the_headman_hours_later_is_merged():
    messages = [
        msg(1, TASK, who="a"),
        msg(2, "Формат — PDF, до 10 слайдов", minutes=300, reply=1, who="b"),
    ]
    result, _ = run([raw([1])], messages)
    (card,) = result.accepted
    assert ids(card) == [1, 2] and "до 10 слайдов" in card.description


def test_pdf_without_caption_in_a_reply_joins_the_sources_without_new_text():
    messages = [
        msg(1, TASK),
        msg(2, "", minutes=3, reply=1, files=[("document", "Требования.pdf", None)]),
    ]
    result, _ = run([raw([1])], messages)
    (card,) = result.accepted
    assert ids(card) == [1, 2]
    assert card.description == "Каждый готовит доклад-презентацию о выдающихся героях РФ."


def test_album_of_photos_joins_as_one_group():
    album = ("photo", None, "g1")
    messages = [
        msg(1, "Конспект лекции в фото"),
        msg(2, "", minutes=1, reply=1, files=[album]),
        msg(3, "", minutes=1, files=[album]),
        msg(4, "", minutes=1, files=[album]),
    ]
    result, _ = run([raw([1], title="Выучить конспект лекции")], messages)
    (card,) = result.accepted
    assert ids(card) == [1, 2, 3, 4]


def test_two_different_assignments_in_a_row_stay_two_cards():
    messages = [msg(1, TASK), msg(2, "Решить задачи 3–7 из учебника", minutes=1)]
    result, _ = run(
        [raw([1]), raw([2], title="Решить задачи 3–7", description="Решить задачи 3–7.")], messages
    )
    assert [ids(card) for card in result.accepted] == [[1], [2]]
    assert not any(card.attention_reasons for card in result.accepted)


def test_one_message_with_two_assignments_does_not_hand_a_reply_to_both():
    messages = [
        msg(1, "Решить задачи 3–7, а также подготовить доклад про клетку"),
        msg(2, "", minutes=2, reply=1, files=[("document", "Образец.pdf", None)]),
    ]
    first = raw([1], title="Решить задачи 3–7", description="Решить задачи 3–7.")
    second = raw(
        [1], title="Подготовить доклад про клетку", description="Подготовить доклад про клетку."
    )
    result, _ = run([first, second], messages)
    assert [ids(card) for card in result.accepted] == [[1], [1]]
    assert all("supplement_ambiguous" in card.attention_reasons for card in result.accepted)


def test_separate_model_card_that_only_clarifies_the_task_is_merged_not_duplicated():
    messages = [msg(1, TASK), msg(2, "Темы: 1) Учёные 2) Творцы", minutes=2, reply=1)]
    clarification = raw([2], title="Темы доклада", description="Темы: 1) Учёные 2) Творцы")
    result, _ = run([raw([1]), clarification], messages)
    (card,) = result.accepted
    assert ids(card) == [1, 2] and "Учёные" in card.description
    assert [row.reason_code for row in result.rejected] == ["merged_supplement"]


def test_independent_reply_task_is_not_merged_and_stays_its_own_card():
    messages = [
        msg(1, TASK),
        msg(2, "Ещё нужно написать реферат про Ломоносова", minutes=2, reply=1),
    ]
    result, _ = run(
        [
            raw([1]),
            raw(
                [2],
                title="Написать реферат про Ломоносова",
                description="Написать реферат про Ломоносова.",
            ),
        ],
        messages,
    )
    assert [ids(card) for card in result.accepted] == [[1], [2]]


def test_student_reply_with_a_requirement_is_flagged_not_merged():
    messages = [
        msg(1, TASK),
        msg(2, "Формат PDF, 10 слайдов", minutes=2, reply=1, who="s", role="student"),
    ]
    result, _ = run([raw([1])], messages)
    (card,) = result.accepted
    assert ids(card) == [1] and "possible_missing_supplement" in card.attention_reasons


def test_chitchat_reply_changes_nothing():
    messages = [msg(1, TASK), msg(2, "спасибо!", minutes=2, reply=1, who="s", role="student")]
    result, _ = run([raw([1])], messages)
    (card,) = result.accepted
    assert ids(card) == [1] and not card.attention_reasons


def test_enumeration_without_reply_is_only_a_review_hint():
    messages = [msg(1, TASK), msg(2, "1) Учёные 2) Творцы", minutes=4)]
    result, _ = run([raw([1])], messages)
    (card,) = result.accepted
    assert ids(card) == [1] and "possible_missing_supplement" in card.attention_reasons
    assert edit_card(card, messages).visibility == "headman_attention"


def test_foreign_message_attached_by_the_model_is_held_not_merged():
    messages = [
        msg(1, TASK),
        msg(2, "Физика: лабораторная до пятницы", minutes=300, topic=7, who="z", role="student"),
    ]
    result, _ = run([raw([1, 2])], messages)
    (card,) = result.accepted
    assert "foreign_source_attached" in card.attention_reasons
    assert edit_card(card, messages).visibility == "held"


def test_adjacent_date_fragment_is_not_called_foreign():
    messages = [msg(1, TASK), msg(2, "до пятницы", minutes=1, who="b", role="student")]
    result, _ = run([raw([1, 2])], messages)
    assert "foreign_source_attached" not in result.accepted[0].attention_reasons


def test_reconcile_is_idempotent_and_never_duplicates_sources():
    messages = [msg(1, TASK), msg(2, "Формат PDF", minutes=2, reply=1)]
    result, data = run([raw([1])], messages)
    again = reconcile_validation(result, data.messages)
    assert again.accepted == result.accepted
    assert len(set(ids(again.accepted[0]))) == len(ids(again.accepted[0]))


def test_candidates_cover_later_messages_and_reach_the_model_payload():
    messages = [
        msg(1, TASK),
        msg(2, "Формат PDF", minutes=300, reply=1),
        msg(3, "", minutes=2, files=[("document", "t.pdf", None)]),
    ]
    payload = candidates_payload(messages)
    reply = next(row for row in payload if row["member_message_id"] == 2)
    assert reply["strength"] == "strong" and reply["minutes_after_anchor"] == 300
    assert any(row["relation"] == "adjacent_file" and row["strength"] == "weak" for row in payload)
    data = AnalysisInput(
        group_id=GROUP,
        group_timezone="Europe/Moscow",
        source_revision=1,
        target_message_ids=[1],
        messages=messages,
    )
    request = prepare_extraction_request(data, {})
    assert request.user_payload["link_candidates"] == payload
    assert "text" not in payload[0] and len(payload) <= 40
