"""Cheap database activity signals; fixed Moscow hourly batches, no provider timers."""

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy.dialects.postgresql import insert

from studgroup.models import GroupAIActivity


def window(now, timezone, *, allow_overnight=False):
    local = now.astimezone(ZoneInfo(timezone))
    if local.hour < 7 and not allow_overnight:
        return None
    slot = local.replace(minute=0, second=0, microsecond=0)
    cutoff = local.replace(hour=23, minute=0, second=0, microsecond=0)
    extended = local.hour == 23
    if extended:
        cutoff += timedelta(hours=1)
    return slot.astimezone(UTC), cutoff.astimezone(UTC), extended


def staggered_offsets(group_ids):
    """Evenly distribute active groups through the hour in stable group-id order.

    A changed roster is rebalanced on the next tick. No random jitter, paid
    provider call, extra process or persistent per-group scheduler is needed.
    """
    ordered = sorted(set(group_ids))
    if not ordered:
        return {}
    return {group_id: (index * 60) // len(ordered) for index, group_id in enumerate(ordered)}


def staggered_slot(now, timezone, minute_offset, *, allow_overnight=False):
    """Return this hour's assigned group slot, or None until its minute arrives.

    The due slot stays eligible for a delayed worker, but the following hour
    cannot start before the group's assigned minute.
    """
    if not 0 <= minute_offset < 60:
        raise ValueError("minute_offset_must_be_between_0_and_59")
    local = now.astimezone(ZoneInfo(timezone))
    if local.hour < 7 and not allow_overnight:
        return None
    slot = local.replace(minute=minute_offset, second=0, microsecond=0)
    if local < slot:
        return None
    return slot.astimezone(UTC)


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
