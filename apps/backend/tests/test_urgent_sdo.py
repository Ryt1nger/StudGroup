"""Urgent run requests are owner-only and deduplicate concurrent/repeated clicks."""

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import uuid4

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


def test_urgent_retry_round_waits_for_primary_then_preserves_backoff(client, monkeypatch):
    from studgroup.models import UrgentSDORun, UrgentSDOTarget, User

    settings = client.app.state.settings
    settings.bot_admin_user_id = 1
    settings.ranepa_access_approved = True
    engine = client.app.state.engine
    now = datetime.now(UTC)
    monday = now.date() - timedelta(days=now.weekday())
    candidates_list = []

    async def candidates(db, config):
        return candidates_list

    monkeypatch.setattr("studgroup.urgent_sdo.eligible_candidates", candidates)
    monkeypatch.setattr("studgroup.urgent_sdo.legal_ready", lambda _: True)

    async def scenario():
        async with AsyncSession(engine, expire_on_commit=False) as db:
            group = await db.scalar(select(Group))
            group.academic_group_code = "TEST-ROUND"
            first_member = await db.scalar(select(Membership))
            user = User(id=uuid4(), telegram_user_id=999811, display_name="Second")
            db.add(user)
            await db.flush()
            db.add(Membership(group_id=group.id, user_id=user.id, status="active", role="student"))
            candidates_list.extend(
                [
                    CrawlCandidate(group.id, first_member.user_id),
                    CrawlCandidate(group.id, user.id),
                ]
            )
            db.add(UrgentSDORun(state="requested", requested_at=now))
            await db.commit()
        await prepare_urgent(engine, settings, monday)
        async with AsyncSession(engine, expire_on_commit=False) as db:
            first = await claim_next(db, now=now)
            assert first is not None
            first_id = first.id
            assert await finish(
                db, first.id, first.group_id, first.user_id, failure_code="source_unavailable", now=now
            )
            await db.commit()
        await finalize_urgent(engine, settings)
        async with AsyncSession(engine, expire_on_commit=False) as db:
            assert (await db.get(RanepaCrawlJob, first_id)).state == "failed"
            second = await claim_next(db, now=now + timedelta(minutes=1))
            assert second is not None and second.id != first_id
            assert await finish(
                db, second.id, second.group_id, second.user_id, now=now + timedelta(minutes=1)
            )
            await db.commit()
        await finalize_urgent(engine, settings)
        async with AsyncSession(engine, expire_on_commit=False) as db:
            assert await claim_next(db, now=now + timedelta(minutes=2)) is None
            retry = await claim_next(db, now=now + timedelta(minutes=31))
            assert retry is not None and retry.id == first_id
            assert await finish(
                db,
                retry.id,
                retry.group_id,
                retry.user_id,
                failure_code="login_failed",
                now=now + timedelta(minutes=31),
            )
            await db.commit()
        await finalize_urgent(engine, settings)
        async with AsyncSession(engine) as db:
            run = await db.scalar(select(UrgentSDORun))
            assert run.state == "completed"
            targets = (await db.scalars(select(UrgentSDOTarget))).all()
            assert sorted(target.state for target in targets) == ["failed", "succeeded"]

    asyncio.run(scenario())
