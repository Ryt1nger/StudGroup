"""Worker dispatch boundary; legacy is default and new groups never reach legacy."""

from studgroup.analysis.package_worker import process_package_next
from studgroup.analysis.shadow_worker import (
    enqueue_next_shadow,
    process_shadow_next,
    purge_expired_shadow_runs,
)
from studgroup.processing import process_next


async def dispatch_next(engine, settings, provider=None, now=None):
    """Dispatch one durable source.

    The legacy claim itself excludes groups explicitly configured as ``new``; those are served
    by the package worker (``NEW_ENGINE_ENABLED``) and never by the legacy engine.

    The legacy engine is locked by default: it runs only when ``legacy_engine_enabled`` is
    explicitly true, so ``AI_ENABLED`` alone cannot make it publish cards.
    """
    await purge_expired_shadow_runs(engine, now=now)
    shadow_status = await process_shadow_next(engine, settings, provider=provider, now=now)
    if shadow_status not in {"idle", "disabled"}:
        return f"shadow_{shadow_status}"
    shadow_closed = shadow_status == "disabled"
    # The new (package) engine owns groups in mode ``new``; the legacy claim never sees them.
    package_status = await process_package_next(engine, settings, provider=provider, now=now)
    if package_status not in {"idle", "disabled"}:
        return f"package_{package_status}"
    if settings.legacy_engine_enabled:
        legacy_status = await process_next(engine, settings, provider=provider, now=now)
    else:
        legacy_status = "disabled"
    enqueued = (
        None
        if settings.ai_shadow_canary_only or shadow_closed
        else await enqueue_next_shadow(engine, now=now)
    )
    if legacy_status in {"idle", "disabled"}:
        if enqueued == "queued":
            return "shadow_queued"
        if shadow_closed and legacy_status == "disabled":
            return "shadow_disabled"
    return legacy_status
