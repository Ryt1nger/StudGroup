"""Worker dispatch boundary; legacy is default and new groups never reach legacy."""

from studgroup.analysis.shadow_worker import (
    enqueue_next_shadow,
    process_shadow_next,
    purge_expired_shadow_runs,
)
from studgroup.processing import process_next


async def dispatch_next(engine, settings, provider=None, now=None):
    """Dispatch one durable source.

    The legacy claim itself excludes groups explicitly configured as ``new``. The new
    writer remains unavailable here until its PostgreSQL E2E gate is complete, so a
    mistakenly configured new group is safely left queued instead of being published by
    both engines.
    """
    await purge_expired_shadow_runs(engine, now=now)
    shadow_status = await process_shadow_next(engine, settings, provider=provider, now=now)
    if shadow_status != "idle":
        return f"shadow_{shadow_status}"
    legacy_status = await process_next(engine, settings, provider=provider, now=now)
    enqueued = (
        None if settings.ai_shadow_canary_only else await enqueue_next_shadow(engine, now=now)
    )
    if legacy_status in {"idle", "disabled"} and enqueued == "queued":
        return "shadow_queued"
    return legacy_status
