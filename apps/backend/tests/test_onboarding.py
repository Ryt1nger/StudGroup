"""Mini App onboarding state survives session closure and cannot bypass required steps."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from studgroup.models import (
    Membership,
    OnboardingProgress,
    Subgroup,
    SubjectSplit,
    WebSession,
)
from studgroup.security import token_hash

pytest_plugins = ["test_schedule_api"]
AUTH = {"Authorization": "Bearer valid"}


def move(client, step, expected=200):
    answer = client.put("/v1/onboarding/progress", json={"step": step}, headers=AUTH)
    assert answer.status_code == expected, answer.text
    return answer


def test_student_registration_survives_new_web_session_and_is_required_once(client):
    assert client.get("/v1/onboarding").status_code == 401
    initial = client.get("/v1/onboarding", headers=AUTH)
    assert initial.status_code == 200
    assert initial.json()["role"] == "student"
    assert initial.json()["step"] == "welcome"
    assert not initial.json()["completed"]

    move(client, "sdo", expected=422)  # cannot skip the earlier onboarding steps
    move(client, "legal")
    move(client, "personal")

    async def second_session():
        async with AsyncSession(client.app.state.engine) as db:
            first = await db.scalar(
                select(WebSession).where(WebSession.token_hash == token_hash("valid"))
            )
            db.add(
                WebSession(
                    token_hash=token_hash("reopened"),
                    user_id=first.user_id,
                    membership_id=first.membership_id,
                    expires_at=datetime.now(UTC) + timedelta(hours=1),
                )
            )
            await db.commit()

    asyncio.run(second_session())
    reopened = client.get("/v1/onboarding", headers={"Authorization": "Bearer reopened"})
    assert reopened.status_code == 200
    assert reopened.json()["step"] == "personal"

    move(client, "subgroups")
    move(client, "sdo")
    move(client, "finish")
    assert client.post("/v1/onboarding/complete", headers=AUTH).status_code == 422
    saved = client.patch("/v1/profile", json={"first_name": "Анна"}, headers=AUTH)
    assert saved.status_code == 200
    reopened_profile = client.get("/v1/profile", headers={"Authorization": "Bearer reopened"})
    assert reopened_profile.status_code == 200
    assert reopened_profile.json()["first_name"] == "Анна"

    completed = client.post("/v1/onboarding/complete", headers=AUTH)
    assert completed.status_code == 200 and completed.json()["completed"]
    assert client.get("/v1/onboarding", headers=AUTH).json()["step"] == "complete"
    assert client.put("/v1/onboarding/progress", headers=AUTH, json={"step": "welcome"}).json()[
        "completed"
    ]

    async def evidence():
        async with AsyncSession(client.app.state.engine) as db:
            return await db.scalar(select(OnboardingProgress))

    persisted = asyncio.run(evidence())
    assert persisted.step == "complete" and persisted.completed_at is not None


def test_required_subgroup_must_be_selected_before_completion(client):
    async def setup_split():
        async with AsyncSession(client.app.state.engine) as db:
            member = await db.scalar(select(Membership))
            split = SubjectSplit(
                id=uuid.uuid4(),
                group_id=member.group_id,
                subject_id=uuid.uuid4(),
                subject_name="Английский язык",
                created_at=datetime.now(UTC),
            )
            db.add(split)
            await db.flush()
            subgroup = Subgroup(
                id=uuid.uuid4(),
                split_id=split.id,
                name="Подгруппа 1",
                position=1,
                signals="[]",
            )
            db.add(subgroup)
            split_id, subgroup_id = str(split.id), str(subgroup.id)
            await db.commit()
            return split_id, subgroup_id

    split_id, subgroup_id = asyncio.run(setup_split())
    assert client.patch("/v1/profile", json={"first_name": "Анна"}, headers=AUTH).status_code == 200
    for step in ("legal", "personal", "subgroups", "sdo", "finish"):
        move(client, step)
    denied = client.post("/v1/onboarding/complete", headers=AUTH)
    assert denied.status_code == 422
    assert denied.json()["error"]["code"] == "subgroups_required"
    selected = client.put(
        "/v1/subgroups/" + split_id,
        json={"subgroup_id": subgroup_id},
        headers=AUTH,
    )
    assert selected.status_code == 200
    assert client.post("/v1/onboarding/complete", headers=AUTH).status_code == 200


def test_headman_has_single_profile_step_not_student_wizard(client):
    async def make_headman():
        async with AsyncSession(client.app.state.engine) as db:
            member = await db.scalar(select(Membership))
            member.role = "headman"
            await db.commit()

    asyncio.run(make_headman())
    status = client.get("/v1/onboarding", headers=AUTH).json()
    assert status["role"] == "headman" and status["step"] == "personal"
    move(client, "welcome", expected=422)
    assert (
        client.patch("/v1/profile", json={"first_name": "Андрей"}, headers=AUTH).status_code == 200
    )
    move(client, "finish")
    assert client.post("/v1/onboarding/complete", headers=AUTH).status_code == 200
    assert client.get("/v1/onboarding", headers=AUTH).json()["completed"]


def test_students_must_have_active_membership_for_onboarding(client):
    assert (
        client.get("/v1/onboarding", headers={"Authorization": "Bearer expired"}).status_code == 401
    )
    assert client.put("/v1/onboarding/progress", json={"step": "legal"}).status_code == 401


def test_new_eight_screen_flow_persists_and_does_not_reopen_after_completion(client):
    """The new screen order is server-persisted; completion is one-time per membership."""
    steps = ("personal", "contacts", "subgroups", "sdo", "legal", "appearance", "finish")
    for step in steps:
        saved = move(client, step)
        assert saved.json()["step"] == step
        assert client.get("/v1/onboarding", headers=AUTH).json()["step"] == step
    assert client.patch("/v1/profile", json={"first_name": "Анна"}, headers=AUTH).status_code == 200
    assert client.post("/v1/onboarding/complete", headers=AUTH).json()["completed"]
    assert client.get("/v1/onboarding", headers=AUTH).json()["step"] == "complete"
    assert move(client, "welcome").json()["completed"] is True


def test_new_staff_wizard_accepts_contacts_and_appearance_without_subgroup_selection(client):
    async def make_headman():
        async with AsyncSession(client.app.state.engine) as db:
            member = await db.scalar(select(Membership))
            member.role = "headman"
            await db.commit()
    asyncio.run(make_headman())
    assert client.get("/v1/onboarding", headers=AUTH).json()["step"] == "personal"
    for step in ("contacts", "subgroups", "sdo", "legal", "appearance", "finish"):
        move(client, step)
    assert client.patch("/v1/profile", json={"first_name": "Андрей"}, headers=AUTH).status_code == 200
    assert client.post("/v1/onboarding/complete", headers=AUTH).json()["completed"]
