"""Operator-only window backfill commands (dry-run unless ``--apply`` is given).

    python -m studgroup.analysis_backfill plan    --group-id UUID --since 2026-10-08
    python -m studgroup.analysis_backfill enqueue --group-id UUID --since 2026-10-08 --limit 50
    python -m studgroup.analysis_backfill requeue --group-id UUID --since 2026-10-08 --limit 50
    python -m studgroup.analysis_backfill review  --group-id UUID --since 2026-10-08
    python -m studgroup.analysis_backfill promote --group-id UUID --since 2026-10-08 --limit 10
    python -m studgroup.analysis_backfill reopen  --group-id UUID --since 2026-10-08 [--apply]

``reopen`` (group in mode ``new`` only) sets the stored live messages of the window back to
``pending`` so the package worker analyses them again; it never deletes or edits any card.

``review`` prints private chat text to the operator's terminal; never paste it into Git, logs
or tickets. None of these commands calls the AI provider; the shadow worker does, within the
group's daily shadow budget.
"""

import argparse
import asyncio
import json
import uuid
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from studgroup.analysis import backfill
from studgroup.main import Settings
from studgroup.models import Group


def _json(value):
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


async def run(args):
    settings = Settings()
    engine = create_async_engine(settings.database_url, pool_pre_ping=True)
    now = datetime.now(UTC)
    try:
        async with AsyncSession(engine) as db:
            group = await db.get(Group, args.group_id)
            if group is None:
                raise SystemExit("group not found")
            since = datetime.combine(
                date.fromisoformat(args.since), datetime.min.time(), ZoneInfo(group.timezone)
            )
            common = {"group_id": args.group_id, "since": since, "now": now}
            if args.command == "plan":
                result = await backfill.plan(db, **common)
            elif args.command == "enqueue":
                result = await backfill.enqueue_window(
                    db, **common, limit=args.limit, apply=args.apply
                )
            elif args.command == "requeue":
                result = await backfill.requeue_failed(
                    db, **common, limit=args.limit, apply=args.apply
                )
            elif args.command == "review":
                result = await backfill.review(db, **common, limit=args.limit)
            elif args.command == "reopen":
                result = await backfill.reopen_window(db, **common, apply=args.apply)
            else:
                result = await backfill.promote(
                    db,
                    **common,
                    limit=args.limit,
                    apply=args.apply,
                    include_expired=args.include_expired,
                    actor=args.actor,
                )
            if args.apply:
                await db.commit()
            else:
                await db.rollback()
        print(json.dumps(result, ensure_ascii=False, indent=2, default=_json))
    finally:
        await engine.dispose()


def parser():
    result = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    result.add_argument(
        "command", choices=["plan", "enqueue", "requeue", "review", "promote", "reopen"]
    )
    result.add_argument("--group-id", type=uuid.UUID, required=True)
    result.add_argument("--since", default="2026-10-08", help="YYYY-MM-DD in the group timezone")
    result.add_argument("--limit", type=int, default=20)
    result.add_argument("--apply", action="store_true", help="write changes (default: dry-run)")
    result.add_argument("--include-expired", action="store_true")
    result.add_argument("--actor", default="owner")
    return result


if __name__ == "__main__":
    asyncio.run(run(parser().parse_args()))
