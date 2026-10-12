import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from studgroup.analysis.card_editor import EditedCard
from studgroup.analysis.contracts import EvidenceRef, ProposedAcademicItem
from studgroup.analysis.resolver import resolve_proposal
from studgroup.models import (
    CanonicalAcademicItem,
    CanonicalItemEvidence,
    CanonicalItemRevision,
    Group,
    RawMessage,
)

pytest_plugins = ["test_schedule_api"]
NOW = datetime(2026, 10, 12, 12, tzinfo=UTC)


def proposal(*, title="Решить задачи 1–3", subgroup=None, deadline=None):
    return ProposedAcademicItem(
        destination="task",
        semantic_kind="homework",
        subject="Математика",
        title=title,
        description=title,
        deadline={
            "at": deadline,
            "date_only": bool(deadline),
            "basis": "schedule_inferred" if deadline else "unknown",
        },
        audience={
            "scope": "subgroup" if subgroup else "group",
            "label": subgroup,
        },
        confidence=90,
        evidence=[
            EvidenceRef(
                telegram_message_id=1,
                revision=1,
                fact_paths=["subject", "description", "deadline"],
            )
        ],
    )


def edited(item, visibility="normal"):
    return EditedCard(
        title=item.title,
        description=item.description,
        visibility=visibility,
    )


async def seed_source(db, group):
    db.add(
        RawMessage(
            group_id=group.id,
            telegram_message_id=1,
            sender_id=1,
            text="По математике решить задачи 1–3",
            message_date=NOW,
            version_date=NOW,
            revision=1,
            delete_at=NOW + timedelta(days=30),
        )
    )
    await db.flush()


def test_create_update_and_evidence_history_are_revisioned(client):
    async def run():
        async with AsyncSession(client.app.state.engine, expire_on_commit=False) as db:
            group = await db.scalar(select(Group).where(Group.telegram_chat_id == -1001))
            await seed_source(db, group)
            initial = proposal()
            created = await resolve_proposal(
                db,
                group_id=group.id,
                proposal=initial,
                edited_card=edited(initial),
                now=NOW,
            )
            await db.commit()
            assert created.action == "create"
            changed = proposal(title="Решить задачи 1–4")
            updated = await resolve_proposal(
                db,
                group_id=group.id,
                proposal=changed,
                edited_card=edited(changed),
                now=NOW + timedelta(minutes=1),
                action="update",
                existing_item_id=created.item.id,
                expected_item_revision=1,
            )
            await db.commit()
            assert updated.action == "update"
            assert updated.item.revision == 2
            assert await db.scalar(select(func.count()).select_from(CanonicalItemRevision)) == 2
            assert await db.scalar(select(func.count()).select_from(CanonicalItemEvidence)) == 6

    asyncio.run(run())


def test_same_subject_and_date_do_not_merge_without_explicit_identity(client):
    async def run():
        async with AsyncSession(client.app.state.engine) as db:
            group = await db.scalar(select(Group).where(Group.telegram_chat_id == -1001))
            await seed_source(db, group)
            due = datetime(2026, 10, 15, tzinfo=UTC)
            first = proposal(title="Задачи 1–3", deadline=due)
            second = proposal(title="Задачи 4–6", deadline=due)
            await resolve_proposal(
                db,
                group_id=group.id,
                proposal=first,
                edited_card=edited(first),
                now=NOW,
            )
            await resolve_proposal(
                db,
                group_id=group.id,
                proposal=second,
                edited_card=edited(second),
                now=NOW,
            )
            await db.commit()
            assert await db.scalar(select(func.count()).select_from(CanonicalAcademicItem)) == 2

    asyncio.run(run())


def test_subgroup_items_remain_separate(client):
    async def run():
        async with AsyncSession(client.app.state.engine) as db:
            group = await db.scalar(select(Group).where(Group.telegram_chat_id == -1001))
            await seed_source(db, group)
            for label in ("Подгруппа 1", "Подгруппа 2"):
                item = proposal(subgroup=label)
                await resolve_proposal(
                    db,
                    group_id=group.id,
                    proposal=item,
                    edited_card=edited(item),
                    now=NOW,
                )
            await db.commit()
            labels = (
                await db.scalars(
                    select(CanonicalAcademicItem.audience_label).order_by(
                        CanonicalAcademicItem.audience_label
                    )
                )
            ).all()
            assert labels == ["Подгруппа 1", "Подгруппа 2"]

    asyncio.run(run())


def test_stale_revision_and_manual_lock_prevent_mutation(client):
    async def run():
        async with AsyncSession(client.app.state.engine, expire_on_commit=False) as db:
            group = await db.scalar(select(Group).where(Group.telegram_chat_id == -1001))
            await seed_source(db, group)
            item = proposal()
            created = await resolve_proposal(
                db,
                group_id=group.id,
                proposal=item,
                edited_card=edited(item),
                now=NOW,
            )
            await db.commit()
            stale = await resolve_proposal(
                db,
                group_id=group.id,
                proposal=item,
                edited_card=edited(item),
                now=NOW,
                action="update",
                existing_item_id=created.item.id,
                expected_item_revision=0,
            )
            assert stale.action == "stale"
            created.item.manual_lock = True
            await db.commit()
            locked = await resolve_proposal(
                db,
                group_id=group.id,
                proposal=item,
                edited_card=edited(item),
                now=NOW,
                action="cancel",
                existing_item_id=created.item.id,
                expected_item_revision=1,
            )
            assert locked.action == "blocked_by_lock"
            assert created.item.state == "active"

    asyncio.run(run())


def test_schedule_inferred_deadline_remains_distinguishable(client):
    async def run():
        async with AsyncSession(client.app.state.engine, expire_on_commit=False) as db:
            group = await db.scalar(select(Group).where(Group.telegram_chat_id == -1001))
            await seed_source(db, group)
            item = proposal(deadline=datetime(2026, 10, 15, tzinfo=UTC))
            result = await resolve_proposal(
                db,
                group_id=group.id,
                proposal=item,
                edited_card=edited(item),
                now=NOW,
            )
            await db.commit()
            assert result.item.deadline_basis == "schedule_inferred"

    asyncio.run(run())
