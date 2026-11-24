"""Urgent run requests are owner-only and deduplicate concurrent/repeated clicks."""

import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from studgroup.models import Group, Membership, RanepaCrawlJob
from studgroup.ranepa_crawl_jobs import claim_next, finish
from studgroup.ranepa_crawl_plan import CrawlCandidate
from studgroup.urgent_sdo import finalize_urgent, prepare_urgent

pytest_plugins = ["test_schedule_api"]


def test_urgent_run_request(client):
    url = "/v1/admin/sdo-runs"
    headers = {"Authorization": "Bearer valid"}
    assert client.post(url).status_code == 401
    assert client.post(url, headers=headers).status_code == 403
    client.app.state.settings.bot_admin_user_id = 1
    first = client.post(url, headers=headers)
    assert first.status_code == 202
    assert first.json()["state"] == "requested"
    second = client.post(url, headers=headers)
    assert second.json()["id"] == first.json()["id"]
    assert client.get(url, headers=headers).json()["run"] == first.json()


def test_urgent_uses_existing_queue_without_bypassing_cooldown(client, monkeypatch):
    settings = client.app.state.settings
    settings.bot_admin_user_id = 1
    settings.ranepa_access_approved = True
    engine = client.app.state.engine
    now = datetime.now(UTC)
    monday = now.date() - timedelta(days=now.weekday())

    async def seed():
        async with AsyncSession(engine, expire_on_commit=False) as db:
            member = await db.scalar(select(Membership))
            group = await db.get(Group, member.group_id)
            group.academic_group_code = "TEST-GROUP"
            job = RanepaCrawlJob(
                user_id=member.user_id,
                group_id=member.group_id,
                week_start=monday,
                priority=0,
                state="succeeded",
                attempts=1,
                created_at=now - timedelta(days=1),
                finished_at=now,
                next_attempt_at=now + timedelta(hours=6),
            )
            db.add(job)
            await db.commit()
            return CrawlCandidate(member.group_id, member.user_id), job.id

    candidate, job_id = asyncio.run(seed())

    async def candidates(db, config):
        return [candidate]

    monkeypatch.setattr("studgroup.urgent_sdo.eligible_candidates", candidates)
    monkeypatch.setattr("studgroup.urgent_sdo.legal_ready", lambda _: True)
    headers = {"Authorization": "Bearer valid"}
    assert client.post("/v1/admin/sdo-runs", headers=headers).status_code == 202

    async def work():
        await prepare_urgent(engine, settings, now.date())
        async with AsyncSession(engine) as db:
            assert await claim_next(db, now=now) is None
            job = await claim_next(db, now=now + timedelta(hours=7))
            assert job.id == job_id
            assert await finish(db, job.id, job.group_id, job.user_id, now=now + timedelta(hours=7))
            await db.commit()
        await finalize_urgent(engine, settings)

    asyncio.run(work())
    run = client.get("/v1/admin/sdo-runs", headers=headers).json()["run"]
    assert run["state"] == "completed"
    assert run["found"] == run["queued"] == run["completed"] == run["succeeded"] == 1
    assert run["remaining"] == 0
