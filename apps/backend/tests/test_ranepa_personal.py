"""Personal snapshot is immediately readable only by its owner and survives failures."""

import asyncio
from datetime import UTC, date, datetime, time
from uuid import uuid4

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
    Subgroup,
    SubjectSplit,
    User,
)
from studgroup.ranepa_personal import (
    personal_with_group_fallback,
    remember_personal,
    schedule_projection,
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
    assert projected["lessons"][0]["starts_at"] == "2026-11-02T09:00:00+03:00"
    client.app.state.settings.ranepa_schedule_view_enabled = True
    live = client.get("/v1/schedule?start=2026-11-02&end=2026-11-08", headers=AUTH)
    assert live.status_code == 200
    assert [row["subject"] for row in live.json()["lessons"]] == ["Новая история"]
    missing = client.get("/v1/schedule?start=2026-11-16&end=2026-11-22", headers=AUTH)
    assert missing.status_code == 200
    assert missing.json()["week_state"] == "needs_clarification"
    assert missing.json()["lessons"] == []
    lesson_id = live.json()["lessons"][0]["id"]
    detail = client.get(f"/v1/lessons/{lesson_id}/subject", headers=AUTH)
    assert detail.status_code == 200
    assert detail.json()["lesson"]["subject"] == "Новая история"
    today = client.get("/v1/today", headers=AUTH)
    assert today.status_code == 200
    assert today.json()["next_lesson"] is None
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
