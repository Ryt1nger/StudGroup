"""Text and vision must not race past the same funded release allowance."""

import asyncio
import os
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool
from test_postgres_image_jobs import seed
from test_release_budget import body, payload

from studgroup.ai import ProviderFailure
from studgroup.analysis.release_budget import RequestAccounting, configure, spent
from studgroup.main import Settings
from studgroup.models import AnalysisBudgetCall, Group, GroupAnalysisSettings, RawMessage

pytestmark = pytest.mark.skipif(
    not os.environ.get("POSTGRES_TEST_URL"), reason="PostgreSQL not configured"
)


def test_text_and_vision_share_atomic_funding_that_survives_source_deletion():
    async def run():
        engine = create_async_engine(os.environ["POSTGRES_TEST_URL"], poolclass=NullPool)
        _, group_id, values = await seed(engine)
        now = datetime.now(UTC)
        settings = Settings(_env_file=None, ai_release_budget_required_groups=[str(group_id)])
        try:
            async with AsyncSession(engine) as db:
                (await db.get(Group, group_id)).pilot_authorized = True
                db.add(
                    GroupAnalysisSettings(
                        group_id=group_id,
                        analysis_mode="shadow",
                        shadow_daily_budget_usd=Decimal(".50"),
                        updated_at=now,
                    )
                )
                await db.flush()
                await configure(
                    db,
                    group_id,
                    amount=".003",
                    since=now,
                    until=now + timedelta(hours=48),
                    receipt="b" * 64,
                )
                await db.commit()
            invoked = []
            entered, release = asyncio.Event(), asyncio.Event()

            async def paid():
                invoked.append(True)
                entered.set()
                await release.wait()
                return body()

            def accounting(kind):
                return RequestAccounting(
                    engine,
                    settings,
                    group_id,
                    kind=kind,
                    attempt_key=kind,
                    mode="shadow",
                    clock=lambda: now,
                )

            first = asyncio.create_task(accounting("image").execute(payload(image=True), paid))
            await asyncio.wait_for(entered.wait(), timeout=5)
            try:
                with pytest.raises(ProviderFailure, match="release_budget_exhausted"):
                    await accounting("cplus").execute(payload(), paid)
            finally:
                release.set()
                await first
            async with AsyncSession(engine) as db:
                await db.delete(await db.get(RawMessage, values["raw_message_id"]))
                await db.commit()
                assert await spent(db, group_id) == Decimal(".0015")
                assert (
                    len(
                        (
                            await db.scalars(
                                select(AnalysisBudgetCall).where(
                                    AnalysisBudgetCall.group_id == group_id
                                )
                            )
                        ).all()
                    )
                    == 1
                )
            with pytest.raises(ProviderFailure, match="release_budget_exhausted"):
                await accounting("legacy").execute(payload(), paid)
            assert len(invoked) == 1
        finally:
            async with AsyncSession(engine) as db:
                await db.delete(await db.get(Group, group_id))
                await db.commit()
            await engine.dispose()

    asyncio.run(run())
