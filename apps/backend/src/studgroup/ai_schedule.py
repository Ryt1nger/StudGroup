"""Cheap database activity signals; fixed Moscow hourly batches, no provider timers."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.dialects.postgresql import insert

from studgroup.models import GroupAIActivity


def window(now, timezone):
    local = now.astimezone(ZoneInfo(timezone))
    if local.hour < 7:
        return None
    slot = local.replace(minute=0, second=0, microsecond=0)
    cutoff = local.replace(hour=23, minute=0, second=0, microsecond=0)
    extended = local.hour == 23
    if extended:
        cutoff += timedelta(hours=1)
    return slot.astimezone(UTC), cutoff.astimezone(UTC), extended


async def signal(db, group_id, now=None):
    now = (now or datetime.now(UTC)).astimezone(UTC)
    await db.execute(
        insert(GroupAIActivity)
        .values(group_id=group_id, last_signal_at=now, signal_count=1)
        .on_conflict_do_update(
            index_elements=[GroupAIActivity.group_id],
            set_={"last_signal_at": now, "signal_count": GroupAIActivity.signal_count + 1},
        )
    )
