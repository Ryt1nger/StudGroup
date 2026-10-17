"""№05: the model ties a card to a real subject of the group; code only checks and carries it.

Synthetic fixtures only. The "model" answers are scripted: they prove that our contract, validation,
card editing and resolver do the right thing with a good or bad answer. They say nothing about how
DeepSeek actually chooses a subject, and they are not results of a real run.
"""

import asyncio
import json
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from test_analysis_hybrid import TwoPassProvider, build_input, envelope, shadow_case
from test_analysis_resolver import NOW, seed_source
from test_card_text_quality import GROUP, msg

from studgroup.analysis.card_editor import edit_card
from studgroup.analysis.contracts import AnalysisInput, SubjectCatalog, SubjectEntry
from studgroup.analysis.extraction_strategy import (
    EXTRACTION_SYSTEM_PROMPT,
    prepare_extraction_request,
    validate_extraction_adapted,
)
from studgroup.analysis.repair import facts_preserved
from studgroup.analysis.resolver import resolve_proposal
from studgroup.analysis.shadow import run_shadow, source_snapshot_sha256
from studgroup.analysis.stored import effective_validation
from studgroup.analysis.subject_catalog import build_catalog, label_key
from studgroup.models import CanonicalAcademicItem, Group
from studgroup.subject_identity import subject_uuid

pytest_plugins = ["test_schedule_api"]

MATH, ENG, RUS, ORG, ALG, ALG2, PHYS = (
    SubjectEntry(id="s-math", name="Математический анализ", teachers=["Иванова И. И."]),
    SubjectEntry(id="s-eng", name="Английский язык", teachers=["Смит А."]),
    SubjectEntry(id="s-rus", name="Русский язык", teachers=["Петрова Ю. П."]),
    SubjectEntry(id="s-org", name="Основы российской государственности"),
    SubjectEntry(id="s-alg", name="Алгебра"),
    SubjectEntry(id="s-alg2", name="Алгебра и геометрия"),
    SubjectEntry(id="s-phys", name="Физика"),
)


def catalog(*entries, complete=False, group=GROUP):
    return SubjectCatalog(
        group_id=group,
        version="v1",
        complete=complete,
        subjects=list(entries or [MATH, ENG, RUS, ORG, ALG, ALG2, PHYS]),
    )


def card_item(subject=None, subject_id=None, **patch):
    raw = {
        "semantic_kind": "homework",
        "subject": subject,
        "title": "Решить номера 5–8",
        "description": "Решить номера 5–8.",
        "confidence": 90,
        "audience": {"scope": "group", "label": None},
        "deadline": {"at": None, "date_only": False, "basis": "unknown"},
        "evidence": [{"telegram_message_id": 1, "revision": 1, "fact_paths": ["description"]}],
    }
    if subject_id is not None:
        raw["subject_id"] = subject_id
    return raw | patch


def data_for(messages, cat=None, group=GROUP):
    return AnalysisInput(
        group_id=group,
        group_timezone="Europe/Moscow",
        source_revision=1,
        target_message_ids=[messages[-1].telegram_message_id],
        messages=messages,
        subject_catalog=cat,
    )


def run(messages, raw, cat=None, group=GROUP):
    data = data_for(messages, cat, group)
    result = validate_extraction_adapted(envelope([raw]), data).result
    return data, result


def edited(messages, raw, cat):
    _data, result = run(messages, raw, cat)
    (proposal,) = result.accepted
    return proposal, edit_card(proposal, messages)


# -- 1-2. abbreviations are understood by the model; the catalog name is what is stored ------------


