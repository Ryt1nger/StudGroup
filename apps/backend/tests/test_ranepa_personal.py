"""Personal snapshot is immediately readable only by its owner and survives failures."""

import asyncio
from datetime import UTC, date, datetime, time, timedelta
from types import SimpleNamespace
from uuid import uuid4
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from studgroup.models import (
    Base,
    Group,
    GroupOfficialSubject,
    Membership,
    MemberSubgroup,
    RanepaPersonalSnapshot,
    SchedulePattern,
    Subgroup,
    SubjectSplit,
    User,
)
from studgroup.ranepa_link_states import LINK_STATES
from studgroup.ranepa_personal import (
    enrich_personal_links,
    personal_with_group_fallback,
    remember_personal,
    schedule_projection,
    schedule_with_group_fallback,
)
from studgroup.ranepa_schedule import ExternalLesson, WeekSnapshot
from studgroup.subject_identity import subject_uuid

pytest_plugins = ["test_schedule_api"]
AUTH = {"Authorization": "Bearer valid"}
CODE = "Э-ОБ-385-04-26-02"


def week(subject="История России"):
    return WeekSnapshot(
        CODE,
        date(2026, 11, 2),
        date(2026, 11, 8),
        (
            ExternalLesson(
                date(2026, 11, 2),
                time(9),
                time(10, 20),
                subject,
                teacher="Иванов",
                room="СДО",
                subgroup_hint="Э-ОБ26-СГЯ-лек(43,44,45)",
            ),
        ),
    )


