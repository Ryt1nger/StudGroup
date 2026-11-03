"""One-time approval is scoped to BI 1.2 and reuses the real paid-call ledger.

Provider responses are synthetic: no external HTTP or actual spend.
"""

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from test_staff_api import db_run

from studgroup.analysis.release_budget import provider_for
from studgroup.cabinet_vision_budget_bootstrap import APPROVAL_DIGEST, apply
from studgroup.models import AnalysisBudgetCall, Group, GroupAnalysisSettings

pytest_plugins = ["test_schedule_api"]


def qualify(client):
    async def change(db):
        group = await db.scalar(select(Group).where(Group.telegram_chat_id == -1001))
        group.name = "би 1.2"

    db_run(client, change)


def budget(client, *, receipt=APPROVAL_DIGEST, since=None, until=None, now=None):
    now = now or datetime.now(UTC)
    since = since or now - timedelta(minutes=5)
    until = until or now + timedelta(hours=24)

    async def commit(db):
        return await apply(db, approval=receipt, since=since, until=until, now=now)

    return db_run(client, commit)


def route(client):
    async def read(db):
        group = await db.scalar(select(Group).where(Group.telegram_chat_id == -1001))
        row = await db.get(GroupAnalysisSettings, group.id)
        return row

    return db_run(client, read)


def test_five_cent_manual_vision_approval_is_immutable_and_does_not_enable_group_ai(client):
    qualify(client)
    now = datetime.now(UTC)
    since, until = now - timedelta(minutes=5), now + timedelta(hours=24)
    assert budget(client, since=since, until=until, now=now)["max_usd"] == "0.05"
    entry = route(client)
    assert entry.release_budget_usd == Decimal("0.05")
    assert entry.shadow_daily_budget_usd == Decimal("0.05")
    assert entry.release_budget_receipt == APPROVAL_DIGEST
    assert entry.analysis_mode == "legacy"
    assert entry.shadow_sample_percent == 0

    # A restart must not reset or extend the $0.05 funding window.
    assert budget(client, since=since, until=until, now=now)["state"] == "configured"
    with pytest.raises(ValueError, match="release_budget_cannot_reset"):
        budget(client, since=since, until=until + timedelta(hours=1), now=now)
    assert route(client).release_budget_until == until


def test_operator_approval_rejects_other_groups_bad_receipts_and_expired_windows(client):
    now = datetime.now(UTC)
    with pytest.raises(ValueError, match="group_not_unique"):
        budget(client)
    qualify(client)
    with pytest.raises(ValueError, match="approval_mismatch"):
        budget(client, receipt="0" * 64)
    with pytest.raises(ValueError, match="window_closed"):
        budget(client, since=now - timedelta(days=4), until=now - timedelta(days=3), now=now)
    assert route(client) is None


class StubVision:
    def __init__(self):
        self.calls = 0

    async def complete_vision(self, **_kwargs):
        self.calls += 1
        return {
            "choices": [{"finish_reason": "stop", "message": {"content": "{}"}}],
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 200,
                "prompt_cache_hit_tokens": 0,
            },
        }


def test_legacy_mode_manual_photo_still_goes_through_shared_five_cent_ledger(client):
    import asyncio

    qualify(client)
    budget(client)

    async def check():
        async with AsyncSession(client.app.state.engine) as db:
            group_id = await db.scalar(select(Group.id).where(Group.telegram_chat_id == -1001))
        stub = StubVision()
        port = provider_for(
            client.app.state.engine,
            client.app.state.settings,
            group_id,
            stub,
            kind="cabinet_image",
            attempt_key="manual-test-two-passes",
        )
        for _ in range(2):
            response = await port.complete_vision(
                system_prompt="read screenshot",
                user_text="read lesson grid",
                image_data_url="data:image/png;base64,aGVsbG8=",
                max_output_tokens=6000,
            )
            assert response["usage"]["completion_tokens"] == 200
        assert stub.calls == 2
        async with AsyncSession(client.app.state.engine) as db:
            count = await db.scalar(
                select(func.count(AnalysisBudgetCall.id)).where(
                    AnalysisBudgetCall.group_id == group_id,
                    AnalysisBudgetCall.state == "settled",
                    AnalysisBudgetCall.kind == "cabinet_image",
                )
            )
            assert count == 2

    # db_run opens its own event loop; only use it outside the async step.
    asyncio.run(check())
