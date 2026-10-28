"""03B regressions: supplements, fallback fields and publication of unconfirmed cards.

All data is synthetic. They pin down that
* a clarification is never lost (it stays a cited, marked source) and never pasted as chat text;
* a plain repeat of the card does not flag it, a clarification with new content does;
* a failed field never overwrites a text the card already carries from an accepted pass;
* a card with a placeholder or a possibly lagging description is still published, but never as a
  confirmed one, and a lagging description says so in its own text.
"""

import asyncio
import json
from datetime import timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from test_analysis_hybrid import envelope
from test_analysis_resolver import NOW, seed_source
from test_analysis_resolver import edited as resolver_edited
from test_analysis_resolver import proposal as resolver_proposal
from test_card_text_quality import GROUP, msg

from studgroup.analysis.backfill import with_supplement_updates
from studgroup.analysis.card_editor import (
    DESCRIPTION_PENDING,
    EditedCard,
    edit_card,
    supplement_adds_information,
)
from studgroup.analysis.contracts import AnalysisInput
from studgroup.analysis.extraction_strategy import validate_extraction_adapted
from studgroup.analysis.linking import PENDING_INTEGRATION, SUPPLEMENT_PATH
from studgroup.analysis.projection import LAGGING_NOTE, project_item
from studgroup.analysis.resolver import resolve_proposal
from studgroup.analysis.title_builder import INSUFFICIENT
from studgroup.models import Group, Homework, RawMessage

pytest_plugins = ["test_schedule_api"]
TASK = "Каждый делает доклад-презентацию о выдающихся героях РФ"


def _task_item(**patch):
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
    return raw | patch


def _reconciled(reply_text):
    messages = [
        msg(1, TASK, role="headman"),
        msg(2, reply_text, minutes=300, reply=1, role="headman"),
    ]
    data = AnalysisInput(
        group_id=GROUP,
        group_timezone="Europe/Moscow",
        source_revision=1,
        target_message_ids=[1],
        messages=messages,
    )
    (card,) = validate_extraction_adapted(envelope([_task_item()]), data).result.accepted
    return card, messages


# -- 1. what a supplement does and does not do ------------------------------------------------


def test_a_reply_that_only_repeats_the_card_is_a_source_without_a_flag():
    card, _ = _reconciled("Обязательно, презентация о героях РФ")
    assert [ref.telegram_message_id for ref in card.evidence] == [1, 2]
    assert PENDING_INTEGRATION not in card.attention_reasons


def test_a_reply_with_new_content_is_kept_as_a_marked_source_and_flags_the_card():
    card, messages = _reconciled("Формат — PDF, до 10 слайдов")
    marked = next(ref for ref in card.evidence if ref.telegram_message_id == 2)
    assert SUPPLEMENT_PATH in marked.fact_paths
    assert PENDING_INTEGRATION in card.attention_reasons
    edited_card = edit_card(card, messages)
    # flagged for the headman, but neither held nor stripped of its sound texts
    assert edited_card.visibility == "headman_attention"
    assert edited_card.description == card.description and edited_card.title == card.title
    assert "10 слайдов" not in edited_card.description


@pytest.mark.parametrize(
    ("known", "reply", "expected"),
    [
        ("Решить задачи 1–3", "Да, задачи 1–3 обязательно", False),
        ("Решить задачи 1–3", "Ещё номер 7 и 9", True),
        ("Написать доклад о героях", "Сдать в печатном виде", True),
        ("Написать доклад о героях", "Формат — презентация", True),
        ("Контрольная в пятницу", "Контрольная в пятницу, обязательно", False),
        ("Контрольная в пятницу", "Перенесли на вторник, обязательно", True),
    ],
)
def test_new_information_detector(known, reply, expected):
    assert supplement_adds_information(known, reply) is expected


# -- 2. a supplement joins an existing card without rewriting it ---------------------------------


