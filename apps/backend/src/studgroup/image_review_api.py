"""Feature-gated staff-only review of image findings; never auto-enables publication."""

import json
from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Request
from sqlalchemy import select

from studgroup.analysis.image_schedule import ScheduleConfirmation, confirm
from studgroup.api import ApiError
from studgroup.headman_domain import StError
from studgroup.models import (
    ImageAnalysisJob,
    ImageScheduleSuggestion,
    MessageAttachment,
    RawMessage,
    Subgroup,
    SubjectSplit,
)
from studgroup.staff_api import CurrentStaff, Db
from studgroup.student_cards import telegram_url

router = APIRouter(prefix="/v1/staff/image-findings")


def gate(request, group_id):
    settings = request.app.state.settings
    if (
        not settings.image_schedule_review_enabled
        or str(group_id) not in settings.image_analysis_groups
    ):
        raise ApiError("not_found", "Раздел недоступен", 404)


@router.get("/schedule")
async def list_schedule(request: Request, context: CurrentStaff, db: Db):
    gate(request, context.group.id)
    now = datetime.now(UTC)
    rows = (
        await db.scalars(
            select(ImageScheduleSuggestion)
            .join(ImageAnalysisJob, ImageAnalysisJob.id == ImageScheduleSuggestion.image_job_id)
            .join(RawMessage, RawMessage.id == ImageAnalysisJob.raw_message_id)
            .join(MessageAttachment, MessageAttachment.id == ImageAnalysisJob.attachment_id)
            .where(
                ImageScheduleSuggestion.group_id == context.group.id,
                ImageScheduleSuggestion.delete_at > now,
                ImageAnalysisJob.group_id == context.group.id,
                ImageAnalysisJob.delete_at > now,
                RawMessage.group_id == context.group.id,
                RawMessage.delete_at > now,
                RawMessage.revision == ImageAnalysisJob.source_revision,
                MessageAttachment.group_id == context.group.id,
                MessageAttachment.active.is_(True),
                MessageAttachment.delete_at > now,
            )
            .order_by(ImageScheduleSuggestion.created_at.desc())
            .limit(100)
        )
    ).all()
    subgroups = (
        await db.execute(
            select(Subgroup, SubjectSplit)
            .join(SubjectSplit, SubjectSplit.id == Subgroup.split_id)
            .where(SubjectSplit.group_id == context.group.id)
            .order_by(SubjectSplit.subject_name, Subgroup.position)
        )
    ).all()
    return {
        "current_schedule_version": context.group.schedule_version,
        "subgroups": [
            {
                "id": subgroup.id,
                "subject_id": split.subject_id,
                "subject": split.subject_name,
                "name": subgroup.name,
            }
            for subgroup, split in subgroups
        ],
        "suggestions": [
            {
                "id": row.id,
                "revision": row.revision,
                "state": row.state,
                "schedule_version": row.schedule_version,
                "rows": json.loads(row.rows_json),
                "source_url": await schedule_source_url(db, context.group, row, now),
                "confirmation": json.loads(row.confirmation_json)
                if row.confirmation_json
                else None,
            }
            for row in rows
        ],
    }


async def schedule_source_url(db, group, suggestion, now):
    from studgroup.analysis.image_jobs import utc

    job = await db.get(ImageAnalysisJob, suggestion.image_job_id)
    if not job or job.group_id != group.id or job.state != "ready" or utc(job.delete_at) <= now:
        return None
    raw = await db.get(RawMessage, job.raw_message_id)
    attachment = await db.get(MessageAttachment, job.attachment_id)
    if (
        raw is None
        or raw.group_id != group.id
        or attachment is None
        or attachment.group_id != group.id
        or not attachment.active
        or utc(attachment.delete_at) <= now
    ):
        return None
    return telegram_url(group, raw, now, job.source_revision)[0]


@router.post("/schedule/{ident}/confirm")
async def confirm_schedule(
    ident: UUID, body: ScheduleConfirmation, request: Request, context: CurrentStaff, db: Db
):
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

    from studgroup.models import ImageAnalysisCall

    settings = request.app.state.settings
    if (
        not settings.image_diagnostics_enabled
        or str(context.group.id) not in settings.image_analysis_groups
    ):
        raise ApiError("not_found", "Раздел недоступен", 404)
    jobs = (
        await db.scalars(
            select(ImageAnalysisJob)
            .where(
                ImageAnalysisJob.group_id == context.group.id,
                ImageAnalysisJob.delete_at > datetime.now(UTC),
            )
            .order_by(ImageAnalysisJob.created_at.desc(), ImageAnalysisJob.id)
            .limit(200)
        )
    ).all()
    calls = (
        await db.scalars(
            select(ImageAnalysisCall).where(
                ImageAnalysisCall.group_id == context.group.id,
                ImageAnalysisCall.job_id.in_([job.id for job in jobs]),
            )
        )
    ).all()
    return {
        "scope": "latest_200_jobs",
        "quality": "UNMEASURED",
        "jobs": [
            {
                "id": job.id,
                "attachment_id": job.attachment_id,
                "source_revision": job.source_revision,
                "pass": job.pass_number,
                "version": job.version,
                "state": job.state,
                "attempts": job.attempts,
                "error": job.error,
                "available_at": job.available_at,
            }
            for job in jobs
        ],
        "calls": len(calls),
        "accounted_cost_usd": str(sum((call.charged_usd for call in calls), Decimal(0))),
        "reserved_usd": str(sum((call.reserved_usd for call in calls), Decimal(0))),
        "unknown_calls": sum(call.state == "unknown" for call in calls),
        "latency_ms": [call.latency_ms for call in calls if call.latency_ms is not None],
        "tokens": sum(call.prompt_tokens + call.completion_tokens for call in calls),
    }
