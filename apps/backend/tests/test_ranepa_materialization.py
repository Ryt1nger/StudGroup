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
    RanepaMaterializationReceipt,
    RanepaPersonalSnapshot,
    Subgroup,
    SubjectSplit,
    User,
)
from studgroup.ranepa_crawl_jobs import _coverage_satisfied, claim_next, enqueue_week, finish
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
            source_complete=True,
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
                assert b["schedule_complete"] is True
                assert b["source_checked"] is False
                assert b["oldest_required_checked_at"] is None
                assert b["last_success_at"].replace(tzinfo=UTC) == CHECKED
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
                # A failed fan-out cannot suppress an alternate account crawl.
                await enqueue_week(db, WEEK, [CrawlCandidate(group_id, users[1])], now=CHECKED)
                retry = await claim_next(db, now=CHECKED + timedelta(minutes=1))
                assert retry is not None and retry.user_id == users[1]
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
                # Delivery proof, not just the source read, enables covered.
                await materialize_verified_facts(db, group_id, users[0], WEEK)
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
                    source_complete=True,
                    checked_at=CHECKED + timedelta(minutes=1),
                )
                own = await db.get(RanepaPersonalSnapshot, (group_id, users[1], WEEK))
                assert own.identity_verified
                assert any(
                    row.get("source_scope") == "subgroup" for row in json.loads(own.rows_json)
                )

                # It must not re-share A as if B were observed by student two.
                outcome = await materialize_verified_facts(db, group_id, users[1], WEEK)
                assert outcome == {"users_materialized": 0, "lessons_added": 0}
                b = await personal_period(db, group_id, users[2], WEEK, WEEK + timedelta(days=6))
                assert {item["subject"] for item in b["lessons"]} == {"Общий предмет"}

                # Nor may the borrowed A row suppress a real B account crawl.
                await enqueue_week(
                    db,
                    WEEK,
                    [CrawlCandidate(group_id, users[2])],
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
                CODE,
                WEEK,
                WEEK + timedelta(days=6),
                (
                    ExternalLesson(
                        WEEK,
                        time(9),
                        time(10, 20),
                        "Неразмеченный факультатив",
                        teacher="Преподаватель B",
                        subgroup_hint="Поток Б",
                    ),
                    ExternalLesson(
                        WEEK,
                        time(11),
                        time(12, 20),
                        "Общий семинар",
                        teacher="Преподаватель",
                        subgroup_hint="Общий поток",
                    ),
                ),
            )
            async with AsyncSession(engine, expire_on_commit=False) as db:
                await remember_personal(
                    db,
                    group_id,
                    users[0],
                    source_week,
                    group_identity_verified=True,
                    source_complete=True,
                    checked_at=CHECKED,
                )
                await db.commit()
            async with AsyncSession(engine, expire_on_commit=False) as db:
                fallback = await personal_with_group_fallback(
                    db, group_id, users[2], WEEK, WEEK + timedelta(days=6)
                )
                assert {item["subject"] for item in fallback["lessons"]} == {"Общий семинар"}
                await materialize_verified_facts(db, group_id, users[0], WEEK)
                await db.commit()
            async with AsyncSession(engine) as db:
                projected = await personal_period(
                    db, group_id, users[2], WEEK, WEEK + timedelta(days=6)
                )
                assert {item["subject"] for item in projected["lessons"]} == {"Общий семинар"}
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_switching_source_subgroup_invalidates_old_split_evidence(tmp_path):
    """A source snapshot from group A cannot be relabelled B after changing choice."""

    async def scenario():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/stale-choice.db")
        try:
            group_id, users, split_id, subgroups = await fixture(engine)
            async with AsyncSession(engine, expire_on_commit=False) as db:
                source_choice = await db.get(MemberSubgroup, (UUID(int=1001), split_id))
                source_choice.subgroup_id = subgroups[1].id
                source_choice.updated_at = CHECKED + timedelta(minutes=1)
                await db.commit()
            async with AsyncSession(engine, expire_on_commit=False) as db:
                fallback = await personal_with_group_fallback(
                    db, group_id, users[2], WEEK, WEEK + timedelta(days=6)
                )
                assert {lesson["subject"] for lesson in fallback["lessons"]} == {"Общий предмет"}
                await materialize_verified_facts(db, group_id, users[0], WEEK)
                await db.commit()
            async with AsyncSession(engine, expire_on_commit=False) as db:
                projected = await personal_period(
                    db, group_id, users[2], WEEK, WEEK + timedelta(days=6)
                )
                assert {lesson["subject"] for lesson in projected["lessons"]} == {"Общий предмет"}
                await enqueue_week(
                    db,
                    WEEK,
                    [CrawlCandidate(group_id, users[2])],
                    now=CHECKED + timedelta(minutes=2),
                )
                claimed = await claim_next(db, now=CHECKED + timedelta(minutes=3))
                assert claimed is not None and claimed.user_id == users[2]
                await db.commit()
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_full_source_replacement_removes_missing_pair_and_changes_room_time(tmp_path):
    async def scenario():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/replace.db")
        try:
            group_id, users, _, _ = await fixture(engine)
            async with AsyncSession(engine) as db:
                await materialize_verified_facts(db, group_id, users[0], WEEK)
                await db.commit()

            moved = ExternalLesson(
                WEEK,
                time(10),
                time(11, 20),
                "Общий предмет",
                teacher="Новый преподаватель",
                room="Д-123",
            )
            revised = WeekSnapshot(CODE, WEEK, WEEK + timedelta(days=6), (moved,))
            async with AsyncSession(engine) as db:
                await remember_personal(
                    db,
                    group_id,
                    users[0],
                    revised,
                    group_identity_verified=True,
                    source_complete=True,
                    checked_at=CHECKED + timedelta(minutes=5),
                )
                await materialize_verified_facts(db, group_id, users[0], WEEK)
                await db.commit()
            async with AsyncSession(engine) as db:
                for user_id in users[1:]:
                    personal = await personal_period(
                        db, group_id, user_id, WEEK, WEEK + timedelta(days=6)
                    )
                    assert len(personal["lessons"]) == 1
                    row = personal["lessons"][0]
                    assert row["subject"] == "Общий предмет"
                    assert row["starts"] == "10:00:00"
                    assert row["teacher"] == "Новый преподаватель"
                    assert row["room"] == "Д-123"
                # A second replay of the same version does not append duplicates.
                repeated = await materialize_verified_facts(
                    db, group_id, users[0], WEEK, checked_at=CHECKED + timedelta(minutes=5)
                )
                assert repeated["lessons_added"] == 0
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_complete_empty_week_clears_peer_rows_but_partial_does_not(tmp_path):
    async def scenario():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/empty.db")
        try:
            group_id, users, _, _ = await fixture(engine)
            async with AsyncSession(engine) as db:
                await materialize_verified_facts(db, group_id, users[0], WEEK)
                await db.commit()
            empty = WeekSnapshot(CODE, WEEK, WEEK + timedelta(days=6), ())
            partial = WeekSnapshot(
                CODE,
                WEEK,
                WEEK + timedelta(days=6),
                (ExternalLesson(WEEK, time(9), time(10, 20), "Общий предмет"),),
            )
            async with AsyncSession(engine) as db:
                await remember_personal(
                    db,
                    group_id,
                    users[0],
                    partial,
                    group_identity_verified=False,
                    source_complete=False,
                    checked_at=CHECKED + timedelta(minutes=1),
                )
                await db.commit()
            async with AsyncSession(engine) as db:
                with pytest.raises(ValueError, match="source_identity_unverified"):
                    await materialize_verified_facts(db, group_id, users[0], WEEK)
                await db.rollback()
                peer = await personal_period(db, group_id, users[1], WEEK, WEEK + timedelta(days=6))
                assert len(peer["lessons"]) == 2
            async with AsyncSession(engine) as db:
                await remember_personal(
                    db,
                    group_id,
                    users[0],
                    empty,
                    group_identity_verified=True,
                    source_complete=True,
                    checked_at=CHECKED + timedelta(minutes=2),
                )
                await materialize_verified_facts(db, group_id, users[0], WEEK)
                await db.commit()
            async with AsyncSession(engine) as db:
                for user_id in users[1:]:
                    peer = await personal_period(
                        db, group_id, user_id, WEEK, WEEK + timedelta(days=6)
                    )
                    assert peer["lessons"] == []
                job = RanepaCrawlJob(group_id=group_id, user_id=users[1], week_start=WEEK)
                assert not await _coverage_satisfied(db, job, CHECKED + timedelta(minutes=3))
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_personal_confirmed_meeting_link_outweighs_shared_refresh(tmp_path):
    async def scenario():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/personal-priority.db")
        try:
            group_id, users, _, _ = await fixture(engine)
            key = (
                WEEK.isoformat(),
                "09:00:00",
                "10:20:00",
                "Общий предмет",
                "Иванов",
                "СДО",
            )
            own = WeekSnapshot(
                CODE,
                WEEK,
                WEEK + timedelta(days=6),
                (
                    ExternalLesson(
                        WEEK,
                        time(9),
                        time(10, 20),
                        "Общий предмет",
                        teacher="Иванов",
                        room="СДО",
                    ),
                ),
            )
            async with AsyncSession(engine) as db:
                await materialize_verified_facts(db, group_id, users[0], WEEK)
                await remember_personal(
                    db,
                    group_id,
                    users[1],
                    own,
                    group_identity_verified=True,
                    source_complete=True,
                    links={key: "https://lms.ranepa.ru/mod/url/view.php?id=1111"},
                    checked_at=CHECKED + timedelta(minutes=1),
                )
                await materialize_verified_facts(
                    db, group_id, users[0], WEEK, checked_at=CHECKED + timedelta(minutes=2)
                )
                await db.commit()
            async with AsyncSession(engine) as db:
                peer = await personal_period(db, group_id, users[1], WEEK, WEEK + timedelta(days=6))
                common = [row for row in peer["lessons"] if row["subject"] == "Общий предмет"]
                assert len(common) == 1
                assert common[0]["online_url"].endswith("id=1111")
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_coverage_requires_committed_recipient_proof_and_fresh_subgroup(tmp_path):
    async def scenario():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/evidence.db")
        try:
            group_id, users, split_id, subgroups = await fixture(engine)
            job_a = RanepaCrawlJob(group_id=group_id, user_id=users[1], week_start=WEEK)
            job_b = RanepaCrawlJob(group_id=group_id, user_id=users[2], week_start=WEEK)
            async with AsyncSession(engine) as db:
                assert not await _coverage_satisfied(db, job_a, CHECKED + timedelta(minutes=1))
                await materialize_verified_facts(db, group_id, users[0], WEEK)
                await db.commit()
            async with AsyncSession(engine) as db:
                assert await _coverage_satisfied(db, job_a, CHECKED + timedelta(minutes=2))
                assert not await _coverage_satisfied(db, job_b, CHECKED + timedelta(minutes=2))
                assert not await _coverage_satisfied(db, job_a, CHECKED + timedelta(hours=7))
                choice = await db.get(MemberSubgroup, (UUID(int=1002), split_id))
                choice.subgroup_id = subgroups[1].id
                choice.updated_at = CHECKED + timedelta(minutes=3)
                await db.commit()
            async with AsyncSession(engine) as db:
                assert not await _coverage_satisfied(db, job_a, CHECKED + timedelta(minutes=4))
                view = await personal_period(db, group_id, users[1], WEEK, WEEK + timedelta(days=6))
                assert {row["subject"] for row in view["lessons"]} == {"Общий предмет"}
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_disabled_source_and_changed_source_version_invalidate_coverage(tmp_path):
    async def scenario():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/invalid-proof.db")
        try:
            group_id, users, _, _ = await fixture(engine)
            job = RanepaCrawlJob(group_id=group_id, user_id=users[1], week_start=WEEK)
            async with AsyncSession(engine) as db:
                await materialize_verified_facts(db, group_id, users[0], WEEK)
                await db.commit()
            async with AsyncSession(engine) as db:
                assert await _coverage_satisfied(db, job, CHECKED + timedelta(minutes=1))
                updated = WeekSnapshot(
                    CODE,
                    WEEK,
                    WEEK + timedelta(days=6),
                    (
                        ExternalLesson(
                            WEEK,
                            time(9),
                            time(10, 20),
                            "Перенесённый предмет",
                        ),
                    ),
                )
                await remember_personal(
                    db,
                    group_id,
                    users[0],
                    updated,
                    group_identity_verified=True,
                    source_complete=True,
                    checked_at=CHECKED + timedelta(minutes=2),
                )
                # Simulate a source update without a matching recipient commit.
                await db.commit()
            async with AsyncSession(engine) as db:
                assert not await _coverage_satisfied(db, job, CHECKED + timedelta(minutes=3))
                await materialize_verified_facts(db, group_id, users[0], WEEK)
                await db.commit()
            async with AsyncSession(engine) as db:
                assert await _coverage_satisfied(db, job, CHECKED + timedelta(minutes=4))
                source_member = await db.scalar(
                    select(Membership).where(
                        Membership.group_id == group_id,
                        Membership.user_id == users[0],
                    )
                )
                source_member.status = "left"
                await db.commit()
            async with AsyncSession(engine) as db:
                assert not await _coverage_satisfied(db, job, CHECKED + timedelta(minutes=5))
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_recipient_receipt_never_crosses_group_memberships(tmp_path):
    async def scenario():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/multi-group-proof.db")
        try:
            group_id, users, _, _ = await fixture(engine)
            async with AsyncSession(engine) as db:
                await materialize_verified_facts(db, group_id, users[0], WEEK)
                other = Group(
                    id=UUID(int=12),
                    name="Other",
                    telegram_chat_id=-60012,
                    academic_group_code="GROUP-B",
                    timezone="Europe/Moscow",
                )
                db.add(other)
                db.add(
                    Membership(
                        id=UUID(int=10050),
                        group_id=other.id,
                        user_id=users[1],
                        role="student",
                        status="active",
                    )
                )
                db.add(
                    RanepaPersonalSnapshot(
                        group_id=other.id,
                        user_id=users[1],
                        week_start=WEEK,
                        week_end=WEEK + timedelta(days=6),
                        rows_json="[]",
                        data_digest="0" * 64,
                        checked_at=CHECKED,
                        identity_verified=False,
                        source_complete=False,
                    )
                )
                await db.commit()
            other_job = RanepaCrawlJob(group_id=UUID(int=12), user_id=users[1], week_start=WEEK)
            async with AsyncSession(engine) as db:
                assert not await _coverage_satisfied(db, other_job, CHECKED + timedelta(minutes=1))
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_subgroup_only_source_never_creates_unmatched_or_common_receipt(tmp_path):
    async def scenario():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/subgroup-receipt.db")
        try:
            group_id, users, _, _ = await fixture(engine)
            source_week = WeekSnapshot(
                CODE,
                WEEK,
                WEEK + timedelta(days=6),
                (
                    ExternalLesson(
                        WEEK,
                        time(11),
                        time(12, 20),
                        "Иностранный язык",
                        teacher="Петров",
                        subgroup_hint="Поток А",
                    ),
                ),
            )
            async with AsyncSession(engine) as db:
                await remember_personal(
                    db,
                    group_id,
                    users[0],
                    source_week,
                    group_identity_verified=True,
                    source_complete=True,
                    checked_at=CHECKED + timedelta(minutes=1),
                )
                await materialize_verified_facts(db, group_id, users[0], WEEK)
                await db.commit()
            async with AsyncSession(engine) as db:
                a = await db.get(
                    RanepaMaterializationReceipt,
                    (group_id, users[1], WEEK, users[0]),
                )
                b = await db.get(
                    RanepaMaterializationReceipt,
                    (group_id, users[2], WEEK, users[0]),
                )
                assert a is not None
                assert json.loads(a.coverage_json)["common"] is False
                assert b is None
                for user_id in users[1:]:
                    job = RanepaCrawlJob(group_id=group_id, user_id=user_id, week_start=WEEK)
                    assert not await _coverage_satisfied(db, job, CHECKED + timedelta(minutes=2))
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_changed_teacher_and_room_do_not_reuse_stale_online_link(tmp_path):
    async def scenario():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/stale-link.db")
        try:
            group_id, users, _, _ = await fixture(engine)
            async with AsyncSession(engine) as db:
                await materialize_verified_facts(db, group_id, users[0], WEEK)
                await db.commit()
            new_week = WeekSnapshot(
                CODE,
                WEEK,
                WEEK + timedelta(days=6),
                (
                    ExternalLesson(
                        WEEK,
                        time(9),
                        time(10, 20),
                        "Общий предмет",
                        teacher="Другой преподаватель",
                        room="Д-101",
                    ),
                ),
            )
            async with AsyncSession(engine) as db:
                await remember_personal(
                    db,
                    group_id,
                    users[0],
                    new_week,
                    group_identity_verified=True,
                    source_complete=True,
                    checked_at=CHECKED + timedelta(minutes=4),
                )
                await materialize_verified_facts(db, group_id, users[0], WEEK)
                await db.commit()
            async with AsyncSession(engine) as db:
                projected = await personal_period(
                    db, group_id, users[2], WEEK, WEEK + timedelta(days=6)
                )
                assert len(projected["lessons"]) == 1
                lesson = projected["lessons"][0]
                assert lesson["teacher"] == "Другой преподаватель"
                assert lesson["room"] == "Д-101"
                assert lesson["online_url"] is None
                assert lesson["course_url"] is None
        finally:
            await engine.dispose()

    asyncio.run(scenario())
