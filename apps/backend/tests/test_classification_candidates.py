"""№04: free discovery vs publication category, and the non-publishable candidate contour.

Synthetic fixtures only: they exercise our code with scripted model answers. They say nothing about
how DeepSeek classifies real messages, and they are not results of the real ``wave-a-spread-1``.
"""

import asyncio
import json
from typing import get_args

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from test_analysis_hybrid import TwoPassProvider, build_input, envelope, shadow_case
from test_card_text_quality import GROUP, msg

from studgroup.analysis.candidates import label_category
from studgroup.analysis.card_editor import edit_card
from studgroup.analysis.contract_prompt import example_output
from studgroup.analysis.contracts import (
    PRODUCT_CATEGORY,
    AnalysisInput,
    AnalysisOutput,
    ProposedAcademicItem,
    ValidationResult,
)
from studgroup.analysis.extraction_strategy import (
    EXTRACTION_SYSTEM_PROMPT,
    validate_extraction_adapted,
)
from studgroup.analysis.review_text import render_held_candidates, render_preview
from studgroup.analysis.shadow import run_shadow
from studgroup.analysis.writer import apply_validated_batch
from studgroup.models import AcademicDeadline, Group, Homework

pytest_plugins = ["test_schedule_api"]


def data_for(messages, target=None):
    return AnalysisInput(
        group_id=GROUP,
        group_timezone="Europe/Moscow",
        source_revision=1,
        target_message_ids=[target or messages[-1].telegram_message_id],
        messages=messages,
    )


def proposal(kind, title, description, mids=(1,), **patch):
    return {
        "semantic_kind": kind,
        "subject": None,
        "title": title,
        "description": description,
        "confidence": 90,
        "audience": {"scope": "group", "label": None},
        "deadline": {"at": None, "date_only": False, "basis": "unknown"},
        "evidence": [
            {"telegram_message_id": m, "revision": 1, "fact_paths": ["description"]} for m in mids
        ],
    } | patch


def candidate(category, title, description, mids=(1,), **patch):
    return {
        "category": category,
        "title": title,
        "description": description,
        "confidence": 80,
        "evidence": [
            {"telegram_message_id": m, "revision": 1, "fact_paths": ["description"]} for m in mids
        ],
    } | patch


def validate(messages, proposals, candidates=None, target=None):
    payload = envelope(proposals, **({} if candidates is None else {"candidates": candidates}))
    return validate_extraction_adapted(payload, data_for(messages, target)).result


# -- 1. the three product categories, as published ------------------------------------------


@pytest.mark.parametrize(
    ("text", "kind", "title", "description", "destination", "product"),
    [
        (
            "Написать эссе о роли личности в истории",
            "homework",
            "Написать эссе о роли личности в истории",
            "Нужно написать эссе о роли личности в истории.",
            "task",
            "homework",
        ),
        (
            "Будет самостоятельная по алгебре, повторите дроби",
            "assessment",
            "Самостоятельная по алгебре",
            "Самостоятельная работа по алгебре, повторить дроби.",
            "deadline",
            "check",
        ),
        (
            "Квиз по цивилизациям, готовимся по конспекту лекции",
            "test",
            "Квиз по цивилизациям",
            "Квиз по цивилизациям, подготовка по конспекту лекции.",
            "deadline",
            "check",
        ),
    ],
    ids=["essay-is-homework", "samostoyatelnaya-is-check", "quiz-is-check"],
)
def test_model_chosen_category_is_published_in_the_matching_contract(
    text, kind, title, description, destination, product
):
    messages = [msg(1, text)]
    result = validate(messages, [proposal(kind, title, description)])
    (card,) = result.accepted
    assert card.destination == destination and PRODUCT_CATEGORY[card.semantic_kind] == product
    assert result.candidates == [] and result.rejected == []
    assert edit_card(card, messages).title == title  # the 03 text rules still apply


# -- 2. events and other non-card findings are kept, never published ---------------------------


