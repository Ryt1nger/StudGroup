"""Isolated source and snapshot DB contract: group scoping and no live schedule writes."""

import asyncio
import json
from datetime import date, time
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from studgroup.analysis.subject_catalog import load_subject_catalog
from studgroup.models import (
    Base,
    Group,
    GroupOfficialSubject,
    GroupScheduleSnapshot,
    GroupScheduleSource,
    SchedulePattern,
)
from studgroup.ranepa_schedule import ExternalLesson, WeekSnapshot
from studgroup.ranepa_storage import configure_source, remember_snapshot

CODE = "Э-ОБ-385-04-26-02"
INTERNAL_ID = UUID("00000000-0000-4000-8000-000000000001")


def make_week(room="305", code=CODE):
    item = ExternalLesson(
        day=date(2026, 11, 2),
        starts=time(9),
        ends=time(10, 20),
        subject="Иностранный язык",
        teacher="Иванова",
        room=room,
    )
    return WeekSnapshot(code, date(2026, 11, 2), date(2026, 11, 8), (item,))


def test_group_scoped_idempotent_read_only_storage(tmp_path):
    async def scenario():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/source.db")
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with AsyncSession(engine) as db:
                group = Group(
                    id=uuid4(),
                    telegram_chat_id=-96001,
                    name="БИ 1.2",
                    timezone="Europe/Moscow",
                )
                other = Group(
                    id=uuid4(),
                    telegram_chat_id=-96002,
                    name="Другая группа",
                    timezone="Europe/Moscow",
                )
                db.add_all([group, other])
                await db.flush()
                source = await configure_source(db, group.id, CODE, INTERNAL_ID)
                assert source.sync_enabled is False
                assert await remember_snapshot(db, group.id, make_week()) == "new"
                assert await remember_snapshot(db, group.id, make_week()) == "unchanged"
                assert await remember_snapshot(db, group.id, make_week(room="407")) == "changed"
                official = (
                    await db.scalars(
                        select(GroupOfficialSubject).where(
                            GroupOfficialSubject.group_id == group.id
                        )
                    )
                ).all()
                assert len(official) == 1
                assert official[0].name == "Иностранный язык"
                assert official[0].subject_id != group.id
                catalog = await load_subject_catalog(db, group.id)
                assert [row.name for row in catalog.subjects] == ["Иностранный язык"]
                assert await load_subject_catalog(db, other.id) is None
                assert (
                    await db.scalar(
                        select(GroupScheduleSnapshot).where(
                            GroupScheduleSnapshot.group_id == other.id
                        )
                    )
                    is None
                )
                saved = await db.get(GroupScheduleSnapshot, (group.id, date(2026, 11, 2)))
                assert json.loads(saved.rows_json)[0]["room"] == "407"
                assert source.last_success_at is not None
                assert (await db.scalars(select(SchedulePattern))).all() == []
                updated = await configure_source(db, group.id, CODE)
                assert updated.upstream_group_uuid == INTERNAL_ID
                await configure_source(db, group.id, "Другая группа")
                assert (
                    await db.scalars(
                        select(GroupOfficialSubject).where(
                            GroupOfficialSubject.group_id == group.id
                        )
                    )
                ).all() == []
                await db.commit()
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_unverified_or_wrong_group_is_rejected(tmp_path):
    async def scenario():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/blocked.db")
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with AsyncSession(engine) as db:
                group = Group(
                    id=uuid4(),
                    telegram_chat_id=-96003,
                    name="БИ 1.2",
                    timezone="Europe/Moscow",
                )
                db.add(group)
                await db.flush()
                await configure_source(db, group.id, CODE)
                try:
                    await remember_snapshot(db, group.id, make_week())
                    assert False, "unverified mapping was accepted"
                except ValueError as error:
                    assert str(error) == "unverified_group_mapping"
                await configure_source(db, group.id, CODE, INTERNAL_ID)
                try:
                    await remember_snapshot(db, group.id, make_week(code="Чужая"))
                    assert False, "foreign schedule was accepted"
                except ValueError as error:
                    assert str(error) == "mismatched_group_code"
                assert (await db.scalars(select(GroupScheduleSource))).one() is not None
                assert (await db.scalars(select(SchedulePattern))).all() == []
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_official_subjects_collapse_obvious_repeats_across_lessons(tmp_path):
    async def scenario():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/dedupe.db")
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with AsyncSession(engine) as db:
                group = Group(
                    id=uuid4(), telegram_chat_id=-96003, name="БИ 1.2", timezone="Europe/Moscow"
                )
                db.add(group)
                await db.flush()
                await configure_source(db, group.id, CODE, INTERNAL_ID)
                lessons = tuple(
                    ExternalLesson(
                        day=date(2026, 11, day),
                        starts=time(9),
                        ends=time(10, 20),
                        subject=name,
                        teacher=None,
                        room=None,
                    )
                    for day, name in [
                        (2, "Линейная алгебра"),
                        (3, " ЛИНЕЙНАЯ   АЛГЕБРА "),
                        (4, "Математический анализ"),
                    ]
                )
                week = WeekSnapshot(CODE, date(2026, 11, 2), date(2026, 11, 8), lessons)
                await remember_snapshot(db, group.id, week)
                await remember_snapshot(db, group.id, week)
                rows = (
                    await db.scalars(
                        select(GroupOfficialSubject).where(
                            GroupOfficialSubject.group_id == group.id
                        )
                    )
                ).all()
                assert sorted(row.name for row in rows) == [
                    "Линейная алгебра",
                    "Математический анализ",
                ]
                assert len({row.subject_id for row in rows}) == 2
        finally:
            await engine.dispose()

    asyncio.run(scenario())