def _supplement_case(client, reply_text):
    async def run():
        async with AsyncSession(client.app.state.engine, expire_on_commit=False) as db:
            group = await db.scalar(select(Group).where(Group.telegram_chat_id == -1001))
            await seed_source(db, group)  # message 1 = "По математике решить задачи 1–3"
            db.add(
                RawMessage(
                    group_id=group.id,
                    telegram_message_id=2,
                    sender_id=1,
                    text=reply_text,
                    message_date=NOW + timedelta(minutes=5),
                    version_date=NOW + timedelta(minutes=5),
                    revision=1,
                    delete_at=NOW + timedelta(days=30),
                )
            )
            await db.flush()
            first = resolver_proposal()
            created = await resolve_proposal(
                db, group_id=group.id, proposal=first, edited_card=resolver_edited(first), now=NOW
            )
            await db.commit()
            messages = [
                msg(1, "По математике решить задачи 1–3", role="headman"),
                msg(2, reply_text, minutes=5, reply=1, role="headman"),
            ]
            data = AnalysisInput(
                group_id=group.id,
                group_timezone="Europe/Moscow",
                source_revision=1,
                target_message_ids=[2],
                messages=messages,
            )
            raw = _task_item(
                subject="Математика",
                title="Решить задачи 1–3",
                description="Решить задачи 1–3.",
                evidence=[{"telegram_message_id": 2, "revision": 1, "fact_paths": ["description"]}],
            )
            validation = validate_extraction_adapted(envelope([raw]), data).result
            raw_by_id = {
                row.telegram_message_id: row for row in (await db.scalars(select(RawMessage))).all()
            }
            updated = await with_supplement_updates(db, group.id, validation, data, raw_by_id)
            return created.item, updated

    return asyncio.run(run())


def test_supplement_update_keeps_the_description_and_marks_the_new_source(client):
    item, validation = _supplement_case(client, "Формат — PDF, до 10 слайдов")
    (update,) = validation.accepted
    assert update.action == "update" and update.existing_item_id == item.id
    assert update.description == item.description and update.title == item.title
    assert PENDING_INTEGRATION in update.attention_reasons
    paths = {ref.telegram_message_id: ref.fact_paths for ref in update.evidence}
    assert set(paths) == {1, 2} and SUPPLEMENT_PATH in paths[2]
    assert "PDF" not in update.description


def test_supplement_update_that_repeats_the_card_does_not_flag_it(client):
    _, validation = _supplement_case(client, "Обязательно, задачи 1–3")
    (update,) = validation.accepted
    assert update.action == "update"
    assert PENDING_INTEGRATION not in update.attention_reasons
    assert {ref.telegram_message_id for ref in update.evidence} == {1, 2}


# -- 3. a failed field never destroys what the card already carries ----------------------------


def _pending_card(title, **patch):
    values = {
        "title": title,
        "description": DESCRIPTION_PENDING,
        "visibility": "headman_attention",
        "attention_reasons": ["unsupported_numeric_fact", "description_pending_ai"],
        "used_fallback": True,
        "fallback_fields": ["description"],
    }
    return EditedCard(**(values | patch))


def _resolve_twice(client, first_card, second_card):
    async def run():
        async with AsyncSession(client.app.state.engine, expire_on_commit=False) as db:
            group = await db.scalar(select(Group).where(Group.telegram_chat_id == -1001))
            await seed_source(db, group)
            value = resolver_proposal()
            created = await resolve_proposal(
                db, group_id=group.id, proposal=value, edited_card=first_card, now=NOW
            )
            await db.commit()
            result = await resolve_proposal(
                db,
                group_id=group.id,
                proposal=value,
                edited_card=second_card,
                now=NOW + timedelta(minutes=1),
                action="update",
                existing_item_id=created.item.id,
                expected_item_revision=1,
            )
            await db.commit()
            return created.item, result

    return asyncio.run(run())


def test_failed_description_does_not_overwrite_the_accepted_one(client):
    good = EditedCard(
        title="Решить задачи 1–3", description="Решить задачи 1–3.", visibility="normal"
    )
    item, result = _resolve_twice(client, good, _pending_card("Решить задачи 1–3"))
    assert result.item.description == "Решить задачи 1–3."
    assert "description_pending_ai" in json.loads(result.item.attention_reasons)  # not "confirmed"
    assert item.revision == 2


def test_failed_title_does_not_overwrite_the_accepted_one(client):
    good = EditedCard(
        title="Решить задачи 1–3", description="Решить задачи 1–3.", visibility="normal"
    )
    weak = EditedCard(
        title=f"Задание: {INSUFFICIENT}",
        description="Решить задачи 1–3.",
        visibility="headman_attention",
        attention_reasons=["noisy_title", "title_insufficient_data"],
        used_fallback=True,
        fallback_fields=["title"],
    )
    _, result = _resolve_twice(client, good, weak)
    assert result.item.title == "Решить задачи 1–3"


def test_a_placeholder_is_replaced_by_the_first_sound_text(client):
    first = _pending_card(f"Задание: {INSUFFICIENT}", attention_reasons=["description_pending_ai"])
    better = EditedCard(
        title="Решить задачи 1–3", description="Решить задачи 1–3.", visibility="normal"
    )
    _, result = _resolve_twice(client, first, better)
    assert result.item.description == "Решить задачи 1–3."
    assert result.item.title == "Решить задачи 1–3"


# -- 4. unconfirmed cards are published, but not as confirmed ---------------------------------


