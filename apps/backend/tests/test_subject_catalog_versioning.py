"""№05A: catalog versioning and one subject identity for C+ and the student API.

Synthetic data only; nothing here is a result of a real model run.
"""

import asyncio
import json
import uuid
from datetime import UTC, date, datetime, time, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from test_analysis_hybrid import TwoPassProvider, build_input, envelope, shadow_case
from test_analysis_resolver import NOW as RESOLVER_NOW
from test_analysis_resolver import seed_source
from test_analysis_shadow import NOW, HybridProvider, enable_shadow
from test_card_text_quality import GROUP, msg
from test_processing import source
from test_subject_catalog import card_item, catalog, data_for, edited

from studgroup.analysis import backfill
from studgroup.analysis.card_editor import edit_card
from studgroup.analysis.contracts import (
    AnalysisInput,
    SubjectCatalog,
    SubjectEntry,
    ValidationResult,
)
from studgroup.analysis.dispatch import dispatch_next
from studgroup.analysis.projection import project_item
from studgroup.analysis.resolver import resolve_proposal
from studgroup.analysis.shadow import run_shadow, source_snapshot_sha256
from studgroup.analysis.shadow_worker import enqueue_shadow
from studgroup.analysis.stored import effective_validation
from studgroup.analysis.subject_catalog import (
    build_catalog,
    catalog_fingerprint,
    catalog_summary,
    with_catalog,
)
from studgroup.main import Settings
from studgroup.models import (
    CanonicalAcademicItem,
    Group,
    Homework,
    RawMessage,
    SchedulePattern,
    ShadowEvaluationRun,
)
from studgroup.processing import context_for
from studgroup.subject_identity import subject_uuid

pytest_plugins = ["test_schedule_api"]


def legacy_id(group, name):
    """The formula every writer on main uses: casefold only (no e/yo folding, no spacing change)."""
    return uuid.uuid5(group, name.casefold())


# -- stable identity ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    ["Математический анализ", "математический  анализ", "МАТЕМАТИЧЕСКИЙ АНАЛИЗ ", "Учёт", "учет"],
)
def test_subject_id_is_the_legacy_casefold_formula(name):
    assert subject_uuid(GROUP, name) == legacy_id(GROUP, name)


def test_only_case_is_folded_and_groups_never_share_an_id():
    assert subject_uuid(GROUP, "ФИЗИКА") == subject_uuid(GROUP, "физика")
    # ё/е, spacing and rename are different ids: nothing is merged automatically
    assert subject_uuid(GROUP, "Учёт") != subject_uuid(GROUP, "Учет")
    assert subject_uuid(GROUP, "Учёт") != subject_uuid(GROUP, " учёт ")
    assert subject_uuid(GROUP, "Физика  X") != subject_uuid(GROUP, "Физика X")
    assert subject_uuid(GROUP, "Физика") != subject_uuid(uuid.UUID(int=7), "Физика")
    assert subject_uuid(GROUP, "Физика") != subject_uuid(GROUP, "Физика (лекции)")


def _rows(*pairs):
    return [type("Row", (), {"subject": s, "teacher": t})() for s, t in pairs]


def test_catalog_ids_do_not_depend_on_row_order_or_spelling():
    rows = _rows(("Физика", "Петров"), ("математический анализ", None), ("ФИЗИКА", None))
    first = build_catalog(GROUP, rows)
    second = build_catalog(GROUP, list(reversed(rows)))
    assert first == second and len(first.subjects) == 2
    assert {e.id for e in first.subjects} == {
        str(legacy_id(GROUP, n)) for n in ("физика", "математический анализ")
    }
    assert catalog_fingerprint(first) == catalog_fingerprint(second)


def test_fingerprint_follows_content_not_labels():
    base = build_catalog(GROUP, _rows(("Физика", None), ("Алгебра", None)))
    relabelled = base.model_copy(update={"version": "another-label"})
    reordered = base.model_copy(update={"subjects": list(reversed(base.subjects))})
    assert (
        catalog_fingerprint(base)
        == catalog_fingerprint(relabelled)
        == catalog_fingerprint(reordered)
    )
    changed = build_catalog(GROUP, _rows(("Физика", None), ("Алгебра", None), ("Химия", None)))
    assert catalog_fingerprint(changed) != catalog_fingerprint(base)
    assert catalog_fingerprint(base.model_copy(update={"complete": True})) != catalog_fingerprint(
        base
    )
    teacher = build_catalog(GROUP, _rows(("Физика", "Смит"), ("Алгебра", None)))
    assert catalog_fingerprint(teacher) != catalog_fingerprint(base)
    assert catalog_summary(None) == {"present": False}


