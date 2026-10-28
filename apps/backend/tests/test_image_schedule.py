import asyncio
import json

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from test_image_jobs import cfg, download_ok, reading, stored, tick
from test_package_worker import make_new
from test_photo_reading import VisionProvider, photo_message
from test_staff_api import AUTH, make

from studgroup.models import ImageScheduleSuggestion, ScheduleChange, SchedulePattern

pytest_plugins = ["test_schedule_api"]


def prepare(client, monkeypatch, **row_patch):
    download_ok(monkeypatch)
    photo_message(client)
    make_new(client)
    row = {
        "weekday": 2,
        "starts": "13:00",
        "ends": "14:30",
        "subject": "Алгебра",
        "room": "201",
        "teacher": "Иванов",
        "week": "all",
        "valid_from": "2026-10-28",
        "valid_until": "2026-12-31",
    } | row_patch
    payload = json.loads(reading()) | {"semantic_types": ["schedule"], "schedule": [row]}
    config = cfg(client)
    provider = VisionProvider(json.dumps(payload))
    assert tick(client, provider, config) == "ready"
    assert tick(client, provider, config) == "ready"
    suggestion = stored(client, ImageScheduleSuggestion)[0]
    client.app.state.settings.image_schedule_review_enabled = True
    client.app.state.settings.image_analysis_groups = config.image_analysis_groups
    return suggestion


def test_schedule_is_only_a_suggestion_until_authorized_confirmation(client, monkeypatch):
    suggestion = prepare(client, monkeypatch)
    assert len(stored(client, ScheduleChange)) == 0
    path = f"/v1/staff/image-findings/schedule/{suggestion.id}/confirm"
    body = {"expected_revision": 1, "expected_schedule_version": 0}
    assert client.post(path, json=body, headers=AUTH).status_code == 403
    make(client, "headman")
    result = client.post(path, json=body, headers=AUTH)
    assert result.status_code == 200, result.text
    assert result.json()["schedule_version"] == 1
    assert len(stored(client, ScheduleChange)) == 1
    assert client.post(path, json=body, headers=AUTH).status_code == 200
    assert len(stored(client, ScheduleChange)) == 1

    async def patterns():
        async with AsyncSession(client.app.state.engine) as db:
            return (
                await db.scalars(
                    select(SchedulePattern).where(SchedulePattern.subject == "Алгебра")
                )
            ).all()

    assert len(asyncio.run(patterns())) == 1


