"""Conservatively unfreeze production groups after exact paid attempts are terminal.

An unknown ledger entry is NEVER deleted, retried, or treated as a confirmed
provider charge. Only the *group-wide* hold can be released when every unknown
attempt is already isolated to a terminal durable job. This also repairs holds
created by older worker versions before per-job isolation existed.
"""

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from studgroup.analysis.release_budget import production_enabled
from studgroup.models import (
    AnalysisBudgetCall,
    Group,
    GroupAnalysisSettings,
    ImageAnalysisCall,
    ImageAnalysisJob,
    ShadowEvaluationRun,
)


async def _terminal(db, group_id, call):
    if call.state != "unknown" or call.reserved_usd != Decimal(0):
        return False
    if call.kind == "image" and call.attempt_key.startswith("image:"):
        try:
            token = uuid.UUID(call.attempt_key.removeprefix("image:"))
        except ValueError:
            return False
        job = await db.scalar(
            select(ImageAnalysisJob).where(
                ImageAnalysisJob.group_id == group_id,
                ImageAnalysisJob.claim_token == token,
            )
        )
        receipt = await db.scalar(
            select(ImageAnalysisCall).where(
                ImageAnalysisCall.group_id == group_id,
                ImageAnalysisCall.claim_token == token,
            )
        )
        return bool(
            job is not None
            and receipt is not None
            and job.state in {"failed", "stale"}
            and job.lease_until is None
            and receipt.state == "unknown"
            and receipt.reserved_usd == Decimal(0)
        )
    if call.kind == "cplus" and call.attempt_key.startswith("cplus:"):
        parts = call.attempt_key.split(":")
        if len(parts) != 3:
            return False
        try:
            ident, generation = uuid.UUID(parts[1]), int(parts[2])
        except ValueError:
            return False
        run = await db.get(ShadowEvaluationRun, ident)
        return bool(
            run is not None
            and run.group_id == group_id
            and run.raw_message_id is None
            and run.attempts == generation
            and run.state == "failed"
            and run.lease_until is None
        )
    return False


async def reconcile_terminal_holds(engine, settings, now=None):
    """Release ONLY verified terminal production holds, no new paid requests.

    Return count of recovered groups. Any ambiguous active or unlinked payment
    remains on full hold until reviewed by an operator.
    """
    now = now or datetime.now(UTC)
    async with AsyncSession(engine) as db:
        ids = (
            await db.scalars(
                select(GroupAnalysisSettings.group_id).where(
                    GroupAnalysisSettings.paid_outcome_hold == "ambiguous_paid_attempt"
                )
            )
        ).all()
    released = 0
    for group_id in ids:
        if not production_enabled(settings, group_id):
            continue
        async with AsyncSession(engine) as db:
            route = await db.scalar(
                select(GroupAnalysisSettings)
                .where(GroupAnalysisSettings.group_id == group_id)
                .with_for_update(skip_locked=True)
            )
            if route is None or route.paid_outcome_hold != "ambiguous_paid_attempt":
                continue
            group = await db.get(Group, group_id)
            if group is None or group.status != "active" or not group.pilot_authorized:
                continue
            calls = (
                await db.scalars(
                    select(AnalysisBudgetCall)
                    .where(
                        AnalysisBudgetCall.group_id == group_id,
                        AnalysisBudgetCall.state.in_(["unknown", "reserved"]),
                    )
                    .with_for_update()
                )
            ).all()
            if not calls or any(call.state == "reserved" for call in calls):
                continue
            active_image = await db.scalar(
                select(ImageAnalysisJob.id)
                .where(ImageAnalysisJob.group_id == group_id, ImageAnalysisJob.state == "running")
                .limit(1)
            )
            active_package = await db.scalar(
                select(ShadowEvaluationRun.id)
                .where(
                    ShadowEvaluationRun.group_id == group_id,
                    ShadowEvaluationRun.raw_message_id.is_(None),
                    ShadowEvaluationRun.state == "running",
                )
                .limit(1)
            )
            if active_image is not None or active_package is not None:
                continue
            if not all([await _terminal(db, group_id, call) for call in calls]):
                continue
            route.paid_outcome_hold = None
            route.updated_at = now
            from studgroup.processing import incident_once

            await incident_once(
                db,
                "ai_request_uncertain",
                now,
                detail="kind=durable reason=terminal_reconciled",
            )
            await db.commit()
            released += 1
    return released
