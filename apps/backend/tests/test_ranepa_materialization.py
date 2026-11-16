"""P0-4/P0-5: transaction-safe group/subgroup upsert and coverage-aware queue."""

import asyncio
import json
from datetime import UTC, date, datetime, time, timedelta
from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from studgroup.models import (
    Base,
    Group,
    Membership,
    MemberSubgroup,
    RanepaCrawlJob,
    RanepaPersonalSnapshot,
    Subgroup,
    SubjectSplit,
    User,
)
from studgroup.ranepa_crawl_jobs import claim_next, enqueue_week, finish
from studgroup.ranepa_crawl_plan import CrawlCandidate
from studgroup.ranepa_materialization import materialize_verified_facts
from studgroup.ranepa_personal import (
    personal_period,
    personal_with_group_fallback,
    remember_personal,
)
from studgroup.ranepa_schedule import ExternalLesson, WeekSnapshot
from studgroup.subject_identity import subject_uuid

WEEK = date(2026, 11, 16)
CODE = "GROUP-A"
CHECKED = datetime(2026, 11, 16, 11, tzinfo=UTC)


async def fixture(engine):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    group_id = UUID(int=11)
    users = [UUID(int=i) for i in (101, 102, 103)]
    async with AsyncSession(engine, expire_on_commit=False) as db:
        db.add(
            Group(
                id=group_id,
                name="Test",
                telegram_chat_id=-60011,
                academic_group_code=CODE,
                timezone="Europe/Moscow",
            )
        )
        for index, user_id in enumerate(users):
            db.add(User(id=user_id, telegram_user_id=61100 + index, display_name=f"S{index}"))
        await db.flush()
        memberships = []
        for user_id in users:
            row = Membership(
                id=UUID(int=900 + user_id.int),
                group_id=group_id,
                user_id=user_id,
                role="student",
                status="active",
            )
            memberships.append(row)
            db.add(row)
        split = SubjectSplit(
            id=UUID(int=300),
            group_id=group_id,
            subject_id=subject_uuid(group_id, "Иностранный язык"),
            subject_name="Иностранный язык",
            created_at=CHECKED,
        )
        db.add(split)
        await db.flush()
        subgroups = [
            Subgroup(
                id=UUID(int=301 + i),
                split_id=split.id,
                position=i + 1,
                name=f"Sub{i}",
                signals="[]",
            )
            for i in range(2)
        ]
        db.add_all(subgroups)
        await db.flush()
        for member, subgroup in zip(memberships, [subgroups[0], subgroups[0], subgroups[1]]):
            db.add(
                MemberSubgroup(
                    membership_id=member.id,
                    split_id=split.id,
                    subgroup_id=subgroup.id,
                    updated_at=CHECKED,
                )
            )
        await db.flush()
        common = ExternalLesson(
            WEEK, time(9), time(10, 20), "Общий предмет", teacher="Иванов", room="СДО"
        )
        specific = ExternalLesson(
            WEEK,
            time(11),
            time(12, 20),
            "Иностранный язык",
            teacher="Петров",
            subgroup_hint="Поток А",
        )
        snapshot = WeekSnapshot(CODE, WEEK, WEEK + timedelta(days=6), (common, specific))
        key = (WEEK.isoformat(), "09:00:00", "10:20:00", "Общий предмет", "Иванов", "СДО")
        await remember_personal(
            db,
            group_id,
            users[0],
            snapshot,
            group_identity_verified=True,
            links={key: "https://lms.ranepa.ru/mod/url/view.php?id=9876"},
            courses={key: "https://lms.ranepa.ru/course/view.php?id=1234"},
            checked_at=CHECKED,
        )
        await db.commit()
    return group_id, users, split.id, subgroups