@pytest.mark.parametrize(
    ("text", "said", "sid", "expected"),
    [
        ("ДЗ по матану: решить номера 5–8", "матан", "s-math", "Математический анализ"),
        ("По англу выучить unit 4", "англ", "s-eng", "Английский язык"),
        ("англ: выучить слова", "Английский язык", None, "Английский язык"),  # exact name, no id
    ],
    ids=["matan", "angl", "exact-name-without-id"],
)
def test_abbreviation_resolves_to_the_catalog_subject(text, said, sid, expected):
    messages = [msg(1, text)]
    proposal, card = edited(messages, card_item(said, sid), catalog())
    assert (proposal.subject, proposal.subject_source) == (expected, "catalog")
    assert proposal.subject_id == (
        sid or next(e.id for e in catalog().subjects if e.name == expected)
    )
    assert card.subject == expected and "unsupported_subject" not in card.attention_reasons


def test_without_a_catalog_the_same_abbreviation_is_still_unconfirmed():
    # the previous behaviour is kept: the text rule decides, an id is meaningless and is dropped
    messages = [msg(1, "ДЗ по матану: решить номера 5–8")]
    proposal, card = edited(messages, card_item("Математический анализ", "s-math"), None)
    assert proposal.subject_id is None and proposal.subject_source is None
    assert card.subject is None and "unsupported_subject" in card.attention_reasons


# -- 3-4. a team label (ПИР) is tied to a subject only when the context says so ---------------------


def test_a_group_label_follows_the_context_the_model_read():
    messages = [
        msg(1, "ПИР у Юлии Петровны, она ведёт русский язык", role="headman"),
        msg(2, "ПИР: сделать конспект главы 3", minutes=5),
    ]
    raw = card_item(
        "ПИР",
        "s-rus",
        evidence=[{"telegram_message_id": 2, "revision": 1, "fact_paths": ["subject"]}],
    )
    data, result = run(messages, raw, catalog())
    (proposal,) = result.accepted
    assert (proposal.subject, proposal.subject_id) == ("Русский язык", "s-rus")
    assert edit_card(proposal, messages).subject == "Русский язык"
    # the model was given the catalog and the confirming message; nothing maps ПИР in our code
    request = prepare_extraction_request(data, {})
    assert {row["id"] for row in request.user_payload["subject_catalog"]["subjects"]} >= {
        "s-rus",
        "s-math",
    }
    assert [row["telegram_message_id"] for row in request.user_payload["messages"]] == [1, 2]
    assert "ПИР" not in json.dumps(request.user_payload["subject_catalog"], ensure_ascii=False)
    assert "ПИР" not in EXTRACTION_SYSTEM_PROMPT


def test_the_same_label_without_context_stays_without_a_subject():
    messages = [msg(1, "ПИР: сделать конспект главы 3")]
    for cat in (catalog(), catalog(complete=True)):
        proposal, card = edited(
            messages, card_item(None), cat
        )  # the model correctly says "unknown"
        assert proposal.subject is None and proposal.subject_id is None
        assert card.subject is None
        assert not [r for r in card.attention_reasons if "subject" in r]  # nothing was invented
    # a made-up id is not a subject either, and a letter-similar subject is never inferred
    proposal, card = edited(messages, card_item(None, "s-pir"), catalog())
    assert proposal.subject is None and "subject_id_unknown" in proposal.attention_reasons
    proposal, card = edited(messages, card_item("Физ", None), catalog(complete=True))
    assert card.subject is None and "subject_not_in_catalog" in card.attention_reasons


# -- 5. in the catalog but never written in the message -----------------------------------------


def test_a_catalog_subject_needs_no_literal_mention_in_the_message():
    messages = [msg(1, "Сделать задания из учебника к пятнице, как обсуждали")]
    _proposal, card = edited(messages, card_item("Физика", "s-phys"), catalog())
    assert card.subject == "Физика" and "unsupported_subject" not in card.attention_reasons
    # contrast: without the catalog the same answer loses the subject
    _p, plain = edited(messages, card_item("Физика"), None)
    assert plain.subject is None and "unsupported_subject" in plain.attention_reasons


# -- 6. not in the catalog -------------------------------------------------------------------------