def test_student_event_is_a_held_candidate_not_a_card():
    messages = [msg(1, "В четверг после пар встреча студентов с выпускниками кафедры")]
    event = candidate(
        "event",
        "Встреча студентов с выпускниками",
        "Встреча студентов с выпускниками кафедры.",
        when_text="в четверг после пар",
        reason="Событие: карточки событий не публикуются",
    )
    result = validate(messages, [], [event])
    assert result.accepted == [] and result.explicit_empty is True
    (held,) = result.candidates
    assert held.category == "event" and held.hold_code == "category_not_published"
    assert held.origin == "model_candidate" and held.when_text == "в четверг после пар"
    assert [ref.telegram_message_id for ref in held.evidence] == [1]
    assert result.adapter_actions["candidate_from_model"] == 1


def test_event_reported_as_a_proposal_keeps_its_content_as_a_candidate():
    messages = [msg(1, "В субботу студенческий турнир по настольным играм")]
    raw = proposal("event", "Турнир по настольным играм", "Студенческий турнир в субботу.")
    result = validate(messages, [raw])
    assert result.accepted == []
    (reject,) = result.rejected
    assert reject.reason_code == "proposal_schema_invalid" and reject.salvaged is True
    (held,) = result.candidates
    assert held.category == "event" and held.origin == "rejected_proposal"
    assert held.title == "Турнир по настольным играм" and held.model_label == "event"
    assert held.proposal_ordinal == 0 and held.hold_code == "rejected_enum_value_not_allowed"
    assert held.issues and held.issues[0].error_type == "enum_value_not_allowed"


def test_organizational_instruction_is_not_homework():
    messages = [msg(1, "Старосты, принесите до пятницы копии паспортов", role="headman")]
    held = validate(
        messages,
        [],
        [candidate("organizational", "Принести копии паспортов", "Принести копии паспортов.")],
    ).candidates
    assert [c.category for c in held] == ["organizational"]
    assert "Organisational instructions" in EXTRACTION_SYSTEM_PROMPT
    assert "not homework unless the students must prepare study work" in EXTRACTION_SYSTEM_PROMPT


def test_ambiguous_useful_information_is_kept_for_review_with_the_models_doubt():
    messages = [msg(1, "Не забудьте про 25-е, преподаватель будет ждать всех")]
    result = validate(
        messages,
        [],
        [
            candidate(
                "other",
                None,
                "Преподаватель будет ждать всех 25-го.",
                reason="Неясно: событие, срок или организационное напоминание",
            ),
            candidate("homework", "Возможное задание", "Возможно, нужно что-то подготовить."),
        ],
    )
    other, doubtful = result.candidates
    assert other.hold_code == "category_not_published" and "Неясно" in other.reason
    assert doubtful.hold_code == "doubtful_candidate"
    restored = ValidationResult.model_validate_json(result.model_dump_json())
    assert restored.candidates == result.candidates  # survives the stored JSON round trip


def test_ordinary_conversation_creates_neither_card_nor_candidate():
    messages = [msg(1, "всем привет, как дела? кто идёт на обед?")]
    result = validate(messages, [])
    assert result.accepted == [] and result.candidates == [] and result.explicit_empty
    # an empty shell is discarded rather than stored
    shell = validate(messages, [], [candidate("information", None, None)])
    assert shell.candidates == []
    assert shell.adapter_actions["candidate_without_content_discarded"] == 1


def test_clarification_of_an_existing_task_is_not_an_independent_card():
    messages = [
        msg(1, "Каждый делает доклад-презентацию о героях РФ", role="headman"),
        msg(2, "Формат — PDF, до 10 слайдов", minutes=300, reply=1, role="headman"),
    ]
    task = proposal("homework", "Доклад-презентация о героях РФ", "Доклад-презентация о героях РФ.")
    separate = proposal(
        "homework", "Формат презентации", "Формат — PDF, до 10 слайдов.", mids=(2,)
    )
    result = validate(messages, [task, separate], target=1)
    assert len(result.accepted) == 1  # merged into the task, not a second card
    assert {ref.telegram_message_id for ref in result.accepted[0].evidence} == {1, 2}
    assert result.candidates == []


