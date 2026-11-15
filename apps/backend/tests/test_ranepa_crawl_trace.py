"""Only safe aggregate events can enter the per-attempt CAPTCHA trace."""

import asyncio
import json
from datetime import UTC, date, datetime
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from studgroup.models import Base, Group, Membership, RanepaCrawlJob, RanepaCrawlObservation, User
from studgroup.ranepa_crawl_jobs import enqueue_week
from studgroup.ranepa_crawl_plan import CrawlCandidate
from studgroup.ranepa_crawl_trace import MAX_EVENTS, CrawlTrace


def test_trace_remembers_preceding_step_and_session_mode():
    current = [10.0]
    trace = CrawlTrace(clock=lambda: current[0])
    trace.record("job_claimed")
    trace.session_available = True
    current[0] += 2.0
    trace.record("month_requested")
    current[0] += 4.0
    trace.mark_captcha("month")
    trace.record("captcha_solver_started")
    trace.mark_captcha("month")
    obj = trace.observation(job_id=uuid4(), attempt=2, failure_code="captcha")
    assert obj.captcha_seen
    assert obj.captcha_location == "month"
    assert obj.preceding_step == "month_requested"
    assert obj.session_available
    assert obj.result_code == "captcha"
    assert json.loads(obj.events_json)[2] == {"step": "captcha_month", "elapsed_ms": 6000}
    assert all(set(item) == {"step", "elapsed_ms"} for item in json.loads(obj.events_json))


def test_trace_only_accepts_allowlisted_steps_and_retains_latest_history():
    trace = CrawlTrace(clock=lambda: 0)
    with pytest.raises(ValueError, match="unsafe_crawl_trace_step"):
        trace.record("https://example.edu/cookie=private")
    with pytest.raises(ValueError, match="unsafe_crawl_trace_captcha_location"):
        trace.mark_captcha("https://example.edu")
    for _ in range(MAX_EVENTS + 15):
        trace.record("week_requested")
    trace.record("sdo_exits_requested")
    trace.mark_captcha("sdo")
    assert len(trace.events) == MAX_EVENTS
    assert trace.preceding_step == "sdo_exits_requested"
    assert trace.events[-1]["step"] == "captcha_sdo"
    assert not any("cookie" in str(event) for event in trace.events)


def test_crawl_trace_is_persisted_per_job_attempt_without_credentials(tmp_path):
    async def scenario():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/trace.db")
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            async with AsyncSession(engine) as db:
                group = Group(
                    id=uuid4(), telegram_chat_id=-56781, name="Test",
                    academic_group_code="A-001", timezone="Europe/Moscow"
                )
                user = User(id=uuid4(), telegram_user_id=56781, display_name="Student")
                db.add_all([group, user])
                await db.flush()
                db.add(
                    Membership(
                        group_id=group.id, user_id=user.id, role="student", status="active"
                    )
                )
                week = date(2026, 11, 9)
                await enqueue_week(db, week, [CrawlCandidate(group.id, user.id)])
                job = await db.scalar(select(RanepaCrawlJob))
                trace = CrawlTrace()
                trace.record("month_requested")
                trace.mark_captcha("month")
                db.add(trace.observation(job_id=job.id, attempt=1, failure_code="captcha"))
                await db.commit()
            async with AsyncSession(engine) as db:
                saved = await db.scalar(select(RanepaCrawlObservation))
                assert saved.result_code == "captcha"
                assert saved.captcha_location == "month"
                assert saved.preceding_step == "month_requested"
                assert saved.attempt == 1
                assert "password" not in saved.events_json
                assert "https:" not in saved.events_json
        finally:
            await engine.dispose()

    asyncio.run(scenario())
