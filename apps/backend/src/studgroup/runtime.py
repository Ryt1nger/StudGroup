"""Opt-in pilot processor inside the API; SQL leases survive process shutdown."""

import asyncio
import logging
from datetime import UTC, datetime
from time import monotonic

from sqlalchemy.ext.asyncio import AsyncSession

from studgroup.analysis.dispatch import dispatch_next
from studgroup.processing import incident, incident_once
from studgroup.processing_health import inspect


async def processing_loop(engine, settings, pause=None):
    pause = settings.ai_loop_pause_seconds if pause is None else pause
    from studgroup.worker import flush_incidents

    logger = logging.getLogger(__name__)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    if not logger.handlers:
        logger.addHandler(logging.StreamHandler())
    previous = None
    last_health_check = None
    while True:
        try:
            status = await dispatch_next(engine, settings)
            if status != previous:
                logger.info("AI_PROCESSING_STATUS %s", status)
                previous = status
            # Do not let a closed model/funding window silently strand group chat messages.
            # This read-only scan does not run AI and cannot spend any money.
            tick = monotonic()
            if last_health_check is None or tick - last_health_check >= 300:
                health = await inspect(engine, settings)
                last_health_check = tick
                if health.paused:
                    details = (
                        f"groups={health.paused_groups} pending={health.pending_messages} "
                        f"reasons={','.join(health.reasons)}"
                    )
                    logger.warning("AI_BACKLOG_PAUSED %s", details)
                    async with AsyncSession(engine) as db:
                        await incident_once(
                            db, "ai_processing_paused", datetime.now(UTC), detail=details
                        )
                        await db.commit()
                else:
                    async with AsyncSession(engine) as db:
                        await incident(db, "ai_processing_paused", datetime.now(UTC), recover=True)
                        await db.commit()
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
