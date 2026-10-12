"""Explicit operator-only analysis mode transition command."""

import argparse
import asyncio
import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from studgroup.analysis.pilot import emergency_rollback, transition_mode
from studgroup.main import Settings


async def change_mode(args):
    settings = Settings()
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    try:
        async with AsyncSession(engine) as db:
            if args.rollback:
                route = await emergency_rollback(
                    db,
                    group_id=args.group_id,
                    actor=args.actor,
                    reason=args.reason,
                    now=datetime.now(UTC),
                )
            else:
                route = await transition_mode(
                    db,
                    group_id=args.group_id,
                    target_mode=args.mode,
                    actor=args.actor,
                    reason=args.reason,
                    now=datetime.now(UTC),
                    gate_approved=args.approve_g4,
                    shadow_daily_budget_usd=args.shadow_daily_budget_usd,
                    shadow_sample_percent=args.shadow_sample_percent,
                )
            await db.commit()
        print(f"analysis mode committed: group={args.group_id} mode={route.mode.value}")
    finally:
        await engine.dispose()


def parser():
    result = argparse.ArgumentParser()
    result.add_argument("--group-id", type=uuid.UUID, required=True)
    result.add_argument("--actor", required=True)
    result.add_argument("--reason", required=True)
    target = result.add_mutually_exclusive_group(required=True)
    target.add_argument("--mode", choices=["legacy", "shadow", "new"])
    target.add_argument("--rollback", action="store_true")
    result.add_argument("--approve-g4", action="store_true")
    result.add_argument("--shadow-daily-budget-usd", type=Decimal, default=Decimal(0))
    result.add_argument("--shadow-sample-percent", type=int, default=100)
    return result


if __name__ == "__main__":
    asyncio.run(change_mode(parser().parse_args()))