def test_missing_effective_date_and_unknown_subgroup_cannot_change_schedule(client, monkeypatch):
    suggestion = prepare(client, monkeypatch, valid_from=None, valid_until=None)
    make(client, "headman")
    response = client.post(
        f"/v1/staff/image-findings/schedule/{suggestion.id}/confirm",
        json={"expected_revision": 1, "expected_schedule_version": 0},
        headers=AUTH,
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "effective_dates_required"
    assert stored(client, ScheduleChange) == []


def test_schedule_review_is_disabled_by_default(client):
    make(client, "headman")
    assert client.get("/v1/staff/image-findings/schedule", headers=AUTH).status_code == 404


def test_explicit_single_lesson_mapping_uses_existing_audited_change(client, monkeypatch):
    suggestion = prepare(
        client,
        monkeypatch,
        weekday=0,
        subject="Математика",
        starts="11:00",
        ends="12:30",
        valid_from="2026-10-26",
    )
    make(client, "headman")
    data = client.get("/v1/staff/schedule?start=2026-11-02&end=2026-11-02", headers=AUTH).json()
    lesson = data["lesson_edits"][0]
    response = client.post(
        f"/v1/staff/image-findings/schedule/{suggestion.id}/confirm",
        headers=AUTH,
        json={
            "expected_revision": 1,
            "expected_schedule_version": 0,
            "targets": [
                {
                    "row_index": 0,
                    "lesson_id": lesson["lesson_id"],
                    "pattern_revision": lesson["pattern_revision"],
                    "override_revision": lesson["override_revision"],
                    "scope": "once",
                }
            ],
        },
    )
    assert response.status_code == 200, response.text
    assert stored(client, ScheduleChange)[0].operation == "lesson_once"
    assert stored(client, ScheduleChange)[0].input_method == "image_confirm"


def test_subgroup_schedule_requires_explicit_mapping(client, monkeypatch):
    suggestion = prepare(client, monkeypatch, subgroup="Первая")
    make(client, "headman")
    response = client.post(
        f"/v1/staff/image-findings/schedule/{suggestion.id}/confirm",
        headers=AUTH,
        json={"expected_revision": 1, "expected_schedule_version": 0},
    )
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "subgroup_mapping_required"
    assert stored(client, ScheduleChange) == []


def test_editor_can_correct_unknown_dates_subject_teacher_and_room(client, monkeypatch):
    suggestion = prepare(client, monkeypatch, valid_from=None, valid_until=None)
    original = suggestion.rows_json
    make(client, "headman")
    corrected = {
        "weekday": 2,
        "starts": "15:00",
        "ends": "16:30",
        "subject": "Информатика",
        "room": "303",
        "teacher": "Петров",
        "valid_from": "2026-10-28",
        "valid_until": "2026-12-31",
    }
    result = client.post(
        f"/v1/staff/image-findings/schedule/{suggestion.id}/confirm",
        headers=AUTH,
        json={
            "expected_revision": 1,
            "expected_schedule_version": 0,
            "corrected_rows": [corrected],
        },
    )
    assert result.status_code == 200, result.text
    current = stored(client, ImageScheduleSuggestion)[0]
    assert current.rows_json == original
    assert json.loads(current.confirmation_json)["rows"][0]["subject"] == "Информатика"

    async def lesson():
        async with AsyncSession(client.app.state.engine) as db:
            row = await db.scalar(
                select(SchedulePattern).where(SchedulePattern.subject == "Информатика")
            )
            return row.teacher, row.location, row.starts.hour

    assert asyncio.run(lesson()) == ("Петров", "303", 15)


def test_editor_subgroup_mapping_is_group_and_subject_scoped(client, monkeypatch):
    from datetime import UTC, datetime

    from studgroup.models import Group, Subgroup, SubjectSplit
    from studgroup.subject_identity import subject_uuid

    async def setup():
        async with AsyncSession(client.app.state.engine) as db:
            group = await db.scalar(select(Group).where(Group.telegram_chat_id == -1001))
            other = await db.scalar(select(Group).where(Group.telegram_chat_id == -1002))
            ids = []
            for owner, subject in ((group, "Алгебра"), (group, "Математика"), (other, "Алгебра")):
                split = SubjectSplit(
                    group_id=owner.id,
                    subject_id=subject_uuid(owner.id, subject),
                    subject_name=subject,
                    created_at=datetime.now(UTC),
                )
                db.add(split)
                await db.flush()
                subgroup = Subgroup(split_id=split.id, position=1, name="Первая", signals="[]")
                db.add(subgroup)
                await db.flush()
                ids.append((str(subgroup.id), str(split.subject_id)))
            await db.commit()
            return ids

    pairs = asyncio.run(setup())
    suggestion = prepare(client, monkeypatch, subgroup="Первая")
    make(client, "headman")
    subgroup_id, subject_id = pairs[0]
    row = json.loads(suggestion.rows_json)[0] | {
        "subgroup_id": subgroup_id,
        "subject_id": subject_id,
    }
    path = f"/v1/staff/image-findings/schedule/{suggestion.id}/confirm"
    for invalid in (
        row | {"subgroup_id": pairs[1][0]},
        row | {"subgroup_id": pairs[2][0]},
        row | {"subject_id": pairs[2][1]},
    ):
        response = client.post(
            path,
            headers=AUTH,
            json={
                "expected_revision": 1,
                "expected_schedule_version": 0,
                "corrected_rows": [invalid],
            },
        )
        assert response.status_code == 409, response.text
        assert stored(client, ScheduleChange) == []
    response = client.post(
        path,
        headers=AUTH,
        json={"expected_revision": 1, "expected_schedule_version": 0, "corrected_rows": [row]},
    )
    assert response.status_code == 200, response.text
    data = client.get("/v1/schedule?start=2026-10-28&end=2026-10-28", headers=AUTH).json()
    assert (
        next(item for item in data["lessons"] if item["subject"] == "Алгебра")["subgroup_id"]
        == subgroup_id
    )


def test_editor_replacement_clears_previous_subject_teacher_and_keeps_audit(client, monkeypatch):
    async def teacher():
        async with AsyncSession(client.app.state.engine) as db:
            pattern = await db.scalar(
                select(SchedulePattern).where(SchedulePattern.subject == "Математика")
            )
            pattern.teacher = "Иванов"
            await db.commit()

    asyncio.run(teacher())
    suggestion = prepare(client, monkeypatch)
    make(client, "headman")
    lesson = client.get("/v1/staff/schedule?start=2026-11-02&end=2026-11-02", headers=AUTH).json()[
        "lesson_edits"
    ][0]
    corrected = {
        "weekday": 0,
        "starts": "11:00",
        "ends": "12:30",
        "subject": "Информатика",
        "teacher": None,
        "room": "303",
        "valid_from": "2026-11-02",
        "valid_until": "2026-11-02",
    }
    response = client.post(
        f"/v1/staff/image-findings/schedule/{suggestion.id}/confirm",
        headers=AUTH,
        json={
            "expected_revision": 1,
            "expected_schedule_version": 0,
            "corrected_rows": [corrected],
            "targets": [
                {
                    "row_index": 0,
                    "lesson_id": lesson["lesson_id"],
                    "pattern_revision": lesson["pattern_revision"],
                    "override_revision": lesson["override_revision"],
                    "scope": "once",
                }
            ],
        },
    )
    assert response.status_code == 200, response.text
    result = client.get("/v1/schedule?start=2026-11-02&end=2026-11-02", headers=AUTH)
    assert result.status_code == 200, result.text
    changed = next(row for row in result.json()["lessons"] if row["subject"] == "Информатика")
    assert changed["teacher"] is None
    assert changed["location"] == "303"
    assert stored(client, ScheduleChange)[0].input_method == "image_confirm"

    async def undo():
        from studgroup.headman_domain import undo as undo_change
        from studgroup.models import Group, HeadmanAudit, Membership, User

        async with AsyncSession(client.app.state.engine) as db:
            group = await db.scalar(select(Group).where(Group.telegram_chat_id == -1001))
            member = await db.scalar(select(Membership).where(Membership.group_id == group.id))
            user = await db.get(User, member.user_id)
            audit = await db.scalar(select(HeadmanAudit).where(HeadmanAudit.group_id == group.id))
            await undo_change(db, group, user, member, audit.id)
            await db.commit()

    asyncio.run(undo())
    restored = client.get("/v1/schedule?start=2026-11-02&end=2026-11-02", headers=AUTH).json()[
        "lessons"
    ]
    assert all(row["subject"] != "Информатика" for row in restored)
    assert next(row for row in restored if row["subject"] == "Математика")["teacher"] == "Иванов"


def test_editor_recurring_replacement_updates_subject_period_and_teacher(client, monkeypatch):
    from datetime import date

    suggestion = prepare(client, monkeypatch)
    make(client, "headman")
    lesson = client.get("/v1/staff/schedule?start=2026-11-02&end=2026-11-02", headers=AUTH).json()[
        "lesson_edits"
    ][0]
    corrected = {
        "weekday": 2,
        "starts": "15:00",
        "ends": "16:30",
        "subject": "Информатика",
        "teacher": "Петров",
        "room": "303",
        "valid_from": "2026-11-04",
        "valid_until": "2026-12-16",
    }
    response = client.post(
        f"/v1/staff/image-findings/schedule/{suggestion.id}/confirm",
        headers=AUTH,
        json={
            "expected_revision": 1,
            "expected_schedule_version": 0,
            "corrected_rows": [corrected],
            "targets": [
                {
                    "row_index": 0,
                    "lesson_id": lesson["lesson_id"],
                    "pattern_revision": lesson["pattern_revision"],
                    "override_revision": lesson["override_revision"],
                    "scope": "recurring",
                }
            ],
        },
    )
    assert response.status_code == 200, response.text

    async def find():
        async with AsyncSession(client.app.state.engine) as db:
            return await db.scalar(
                select(SchedulePattern).where(SchedulePattern.subject == "Информатика")
            )

    pattern = asyncio.run(find())
    assert (pattern.valid_from, pattern.valid_until) == (date(2026, 11, 4), date(2026, 12, 16))
    assert pattern.teacher == "Петров"
    assert pattern.weekday == 2
    assert stored(client, ScheduleChange)[0].operation == "lesson_recurring"


def test_editor_confirmation_does_not_restart_vision(client, monkeypatch):
    from studgroup.models import ImageAnalysisCall

    suggestion = prepare(client, monkeypatch)
    make(client, "headman")
    before = len(stored(client, ImageAnalysisCall))
    result = client.post(
        f"/v1/staff/image-findings/schedule/{suggestion.id}/confirm",
        json={"expected_revision": 1, "expected_schedule_version": 0},
        headers=AUTH,
    )
    assert result.status_code == 200, result.text
    provider = VisionProvider(reading())
    assert tick(client, provider, cfg(client)) == "idle"
    assert provider.vision == []
    assert len(stored(client, ImageAnalysisCall)) == before
