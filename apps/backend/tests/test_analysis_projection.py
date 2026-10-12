import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from studgroup.analysis.card_editor import EditedCard
from studgroup.analysis.contracts import EvidenceRef, ProposedAcademicItem
from studgroup.analysis.projection import project_item
from studgroup.analysis.resolver import resolve_proposal
from studgroup.models import (
    AcademicDeadline,
    CanonicalProjectionLink,
    Group,
    Homework,
    Membership,
    Notification,
    RawMessage,
    WebSession,
)
from studgroup.security import token_hash

pytest_plugins = ["test_schedule_api"]
NOW = datetime(2026, 10, 12, 12, tzinfo=UTC)


def proposal(
    title="Решить задачи 1–3",
    *,
    destination="task",
    kind="homework",
    audience_scope="group",
    audience_label=None,
    attention_reasons=(),
):
    return ProposedAcademicItem(
        destination=destination,
        semantic_kind=kind,
        subject="Математика",
        title=title,
        description=title,
        deadline={
            "at": datetime(2026, 10, 15, tzinfo=UTC),
            "date_only": True,
            "basis": "schedule_inferred",
        },
        audience={"scope": audience_scope, "label": audience_label},
        confidence=90,
        attention_reasons=list(attention_reasons),
        evidence=[
            EvidenceRef(
                telegram_message_id=101,
                revision=1,
                fact_paths=["subject", "description", "deadline"],
            )
        ],
    )


def edited(item):
    reasons = list(item.attention_reasons)
    return EditedCard(
        title=item.title,
        description=item.description,
        visibility="headman_attention" if reasons else "normal",
        attention_reasons=reasons,
    )


async def seed_source(db, group):
    db.add(
        RawMessage(
            group_id=group.id,
            telegram_message_id=101,
            sender_id=1,
            text="По математике решить задачи 1–3 к следующей паре",
            message_date=NOW,
            version_date=NOW,
            revision=1,
            delete_at=NOW + timedelta(days=30),
        )
    )
    await db.flush()


async def resolve(db, group, value, *, action="upsert", existing=None, revision=None):
    return await resolve_proposal(
        db,
        group_id=group.id,
        proposal=value,
        edited_card=edited(value),
        now=NOW,
        action=action,
        existing_item_id=existing,
        expected_item_revision=revision,
    )


def test_shadow_mode_is_a_strict_noop(client):
    async def run():
        async with AsyncSession(client.app.state.engine) as db:
            group = await db.scalar(select(Group).where(Group.telegram_chat_id == -1001))
            await seed_source(db, group)
            item = (await resolve(db, group, proposal())).item
            result = await project_item(db, item, NOW)
            await db.commit()
            assert result.action == "shadow_noop"
            for model in (Homework, AcademicDeadline, CanonicalProjectionLink, Notification):
                assert await db.scalar(select(func.count()).select_from(model)) == 0

    asyncio.run(run())


def test_new_writer_projects_idempotently_and_updates_same_card(client):
    async def run():
        async with AsyncSession(client.app.state.engine, expire_on_commit=False) as db:
            group = await db.scalar(select(Group).where(Group.telegram_chat_id == -1001))
            await seed_source(db, group)
            first = await resolve(db, group, proposal())
            created = await project_item(db, first.item, NOW, writer_mode="new")
            unchanged = await project_item(db, first.item, NOW, writer_mode="new")
            changed_value = proposal(title="Решить задачи 1–4")
            changed = await resolve(
                db,
                group,
                changed_value,
                action="update",
                existing=first.item.id,
                revision=1,
            )
            updated = await project_item(
                db, changed.item, NOW + timedelta(minutes=1), writer_mode="new"
            )
            await db.commit()
            cards = (await db.scalars(select(Homework))).all()
            assert created.action == "created"
            assert unchanged.action == "unchanged"
            assert updated.action == "updated"
            assert len(cards) == 1
            assert cards[0].id == created.entity_id == updated.entity_id
            assert cards[0].title == "Решить задачи 1–4"
            assert cards[0].revision == 2
            assert await db.scalar(select(func.count()).select_from(Notification)) == 2

    asyncio.run(run())