def test_representative_upsert_is_idempotent_and_preserves_subgroup_isolation(tmp_path):
    async def scenario():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/material.db")
        try:
            group_id, users, split_id, groups = await fixture(engine)
            async with AsyncSession(engine) as db:
                stats = await materialize_verified_facts(
                    db, group_id, users[0], WEEK, checked_at=CHECKED
                )
                assert stats == {"users_materialized": 2, "lessons_added": 3}
                await db.commit()
            async with AsyncSession(engine) as db:
                a = await personal_period(db, group_id, users[1], WEEK, WEEK + timedelta(days=6))
                b = await personal_period(db, group_id, users[2], WEEK, WEEK + timedelta(days=6))
                assert {row["subject"] for row in a["lessons"]} == {
                    "Общий предмет",
                    "Иностранный язык",
                }
                assert {row["subject"] for row in b["lessons"]} == {"Общий предмет"}
                assert b["lessons"][0]["online_url"] == (
                    "https://lms.ranepa.ru/mod/url/view.php?id=9876"
                )
                assert b["lessons"][0]["course_url"] == (
                    "https://lms.ranepa.ru/course/view.php?id=1234"
                )
                before = await db.get(RanepaPersonalSnapshot, (group_id, users[1], WEEK))
                first_digest = before.data_digest

            later = CHECKED + timedelta(minutes=3)
            async with AsyncSession(engine) as db:
                stats = await materialize_verified_facts(
                    db, group_id, users[0], WEEK, checked_at=later
                )
                assert stats["lessons_added"] == 0
                await db.commit()
            async with AsyncSession(engine) as db:
                snap = await db.get(RanepaPersonalSnapshot, (group_id, users[1], WEEK))
                rows = json.loads(snap.rows_json)
                assert len(rows) == 2
                assert snap.data_digest != first_digest
                assert all(row["materialized_at"] == later.isoformat() for row in rows)
                assert snap.identity_verified is False

                # Changing a confirmed membership instantly hides stale subgroup facts.
                choice = await db.get(MemberSubgroup, (UUID(int=1002), split_id))
                choice.subgroup_id = groups[1].id
                await db.commit()
            async with AsyncSession(engine) as db:
                new_view = await personal_period(
                    db, group_id, users[1], WEEK, WEEK + timedelta(days=6)
                )
                assert {row["subject"] for row in new_view["lessons"]} == {"Общий предмет"}
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_materialization_failure_rolls_back_all_students(tmp_path):
    async def scenario():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/rollback.db")
        try:
            group_id, users, _, _ = await fixture(engine)
            async with AsyncSession(engine) as db:
                bad = RanepaPersonalSnapshot(
                    group_id=group_id,
                    user_id=users[2],
                    week_start=WEEK,
                    week_end=WEEK + timedelta(days=6),
                    identity_verified=False,
                    data_digest="invalid",
                    rows_json="not-json",
                    checked_at=CHECKED,
                )
                db.add(bad)
                await db.commit()
            async with AsyncSession(engine) as db:
                with pytest.raises(json.JSONDecodeError):
                    await materialize_verified_facts(db, group_id, users[0], WEEK)
                await db.rollback()
            async with AsyncSession(engine) as db:
                target_a = await db.get(RanepaPersonalSnapshot, (group_id, users[1], WEEK))
                assert target_a is None
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_queue_skips_covered_account_but_not_uncovered_subgroup(tmp_path):
    async def scenario():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/queue.db")
        try:
            group_id, users, _, _ = await fixture(engine)
            async with AsyncSession(engine, expire_on_commit=False) as db:
                candidates = [CrawlCandidate(group_id, user_id) for user_id in users]
                assert await enqueue_week(db, WEEK, candidates, now=CHECKED) == 3
                first = await claim_next(db, now=CHECKED + timedelta(minutes=1))
                assert first.user_id == users[0]
                assert await finish(
                    db, first.id, group_id, users[0], now=CHECKED + timedelta(minutes=1)
                )
                # Same confirmed subgroup: already covered, no actual crawl.
                # Next account belongs to another subgroup, so its scan remains due.
                next_job = await claim_next(db, now=CHECKED + timedelta(minutes=2))
                assert next_job.user_id == users[2]
                covered = await db.scalar(
                    select(RanepaCrawlJob).where(RanepaCrawlJob.user_id == users[1])
                )
                assert covered.state == "covered" and covered.attempts == 0
                await db.commit()
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_borrowed_snapshot_cannot_be_rebroadcast_or_cover_a_new_subgroup(tmp_path):
    """A verified account with peer rows must not relabel those rows after a switch."""

    async def scenario():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/borrowed.db")
        try:
            group_id, users, split_id, subgroups = await fixture(engine)
            async with AsyncSession(engine, expire_on_commit=False) as db:
                await materialize_verified_facts(db, group_id, users[0], WEEK)
                # The second student previously received subgroup A from the source.
                choice = await db.get(MemberSubgroup, (UUID(int=1002), split_id))
                choice.subgroup_id = subgroups[1].id
                await db.flush()
                # Their own subsequent verified read has no lessons, but the
                # cache deliberately preserves materialized peer facts.
                empty = WeekSnapshot(CODE, WEEK, WEEK + timedelta(days=6), ())
                await remember_personal(
                    db,
                    group_id,
                    users[1],
                    empty,
                    group_identity_verified=True,
                    checked_at=CHECKED + timedelta(minutes=1),
                )
                own = await db.get(RanepaPersonalSnapshot, (group_id, users[1], WEEK))
                assert own.identity_verified
                assert any(
                    row.get("source_scope") == "subgroup"
                    for row in json.loads(own.rows_json)
                )

                # It must not re-share A as if B were observed by student two.
                outcome = await materialize_verified_facts(db, group_id, users[1], WEEK)
                assert outcome == {"users_materialized": 0, "lessons_added": 0}
                b = await personal_period(db, group_id, users[2], WEEK, WEEK + timedelta(days=6))
                assert {item["subject"] for item in b["lessons"]} == {"Общий предмет"}

                # Nor may the borrowed A row suppress a real B account crawl.
                await enqueue_week(
                    db, WEEK, [CrawlCandidate(group_id, users[2])],
                    now=CHECKED + timedelta(minutes=2),
                )
                claimed = await claim_next(db, now=CHECKED + timedelta(minutes=3))
                assert claimed is not None and claimed.user_id == users[2]
                await db.commit()
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_unconfigured_subgroup_hint_is_neither_fallback_nor_shared(tmp_path):
    """A subgroup tag in an unconfigured subject is not a common-group fact."""

    async def scenario():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/hints.db")
        try:
            group_id, users, _, _ = await fixture(engine)
            source_week = WeekSnapshot(
                CODE, WEEK, WEEK + timedelta(days=6),
                (
                    ExternalLesson(
                        WEEK, time(9), time(10, 20),
                        "Неразмеченный факультатив",
                        teacher="Преподаватель B",
                        subgroup_hint="Поток Б",
                    ),
                    ExternalLesson(
                        WEEK, time(11), time(12, 20),
                        "Общий семинар", teacher="Преподаватель",
                        subgroup_hint="Общий поток",
                    ),
                ),
            )
            async with AsyncSession(engine, expire_on_commit=False) as db:
                await remember_personal(
                    db, group_id, users[0], source_week,
                    group_identity_verified=True, checked_at=CHECKED,
                )
                await db.commit()
            async with AsyncSession(engine, expire_on_commit=False) as db:
                fallback = await personal_with_group_fallback(
                    db, group_id, users[2], WEEK, WEEK + timedelta(days=6)
                )
                assert {item["subject"] for item in fallback["lessons"]} == {
                    "Общий семинар"
                }
                await materialize_verified_facts(db, group_id, users[0], WEEK)
                await db.commit()
            async with AsyncSession(engine) as db:
                projected = await personal_period(
                    db, group_id, users[2], WEEK, WEEK + timedelta(days=6)
                )
                assert {item["subject"] for item in projected["lessons"]} == {
                    "Общий семинар"
                }
        finally:
            await engine.dispose()

    asyncio.run(scenario())
