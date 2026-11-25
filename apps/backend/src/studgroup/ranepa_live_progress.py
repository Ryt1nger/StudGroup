"""Owner-only, ephemeral crawl progress. SQL remains authoritative for outcomes."""

import asyncio
import json
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime

from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import select

from studgroup.models import Group, RanepaCrawlJob, User
from studgroup.ranepa_crawl_jobs import LEASE
from studgroup.ranepa_crawl_trace import STEPS

TTL = 15


def progress_key(job_id, attempt):
    return f"studgroup:sdo:progress:{job_id}:{attempt}"


def progress_payload(trace, stats, mode):
    step = trace.events[-1]["step"] if trace.events else "job_claimed"
    return {
        "step": step if step in STEPS else "job_claimed",
        "saved_weeks": min(6, max(0, stats.saved_weeks)),
        "mode": mode,
        "updated_at": datetime.now(UTC).isoformat(),
    }


@asynccontextmanager
async def live_progress(engine, settings, job_id, attempt, trace, stats, mode):
    # The test database and offline analysis do not need a Redis server.
    if engine.dialect.name != "postgresql":
        yield
        return
    client = Redis.from_url(settings.redis_url, socket_connect_timeout=0.5, socket_timeout=0.5)

    async def publish():
        while True:
            try:
                await client.set(
                    progress_key(job_id, attempt),
                    json.dumps(progress_payload(trace, stats, mode)),
                    ex=TTL,
                )
            except (RedisError, OSError, TimeoutError):
                pass  # An observability failure must never fail a crawl.
            await asyncio.sleep(2)

    task = asyncio.create_task(publish())
    try:
        yield
    finally:
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        with suppress(RedisError, OSError, TimeoutError):
            await client.aclose()


async def current_account(db, redis):
    row = (
        await db.execute(
            select(RanepaCrawlJob, User.display_name, Group.name)
            .join(User, User.id == RanepaCrawlJob.user_id)
            .join(Group, Group.id == RanepaCrawlJob.group_id)
            .where(
                RanepaCrawlJob.state == "running",
                RanepaCrawlJob.claimed_at >= datetime.now(UTC) - LEASE,
            )
            .order_by(RanepaCrawlJob.claimed_at.desc())
            .limit(1)
        )
    ).first()
    if row is None:
        return None
    job, student, group = row
    live = None
    if redis is not None:
        try:
            async with asyncio.timeout(1):
                raw = await redis.get(progress_key(job.id, job.attempts))
            if raw:
                candidate = json.loads(raw)
                if (
                    isinstance(candidate, dict)
                    and candidate.get("step") in STEPS
                    and type(candidate.get("saved_weeks")) is int
                    and 0 <= candidate["saved_weeks"] <= 6
                    and candidate.get("mode") in {"manual", "worker"}
                ):
                    live = candidate
        except (RedisError, OSError, TimeoutError, ValueError, TypeError):
            pass
    return {
        "student": student,
        "group": group,
        "attempt": job.attempts,
        "started_at": job.claimed_at,
        "live": live,
    }
