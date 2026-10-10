import asyncio
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from test_processing import source

from studgroup.main import Settings
from studgroup.models import AIControl, RawMessage
from studgroup.processing_diagnostics import snapshot

pytest_plugins = ["test_schedule_api"]


def test_snapshot_reads_counters_without_claiming_work_or_reserving_budget(client):
    source(client, mid=31)
    source(client, mid=32)

    async def check():
        async with AsyncSession(client.app.state.engine) as db:
            imported = await db.scalar(
                select(RawMessage).where(RawMessage.telegram_message_id == 32)
            )
            imported.imported = True
            await db.commit()
            result = await snapshot(db, Settings(_env_file=None), datetime.now(UTC))
            group = result["groups"][0]
            assert group["raw"] == 2 and group["imported"] == 1 and group["live"] == 1
            assert group["bootstrap_scope"] == 2
            assert group["bot_added_at"] is not None
            assert group["pending_live"] == 1
            assert await db.get(AIControl, 1) is None
            assert "text" not in result

    asyncio.run(check())