def test_same_item_reported_as_card_and_candidate_stays_one_card():
    messages = [msg(1, "Решить задачи 3–7 со страницы 41")]
    card = proposal("homework", "Решить задачи 3–7", "Решить задачи 3–7 со страницы 41.")
    dup = candidate("homework", "Решить задачи 3–7", "Решить задачи 3–7 со страницы 41.")
    result = validate(messages, [card], [dup])
    assert len(result.accepted) == 1 and result.candidates == []
    assert result.adapter_actions["candidate_duplicates_card_discarded"] == 1


# -- 3. a model value outside the contract leaves a trace, not a hole ----------------------------


@pytest.mark.parametrize(
    ("raw", "category", "code"),
    [
        (proposal("лекция", "Лекция по физике", "Лекция в 9:00."), "other", "enum_value_not_allowed"),
        (proposal("quiz", "Квиз по цивилизациям", "Квиз по цивилизациям."), "check", "enum_value_not_allowed"),
        (
            {k: v for k, v in proposal("test", "Тест", "Тест по истории.").items() if k != "semantic_kind"}
            | {"destination": "deadline"},
            "check",
            "semantic_kind_missing",
        ),
        (
            proposal("homework", "Задание", "Решить задачу.") | {"priority": "high"},
            "homework",
            "proposal_field_not_allowed",
        ),
    ],
    ids=["unknown-kind", "quiz-label", "missing-kind", "unknown-field"],
)
def test_contract_error_keeps_the_available_content(raw, category, code):
    result = validate([msg(1, "Тест по истории, квиз, лекция, решить задачу")], [raw])
    assert result.accepted == []
    (held,) = result.candidates
    assert held.origin == "rejected_proposal" and held.category == category
    assert held.title and held.description and held.evidence
    assert held.hold_code == f"rejected_{code}"[:64]
    assert result.rejected[0].salvaged is True


def test_rejected_proposal_with_unusable_evidence_is_cited_to_the_target_message_only():
    messages = [msg(1, "Разное"), msg(2, "Квиз по цивилизациям в среду")]
    raw = proposal("quiz", "Квиз по цивилизациям", "Квиз в среду.", mids=(999,))
    result = validate(messages, [raw], target=2)
    (held,) = result.candidates
    assert [ref.telegram_message_id for ref in held.evidence] == [2]
    assert "evidence_defaulted_to_target" in held.attention_reasons


def test_content_free_rejections_leave_only_the_counter():
    result = validate([msg(1, "Привет")], ["homework", {"semantic_kind": "x"}])
    assert result.candidates == []
    assert result.adapter_actions["rejected_without_content"] == 2
    assert all(not item.salvaged for item in result.rejected)


def test_a_candidate_never_cites_a_message_outside_the_request():
    messages = [msg(1, "Встреча в четверг")]
    entry = candidate("event", "Встреча", "Встреча в четверг.", mids=(1, 404))
    (held,) = validate(messages, [], [entry]).candidates
    assert [ref.telegram_message_id for ref in held.evidence] == [1]


def test_label_vocabulary_only_labels_the_models_own_words():
    assert [label_category(x) for x in ("Quiz", "event", "организационное", "random", None)] == [
        "check",
        "event",
        "organizational",
        "other",
        "other",
    ]


# -- 4. candidates never reach the product ---------------------------------------------------


def test_candidates_are_never_applied_or_shown_to_students(client):
    messages = [msg(1, "Встреча студентов с выпускниками в четверг")]
    result = validate(
        messages,
        [proposal("event", "Встреча", "Встреча в четверг.")],
        [candidate("event", "Встреча студентов", "Встреча студентов с выпускниками.")],
    )
    assert len(result.candidates) == 2

    async def run():
        async with AsyncSession(client.app.state.engine) as db:
            group = await db.scalar(select(Group).where(Group.telegram_chat_id == -1001))
            outcome = await apply_validated_batch(
                db, group_id=group.id, validation=result, messages=messages, now=messages[0].message_date
            )
            await db.commit()
            assert outcome.product_writes == 0 and outcome.notifications_requested == 0
            assert {o.application_status for o in outcome.outcomes} == {"not_applied"}
            for model in (Homework, AcademicDeadline):
                assert await db.scalar(select(func.count()).select_from(model)) == 0

    asyncio.run(run())
    headers = {"Authorization": "Bearer valid"}
    assert client.get("/v1/homework", headers=headers).json()["items"] == []


