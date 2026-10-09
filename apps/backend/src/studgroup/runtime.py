"""Opt-in pilot processor inside the API; SQL leases survive process shutdown."""

import asyncio
import logging
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from studgroup.processing import incident, process_next


async def processing_loop(engine, settings, pause=5):
    from studgroup.worker import flush_incidents

    logger = logging.getLogger(__name__)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if not logger.handlers:
        logger.addHandler(logging.StreamHandler())
    previous = None
    while True:
        try:
            status = await process_next(engine, settings)
            if status != previous:
                logger.info("AI_PROCESSING_STATUS %s", status)
                previous = status
            await flush_incidents(engine, settings)
        except Exception as error:  # noqa: BLE001 -- keep the SQL-backed poller alive; never catch cancellation
            # Source data and reservations are in SQL. Never log exception text:
            # database/HTTP exceptions can contain credentials or private content.
            logging.getLogger(__name__).error("pipeline_iteration_failed: %s", type(error).__name__)
            try:
                async with AsyncSession(engine) as db:
                    await incident(
                        db, "pipeline_error", datetime.now(UTC), detail=type(error).__name__
                    )
                    await db.commit()
                await flush_incidents(engine, settings)
            except Exception:  # noqa: BLE001 -- alert delivery cannot terminate recovery or leak data
                logger.error("owner_alert_pending_or_database_unavailable")
        await asyncio.sleep(pause)
