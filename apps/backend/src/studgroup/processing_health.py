"""Read-only detection of paused live-message backlogs in authorized groups.

No source text, group identifiers, secrets, or model calls leave this module.
"""

from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from studgroup.models import Group, GroupAnalysisSettings, RawMessage


def utc(value):
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


@dataclass(frozen=True)
class BacklogHealth:
    pending_messages: int
    paused_groups: int
    reasons: tuple[str, ...]

    @property
    def paused(self):
        return self.paused_groups > 0


async def inspect(engine, settings, now=None):
    """Inspect every authorized new-engine group without changing its durable queue."""
    now = utc(now or datetime.now(UTC))
    reasons = Counter()
    pending_messages = 0
    paused_groups = 0
    async with AsyncSession(engine) as db:
        groups = (
            await db.execute(
                select(Group, GroupAnalysisSettings)
                .join(GroupAnalysisSettings, GroupAnalysisSettings.group_id == Group.id)
                .where(
                    Group.status == "active",
                    Group.pilot_authorized.is_(True),
                    GroupAnalysisSettings.analysis_mode == "new",
                )
            )
        ).all()
        for group, route in groups:
            eligible = [
                RawMessage.group_id == group.id,
                RawMessage.processing_state == "pending",
                RawMessage.imported.is_(False),
                RawMessage.delete_at > now,
            ]
            if group.bot_added_at is not None:
                eligible.append(RawMessage.message_date >= group.bot_added_at)
            count = int(await db.scalar(select(func.count(RawMessage.id)).where(*eligible)) or 0)
            if count == 0:
                continue
            if not settings.new_engine_enabled:
                reason = "engine_disabled"
            elif (
                settings.new_engine_enabled_until and utc(settings.new_engine_enabled_until) <= now
            ):
                reason = "engine_window_closed"
            elif route.paid_outcome_hold:
                reason = "paid_outcome_held"
            elif route.release_budget_until and utc(route.release_budget_until) <= now:
                reason = "release_budget_window_closed"
            elif route.release_budget_since and utc(route.release_budget_since) > now:
                reason = "release_budget_window_not_started"
            elif (
                str(group.id) in settings.ai_release_budget_required_groups
                and route.release_budget_usd is None
            ):
                reason = "release_budget_not_configured"
            else:
                continue
            reasons[reason] += 1
            pending_messages += count
            paused_groups += 1
    return BacklogHealth(
        pending_messages=pending_messages,
        paused_groups=paused_groups,
        reasons=tuple(sorted(reasons)),
    )