def test_personal_snapshot_is_immediately_scoped_and_idempotent(client):
    async def seed():
        async with AsyncSession(client.app.state.engine, expire_on_commit=False) as db:
            own = await db.scalar(select(Group).where(Group.telegram_chat_id == -1001))
            own.academic_group_code = CODE
            member = await db.scalar(select(Membership).where(Membership.group_id == own.id))
            assert await remember_personal(db, own.id, member.user_id, week()) == "new"
            assert await remember_personal(db, own.id, member.user_id, week()) == "unchanged"
            assert (
                await remember_personal(db, own.id, member.user_id, week("Новая история"))
                == "changed"
            )
            key = ("2026-11-02", "09:00:00", "10:20:00", "Новая история", "Иванов", "СДО")
            with pytest.raises(ValueError, match="unsafe_lms_link"):
                await remember_personal(
                    db,
                    own.id,
                    member.user_id,
                    week("Новая история"),
                    links={key: "https://lms.ranepa.ru/mod/url/view.php?id=1&sesskey=private"},
                )
            with pytest.raises(ValueError, match="unsafe_lms_course"):
                await remember_personal(
                    db,
                    own.id,
                    member.user_id,
                    week("Новая история"),
                    courses={key: "https://lms.ranepa.ru/course/view.php?id=1&sesskey=private"},
                )
            with pytest.raises(ValueError, match="contradictory_lms_state"):
                await remember_personal(
                    db,
                    own.id,
                    member.user_id,
                    week("Новая история"),
                    link_states={key: "matched"},
                )
            assert (
                await remember_personal(
                    db,
                    own.id,
                    member.user_id,
                    week("Новая история"),
                    courses={key: "https://lms.ranepa.ru/course/view.php?id=89282"},
                    link_states={key: "no_course_links"},
                )
                == "changed"
            )
            outsider = User(id=uuid4(), telegram_user_id=9991, display_name="Other")
            db.add(outsider)
            await db.flush()
            with pytest.raises(ValueError, match="inactive_or_unconfigured_group_member"):
                await remember_personal(db, own.id, outsider.id, week())
            await db.commit()
            return own.id, member.user_id

    group_id, user_id = asyncio.run(seed())
    response = client.get("/v1/ranepa-schedule?start=2026-11-02&end=2026-11-08", headers=AUTH)
    assert response.status_code == 200
    body = response.json()
    assert body["complete"] is True and body["last_success_at"] is not None
    assert len(body["lessons"]) == 1
    assert body["lessons"][0]["subject"] == "Новая история"
    assert body["lessons"][0]["stream_label"] == "Э-ОБ26-СГЯ-лек(43,44,45)"
    assert body["lessons"][0]["link_state"] == "no_course_links"
    assert body["lessons"][0]["course_url"] == "https://lms.ranepa.ru/course/view.php?id=89282"
    projected = schedule_projection(
        {
            "start": date(2026, 11, 2),
            "end": date(2026, 11, 8),
            "complete": True,
            "lessons": body["lessons"],
        },
        type("GroupView", (), {"timezone": "Europe/Moscow", "first_week_anchor": None})(),
    )
    assert projected["week_state"] == "ready"
    assert projected["lessons"][0]["subject"] == "Новая история"
    assert projected["lessons"][0]["course_url"] == "https://lms.ranepa.ru/course/view.php?id=89282"
    assert projected["lessons"][0]["starts_at"] == "2026-11-02T09:00:00+03:00"
    client.app.state.settings.ranepa_schedule_view_enabled = True
    live = client.get("/v1/schedule?start=2026-11-02&end=2026-11-08", headers=AUTH)
    assert live.status_code == 200
    assert {row["subject"] for row in live.json()["lessons"]} == {
        "Математика",
        "Новая история",
    }
    missing = client.get("/v1/schedule?start=2026-11-16&end=2026-11-22", headers=AUTH)
    assert missing.status_code == 200
    assert missing.json()["week_state"] == "ready"
    assert [row["subject"] for row in missing.json()["lessons"]] == ["Математика"]
    lesson_id = next(
        row["id"] for row in live.json()["lessons"] if row["subject"] == "Новая история"
    )
    detail = client.get(f"/v1/lessons/{lesson_id}/subject", headers=AUTH)
    assert detail.status_code == 200
    assert detail.json()["lesson"]["subject"] == "Новая история"
    today = client.get("/v1/today", headers=AUTH)
    assert today.status_code == 200
    assert today.json()["group_timezone"] == "Europe/Moscow"
    client.app.state.settings.ranepa_schedule_view_enabled = False
    assert (
        client.get("/v1/ranepa-schedule?start=2026-11-02&end=2026-11-09", headers=AUTH).json()[
            "complete"
        ]
        is False
    )
    assert client.get("/v1/ranepa-schedule?start=2026-11-02&end=2026-11-08").status_code == 401

    async def stored():
        async with AsyncSession(client.app.state.engine) as db:
            return (await db.scalars(select(RanepaPersonalSnapshot))).all()

    rows = asyncio.run(stored())
    assert len(rows) == 1 and rows[0].group_id == group_id and rows[0].user_id == user_id

    async def clear_verified_week():
        async with AsyncSession(client.app.state.engine, expire_on_commit=False) as db:
            empty = WeekSnapshot(CODE, date(2026, 11, 2), date(2026, 11, 8), ())
            assert (
                await remember_personal(db, group_id, user_id, empty, group_identity_verified=True)
                == "changed"
            )
            await db.commit()

    asyncio.run(clear_verified_week())
    cleared = client.get("/v1/ranepa-schedule?start=2026-11-02&end=2026-11-08", headers=AUTH)
    assert cleared.json()["complete"] is True and cleared.json()["lessons"] == []


def test_every_group_retains_editor_published_schedule_without_personal_data(client):
    async def configure():
        async with AsyncSession(client.app.state.engine) as db:
            group = await db.scalar(select(Group).where(Group.telegram_chat_id == -1001))
            group.academic_group_code = CODE
            await db.commit()

    asyncio.run(configure())
    client.app.state.settings.ranepa_schedule_view_enabled = True
    response = client.get("/v1/schedule?start=2026-11-02&end=2026-11-08", headers=AUTH)
    assert response.status_code == 200
    assert response.json()["week_state"] == "ready"
    assert [row["subject"] for row in response.json()["lessons"]] == ["Математика"]


