"""Opt-in pilot processor inside the API; SQL leases survive process shutdown."""

import asyncio
import logging

from studgroup.processing import process_next


async def processing_loop(engine, settings, pause=5):
    from studgroup.worker import flush_incidents

    while True:
        try:
            await process_next(engine, settings)
            await flush_incidents(engine, settings)
        except Exception as error:  # noqa: BLE001 -- keep the SQL-backed poller alive; never catch cancellation
            # Source data and reservations are in SQL. Never log exception text:
            # database/HTTP exceptions can contain credentials or private content.
            logging.getLogger(__name__).error("pipeline_iteration_failed: %s", type(error).__name__)
        await asyncio.sleep(pause)