def test_a_subject_missing_from_the_catalog():
    messages = [msg(1, "По химии решить номера 5–8")]
    proposal, card = edited(messages, card_item("Химия"), catalog(complete=True))
    assert proposal.subject is None and proposal.subject_source is None
    assert card.subject is None and "subject_not_in_catalog" in card.attention_reasons
    # an incomplete catalog proves nothing: the explicit text rule keeps what the chat states
    proposal, card = edited(messages, card_item("химии"), catalog(complete=False))
    assert proposal.subject == "химии" and proposal.subject_source is None
    assert card.subject == "химии" and "subject_not_in_catalog" not in card.attention_reasons


# -- 7. several similar subjects -------------------------------------------------------------------


def test_similar_subjects_are_told_apart_by_id_or_exact_name_only():
    messages = [msg(1, "По алгебре решить номера 5–8")]
    by_name = edited(messages, card_item("Алгебра"), catalog())[0]
    assert by_name.subject_id == "s-alg"  # exact name, not "starts with"
    by_id = edited(messages, card_item("алгебра и геом", "s-alg2"), catalog())[0]
    assert (by_id.subject, by_id.subject_id) == ("Алгебра и геометрия", "s-alg2")
    # an id next to the exact name of ANOTHER subject is a contradiction, never a confirmation
    clash = edited(messages, card_item("алгебра", "s-alg2"), catalog())[0]
    assert clash.subject is None and "subject_id_mismatch" in clash.attention_reasons
    prefix = edited(messages, card_item("Алг"), catalog(complete=True))[1]
    assert prefix.subject is None  # a prefix is never a match
    twin = SubjectEntry(id="s-alg-b", name="Алгебра (подгруппа)", aliases=["алгебра"])
    ambiguous = edited(messages, card_item("Алгебра"), catalog(ALG, twin))
    assert ambiguous[0].subject is None and "subject_ambiguous" in ambiguous[0].attention_reasons


# -- 8-9. empty, incomplete and foreign catalogs ------------------------------------------------------


def test_an_empty_catalog_changes_nothing():
    messages = [msg(1, "По физике решить номера 5–8")]
    data, result = run(
        messages, card_item("Физика", "s-phys"), SubjectCatalog(group_id=GROUP, subjects=[])
    )
    (proposal,) = result.accepted
    assert result.adapter_actions["subject_catalog_ignored_empty"] == 1
    assert proposal.subject == "Физика" and proposal.subject_id is None
    assert "subject_catalog" not in prepare_extraction_request(data, {}).user_payload


def test_a_catalog_of_another_group_is_not_used():
    other = catalog(group=uuid.UUID(int=999))
    messages = [msg(1, "ДЗ по матану: решить номера 5–8")]
    data, result = run(messages, card_item("матан", "s-math"), other)
    (proposal,) = result.accepted
    assert result.adapter_actions["subject_catalog_ignored_foreign_group"] == 1
    assert proposal.subject_source is None and proposal.subject_id is None
    assert "subject_catalog" not in prepare_extraction_request(data, {}).user_payload
    # an id that belongs to another group's catalog is unknown in this group's catalog
    own = catalog(MATH, ENG, complete=True)
    proposal, _card = edited(
        [msg(1, "По физике решить номера 5–8")], card_item("Физика", "s-phys"), own
    )
    assert proposal.subject is None and proposal.subject_id is None


def test_the_model_cannot_set_the_confirmation_itself():
    messages = [msg(1, "По физике решить номера 5–8")]
    _data, forged = run(messages, card_item("Физика", None, subject_source="catalog"), None)
    assert forged.accepted == [] and forged.rejected  # not an allowed field
    _data, copied_null = run(messages, card_item("Физика", None, subject_source=None), None)
    assert len(copied_null.accepted) == 1  # a copied null is harmless


# -- 10-11. the checked subject reaches the canonical item and is never erased ----------------------