def test_projected_card_and_catalog_use_the_same_subject_id(client):
    async def go():
        async with AsyncSession(client.app.state.engine, expire_on_commit=False) as db:
            group = await db.scalar(select(Group).where(Group.telegram_chat_id == -1001))
            await seed_source(db, group)
            cat = build_catalog(group.id, _rows(("Учёт и анализ", None)))
            entry = cat.subjects[0]
            messages = [msg(1, "По учету решить номера 5–8")]
            data = data_for(messages, cat, group=group.id)
            from studgroup.analysis.extraction_strategy import validate_extraction_adapted

            (proposal,) = validate_extraction_adapted(
                envelope([card_item("учет", entry.id)]), data
            ).result.accepted
            card = edit_card(proposal, messages)
            created = await resolve_proposal(
                db, group_id=group.id, proposal=proposal, edited_card=card, now=RESOLVER_NOW
            )
            await db.flush()
            await project_item(
                db, created.item, RESOLVER_NOW, writer_mode="new", emit_notifications=False
            )
            await db.commit()
            homework = await db.scalar(select(Homework))
            return entry.id, str(homework.subject_id), homework.subject_name

    catalog_id, projected_id, name = asyncio.run(go())
    assert name == "Учёт и анализ"
    assert catalog_id == projected_id  # one identity, ё and all


# -- the contract is checked, not trusted ---------------------------------------------------------


def test_an_id_must_belong_to_this_groups_catalog_and_agree_with_the_wording():
    messages = [msg(1, "По физике решить номера 5–8")]
    own = catalog(complete=False)
    # an id of a subject that exists only in another group's catalog
    foreign_id = str(subject_uuid(uuid.UUID(int=9), "Химия"))
    proposal, _card = edited(messages, card_item("Химия", foreign_id), catalog(complete=True))
    assert proposal.subject is None and proposal.subject_id is None
    assert "subject_id_unknown" in proposal.attention_reasons
    # an incomplete catalog never upgrades an unknown id or an unlisted name to "confirmed"
    proposal, _card = edited(messages, card_item("Химия", foreign_id), own)
    assert proposal.subject_source is None and proposal.subject_id is None
    # id and wording naming two different catalog subjects: nothing is confirmed
    proposal, _card = edited(messages, card_item("Физика", "s-math"), own)
    assert (proposal.subject, proposal.subject_id, proposal.subject_source) == (None, None, None)
    assert "subject_id_mismatch" in proposal.attention_reasons
    # id and wording agreeing: confirmed with the canonical name
    proposal, _card = edited(messages, card_item("физика", "s-phys"), own)
    assert (proposal.subject, proposal.subject_id) == ("Физика", "s-phys")


# -- run identity: a catalog change creates neither a second run nor a second card ---------------


def schedule(client, *subjects):
    async def go():
        async with AsyncSession(client.app.state.engine) as db:
            group = await db.scalar(select(Group).where(Group.telegram_chat_id == -1001))
            for name in subjects:
                db.add(
                    SchedulePattern(
                        group_id=group.id,
                        subject=name,
                        weekday=0,
                        starts=time(9),
                        ends=time(10),
                        valid_from=date(2026, 9, 1),
                        valid_until=date(2027, 1, 31),
                        week="all",
                    )
                )
            await db.commit()

    asyncio.run(go())


def runs(client):
    async def go():
        async with AsyncSession(client.app.state.engine) as db:
            return (await db.scalars(select(ShadowEvaluationRun))).all()

    return asyncio.run(go())


def analyse(client):
    enable_shadow(client)
    source(client, text="ДЗ по физике: решить номера 5–8")
    schedule(client, "Физика", "Алгебра")
    settings = Settings(ai_enabled=False, ai_shadow_enabled=True)
    provider = HybridProvider()
    assert (
        asyncio.run(dispatch_next(client.app.state.engine, settings, provider, NOW))
        == "shadow_queued"
    )
    assert (
        asyncio.run(dispatch_next(client.app.state.engine, settings, provider, NOW))
        == "shadow_completed"
    )
    return provider


def test_the_run_records_the_catalog_it_was_analysed_with(client):
    analyse(client)
    (row,) = runs(client)
    data = AnalysisInput.model_validate_json(row.input_json)
    assert {e.name for e in data.subject_catalog.subjects} == {
        "Математика",
        "Физика",
        "Алгебра",
    }  # + the fixture's own
    assert data.subject_catalog.subjects[0].id == str(
        subject_uuid(data.group_id, data.subject_catalog.subjects[0].name)
    )
    recorded = json.loads(row.metrics_json)["subject_catalog"]
    assert recorded["fingerprint"] == catalog_fingerprint(data.subject_catalog)
    assert recorded["complete"] is False and recorded["subjects"] == 3
    assert row.source_snapshot_sha256 == source_snapshot_sha256(data)


