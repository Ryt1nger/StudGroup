"""Single-writer, group-scoped routing with legacy as the fail-safe default."""

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import func, select

from studgroup.analysis.contracts import ANALYSIS_ENGINE_VERSION
from studgroup.models import Group, GroupAnalysisSettings, ShadowEvaluationRun


class AnalysisMode(StrEnum):
    LEGACY = "legacy"
    SHADOW = "shadow"
    NEW = "new"


@dataclass(frozen=True)
class RouteDecision:
    mode: AnalysisMode
    legacy_writes: bool
    shadow_runs: bool
    new_writes: bool
    engine_version: str
    shadow_daily_budget_usd: Decimal
    shadow_sample_percent: int

    def __post_init__(self):
        if int(self.legacy_writes) + int(self.new_writes) != 1:
            raise ValueError("exactly_one_product_writer_required")


def route_from_settings(settings):
    mode = AnalysisMode(settings.analysis_mode) if settings else AnalysisMode.LEGACY
    return RouteDecision(
        mode=mode,
        legacy_writes=mode in {AnalysisMode.LEGACY, AnalysisMode.SHADOW},
        shadow_runs=mode == AnalysisMode.SHADOW,
        new_writes=mode == AnalysisMode.NEW,
        engine_version=settings.engine_version if settings else ANALYSIS_ENGINE_VERSION,
        shadow_daily_budget_usd=(settings.shadow_daily_budget_usd if settings else Decimal(0)),
        shadow_sample_percent=settings.shadow_sample_percent if settings else 0,
    )


async def get_route(db, group_id):
    return route_from_settings(await db.get(GroupAnalysisSettings, group_id))


def sampled(group_id, sample_key, percent):
    if percent <= 0:
        return False
    if percent >= 100:
        return True
    bucket = (
        int(
            hashlib.sha256(f"{group_id}:{sample_key}".encode()).hexdigest()[:8],
            16,
        )
        % 100
    )
    return bucket < percent


async def authorize_shadow_run(db, group_id, route, now, estimated_cost, sample_key):
    if not route.shadow_runs:
        return False, "shadow_mode_disabled"
    if not sampled(group_id, sample_key, route.shadow_sample_percent):
        return False, "shadow_sample_skipped"
    estimate = Decimal(estimated_cost)
    if estimate < 0:
        raise ValueError("shadow_cost_estimate_must_be_non_negative")
    utc_now = now.replace(tzinfo=UTC) if now.tzinfo is None else now.astimezone(UTC)
    day_start = datetime(utc_now.year, utc_now.month, utc_now.day, tzinfo=UTC)
    spent = await db.scalar(
        select(func.coalesce(func.sum(ShadowEvaluationRun.charged_usd), 0)).where(
            ShadowEvaluationRun.group_id == group_id,
            ShadowEvaluationRun.created_at >= day_start,
        )
    )
    if Decimal(spent) + estimate > route.shadow_daily_budget_usd:
        return False, "shadow_budget_exhausted"
    return True, "shadow_authorized"


async def configure_group_mode(
    db,
    group_id,
    mode,
    now,
    *,
    shadow_daily_budget_usd=Decimal(0),
    shadow_sample_percent=100,
    new_writer_gate_approved=False,
):
    requested = AnalysisMode(mode)
    if requested == AnalysisMode.NEW and not new_writer_gate_approved:
        raise ValueError("new_writer_requires_explicit_gate")
    if not Decimal(0) <= Decimal(shadow_daily_budget_usd):
        raise ValueError("shadow_budget_must_be_non_negative")
    if not 0 <= shadow_sample_percent <= 100:
        raise ValueError("shadow_sample_percent_out_of_range")
    if await db.get(Group, group_id) is None:
        raise ValueError("analysis_group_not_found")
    row = await db.get(GroupAnalysisSettings, group_id, with_for_update=True)
    if row is None:
        row = GroupAnalysisSettings(group_id=group_id)
        db.add(row)
    row.analysis_mode = requested.value
    row.engine_version = ANALYSIS_ENGINE_VERSION
    row.shadow_daily_budget_usd = Decimal(shadow_daily_budget_usd)
    row.shadow_sample_percent = shadow_sample_percent
    row.updated_at = now
    await db.flush()
    return route_from_settings(row)