def test_peer_schedule_fills_group_calendar_without_sharing_private_sdo_url(client):
    async def configure():
        async with AsyncSession(client.app.state.engine) as db:
            group = await db.scalar(select(Group).where(Group.telegram_chat_id == -1001))
            group.academic_group_code = CODE
            peer = User(id=uuid4(), telegram_user_id=887711, display_name="Peer")
            db.add(peer)
            await db.flush()
            db.add(
                Membership(
                    id=uuid4(),
                    group_id=group.id,
                    user_id=peer.id,
                    status="active",
                    role="student",
                )
            )
            await db.flush()
            snapshot = WeekSnapshot(
                CODE,
                date(2026, 11, 2),
                date(2026, 11, 8),
                (
                    ExternalLesson(
                        date(2026, 11, 2),
                        time(11),
                        time(12),
                        "Физика",
                        teacher="Тест",
                        room="СДО",
                    ),
                ),
            )
            key = ("2026-11-02", "11:00:00", "12:00:00", "Физика", "Тест", "СДО")
            await remember_personal(
                db,
                group.id,
                peer.id,
                snapshot,
                group_identity_verified=True,
                links={key: "https://lms.ranepa.ru/mod/url/view.php?id=9911"},
            )
            await db.commit()

    asyncio.run(configure())
    client.app.state.settings.ranepa_schedule_view_enabled = True
    response = client.get("/v1/schedule?start=2026-11-02&end=2026-11-08", headers=AUTH)
    assert response.status_code == 200
    lessons = response.json()["lessons"]
    assert {row["subject"] for row in lessons} == {"Математика", "Физика"}
    physics = next(row for row in lessons if row["subject"] == "Физика")
    assert physics["online_url"] is None
    assert physics["course_url"] is None


def test_today_retains_group_timetable_when_sdo_is_enabled_without_any_own_scan(client):
    async def configure():
        today = datetime.now(ZoneInfo("Europe/Moscow")).date()
        async with AsyncSession(client.app.state.engine) as db:
            group = await db.scalar(select(Group).where(Group.telegram_chat_id == -1001))
            group.academic_group_code = CODE
            row = await db.scalar(
                select(SchedulePattern).where(
                    SchedulePattern.group_id == group.id,
                    SchedulePattern.subject == "Математика",
                )
            )
            row.valid_from = today - timedelta(days=7)
            row.valid_until = today + timedelta(days=70)
            row.weekday = (today + timedelta(days=1)).weekday()
            await db.commit()

    asyncio.run(configure())
    client.app.state.settings.ranepa_schedule_view_enabled = True
    result = client.get("/v1/today", headers=AUTH)
    assert result.status_code == 200
    body = result.json()
    assert body["next_lesson"]["lesson"]["subject"] == "Математика"
    assert body["freshness"]["stale"] is False
    assert body["day_lessons_state"] == "empty"


