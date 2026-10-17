"""Subject ids: one formula, the one that already produced every stored ``subject_id``.

``subject_id = uuid5(group_id, subject_name.casefold())``. It does NOT fold ё into е, does NOT
collapse or strip whitespace and is not applied to a "normalised" name. These tests pin that
behaviour on every writer so that a later catalog/matching layer cannot silently create a second
identity (which would split one subject into two in the student API).
"""

import asyncio
import re
import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from test_analysis_resolver import NOW, proposal, seed_source
from test_headman import actor, run

from studgroup import headman_domain as domain
from studgroup.analysis.card_editor import EditedCard
from studgroup.analysis.projection import project_item
from studgroup.analysis.resolver import resolve_proposal
from studgroup.models import Group, Homework

pytest_plugins = ["test_schedule_api", "test_headman"]  # the latter provides `foreman`
SRC = Path(__file__).resolve().parents[1] / "src" / "studgroup"


def legacy_id(group_id, name):
    """The formula as it is written in processing, headman_domain, import and projection code."""
    return uuid.uuid5(group_id, name.casefold())


def subject_ids(client):
    async def read():
        async with AsyncSession(client.app.state.engine) as db:
            rows = (await db.scalars(select(Homework))).all()
            return {row.subject_name: row.subject_id for row in rows}

    return asyncio.run(read())


def make_cards(client, *names):
    async def scenario(db):
        user, member, group = await actor(db)
        for name in names:
            await domain.create_card(
                db,
                group,
                user,
                member,
                "h",
                {"subject": name, "title": "ДЗ", "description": "Задачи"},
            )
        return group.id

    return run(client, scenario)


# -- manual cards (headman_domain) ---------------------------------------------------------------


def test_manual_card_id_is_the_casefold_formula(foreman):
    group_id = make_cards(foreman, "Учёт", "Математический анализ")
    ids = subject_ids(foreman)
    assert ids["Учёт"] == legacy_id(group_id, "Учёт")
    assert ids["Математический анализ"] == legacy_id(group_id, "математический анализ")


def test_yo_whitespace_and_case_are_exactly_as_before(foreman):
    group_id = make_cards(foreman, "Учёт", "Учет", "УЧЁТ", "Физика X", "Физика  X", "Физика")
    ids = subject_ids(foreman)
    assert ids["Учёт"] == ids["УЧЁТ"]  # case folds
    assert ids["Учёт"] != ids["Учет"]  # ё is NOT folded into е: two subjects, never merged here
    assert ids["Физика X"] != ids["Физика  X"]  # whitespace is NOT collapsed
    assert ids["Физика"] == legacy_id(group_id, "физика")


def test_a_rename_gives_a_new_id_and_leaves_other_cards_alone(foreman):
    async def scenario(db):
        user, member, group = await actor(db)
        key_a, _ = await domain.create_card(
            db, group, user, member, "h", {"subject": "Физика", "title": "ДЗ 1", "description": "а"}
        )
        await domain.create_card(
            db, group, user, member, "h", {"subject": "Физика", "title": "ДЗ 2", "description": "б"}
        )
        await domain.mutate(
            db, group, user, member, key_a, 1, "edit",
            {"subject_name": "Общая физика", "subject_id": legacy_id(group.id, "Общая физика")},
        )  # fmt: skip
        rows = (await db.scalars(select(Homework).order_by(Homework.title))).all()
        return group.id, [(r.title, r.subject_name, r.subject_id) for r in rows]

    group_id, rows = run(foreman, scenario)
    assert rows == [
        ("ДЗ 1", "Общая физика", legacy_id(group_id, "Общая физика")),
        ("ДЗ 2", "Физика", legacy_id(group_id, "Физика")),  # untouched
    ]


# -- C+ projection ---------------------------------------------------------------------------------


def project_subject(client, subject):
    async def go():
        async with AsyncSession(client.app.state.engine, expire_on_commit=False) as db:
            group = await db.scalar(select(Group).where(Group.telegram_chat_id == -1001))
            await seed_source(db, group)
            item = proposal().model_copy(update={"subject": subject})
            card = EditedCard(
                title=item.title, description=item.description, visibility="normal", subject=subject
            )
            created = await resolve_proposal(
                db, group_id=group.id, proposal=item, edited_card=card, now=NOW
            )
            await db.flush()
            await project_item(db, created.item, NOW, writer_mode="new", emit_notifications=False)
            await db.commit()
            return group.id

    return asyncio.run(go())


def test_projected_card_uses_the_same_formula(client):
    group_id = project_subject(client, "Учёт и анализ")
    assert subject_ids(client) == {"Учёт и анализ": legacy_id(group_id, "учёт и анализ")}


def test_manual_and_projected_cards_of_one_subject_share_one_id(client):
    group_id = project_subject(client, "Учёт и анализ")

    async def manual(db):
        user, member, group = await actor(db)
        await domain.create_card(
            db,
            group,
            user,
            member,
            "h",
            {"subject": "Учёт и анализ", "title": "ДЗ", "description": "Задачи"},
        )

    # actor() needs a headman membership, as in the headman tests
    async def promote(db):
        from studgroup.models import Membership

        (await db.scalar(select(Membership))).role = "headman"

    run(client, promote)
    run(client, manual)
    ids = subject_ids(client)
    assert set(ids) == {"Учёт и анализ"} and ids["Учёт и анализ"] == legacy_id(
        group_id, "учёт и анализ"
    )


def test_same_name_in_two_groups_is_two_subjects():
    first, second = uuid.uuid4(), uuid.uuid4()
    assert legacy_id(first, "Физика") != legacy_id(second, "Физика")


# -- one definition, no parallel systems -----------------------------------------------------------


def test_no_second_formula_in_the_source_tree():
    """Every subject id is built by studgroup.subject_identity.subject_uuid, nowhere else."""
    pattern = re.compile(r"uuid5\([^)\n]*casefold\(\)")
    offenders = [
        str(path.relative_to(SRC))
        for path in SRC.rglob("*.py")
        if path.name != "subject_identity.py" and pattern.search(path.read_text(encoding="utf-8"))
    ]
    assert offenders == []


def test_the_shared_function_is_the_legacy_formula():
    from studgroup.subject_identity import subject_uuid

    group = uuid.UUID(int=42)
    for name in ["Учёт", "Учет", "УЧЁТ", "Физика  X", " Физика", "Математика", ""]:
        assert subject_uuid(group, name) == uuid.uuid5(group, name.casefold())
    assert subject_uuid(group, "Учёт") != subject_uuid(group, "Учет")