def _snapshot(row):
    return SimpleNamespace(
        title=row.title,
        description=row.description,
        status=row.status,
        verification_state=row.verification_state,
    )


def _project(client, cards):
    async def run():
        async with AsyncSession(client.app.state.engine, expire_on_commit=False) as db:
            group = await db.scalar(select(Group).where(Group.telegram_chat_id == -1001))
            await seed_source(db, group)
            value = resolver_proposal()
            created = await resolve_proposal(
                db, group_id=group.id, proposal=value, edited_card=cards[0], now=NOW
            )
            assert created.action == "create"
            await project_item(db, created.item, NOW, writer_mode="new")
            await db.commit()
            rows = [_snapshot((await db.scalars(select(Homework))).one())]
            item = created.item
            for step, card in enumerate(cards[1:], start=1):
                result = await resolve_proposal(
                    db,
                    group_id=group.id,
                    proposal=value,
                    edited_card=card,
                    now=NOW + timedelta(minutes=step),
                    action="update",
                    existing_item_id=item.id,
                    expected_item_revision=item.revision,
                )
                await project_item(
                    db, result.item, NOW + timedelta(minutes=step), writer_mode="new"
                )
                await db.commit()
                row = (await db.scalars(select(Homework))).one()
                await db.refresh(row)
                rows.append(_snapshot(row))
            return rows

    return asyncio.run(run())


def test_pending_description_card_is_published_but_marked_unconfirmed(client):
    (row,) = _project(client, [_pending_card("Решить задачи 1–3")])
    assert row.status == "published"
    assert row.verification_state == "needs_clarification"
    assert row.description == DESCRIPTION_PENDING  # the placeholder says so itself, no extra note


def test_insufficient_title_card_is_published_but_marked_unconfirmed(client):
    card = EditedCard(
        title=f"Задание: {INSUFFICIENT}",
        description="Решить задачи 1–3.",
        visibility="headman_attention",
        attention_reasons=["noisy_title", "title_insufficient_data"],
        used_fallback=True,
        fallback_fields=["title"],
    )
    (row,) = _project(client, [card])
    assert row.status == "published" and row.verification_state == "needs_clarification"
    assert row.title == f"Задание: {INSUFFICIENT}"


def test_lagging_description_says_so_and_the_note_goes_away_after_integration(client):
    lagging = EditedCard(
        title="Решить задачи 1–3",
        description="Решить задачи 1–3.",
        visibility="headman_attention",
        attention_reasons=[PENDING_INTEGRATION],
    )
    integrated = EditedCard(
        title="Решить задачи 1–3",
        description="Решить задачи 1–3, формат PDF.",
        visibility="normal",
    )
    waiting, done = _project(client, [lagging, integrated])
    assert waiting.description == f"Решить задачи 1–3.\n\n{LAGGING_NOTE}"
    assert waiting.verification_state == "needs_clarification"
    assert done.description == "Решить задачи 1–3, формат PDF."
    assert done.verification_state == "from_group_message"


def test_a_plain_card_gets_no_note(client):
    plain = EditedCard(
        title="Решить задачи 1–3", description="Решить задачи 1–3.", visibility="normal"
    )
    (row,) = _project(client, [plain])
    assert (
        row.description == "Решить задачи 1–3." and row.verification_state == "from_group_message"
    )


# -- 5. only serious doubts hide a card ---------------------------------------------------------


def test_unusable_new_description_and_material_doubt_both_hold_a_card():
    source = [msg(1, "Решить задачи 1–3")]
    soft = edit_card(_task_item_proposal(description="Решить задачи 1–7 на 99 страницах"), source)
    assert (
        soft.visibility == "held"
        and "description_pending_ai" in soft.attention_reasons
    )
    hard = edit_card(_task_item_proposal(attention_reasons=["material_conflict"]), source)
    assert hard.visibility == "held"


def _task_item_proposal(**patch):
    from test_card_text_quality import item

    values = {
        "destination": "task",
        "semantic_kind": "homework",
        "title": "Решить задачи 1–3",
        "description": "Решить задачи 1–3.",
    }
    return item((1,), **(values | patch))


def test_a_kept_long_title_raises_no_flag():
    long_title = (
        "Индивидуальное монологическое высказывание о себе на английском языке по теме "
        "семьи и друзей объёмом около двух минут"
    )
    card = edit_card(
        _task_item_proposal(title=long_title, description="Монолог."),
        [msg(1, "Ребята, кто что делал?")],  # nothing safe to rebuild a shorter title from
    )
    # the model's own title survives (cut by the common title hygiene), nothing is replaced
    assert card.title.startswith(long_title[:80]) and len(card.title) <= 90
    assert card.fallback_fields == []
    assert card.attention_reasons == [] and card.visibility == "normal"