def _resolver_case(client, proposals_and_actions):
    async def go():
        async with AsyncSession(client.app.state.engine, expire_on_commit=False) as db:
            group = await db.scalar(select(Group).where(Group.telegram_chat_id == -1001))
            await seed_source(db, group)
            outcomes = []
            for proposal, card, extra in proposals_and_actions:
                outcomes.append(
                    await resolve_proposal(
                        db,
                        group_id=group.id,
                        proposal=proposal,
                        edited_card=card,
                        now=NOW,
                        **extra(outcomes),
                    )
                )
                await db.commit()
            return outcomes, (await db.scalars(select(CanonicalAcademicItem))).all()

    return asyncio.run(go())


def test_resolver_stores_the_checked_subject_not_the_raw_text(client):
    messages = [msg(1, "По матану решить номера 5–8")]
    confirmed, card = edited(messages, card_item("матан", "s-math"), catalog())
    unconfirmed, bad_card = edited(
        [msg(1, "По химии решить номера 5–8")], card_item("Химия"), catalog(complete=True)
    )
    assert (
        unconfirmed.subject is None
    )  # adapter already refused; make the raw text differ on purpose:
    forged = unconfirmed.model_copy(
        update={"subject": "Химия"}
    )  # raw proposal text the card editor refused
    outcomes, items = _resolver_case(
        client,
        [
            (confirmed, card, lambda _o: {}),
            (forged, bad_card, lambda _o: {}),
        ],
    )
    assert [o.action for o in outcomes] == ["create", "create"]
    assert sorted(item.subject or "-" for item in items) == ["-", "Математический анализ"]


def test_old_stored_proposals_without_catalog_fields_still_resolve(client):
    messages = [msg(1, "По математике решить номера 5–8")]
    old, card = edited(messages, card_item("математике"), None)
    assert old.subject_id is None and card.subject == "математике"
    _outcomes, items = _resolver_case(client, [(old, card, lambda _o: {})])
    assert items[0].subject == "математике"


def test_a_failed_reanalysis_does_not_erase_a_confirmed_subject(client):
    messages = [msg(1, "По матану решить номера 5–8")]
    first, first_card = edited(messages, card_item("матан", "s-math"), catalog())
    later, later_card = edited(messages, card_item(None, title="Решить номера 5–9"), catalog())
    assert later.subject is None and later_card.subject is None

    def update(outcomes):
        return {
            "action": "update",
            "existing_item_id": outcomes[0].item.id,
            "expected_item_revision": outcomes[0].item.revision,
        }

    outcomes, items = _resolver_case(
        client, [(first, first_card, lambda _o: {}), (later, later_card, update)]
    )
    assert outcomes[1].action == "update"
    assert items[0].subject == "Математический анализ" and items[0].title.startswith(
        "Решить номера 5"
    )


def test_a_new_confirmed_subject_replaces_the_old_one(client):
    messages = [msg(1, "По англу решить номера 5–8")]
    first, first_card = edited(messages, card_item("Физика", "s-phys"), catalog())
    later, later_card = edited(messages, card_item("англ", "s-eng"), catalog())

    def update(outcomes):
        return {
            "action": "update",
            "existing_item_id": outcomes[0].item.id,
            "expected_item_revision": outcomes[0].item.revision,
        }

    _o, items = _resolver_case(
        client, [(first, first_card, lambda _o: {}), (later, later_card, update)]
    )
    assert items[0].subject == "Английский язык"


# -- the chain end to end and the contract pieces --------------------------------------------------