def test_one_message_can_project_multiple_subgroup_cards(client):
    async def run():
        async with AsyncSession(client.app.state.engine) as db:
            group = await db.scalar(select(Group).where(Group.telegram_chat_id == -1001))
            await seed_source(db, group)
            for label, title in (
                ("Подгруппа 1", "Решить задачи 1–3"),
                ("Подгруппа 2", "Решить задачи 4–6"),
            ):
                value = proposal(
                    title=title,
                    audience_scope="subgroup",
                    audience_label=label,
                )
                item = (await resolve(db, group, value)).item
                await project_item(db, item, NOW, writer_mode="new")
            await db.commit()
            titles = (await db.scalars(select(Homework.title).order_by(Homework.title))).all()
            assert titles == [
                "[Подгруппа 1] Решить задачи 1–3",
                "[Подгруппа 2] Решить задачи 4–6",
            ]

    asyncio.run(run())


def test_deadline_projects_but_personal_item_is_blocked(client):
    async def run():
        async with AsyncSession(client.app.state.engine) as db:
            group = await db.scalar(select(Group).where(Group.telegram_chat_id == -1001))
            await seed_source(db, group)
            deadline = proposal(
                title="Контрольная работа",
                destination="deadline",
                kind="test",
            )
            projected = await project_item(
                db, (await resolve(db, group, deadline)).item, NOW, writer_mode="new"
            )
            personal = proposal(audience_scope="personal")
            blocked = await project_item(
                db, (await resolve(db, group, personal)).item, NOW, writer_mode="new"
            )
            await db.commit()
            assert projected.action == "created"
            assert projected.entity_type == "deadline"
            assert blocked.action == "audience_blocked"
            assert await db.scalar(select(func.count()).select_from(AcademicDeadline)) == 1
            assert await db.scalar(select(func.count()).select_from(Homework)) == 0

    asyncio.run(run())


def test_manual_lock_blocks_projection(client):
    async def run():
        async with AsyncSession(client.app.state.engine) as db:
            group = await db.scalar(select(Group).where(Group.telegram_chat_id == -1001))
            await seed_source(db, group)
            item = (await resolve(db, group, proposal())).item
            item.manual_lock = True
            result = await project_item(db, item, NOW, writer_mode="new")
            await db.commit()
            assert result.action == "blocked_by_lock"
            assert await db.scalar(select(func.count()).select_from(Homework)) == 0

    asyncio.run(run())


def test_inferred_and_attention_markers_are_hidden_from_students(client):
    async def run():
        async with AsyncSession(client.app.state.engine) as db:
            group = await db.scalar(select(Group).where(Group.telegram_chat_id == -1001))
            await seed_source(db, group)
            for title, reasons in (
                ("Расчётный срок", ()),
                ("Нужно внимание", ("minor_ambiguity",)),
            ):
                value = proposal(title=title, attention_reasons=reasons)
                item = (await resolve(db, group, value)).item
                await project_item(db, item, NOW, writer_mode="new")
            await db.commit()

    asyncio.run(run())
    headers = {"Authorization": "Bearer valid"}
    student = client.get("/v1/homework", headers=headers).json()["items"]
    assert {row["verification_state"] for row in student} == {"from_group_message"}

    async def promote():
        async with AsyncSession(client.app.state.engine) as db:
            session = await db.get(WebSession, token_hash("valid"))
            membership = await db.get(Membership, session.membership_id)
            membership.role = "headman"
            await db.commit()

    asyncio.run(promote())
    headman = client.get("/v1/homework", headers=headers).json()["items"]
    assert {row["verification_state"] for row in headman} == {
        "inferred",
        "needs_clarification",
    }