def test_changing_the_schedule_neither_repeats_the_run_nor_rewrites_it(client):
    analyse(client)
    (before,) = runs(client)
    schedule(client, "Химия")  # the schedule changes while the messages do not

    async def again():
        async with AsyncSession(client.app.state.engine) as db:
            group = await db.scalar(select(Group).where(Group.telegram_chat_id == -1001))
            raw = await db.scalar(select(RawMessage))
            context, _ = await context_for(db, raw, group, NOW, semantic_neighborhood=True)
            row = await enqueue_shadow(db, group=group, job=None, raw=raw, context=context, now=NOW)
            row_id = row.id
            await db.commit()
            return row_id

    assert asyncio.run(again()) == before.id  # the existing run is returned, nothing new is queued
    (after,) = runs(client)
    assert after.input_json == before.input_json  # a finished run is reproducible as it was


def test_an_owner_requeue_uses_the_current_catalog_and_keeps_the_old_outcome(client):
    analyse(client)
    (before,) = runs(client)
    old_fingerprint = json.loads(before.metrics_json)["subject_catalog"]["fingerprint"]
    old_messages = AnalysisInput.model_validate_json(before.input_json).messages
    schedule(client, "Химия")

    async def requeue():
        async with AsyncSession(client.app.state.engine) as db:
            row = await db.scalar(select(ShadowEvaluationRun))
            row.state, row.last_error = "failed", "invalid_shadow_context"
            await db.commit()
            group = await db.scalar(select(Group).where(Group.telegram_chat_id == -1001))
            result = await backfill.requeue_failed(
                db,
                group_id=group.id,
                since=datetime(2020, 1, 1, tzinfo=UTC),
                now=datetime.now(UTC) + timedelta(days=1),
                limit=10,
                apply=True,
                force=True,
            )
            await db.commit()
            return result

    assert asyncio.run(requeue())["requeued"] == 1
    (after,) = runs(client)
    assert after.id == before.id and after.state == "queued"
    data = AnalysisInput.model_validate_json(after.input_json)
    assert {e.name for e in data.subject_catalog.subjects} == {
        "Математика",
        "Физика",
        "Алгебра",
        "Химия",
    }
    assert data.messages == old_messages  # only the reference data moved
    history = json.loads(after.metrics_json)["history"]
    assert history[-1]["metrics"]["subject_catalog"]["fingerprint"] == old_fingerprint
    assert catalog_fingerprint(data.subject_catalog) != old_fingerprint

    async def count_runs():
        async with AsyncSession(client.app.state.engine) as db:
            return await db.scalar(select(func.count()).select_from(ShadowEvaluationRun))

    assert asyncio.run(count_runs()) == 1


# -- the same messages under two catalogs; the same catalog twice ------------------------------------


def _shadow(catalog_value):
    case, good, _bad = shadow_case()
    base = build_input(case)
    data = base.model_copy(update={"subject_catalog": catalog_value(base.group_id)})
    answer = good | {"subject": "физика", "subject_id": None}
    result = asyncio.run(run_shadow(TwoPassProvider(envelope([answer])), data, []))
    return data, result.validation


def test_same_messages_under_a_changed_catalog_give_a_traceable_difference():
    with_physics = lambda g: SubjectCatalog(
        group_id=g,
        complete=True,
        subjects=[SubjectEntry(id=str(subject_uuid(g, "Физика")), name="Физика")],
    )
    without = lambda g: SubjectCatalog(
        group_id=g,
        complete=True,
        subjects=[SubjectEntry(id=str(subject_uuid(g, "Алгебра")), name="Алгебра")],
    )
    data_a, result_a = _shadow(with_physics)
    data_b, result_b = _shadow(without)
    assert source_snapshot_sha256(data_a) == source_snapshot_sha256(data_b)  # same source
    assert catalog_fingerprint(data_a.subject_catalog) != catalog_fingerprint(
        data_b.subject_catalog
    )
    assert (
        result_a.accepted[0].subject == "Физика"
        and result_a.accepted[0].subject_source == "catalog"
    )
    assert result_b.accepted[0].subject is None  # the outcome differs and the fingerprint says why


def test_the_same_catalog_twice_is_deterministic():
    make = lambda g: SubjectCatalog(
        group_id=g,
        complete=True,
        subjects=[SubjectEntry(id=str(subject_uuid(g, "Физика")), name="Физика")],
    )
    first_data, first = _shadow(make)
    second_data, second = _shadow(make)
    assert catalog_fingerprint(first_data.subject_catalog) == catalog_fingerprint(
        second_data.subject_catalog
    )
    assert first.model_dump_json() == second.model_dump_json()


