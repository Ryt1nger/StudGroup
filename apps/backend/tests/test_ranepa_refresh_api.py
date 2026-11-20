"""P0-3: a manual refresh queues one consenting account without running a browser."""

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from studgroup.models import Group, Membership, RanepaCrawlJob, UserProfile

pytest_plugins = ["test_schedule_api"]
AUTH = {"Authorization": "Bearer valid"}


def test_refresh_never_launches_crawl_and_is_idempotent(client, monkeypatch):
    async def consent(*_args, **_kwargs):
        return True

    monkeypatch.setattr("studgroup.ranepa_personal_api.has_consent", consent)

    async def setup():
        async with AsyncSession(client.app.state.engine, expire_on_commit=False) as db:
            group = await db.scalar(select(Group).where(Group.telegram_chat_id == -1001))
            member = await db.scalar(select(Membership).where(Membership.group_id == group.id))
            group.academic_group_code = "GROUP-TEST"
            profile = await db.get(UserProfile, member.user_id)
            if profile is None:
                profile = UserProfile(user_id=member.user_id, updated_at=datetime.now(UTC))
                db.add(profile)
            profile.sdu_credential_id = uuid4()
            await db.commit()
            return group.id, member.user_id

    group_id, user_id = asyncio.run(setup())
    first = client.post("/v1/ranepa-schedule/refresh", headers=AUTH)
    assert first.status_code == 200
    assert first.json()["state"] == "queued"
    assert first.json()["queued"] is True
    second = client.post("/v1/ranepa-schedule/refresh", headers=AUTH)
    assert second.status_code == 200
    assert second.json()["state"] == "queued"

    async def jobs():
        async with AsyncSession(client.app.state.engine) as db:
            return await db.scalar(
                select(func.count())
                .select_from(RanepaCrawlJob)
                .where(
                    RanepaCrawlJob.group_id == group_id,
                    RanepaCrawlJob.user_id == user_id,
                )
            )

    assert asyncio.run(jobs()) == 1
    assert client.post("/v1/ranepa-schedule/refresh").status_code == 401
    current = client.get("/v1/ranepa-schedule?start=2026-11-16&end=2026-11-22", headers=AUTH)
    assert current.status_code == 200
    assert current.json()["schedule_complete"] is False
    assert current.json()["account_connected"] is True
    assert current.json()["refresh_state"] == "queued"


def test_refresh_requires_account_and_explicit_sdo_consent(client, monkeypatch):
    async def prepare():
        async with AsyncSession(client.app.state.engine) as db:
            group = await db.scalar(select(Group).where(Group.telegram_chat_id == -1001))
            group.academic_group_code = "GROUP-TEST"
            await db.commit()

    asyncio.run(prepare())
    response = client.post("/v1/ranepa-schedule/refresh", headers=AUTH)
    assert response.status_code == 409

    async def no_consent(*_args, **_kwargs):
        return False

    monkeypatch.setattr("studgroup.ranepa_personal_api.has_consent", no_consent)

    async def prepare_profile():
        async with AsyncSession(client.app.state.engine) as db:
            member = await db.scalar(select(Membership).where(Membership.status == "active"))
            profile = await db.get(UserProfile, member.user_id)
            if profile is None:
                profile = UserProfile(user_id=member.user_id, updated_at=datetime.now(UTC))
                db.add(profile)
            profile.sdu_credential_id = uuid4()
            await db.commit()

    asyncio.run(prepare_profile())
    assert client.post("/v1/ranepa-schedule/refresh", headers=AUTH).status_code == 403


def test_freshness_exposes_per_account_job_state_without_restarting_crawl(client, monkeypatch):
    async def consent(*_args, **_kwargs):
        return True

    monkeypatch.setattr("studgroup.ranepa_personal_api.has_consent", consent)

    async def prepare():
        async with AsyncSession(client.app.state.engine, expire_on_commit=False) as db:
            group = await db.scalar(select(Group).where(Group.telegram_chat_id == -1001))
            member = await db.scalar(select(Membership).where(Membership.group_id == group.id))
            group.academic_group_code = "GROUP-TEST"
            profile = await db.get(UserProfile, member.user_id)
            if profile is None:
                profile = UserProfile(user_id=member.user_id, updated_at=datetime.now(UTC))
                db.add(profile)
            profile.sdu_credential_id = uuid4()
            await db.commit()
            return group.id, member.user_id

    group_id, user_id = asyncio.run(prepare())
    path = "/v1/ranepa-schedule?start=2026-11-16&end=2026-11-22"
    assert client.get(path, headers=AUTH).json()["refresh_state"] == "idle"
    assert client.post("/v1/ranepa-schedule/refresh", headers=AUTH).json()["state"] == "queued"
    assert client.get(path, headers=AUTH).json()["refresh_state"] == "queued"

    async def set_state(state, failure=None):
        async with AsyncSession(client.app.state.engine, expire_on_commit=False) as db:
            job = await db.scalar(
                select(RanepaCrawlJob).where(
                    RanepaCrawlJob.group_id == group_id,
                    RanepaCrawlJob.user_id == user_id,
                )
            )
            job.state = state
            job.failure_code = failure
            await db.commit()

    asyncio.run(set_state("running"))
    assert client.get(path, headers=AUTH).json()["refresh_state"] == "running"
    asyncio.run(set_state("failed", "login_failed"))
    assert client.get(path, headers=AUTH).json()["refresh_state"] == "credentials_required"