def test_group_fallback_never_gives_another_subgroups_lesson(tmp_path):
    async def scenario():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/subgroups.db")
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            async with AsyncSession(engine, expire_on_commit=False) as db:
                group = Group(
                    id=uuid4(),
                    telegram_chat_id=-99010,
                    name="Group",
                    academic_group_code=CODE,
                    timezone="Europe/Moscow",
                )
                users = [
                    User(id=uuid4(), telegram_user_id=700 + i, display_name=f"U{i}")
                    for i in range(3)
                ]
                db.add_all([group, *users])
                await db.flush()
                members = [
                    Membership(
                        id=uuid4(),
                        group_id=group.id,
                        user_id=user.id,
                        role="student",
                        status="active",
                    )
                    for user in users
                ]
                split = SubjectSplit(
                    id=uuid4(),
                    group_id=group.id,
                    subject_id=subject_uuid(group.id, "Иностранный язык"),
                    subject_name="Иностранный язык",
                    created_at=datetime.now(UTC),
                )
                db.add_all([*members, split])
                await db.flush()
                a = Subgroup(id=uuid4(), split_id=split.id, position=1, name="A", signals="[]")
                b = Subgroup(id=uuid4(), split_id=split.id, position=2, name="B", signals="[]")
                db.add_all([a, b])
                await db.flush()
                for member, subgroup in zip(members, (a, a, b), strict=True):
                    db.add(
                        MemberSubgroup(
                            membership_id=member.id,
                            split_id=split.id,
                            subgroup_id=subgroup.id,
                            updated_at=datetime.now(UTC),
                        )
                    )
                await db.flush()
                for user, teacher, stream in (
                    (users[1], "Teacher A", "Поток A"),
                    (users[2], "Teacher B", "Поток B"),
                ):
                    snapshot = WeekSnapshot(
                        CODE,
                        date(2026, 11, 2),
                        date(2026, 11, 8),
                        (
                            ExternalLesson(
                                date(2026, 11, 2),
                                time(9),
                                time(10, 20),
                                "Иностранный язык",
                                teacher=teacher,
                                subgroup_hint=stream,
                            ),
                            ExternalLesson(
                                date(2026, 11, 2),
                                time(10, 40),
                                time(12),
                                "История России",
                                teacher="Common",
                                subgroup_hint="Общий поток",
                            ),
                        ),
                    )
                    await remember_personal(
                        db, group.id, user.id, snapshot, group_identity_verified=True
                    )
                await db.commit()
                result = await personal_with_group_fallback(
                    db, group.id, users[0].id, date(2026, 11, 2), date(2026, 11, 8)
                )
                assert result["fallback_used"] is True and result["complete"] is False
                assert {(row["subject"], row["teacher"]) for row in result["lessons"]} == {
                    ("Иностранный язык", "Teacher A"),
                    ("История России", "Common"),
                }
                assert all(row["online_url"] is None for row in result["lessons"])
                names = (
                    await db.scalars(
                        select(GroupOfficialSubject.name).where(
                            GroupOfficialSubject.group_id == group.id
                        )
                    )
                ).all()
                assert sorted(names) == ["Иностранный язык", "История России"]
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_one_verified_peer_shares_common_lessons_but_respects_dynamic_subject_subgroups(tmp_path):
    """A single representative is enough for common classes, never another split."""

    async def scenario():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/one-peer.db")
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            async with AsyncSession(engine, expire_on_commit=False) as db:
                group = Group(
                    id=uuid4(),
                    telegram_chat_id=-99011,
                    name="Group",
                    academic_group_code=CODE,
                    timezone="Europe/Moscow",
                )
                users = [
                    User(id=uuid4(), telegram_user_id=800 + i, display_name=f"U{i}")
                    for i in range(4)
                ]
                db.add_all([group, *users])
                await db.flush()
                members = [
                    Membership(
                        id=uuid4(),
                        group_id=group.id,
                        user_id=user.id,
                        role="student",
                        status="active",
                    )
                    for user in users
                ]
                split_name = "Лабораторный практикум"
                split = SubjectSplit(
                    id=uuid4(),
                    group_id=group.id,
                    subject_id=subject_uuid(group.id, split_name),
                    subject_name=split_name,
                    created_at=datetime.now(UTC),
                )
                db.add_all([*members, split])
                await db.flush()
                options = [
                    Subgroup(
                        id=uuid4(),
                        split_id=split.id,
                        position=i + 1,
                        name=f"Поток {i + 1}",
                        signals="[]",
                    )
                    for i in range(3)
                ]
                db.add_all(options)
                await db.flush()
                for member, subgroup in zip(
                    members, [options[0], options[0], options[1], options[2]], strict=True
                ):
                    db.add(
                        MemberSubgroup(
                            membership_id=member.id,
                            split_id=split.id,
                            subgroup_id=subgroup.id,
                            updated_at=datetime.now(UTC),
                        )
                    )
                snapshot = WeekSnapshot(
                    CODE,
                    date(2026, 11, 2),
                    date(2026, 11, 8),
                    (
                        ExternalLesson(
                            date(2026, 11, 2),
                            time(9),
                            time(10, 20),
                            split_name,
                            teacher="Преподаватель первого потока",
                            subgroup_hint="Поток 1",
                        ),
                        ExternalLesson(
                            date(2026, 11, 2),
                            time(10, 40),
                            time(12),
                            "Общий предмет",
                            teacher="Преподаватель",
                            room="СДО",
                        ),
                    ),
                )
                await remember_personal(
                    db, group.id, users[0].id, snapshot, group_identity_verified=True
                )
                await db.commit()
                expectations = (
                    (users[1], {"Лабораторный практикум", "Общий предмет"}),
                    (users[2], {"Общий предмет"}),
                    (users[3], {"Общий предмет"}),
                )
                for target, subjects in expectations:
                    result = await personal_with_group_fallback(
                        db, group.id, target.id, date(2026, 11, 2), date(2026, 11, 8)
                    )
                    assert result["fallback_used"] is True
                    assert {item["subject"] for item in result["lessons"]} == subjects
                    assert all(item["online_url"] is None for item in result["lessons"])
                    assert all(item["course_url"] is None for item in result["lessons"])
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_occurrence_merge_keeps_partial_day_and_only_cancels_explicit_match():
    group = SimpleNamespace(timezone="Europe/Moscow", first_week_anchor=None)
    base = "2026-11-02T"
    published = {
        "week_state": "ready",
        "selected_week": None,
        "lessons": [
            {
                "id": f"editor:{name}",
                "subject": name,
                "teacher": "Иванов",
                "starts_at": base + start + "+03:00",
                "ends_at": base + end + "+03:00",
                "location": "Аудитория",
                "online_url": None,
                "status": "scheduled",
            }
            for name, start, end in [
                ("Математика", "09:00:00", "10:20:00"),
                ("Физика", "10:40:00", "12:00:00"),
            ]
        ],
    }
    personal = {
        "start": date(2026, 11, 2),
        "end": date(2026, 11, 8),
        "complete": True,
        "covered_dates": ["2026-11-02"],
        "lessons": [
            {
                "day": "2026-11-02",
                "starts": "09:00:00",
                "ends": "10:20:00",
                "subject": "Математика",
                "teacher": "Иванов",
                "room": "СДО",
                "online_url": None,
                "course_url": None,
                "cancelled": False,
            }
        ],
    }
    first = schedule_with_group_fallback(personal, published, group)
    assert {row["id"] for row in first["lessons"]} == {
        "editor:Математика",
        "editor:Физика",
    }
    assert all(row["status"] == "scheduled" for row in first["lessons"])
    personal["lessons"][0]["cancelled"] = True
    second = schedule_with_group_fallback(personal, published, group)
    assert {row["id"] for row in second["lessons"]} == {
        "editor:Математика",
        "editor:Физика",
    }
    assert {row["subject"] for row in second["lessons"] if row["status"] == "cancelled"} == {
        "Математика"
    }
    assert published["lessons"][0]["status"] == "scheduled"