# -- 5. compatibility with the old contract, stored results and the owner reports ---------------


def test_published_contract_values_are_unchanged():
    kinds = get_args(ProposedAcademicItem.model_fields["semantic_kind"].annotation)
    assert kinds == (
        "homework", "test", "control_point", "assessment", "credit", "exam",
        "other_academic_deadline",
    )  # fmt: skip
    assert get_args(ProposedAcademicItem.model_fields["destination"].annotation) == ("task", "deadline")


def test_a_result_stored_before_this_change_still_loads_and_an_old_answer_still_validates():
    messages = [msg(1, "Решить задачи 3–7")]
    result = validate(messages, [proposal("homework", "Решить задачи 3–7", "Решить задачи 3–7.")])
    old_json = json.loads(result.model_dump_json())
    del old_json["candidates"]
    for item in old_json["rejected"]:
        item.pop("salvaged", None)
    restored = ValidationResult.model_validate(old_json)
    assert restored.candidates == [] and len(restored.accepted) == 1
    assert AnalysisOutput.model_validate(
        {"source_revision": 1, "proposals": [], "explicit_empty": True}
    ).candidates == []


def test_the_prompt_example_validates_through_the_production_path_with_its_candidate():
    payload = example_output()
    messages = [msg(i, f"сообщение {i}") for i in (101, 102, 103, 104, 105)]
    result = validate_extraction_adapted(json.dumps(payload), data_for(messages, 105)).result
    assert len(result.accepted) == 3 and [c.category for c in result.candidates] == ["event"]


def test_owner_report_lists_held_candidates_without_publishing_them():
    held = [
        {"category": "event", "title": "Встреча с выпускниками", "description": None,
         "cited": [12], "target_message_id": 12},
    ]  # fmt: skip
    text = render_held_candidates(held)
    assert "студентам не показываются" in text and "событие: Встреча с выпускниками" in text
    preview = {"cards": [], "review_marks": [], "held_candidates": held, "outcomes": {}}
    assert any("Не карточки" in chunk for chunk in render_preview(preview, -100, 0, 5))


# -- 6. what the prompt asks, and the cost -------------------------------------------------------


def test_prompt_separates_discovery_from_category_and_forbids_keyword_decisions():
    text = EXTRACTION_SYSTEM_PROMPT
    for phrase in (
        "Step 1, discover",
        "Step 2, decide the product category",
        "Do not judge by keywords",
        "ДЗ (proposals",
        "КТ / проверочные",
        "События (candidates, category event)",
        "A quiz or a самостоятельная is test or assessment, never",
        "Ordinary conversation, greetings, jokes",
        "candidates?",
    ):
        assert phrase in text, phrase


def test_extraction_adds_no_provider_call_with_or_without_candidates():
    case, good, _ = shadow_case()
    event = candidate("event", "Встреча", "Встреча в четверг.", mids=(10,))
    unknown = good | {"semantic_kind": "лекция"}
    plain = TwoPassProvider(envelope([good]))
    rich = TwoPassProvider(envelope([good, unknown], candidates=[event]))
    asyncio.run(run_shadow(plain, build_input(case), []))
    result = asyncio.run(run_shadow(rich, build_input(case), []))
    assert len(plain.requests) == len(rich.requests) == 2  # context + extraction, as before
    assert len(result.validation.accepted) == 1 and len(result.validation.candidates) == 2
    assert result.repair["enabled"] is False


def test_optional_repair_removes_the_held_copy_of_a_proposal_it_recovered():
    case, good, _ = shadow_case()
    unknown = good | {"semantic_kind": "quiz"}

    def reclassify(request):
        return [
            {"ordinal": e["ordinal"], "proposal": e["original"] | {"semantic_kind": "test", "destination": "deadline"}}
            for e in request.user_payload["items"]
        ]

    provider = TwoPassProvider(envelope([good, unknown]), reclassify)
    result = asyncio.run(run_shadow(provider, build_input(case), [], repair_enabled=True))
    assert len(result.validation.accepted) == 2 and result.validation.candidates == []
