import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from studgroup.export_publication import fragment_sink
from studgroup.models import AICandidate, AIJob, Group, Homework, RawMessage

pytest_plugins = ["test_schedule_api"]


def test_export_fragment_commits_immediately_and_retry_is_idempotent(client):
    async def run():
        engine = client.app.state.engine
        now = datetime.now(UTC).replace(microsecond=0)
        async with AsyncSession(engine, expire_on_commit=False) as db:
            group = await db.scalar(select(Group).where(Group.telegram_chat_id == -1001))
            group.pilot_authorized = True
            gid = group.id
            raw = RawMessage(
                group_id=gid,
                telegram_message_id=987,
                text="ДЗ сегодня до 23:59 решить задачи",
                message_date=now,
                version_date=now,
                revision=1,
                imported=True,
                processing_state="needs_context",
                delete_at=now + timedelta(days=30),
            )
            db.add(raw)
            await db.commit()
        fragment = {
            "sources": [{"message_id": 987, "text": raw.text, "message_date": now.isoformat()}],
            "assignments": [
                {
                    "kind": "homework",
                    "subject": "Математика",
                    "title": "Задачи",
                    "description": "Решить задачи",
                    "deadline_at": None,
                    "deadline_date_only": False,
                    "urgency": "normal",
                    "confidence": 95,
                    "source_message_ids": [987],
                }
            ],
        }
        sink = fragment_sink(engine, gid)
        assert await sink("fragment-one", fragment) == {"published": 1}
        assert await sink("fragment-one", fragment) == {"already_committed": True}
        async with AsyncSession(engine) as db:
            assert (
                await db.scalar(
                    select(func.count())
                    .select_from(Homework)
                    .where(Homework.raw_message_id == raw.id)
                )
                == 1
            )
            candidate = await db.scalar(select(AICandidate).where(AICandidate.group_id == gid))
            assert candidate.state == "published"
        # A changed source refuses publication and rolls back the fragment ledger too.
        fragment["sources"][0]["text"] = "changed"
        with pytest.raises(ValueError, match="source_missing_stale"):
            await sink("fragment-stale", fragment)
        async with AsyncSession(engine) as db:
            assert (
                await db.scalar(
                    select(func.count()).select_from(AIJob).where(AIJob.group_id == gid)
                )
                == 1
            )

    asyncio.run(run())
