"""Worker dispatch boundary; legacy is default and new groups never reach legacy."""

from studgroup.processing import process_next


async def dispatch_next(engine, settings, provider=None, now=None):
    """Dispatch one durable source.

    The legacy claim itself excludes groups explicitly configured as ``new``. The new
    writer remains unavailable here until its PostgreSQL E2E gate is complete, so a
    mistakenly configured new group is safely left queued instead of being published by
    both engines.
    """
    return await process_next(engine, settings, provider=provider, now=now)