def test_every_matcher_state_persists_with_real_snapshot_writer(client):
    async def scenario():
        async with AsyncSession(client.app.state.engine, expire_on_commit=False) as db:
            group = await db.scalar(select(Group).where(Group.telegram_chat_id == -1001))
            group.academic_group_code = CODE
            member = await db.scalar(select(Membership).where(Membership.group_id == group.id))
            key = ("2026-11-02", "09:00:00", "10:20:00", "История России", "Иванов", "СДО")
            for state in sorted(LINK_STATES):
                url = (
                    {key: "https://lms.ranepa.ru/mod/url/view.php?id=9911"}
                    if state == "matched"
                    else {}
                )
                await remember_personal(
                    db,
                    group.id,
                    member.user_id,
                    week(),
                    links=url,
                    link_states={key: state},
                    group_identity_verified=True,
                )
                await db.commit()
                rows = await db.get(
                    RanepaPersonalSnapshot,
                    (group.id, member.user_id, date(2026, 11, 2)),
                )
                assert rows.identity_verified is True
                assert __import__("json").loads(rows.rows_json)[0]["link_state"] == state

    asyncio.run(scenario())


def test_link_enrichment_rollback_does_not_erase_committed_week(client):
    async def scenario():
        async with AsyncSession(client.app.state.engine, expire_on_commit=False) as db:
            group = await db.scalar(select(Group).where(Group.telegram_chat_id == -1001))
            group.academic_group_code = CODE
            member = await db.scalar(select(Membership).where(Membership.group_id == group.id))
            await remember_personal(
                db, group.id, member.user_id, week(), group_identity_verified=True
            )
            await db.commit()
            group_id, user_id = group.id, member.user_id
            key = ("2026-11-02", "09:00:00", "10:20:00", "История России", "Иванов", "СДО")
            before = await db.get(
                RanepaPersonalSnapshot,
                (group_id, user_id, date(2026, 11, 2)),
            )
            baseline_checked_at = before.checked_at
            with pytest.raises(ValueError, match="unsafe_lms_link"):
                await enrich_personal_links(
                    db,
                    group_id,
                    user_id,
                    date(2026, 11, 2),
                    links={key: "https://invalid.example/join"},
                    link_states={key: "matched"},
                )
            await db.rollback()
            stored = await db.get(
                RanepaPersonalSnapshot,
                (group_id, user_id, date(2026, 11, 2)),
            )
            assert __import__("json").loads(stored.rows_json)[0]["subject"] == "История России"
            assert stored.checked_at == baseline_checked_at
            await enrich_personal_links(
                db,
                group_id,
                user_id,
                date(2026, 11, 2),
                link_states={key: "identity_missing"},
            )
            await db.commit()
            stored = await db.get(
                RanepaPersonalSnapshot,
                (group_id, user_id, date(2026, 11, 2)),
            )
            assert stored.checked_at == baseline_checked_at
            assert __import__("json").loads(stored.rows_json)[0]["link_state"] == "identity_missing"

    asyncio.run(scenario())