def test_run_shadow_sends_the_catalog_and_returns_the_canonical_subject():
    case, good, _bad = shadow_case()
    base = build_input(case)
    data = base.model_copy(update={"subject_catalog": catalog(group=base.group_id)})
    answer = good | {"subject": "физика", "subject_id": "s-phys"}
    provider = TwoPassProvider(envelope([answer]))
    result = asyncio.run(run_shadow(provider, data, []))
    assert len(provider.requests) == 2  # no extra call for the catalog
    extraction = provider.requests[1]
    assert extraction.user_payload["subject_catalog"]["subjects"][0]["id"] == "s-math"
    assert "subject_catalog" not in provider.requests[0].user_payload  # Pass 1 is unchanged
    (proposal,) = result.validation.accepted
    assert (proposal.subject, proposal.subject_id, proposal.subject_source) == (
        "Физика",
        "s-phys",
        "catalog",
    )
    # stored result and the frozen input both survive serialisation
    stored = type(result.validation).model_validate_json(result.validation.model_dump_json())
    assert stored.accepted[0].subject_source == "catalog"
    assert (
        AnalysisInput.model_validate_json(data.model_dump_json()).subject_catalog
        == data.subject_catalog
    )


def test_the_catalog_is_not_part_of_the_source_snapshot_and_old_inputs_load():
    case, _good, _bad = shadow_case()
    base = build_input(case)
    with_catalog = base.model_copy(update={"subject_catalog": catalog(group=base.group_id)})
    assert source_snapshot_sha256(base) == source_snapshot_sha256(with_catalog)
    old = json.loads(base.model_dump_json())
    old.pop("subject_catalog")
    assert AnalysisInput.model_validate_json(json.dumps(old)).subject_catalog is None


def test_stored_confirmation_survives_the_effective_view():
    messages = [msg(1, "По матану решить номера 5–8")]
    _data, result = run(messages, card_item("матан", "s-math"), catalog())
    view = effective_validation(result, messages, "Europe/Moscow")
    assert (view.accepted[0].subject, view.accepted[0].subject_source) == (
        "Математический анализ",
        "catalog",
    )


def test_a_repair_may_confirm_the_same_subject_but_not_swap_it():
    messages = [msg(1, "По матану решить номера 5–8")]
    data = data_for(messages, catalog())
    raw = card_item("матан", "s-math")
    _d, result = run(messages, card_item("матан", "s-math"), catalog())
    (repaired,) = result.accepted
    assert facts_preserved(raw, repaired, data)[0] is True
    swapped = repaired.model_copy(update={"subject": "Физика", "subject_id": "s-phys"})
    assert facts_preserved(raw, swapped, data)[:2] == (False, "repair_changed_text")


def test_catalog_built_from_the_schedule_and_verified_facts():
    rows = [
        SimpleNamespace(subject="Математический анализ", teacher="Иванова И. И."),
        SimpleNamespace(subject="математический анализ", teacher="Иванова И. И."),
        SimpleNamespace(subject="Физика", teacher=None),
    ]
    facts = [
        SimpleNamespace(
            group_id=GROUP,
            status="verified",
            fact_type="subject_alias",
            value_json=json.dumps({"alias": "матан", "subject": "Математический анализ"}),
        ),
        SimpleNamespace(
            group_id=GROUP,
            status="proposed",
            fact_type="subject_alias",
            value_json=json.dumps({"alias": "физ", "subject": "Физика"}),
        ),
        SimpleNamespace(
            group_id=uuid.UUID(int=5),
            status="verified",
            fact_type="subject_alias",
            value_json=json.dumps({"alias": "чужое", "subject": "Физика"}),
        ),
    ]
    built = build_catalog(GROUP, rows, facts)
    again = build_catalog(GROUP, list(reversed(rows)), facts)
    assert built == again and built.complete is False and built.version
    by_name = {entry.name: entry for entry in built.subjects}
    assert by_name["Математический анализ"].aliases == ["матан"]
    assert by_name["Математический анализ"].teachers == ["Иванова И. И."]
    assert by_name["Физика"].aliases == []  # unverified and foreign aliases are not trusted
    assert {e.id for e in built.subjects} == {
        str(subject_uuid(GROUP, n)) for n in ("Математический анализ", "Физика")
    }
    assert label_key("  Алгебра, и Геометрия ") == "алгебра и геометрия"
