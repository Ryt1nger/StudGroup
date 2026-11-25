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
    replacement = client.post(url + "?replace=true", headers=headers)
    assert replacement.status_code == 202
    assert replacement.json()["id"] != first.json()["id"]
    assert replacement.json()["state"] == "requested"
    assert client.get(url, headers=headers).json()["run"] == replacement.json()

    async def previous():
        from studgroup.models import UrgentSDORun

        async with AsyncSession(client.app.state.engine) as db:
            rows = (await db.scalars(select(UrgentSDORun))).all()
            assert sorted(row.state for row in rows) == ["cancelled", "requested"]
            assert next(row for row in rows if row.state == "cancelled").finished_at is not None

    asyncio.run(previous())


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


def test_urgent_retry_round_waits_for_primary_then_runs_fast_retry(client, monkeypatch):
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
                db,
                first.id,
                first.group_id,
                first.user_id,
                failure_code="source_unavailable",
                now=now,
                fast_retry=True,
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
            retry = await claim_next(db, now=now + timedelta(minutes=2))
            assert retry is not None and retry.id == first_id
            assert await finish(
                db,
                retry.id,
                retry.group_id,
                retry.user_id,
                failure_code="source_unavailable",
                now=now + timedelta(minutes=2),
                fast_retry=True,
            )
            assert retry.next_attempt_at == now + timedelta(minutes=62)
            await db.commit()
        await finalize_urgent(engine, settings)
        async with AsyncSession(engine, expire_on_commit=False) as db:
            assert await claim_next(db, now=now + timedelta(minutes=3)) is None
            last = await claim_next(db, now=now + timedelta(minutes=62))
            assert last is not None and last.id == first_id
            assert await finish(
                db,
                last.id,
                last.group_id,
                last.user_id,
                failure_code="login_failed",
                now=now + timedelta(minutes=62),
            )
            await db.commit()
        await finalize_urgent(engine, settings)
        async with AsyncSession(engine) as db:
            run = await db.scalar(select(UrgentSDORun))
            assert run.state == "completed"
            targets = (await db.scalars(select(UrgentSDOTarget))).all()
            assert sorted(target.state for target in targets) == ["failed", "succeeded"]

    asyncio.run(scenario())


def test_legacy_login_recheck_requires_later_consent_and_no_observed_rejection(client):
    from studgroup.models import LegalConsentEvent, UserProfile
    from studgroup.ranepa_crawl_trace import CrawlTrace
    from studgroup.urgent_sdo import previous_login_block, release_legacy_login_blocks

    now = datetime.now(UTC)
    engine = client.app.state.engine

    async def scenario():
        async with AsyncSession(engine, expire_on_commit=False) as db:
            member = await db.scalar(select(Membership))
            job = RanepaCrawlJob(
                group_id=member.group_id,
                user_id=member.user_id,
                week_start=now.date() - timedelta(days=now.weekday()),
                priority=0,
                state="failed",
                attempts=1,
                created_at=now - timedelta(hours=2),
                finished_at=now - timedelta(hours=1),
                failure_code="login_failed",
                retry_reason="credentials_required",
            )
            db.add(job)
            profile = await db.get(UserProfile, member.user_id)
            if profile is None:
                profile = UserProfile(user_id=member.user_id, updated_at=now)
                db.add(profile)
            profile.sdu_credential_id = uuid4()
            profile.updated_at = now
            await db.flush()
            assert not await release_legacy_login_blocks(db, member.user_id)
            db.add(
                LegalConsentEvent(
                    user_id=member.user_id,
                    purpose="sdo",
                    action="grant",
                    document_version="test",
                    document_sha256="a" * 64,
                    source="miniapp",
                    created_at=now - timedelta(minutes=1),
                )
            )
            trace = CrawlTrace()
            trace.failure_detail = "login_rejected"
            observation = trace.observation(job_id=job.id, attempt=1, failure_code="login_failed")
            db.add(observation)
            await db.flush()
            assert not await release_legacy_login_blocks(db, member.user_id)
            assert await previous_login_block(db, member.user_id)
            await db.delete(observation)
            await db.flush()
            assert await release_legacy_login_blocks(db, member.user_id)
            assert not await previous_login_block(db, member.user_id)
            assert not await release_legacy_login_blocks(db, member.user_id)

    asyncio.run(scenario())


def test_autonomous_preparation_revalidates_legacy_login_block(client, monkeypatch):
    from studgroup.models import LegalConsentEvent, RanepaCrawlObservation, UserProfile
    from studgroup.ranepa_crawl_trace import CrawlTrace
    from studgroup.ranepa_worker import prepare_week

    settings = client.app.state.settings
    settings.ranepa_access_approved = True
    engine = client.app.state.engine
    now = datetime.now(UTC)
    monday = now.date() - timedelta(days=now.weekday())
    candidates = []

    async def eligible(db, config, **kwargs):
        return candidates

    monkeypatch.setattr("studgroup.ranepa_worker.eligible_candidates", eligible)
    monkeypatch.setattr("studgroup.ranepa_worker.legal_ready", lambda _: True)

    async def scenario():
        async with AsyncSession(engine, expire_on_commit=False) as db:
            member = await db.scalar(select(Membership))
            group = await db.get(Group, member.group_id)
            group.academic_group_code = "TEST-AUTONOMOUS"
            candidates.append(CrawlCandidate(group.id, member.user_id))
            job = RanepaCrawlJob(
                group_id=group.id,
                user_id=member.user_id,
                week_start=monday,
                priority=0,
                state="failed",
                attempts=1,
                created_at=now - timedelta(hours=2),
                finished_at=now - timedelta(hours=1),
                failure_code="login_failed",
                retry_reason="credentials_required",
            )
            db.add(job)
            profile = await db.get(UserProfile, member.user_id)
            if profile is None:
                profile = UserProfile(user_id=member.user_id, updated_at=now)
                db.add(profile)
            profile.sdu_credential_id = uuid4()
            profile.updated_at = now
            db.add(
                LegalConsentEvent(
                    user_id=member.user_id,
                    purpose="sdo",
                    action="grant",
                    document_version="test",
                    document_sha256="a" * 64,
                    source="miniapp",
                    created_at=now - timedelta(minutes=1),
                )
            )
            await db.flush()
            job_id = job.id
            trace = CrawlTrace()
            trace.failure_detail = "login_rejected"
            db.add(trace.observation(job_id=job_id, attempt=1, failure_code="login_failed"))
            await db.commit()

        await prepare_week(engine, monday, settings)
        async with AsyncSession(engine) as db:
            job = await db.get(RanepaCrawlJob, job_id)
            assert job.state == "failed"
            assert job.retry_reason == "credentials_required"
            observation = await db.get(RanepaCrawlObservation, (job_id, 1))
            await db.delete(observation)
            await db.commit()

        await prepare_week(engine, monday, settings)
        async with AsyncSession(engine) as db:
            job = await db.get(RanepaCrawlJob, job_id)
            assert job.state == "queued"
            assert job.failure_code is None
            assert job.attempts == 1

    asyncio.run(scenario())