def test_verified_sdo_link_is_reused_for_the_same_account_in_a_later_week(client):
    async def scenario():
        async with AsyncSession(client.app.state.engine, expire_on_commit=False) as db:
            group = await db.scalar(select(Group).where(Group.telegram_chat_id == -1001))
            group.academic_group_code = CODE
            member = await db.scalar(select(Membership).where(Membership.group_id == group.id))
            first = week()
            first_key = (
                "2026-11-02",
                "09:00:00",
                "10:20:00",
                "История России",
                "Иванов",
                "СДО",
            )
            url = "https://lms.ranepa.ru/mod/url/view.php?id=9911"
            course = "https://lms.ranepa.ru/course/view.php?id=8822"
            await remember_personal(
                db,
                group.id,
                member.user_id,
                first,
                links={first_key: url},
                courses={first_key: course},
                group_identity_verified=True,
                source_complete=True,
            )
            later = WeekSnapshot(
                CODE,
                date(2026, 11, 9),
                date(2026, 11, 15),
                (
                    ExternalLesson(
                        date(2026, 11, 9),
                        time(9),
                        time(10, 20),
                        "История России",
                        teacher="Иванов",
                        room="СДО",
                        subgroup_hint="Э-ОБ26-СГЯ-лек(43,44,45)",
                    ),
                ),
            )
            await remember_personal(
                db,
                group.id,
                member.user_id,
                later,
                group_identity_verified=True,
                source_complete=True,
            )
            later_key = (
                "2026-11-09",
                "09:00:00",
                "10:20:00",
                "История России",
                "Иванов",
                "СДО",
            )
            await enrich_personal_links(
                db,
                group.id,
                member.user_id,
                later.week_start,
                link_states={later_key: "not_found"},
            )
            await db.commit()
            stored = await db.get(
                RanepaPersonalSnapshot,
                (group.id, member.user_id, later.week_start),
            )
            return __import__("json").loads(stored.rows_json)[0]

    row = asyncio.run(scenario())
    assert row["online_url"] == "https://lms.ranepa.ru/mod/url/view.php?id=9911"
    assert row["course_url"] == "https://lms.ranepa.ru/course/view.php?id=8822"
    assert row["link_state"] == "matched"
    assert row["link_source"] == "cached"
    assert row["link_cached_at"]


def test_course_candidate_memory_selects_correct_teacher_and_survives_failed_rescan(client):
    """One Moodle course can contain several professors' permanent meetings."""

    async def scenario():
        async with AsyncSession(client.app.state.engine, expire_on_commit=False) as db:
            group = await db.scalar(select(Group).where(Group.telegram_chat_id == -1001))
            group.academic_group_code = CODE
            member = await db.scalar(select(Membership).where(Membership.group_id == group.id))
            first = week()
            course = "https://lms.ranepa.ru/course/view.php?id=85813"
            key = (
                "2026-11-02",
                "09:00:00",
                "10:20:00",
                "История России",
                "Иванов",
                "СДО",
            )
            await remember_personal(
                db,
                group.id,
                member.user_id,
                first,
                group_identity_verified=True,
                source_complete=True,
            )
            from studgroup.ranepa_lms import LinkCandidate

            candidates = (
                LinkCandidate(
                    "https://lms.ranepa.ru/mod/url/view.php?id=101",
                    "Иванов И. И. Э-ОБ26-СГЯ-лек(43,44,45) постоянная ссылка",
                ),
                LinkCandidate(
                    "https://lms.ranepa.ru/mod/url/view.php?id=102",
                    "Петров П. П. Э-ОБ26-СГЯ-лек(43,44,45) постоянная ссылка",
                ),
            )
            await enrich_personal_links(
                db,
                group.id,
                member.user_id,
                first.week_start,
                courses={key: course},
                link_states={key: "not_found"},
                candidates={key: candidates},
            )
            await db.commit()
            old = await db.get(RanepaPersonalSnapshot, (group.id, member.user_id, first.week_start))
            stored = __import__("json").loads(old.rows_json)[0]
            assert len(stored["link_candidates"]) == 2
            assert stored["online_url"].endswith("id=101")

            second = WeekSnapshot(
                CODE,
                date(2026, 11, 9),
                date(2026, 11, 15),
                (
                    ExternalLesson(
                        date(2026, 11, 9),
                        time(9),
                        time(10, 20),
                        "История России",
                        teacher="Петров",
                        room="СДО",
                        subgroup_hint="Э-ОБ26-СГЯ-лек(43,44,45)",
                    ),
                ),
            )
            await remember_personal(
                db,
                group.id,
                member.user_id,
                second,
                group_identity_verified=True,
                source_complete=True,
            )
            await db.commit()
            second_key = (
                "2026-11-09",
                "09:00:00",
                "10:20:00",
                "История России",
                "Петров",
                "СДО",
            )
            await enrich_personal_links(
                db,
                group.id,
                member.user_id,
                second.week_start,
                link_states={second_key: "navigation_error"},
            )
            await db.commit()
            later = await db.get(
                RanepaPersonalSnapshot, (group.id, member.user_id, second.week_start)
            )
            return __import__("json").loads(later.rows_json)[0]

    row = asyncio.run(scenario())
    assert row["course_url"] == "https://lms.ranepa.ru/course/view.php?id=85813"
    assert row["online_url"] == "https://lms.ranepa.ru/mod/url/view.php?id=102"
    assert row["link_state"] == "matched"
    assert row["link_source"] == "cached"
    assert len(row["link_candidates"]) == 2