# -- old data keeps reading ---------------------------------------------------------------------------


def test_old_snapshots_and_results_without_catalog_fields_still_read():
    case, good, _bad = shadow_case()
    base = build_input(case)
    old_input = json.loads(base.model_dump_json())
    old_input.pop("subject_catalog")
    data = AnalysisInput.model_validate_json(json.dumps(old_input))
    assert data.subject_catalog is None and source_snapshot_sha256(data) == source_snapshot_sha256(
        base
    )
    assert catalog_summary(data.subject_catalog) == {"present": False}

    result = asyncio.run(run_shadow(TwoPassProvider(envelope([good])), base, [])).validation
    old_result = json.loads(result.model_dump_json())
    for proposal in old_result["accepted"]:
        proposal.pop("subject_id"), proposal.pop("subject_source")
    reread = ValidationResult.model_validate_json(json.dumps(old_result))
    view = effective_validation(reread, data.messages, "Europe/Moscow")
    (proposal,) = view.accepted
    assert proposal.subject == "Физика" and proposal.subject_source is None
    assert edit_card(proposal, data.messages).subject == "Физика"  # the old text rule is unchanged


def test_with_catalog_replaces_only_the_catalog_and_tolerates_a_broken_snapshot():
    case, _good, _bad = shadow_case()
    base = build_input(case)
    cat = catalog(group=base.group_id)
    refreshed = AnalysisInput.model_validate_json(with_catalog(base.model_dump_json(), cat))
    assert refreshed.messages == base.messages and refreshed.subject_catalog == cat
    assert with_catalog("not json", cat) is None


# -- a confirmed subject survives a failed or silent re-analysis ------------------------------------------


@pytest.mark.parametrize(
    "later_item",
    [
        card_item(None, title="Решить номера 5–9"),  # the model dropped the subject
        card_item(None, "s-unknown", title="Решить номера 5–9"),  # it named an id nobody has
        card_item("Физика", "s-math", title="Решить номера 5–9"),  # id and wording disagree
    ],
    ids=["silent", "unknown-id", "contradiction"],
)
def test_a_confirmed_subject_is_not_erased(client, later_item):
    messages = [msg(1, "По матану решить номера 5–8")]
    first, first_card = edited(messages, card_item("матан", "s-math"), catalog())
    later, later_card = edited(messages, later_item, catalog(complete=True))
    assert later_card.subject is None

    async def go():
        async with AsyncSession(client.app.state.engine, expire_on_commit=False) as db:
            group = await db.scalar(select(Group).where(Group.telegram_chat_id == -1001))
            await seed_source(db, group)
            created = await resolve_proposal(
                db, group_id=group.id, proposal=first, edited_card=first_card, now=RESOLVER_NOW
            )
            await db.commit()
            updated = await resolve_proposal(
                db,
                group_id=group.id,
                proposal=later,
                edited_card=later_card,
                now=RESOLVER_NOW + timedelta(minutes=1),
                action="update",
                existing_item_id=created.item.id,
                expected_item_revision=1,
            )
            await db.commit()
            return updated.action, (await db.scalar(select(CanonicalAcademicItem))).subject

    action, subject = asyncio.run(go())
    assert action == "update" and subject == "Математический анализ"


def test_spelling_variants_stay_separate_catalog_subjects():
    rows = _rows(("Учёт", None), ("Учет", None), ("УЧЁТ", None))
    catalog = build_catalog(GROUP, rows)
    # "Учёт" and "УЧЁТ" differ only by case (one subject); "Учет" is another one
    assert len(catalog.subjects) == 2
    assert {e.id for e in catalog.subjects} == {
        str(legacy_id(GROUP, "Учёт")),
        str(legacy_id(GROUP, "Учет")),
    }


def test_a_fact_attaches_by_exact_name_or_to_the_single_similar_subject_only():
    from types import SimpleNamespace

    def fact(subject, alias):
        return SimpleNamespace(
            group_id=GROUP,
            status="verified",
            fact_type="subject_alias",
            value_json=json.dumps({"subject": subject, "alias": alias}, ensure_ascii=False),
        )

    single = build_catalog(GROUP, _rows(("Учёт", None)), [fact("учет", "бух")])
    assert single.subjects[0].aliases == ["бух"]  # one similar subject: unambiguous
    both = _rows(("Учёт", None), ("Учет", None))
    exact = build_catalog(GROUP, both, [fact("учёт", "бух")])
    assert {e.name: e.aliases for e in exact.subjects} == {"Учёт": ["бух"], "Учет": []}
    vague = build_catalog(GROUP, both, [fact("учёт.", "бух")])  # matches both: attach to none
    assert all(entry.aliases == [] for entry in vague.subjects)
