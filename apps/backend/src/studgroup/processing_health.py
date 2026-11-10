"""Read-only detection of paused live-message backlogs in authorized groups.

No source text, group identifiers, secrets, or model calls leave this module.
"""

from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from studgroup.analysis.release_budget import production_enabled, recurring_policy_status, spent
from studgroup.models import Group, GroupAnalysisSettings, MessageAttachment, RawMessage


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
            production = production_enabled(settings, group.id)
            recurring = None if production else recurring_policy_status(settings, group.id, now)
            if not (settings.new_engine_enabled or settings.owner_funded_pilot):
                reason = "engine_disabled"
            elif (
                not production
                and settings.new_engine_enabled_until
                and utc(settings.new_engine_enabled_until) <= now
            ):
                reason = "engine_window_closed"
            elif route.paid_outcome_hold:
                reason = "paid_outcome_held"
            elif recurring is not None and recurring != "active":
                reason = recurring
            elif recurring == "active" and (
                await spent(
                    db,
                    group.id,
                    since=now.replace(day=1, hour=0, minute=0, second=0, microsecond=0),
                )
                >= Decimal(str(settings.ai_recurring_monthly_budget_usd))
            ):
                reason = "recurring_monthly_budget_exhausted"
            elif recurring == "active" and (
                await spent(
                    db, group.id, since=now.replace(hour=0, minute=0, second=0, microsecond=0)
                )
                >= Decimal(str(settings.ai_recurring_daily_budget_usd))
            ):
                reason = "recurring_daily_budget_exhausted"
            elif (
                not production
                and recurring is None
                and route.release_budget_until
                and utc(route.release_budget_until) <= now
            ):
                reason = "release_budget_window_closed"
            elif (
                not production
                and recurring is None
                and route.release_budget_since
                and utc(route.release_budget_since) > now
            ):
                reason = "release_budget_window_not_started"
            elif (
                not production
                and recurring is None
                and str(group.id) in settings.ai_release_budget_required_groups
                and route.release_budget_usd is None
            ):
                reason = "release_budget_not_configured"
            else:
                from studgroup.analysis.image_jobs import enabled

                protected = (
                    route.release_budget_usd is not None
                    or str(group.id) in settings.ai_release_budget_required_groups
                    or recurring == "active"
                    or production
                )
                if (
                    not settings.photo_reading_enabled
                    or not protected
                    or enabled(settings, group.id)
                ):
                    continue
                unread_photo_sources = select(MessageAttachment.raw_message_id).where(
                    MessageAttachment.group_id == group.id,
                    MessageAttachment.kind == "photo",
                    MessageAttachment.active.is_(True),
                    MessageAttachment.processing_state == "metadata_only",
                    MessageAttachment.delete_at > now,
                )
                count = int(
                    await db.scalar(
                        select(func.count(RawMessage.id)).where(
                            *eligible, RawMessage.id.in_(unread_photo_sources)
                        )
                    )
                    or 0
                )
                if count == 0:
                    continue
                reason = "photo_reader_unavailable"
            reasons[reason] += 1
            pending_messages += count
            paused_groups += 1
    return BacklogHealth(
        pending_messages=pending_messages,
        paused_groups=paused_groups,
        reasons=tuple(sorted(reasons)),
    )