def test_ambiguous_teacher_meetings_are_stored_but_not_guessed(client):
    async def scenario():
        async with AsyncSession(client.app.state.engine, expire_on_commit=False) as db:
            group = await db.scalar(select(Group).where(Group.telegram_chat_id == -1001))
            group.academic_group_code = CODE
            member = await db.scalar(select(Membership).where(Membership.group_id == group.id))
            snapshot = week()
            key = (
                "2026-11-02",
                "09:00:00",
                "10:20:00",
                "История России",
                "Иванов",
                "СДО",
            )
            from studgroup.ranepa_lms import LinkCandidate

            await remember_personal(
                db, group.id, member.user_id, snapshot, group_identity_verified=True
            )
            await enrich_personal_links(
                db,
                group.id,
                member.user_id,
                snapshot.week_start,
                courses={key: "https://lms.ranepa.ru/course/view.php?id=1011"},
                link_states={key: "ambiguous"},
                candidates={
                    key: (
                        LinkCandidate(
                            "https://lms.ranepa.ru/mod/url/view.php?id=201",
                            "Иванов И.И. Э-ОБ26-СГЯ-лек(43,44,45) постоянная ссылка",
                        ),
                        LinkCandidate(
                            "https://lms.ranepa.ru/mod/url/view.php?id=202",
                            "Иванов И.И. Э-ОБ26-СГЯ-лек(43,44,45) постоянная ссылка 2",
                        ),
                    )
                },
            )
            await db.commit()
            row = await db.get(
                RanepaPersonalSnapshot, (group.id, member.user_id, snapshot.week_start)
            )
            return __import__("json").loads(row.rows_json)[0]

    row = asyncio.run(scenario())
    assert row["online_url"] is None
    assert row["link_state"] == "ambiguous"
    assert len(row["link_candidates"]) == 2


def test_materialization_does_not_expose_private_course_candidate_inventory():
    from studgroup.ranepa_materialization import _shared_row

    original = {
        "day": "2026-11-02",
        "starts": "09:00:00",
        "ends": "10:20:00",
        "subject": "История России",
        "teacher": "Иванов",
        "room": "СДО",
        "online_url": "https://lms.ranepa.ru/mod/url/view.php?id=101",
        "link_state": "matched",
        "course_url": "https://lms.ranepa.ru/course/view.php?id=85813",
        "link_candidates": [
            {
                "url": "https://lms.ranepa.ru/mod/url/view.php?id=102",
                "label": "Петров постоянная",
                "course_url": "https://lms.ranepa.ru/course/view.php?id=85813",
            }
        ],
    }
    shared = _shared_row(
        original,
        scope="common",
        source_user_id="source",
        week_start=date(2026, 11, 2),
        source_snapshot_id="digest",
        source_checked_at="2026-11-19T00:00:00Z",
        materialized_at="2026-11-19T00:00:00Z",
    )
    assert "link_candidates" not in shared
    assert "link_candidates" in original
    assert shared["online_url"].endswith("id=101")
