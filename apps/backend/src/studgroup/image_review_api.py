"""Feature-gated staff-only review of image findings; never auto-enables publication."""

import json
from uuid import UUID

from fastapi import APIRouter, Request
from sqlalchemy import select

from studgroup.analysis.image_schedule import ScheduleConfirmation, confirm
from studgroup.api import ApiError
from studgroup.headman_domain import StError
from studgroup.models import ImageScheduleSuggestion
from studgroup.staff_api import CurrentStaff, Db

router = APIRouter(prefix="/v1/staff/image-findings")


def gate(request, group_id):
    settings = request.app.state.settings
    if not settings.image_schedule_review_enabled or str(group_id) not in settings.image_analysis_groups:
        raise ApiError("not_found", "Раздел недоступен", 404)


@router.get("/schedule")
async def list_schedule(request: Request, context: CurrentStaff, db: Db):
    from datetime import UTC, datetime

    gate(request, context.group.id)
    rows = (await db.scalars(select(ImageScheduleSuggestion).where(
        ImageScheduleSuggestion.group_id == context.group.id,
        ImageScheduleSuggestion.delete_at > datetime.now(UTC),
    ).order_by(ImageScheduleSuggestion.created_at.desc()).limit(100))).all()
    return {"suggestions": [{"id": row.id, "revision": row.revision, "state": row.state,
        "schedule_version": row.schedule_version, "rows": json.loads(row.rows_json)} for row in rows]}


@router.post("/schedule/{ident}/confirm")
async def confirm_schedule(ident: UUID, body: ScheduleConfirmation, request: Request, context: CurrentStaff, db: Db):
    gate(request, context.group.id)
    try:
        version = await confirm(db, context.group.id, context.user, context.member, ident, body)
    except StError as error:
        raise ApiError("action_rejected", str(error), 409) from None
    await db.commit()
    return {"schedule_version": version}


@router.get("/images")
async def image_diagnostics(request: Request, context: CurrentStaff, db: Db):
    from datetime import UTC, datetime
    from decimal import Decimal

    from studgroup.models import ImageAnalysisCall, ImageAnalysisJob

    settings = request.app.state.settings
    if not settings.image_diagnostics_enabled or str(context.group.id) not in settings.image_analysis_groups:
        raise ApiError("not_found", "Раздел недоступен", 404)
    jobs = (await db.scalars(select(ImageAnalysisJob).where(
        ImageAnalysisJob.group_id == context.group.id, ImageAnalysisJob.delete_at > datetime.now(UTC),
    ).order_by(ImageAnalysisJob.created_at.desc(), ImageAnalysisJob.id).limit(200))).all()
    calls = (await db.scalars(select(ImageAnalysisCall).where(
        ImageAnalysisCall.group_id == context.group.id,
        ImageAnalysisCall.job_id.in_([job.id for job in jobs]),
    ))).all()
    return {"scope": "latest_200_jobs", "quality": "UNMEASURED", "jobs": [
        {"id": job.id, "attachment_id": job.attachment_id, "source_revision": job.source_revision,
         "pass": job.pass_number, "version": job.version, "state": job.state,
         "attempts": job.attempts, "error": job.error, "available_at": job.available_at}
        for job in jobs], "calls": len(calls),
        "accounted_cost_usd": str(sum((call.charged_usd for call in calls), Decimal(0))),
        "reserved_usd": str(sum((call.reserved_usd for call in calls), Decimal(0))),
        "unknown_calls": sum(call.state == "unknown" for call in calls),
        "latency_ms": [call.latency_ms for call in calls if call.latency_ms is not None],
        "tokens": sum(call.prompt_tokens + call.completion_tokens for call in calls)}
