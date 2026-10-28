"""Titles and descriptions: the model's sound text survives, chat phrasing never becomes a title,
and supplements never paste raw chat text into a description. All examples are synthetic."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from test_analysis_hybrid import envelope
from test_analysis_resolver import edited, seed_source
from test_analysis_resolver import proposal as resolver_proposal

from studgroup.analysis.card_editor import (
    DESCRIPTION_PENDING,
    edit_card,
)
from studgroup.analysis.contracts import (
    AnalysisInput,
    EvidenceRef,
    ProposedAcademicItem,
    SourceMessage,
)
from studgroup.analysis.extraction_strategy import (
    EXTRACTION_PROMPT_VERSION,
    EXTRACTION_SYSTEM_PROMPT,
    validate_extraction_adapted,
)
from studgroup.analysis.resolver import resolve_proposal
from studgroup.analysis.title_builder import build_title, is_conversational_title
from studgroup.models import CanonicalItemEvidence, Group, RawMessage

pytest_plugins = ["test_schedule_api"]

GROUP = UUID("00000000-0000-0000-0000-000000000004")
T0 = datetime(2026, 10, 12, 9, tzinfo=UTC)
RETAKE_SOURCE = "У кого двойка или не было работы на самостоятельной по алгебре, пишем повторно"
RETAKE_DESCRIPTION = (
    "Для студентов, получивших двойку или пропустивших самостоятельную по алгебре. "
    "Необходимо написать работу повторно."
)


def msg(mid, text, *, minutes=0, reply=None, role="student", who="a"):
    return SourceMessage(
        telegram_message_id=mid,
        revision=1,
        message_date=T0 + timedelta(minutes=minutes),
        text=text,
        reply_to_message_id=reply,
        topic_id=7,
        author_alias=f"member-{who}",
        author_role=role,
    )


def item(mids=(1,), **patch):
    values = {
        "destination": "deadline",
        "semantic_kind": "assessment",
        "subject": None,
        "title": "Повторная самостоятельная по алгебре",
        "description": RETAKE_DESCRIPTION,
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


# -- 1. a sound model title and description are kept ----------------------------------------


def test_sound_model_title_and_description_are_kept_even_without_a_verbatim_match():
    card = edit_card(item(), [msg(1, RETAKE_SOURCE)])
    assert card.title == "Повторная самостоятельная по алгебре"
    assert card.description == RETAKE_DESCRIPTION
    assert not card.used_fallback and card.fallback_fields == []
    assert card.visibility == "normal"


def test_title_naming_the_work_is_kept_when_it_is_a_noun_phrase_not_an_infinitive():
    card = edit_card(
        item(title="Монолог о себе на английском", description="Подготовить монолог о себе."),
        [msg(1, "Подготовить монолог о себе, минуты на две, по-английски")],
    )
    assert card.title == "Монолог о себе на английском" and not card.used_fallback


# -- 2. chat phrasing never becomes the title -----------------------------------------------


def test_conversational_model_title_is_rebuilt_from_the_sound_description():
    bad = "У кого двойка или не было работы на самостоятельной по алгебре"
    card = edit_card(item(title=bad), [msg(1, RETAKE_SOURCE)])
    assert not card.title.startswith("У кого")
    assert card.title == "Написать работу повторно"
    assert card.fallback_fields == ["title"]  # the description was judged separately and kept
    assert card.description == RETAKE_DESCRIPTION
    assert "noisy_title" in card.attention_reasons


@pytest.mark.parametrize(
    "chat",
    [
        "У кого двойка или не было работы на самостоятельной по алгебре",
        "Это англ группа Преподавателя",
        "Не затягивать с видеороликом для КТ3",
        "Ребята, кто-нибудь знает когда сдавать?",
        "Если кто-то забыл, напишите старосте",
        "ок",
    ],
)
def test_a_chat_fragment_is_never_offered_as_a_rebuilt_title(chat):
    title, origin = build_title(None, chat, "Задание")
    assert origin == "insufficient" and title == "Задание: уточнить по сообщению в чате"


def test_no_safe_title_is_an_explicit_request_for_clarification_not_a_chat_phrase():
    card = edit_card(
        item(title="У кого двойка", description=None),
        [msg(1, "У кого двойка или не было работы на самостоятельной по алгебре")],
    )
    assert card.title == "Оценочная работа: уточнить по сообщению в чате"
    assert "title_insufficient_data" in card.attention_reasons
    assert "У кого" not in card.title


def test_conversational_detector_does_not_flag_real_titles():
    for good in (
        "Повторная самостоятельная по алгебре",
        "Тест по истории в СДО",
        "Решить задачи 3–7",
        "Контрольная точка 1",
        "Подготовить доклад про клетку",
    ):
        assert not is_conversational_title(good)
    for bad in (
        "У кого двойка",
        "Ребята, внимание",
        "Если кто-то",
        "Когда сдавать?",
        "Не затягивать",
    ):
        assert is_conversational_title(bad)


def test_overlong_model_title_is_shortened_from_the_description_not_cut_with_an_ellipsis():
    long_title = (
        "Подготовить индивидуальное монологическое высказывание о себе примерно на 2 минуты "
        "по теме семьи и друзей на английском языке"
    )
    card = edit_card(
        item(
            semantic_kind="homework",
            destination="task",
            title=long_title,
            description="Подготовить монолог о себе на английском, около 2 минут.",
        ),
        [msg(1, "Подготовить индивидуальное монологическое высказывание о себе, 2 минуты")],
    )
    assert card.title == "Подготовить монолог о себе на английском"
    assert not card.title.endswith("…") and "title_too_long" in card.attention_reasons


# -- 3. paraphrase of a description is kept --------------------------------------------------


def test_paraphrase_with_a_synonym_of_the_same_work_is_not_an_invented_fact():
    card = edit_card(
        item(
            semantic_kind="homework",
            destination="task",
            title="Доклад о клетке на 5 минут",
            description="Нужно подготовить доклад о клетке длительностью 5 минут.",
        ),
        [msg(1, "Подготовить выступление про клетку на 5 минут")],
    )
    assert not card.used_fallback and card.visibility == "normal"
    assert card.description.startswith("Нужно подготовить доклад")


def test_abbreviation_in_the_chat_supports_the_full_name_in_the_description():
    card = edit_card(
        item(
            semantic_kind="control_point",
            title="Видеоролик для контрольной точки 3",
            description="Подготовить видеоролик для контрольной точки 3.",
        ),
        [msg(1, "КТ3: нужен видеоролик")],
    )
    assert not card.used_fallback and card.visibility == "normal"


def test_model_may_structure_the_description_with_numbered_points():
    card = edit_card(
        item(
            semantic_kind="homework",
            destination="task",
            title="Конспект и задачи к семинару",
            description="1) Составить конспект. 2) Решить задачи.\n3) Принести тетрадь.",
        ),
        [msg(1, "К семинару конспект, задачи и тетрадь")],
    )
    assert not card.used_fallback, card.attention_reasons


# -- 4. a subject error does not destroy sound text --------------------------------------------


def test_unsupported_subject_is_dropped_without_replacing_title_or_description():
    card = edit_card(
        item(
            semantic_kind="homework",
            destination="task",
            subject="Математический анализ",
            title="Решить задачи 3–7 со страницы 41",
            description="Решить задачи 3–7 из задачника, страница 41.",
        ),
        [msg(1, "#матан Решить задачи 3–7 со страницы 41 из задачника")],
    )
    assert card.subject is None and "unsupported_subject" in card.attention_reasons
    assert card.title == "Решить задачи 3–7 со страницы 41"
    assert card.description == "Решить задачи 3–7 из задачника, страница 41."
    assert not card.used_fallback and card.visibility == "headman_attention"


# -- 5. a supplement is a cited source, never pasted chat text -----------------------------------


def test_text_supplement_joins_the_sources_but_its_chat_text_stays_out_of_the_description():
    task = "Каждый делает доклад-презентацию о выдающихся героях РФ"
    messages = [
        msg(1, task, role="headman"),
        msg(2, "Формат — PDF, до 10 слайдов", minutes=300, reply=1, role="headman"),
    ]
    data = AnalysisInput(
        group_id=GROUP,
        group_timezone="Europe/Moscow",
        source_revision=1,
        target_message_ids=[1],
        messages=messages,
    )
    raw = {
        "semantic_kind": "homework",
        "subject": None,
        "title": "Подготовить доклад-презентацию о героях РФ",
        "description": "Каждый готовит доклад-презентацию о выдающихся героях РФ.",
        "confidence": 90,
        "audience": {"scope": "group", "label": None},
        "deadline": {"at": None, "date_only": False, "basis": "unknown"},
        "evidence": [{"telegram_message_id": 1, "revision": 1, "fact_paths": ["description"]}],
    }
    result = validate_extraction_adapted(envelope([raw]), data).result
    (card,) = result.accepted
    assert [ref.telegram_message_id for ref in card.evidence] == [1, 2]
    assert card.description == raw["description"]
    assert "10 слайдов" not in card.description and "Дополнение" not in card.description
    assert "supplement_pending_integration" in card.attention_reasons
    assert result.adapter_actions["supplement_pending_integration"] == 1
    assert "supplement_text_appended" not in result.adapter_actions
    edited_card = edit_card(card, messages)
    assert edited_card.description == raw["description"]  # sound text untouched by the flag


def test_resolver_records_a_new_supplement_source_even_when_no_visible_field_changes(client):
    async def run():
        async with AsyncSession(client.app.state.engine, expire_on_commit=False) as db:
            group = await db.scalar(select(Group).where(Group.telegram_chat_id == -1001))
            await seed_source(db, group)
            db.add(
                RawMessage(
                    group_id=group.id,
                    telegram_message_id=2,
                    sender_id=1,
                    text="Формат — PDF",
                    message_date=T0,
                    version_date=T0,
                    revision=1,
                    delete_at=T0 + timedelta(days=30),
                )
            )
            await db.flush()
            first = resolver_proposal()
            created = await resolve_proposal(
                db, group_id=group.id, proposal=first, edited_card=edited(first), now=T0
            )
            await db.commit()
            again = first.model_copy(
                update={
                    "evidence": [
                        *first.evidence,
                        EvidenceRef(telegram_message_id=2, revision=1, fact_paths=["description"]),
                    ]
                }
            )
            result = await resolve_proposal(
                db,
                group_id=group.id,
                proposal=again,
                edited_card=edited(again),
                now=T0 + timedelta(minutes=1),
                action="update",
                existing_item_id=created.item.id,
                expected_item_revision=1,
            )
            await db.commit()
            assert result.action == "no_change" and result.item.revision == 1
            count = await db.scalar(
                select(func.count())
                .select_from(CanonicalItemEvidence)
                .where(CanonicalItemEvidence.item_id == created.item.id)
            )
            assert count == 4  # three paths of the task message + the supplement

    asyncio.run(run())


# -- 6. unsupported facts are still blocked ----------------------------------------------------


@pytest.mark.parametrize(
    ("description", "reason"),
    [
        ("Решить 12 задач из задачника.", "unsupported_numeric_fact"),
        ("Подготовить реферат о клетке.", "unsupported_critical_fact"),
        ("Подготовить доклад о клетке к пятнице.", "unsupported_date_fact"),
        ("Доклад о клетке для подгруппы.", "unsupported_audience_fact"),
    ],
)
def test_invented_numbers_works_dates_and_audience_still_fail_the_description(description, reason):
    card = edit_card(
        item(
            semantic_kind="homework",
            destination="task",
            title="Доклад о клетке",
            description=description,
        ),
        [msg(1, "Подготовить доклад про клетку, задачи из задачника")],
    )
    assert reason in card.attention_reasons
    assert card.description == DESCRIPTION_PENDING
    assert "description" in card.fallback_fields and card.visibility == "held"
    for invented in ("12", "реферат", "пятниц", "подгрупп"):
        assert invented not in card.description


def test_description_keeps_its_supported_sentences_and_drops_only_the_invented_one():
    card = edit_card(
        item(
            semantic_kind="homework",
            destination="task",
            title="Решить задачи из задачника",
            description="Решить задачи из задачника. Всего нужно сделать 12 штук. Сдать в PDF.",
        ),
        [msg(1, "Решить задачи из задачника, сдать в PDF")],
    )
    assert card.description == "Решить задачи из задачника. Сдать в PDF."
    assert "12" not in card.description
    assert "description_trimmed" in card.attention_reasons
    assert "description_pending_ai" not in card.attention_reasons


def test_invented_fact_in_the_title_replaces_only_the_title():
    card = edit_card(
        item(
            semantic_kind="homework",
            destination="task",
            title="Решить 12 задач",
            description="Решить задачи из задачника.",
        ),
        [msg(1, "Решить задачи из задачника")],
    )
    assert "12" not in card.title and card.title == "Решить задачи из задачника"
    assert card.fallback_fields == ["title"]
    assert card.description == "Решить задачи из задачника."


def test_unsupported_explicit_deadline_is_flagged_but_does_not_touch_the_texts():
    card = edit_card(
        item(
            deadline={
                "at": datetime(2026, 10, 20, tzinfo=UTC),
                "date_only": True,
                "basis": "explicit",
            }
        ),
        [msg(1, RETAKE_SOURCE)],
    )
    assert "unsupported_explicit_deadline" in card.attention_reasons
    assert card.title == "Повторная самостоятельная по алгебре" and not card.used_fallback


# -- 7. the fallback description is never assembled from raw chat ---------------------------------


def test_fallback_description_contains_no_source_text():
    secret = "уникальнаяфразаизчата"
    card = edit_card(
        item(description=None, title="Повторная самостоятельная по алгебре"),
        [msg(1, f"{RETAKE_SOURCE} {secret}"), msg(2, "второе сообщение чата", minutes=1)],
    )
    assert card.description == DESCRIPTION_PENDING
    assert secret not in card.description and "•" not in card.description
    assert "description_pending_ai" in card.attention_reasons
    assert (
        card.title == "Повторная самостоятельная по алгебре"
    )  # title survives a missing description


# -- 8. older valid cards are not made worse ----------------------------------------------------


@pytest.mark.parametrize(
    ("source", "title", "description"),
    [
        (
            "По математике решить номера 10–16, страницы 43–44",
            "Решить номера 10–16",
            "Решить номера 10–16 на страницах 43–44.",
        ),
        (
            "Подготовить доклад про клетку",
            "Подготовить доклад про клетку",
            "Подготовить доклад про клетку.",
        ),
        (
            "Тест по истории в СДО до среды",
            "Тест по истории в СДО",
            "Пройти тест по истории в СДО до среды.",
        ),
    ],
)
def test_previously_valid_cards_keep_title_description_and_visibility(source, title, description):
    card = edit_card(
        item(semantic_kind="homework", destination="task", title=title, description=description),
        [msg(1, source)],
    )
    assert (card.title, card.description) == (title, description)
    assert not card.used_fallback and card.visibility == "normal"


# -- prompt -----------------------------------------------------------------------------------


def test_prompt_version_and_the_title_and_description_contracts():
    assert EXTRACTION_PROMPT_VERSION == "cplus-extraction-14"
    prompt = EXTRACTION_SYSTEM_PROMPT
    assert "Title contract" in prompt and "ideally under 60 characters" in prompt
    assert "Description contract" in prompt and "in your own words" in prompt
    assert "Never quote, list or paste chat messages" in prompt
    assert "Show deadline, materials, audience and sources only in their own fields" not in prompt
