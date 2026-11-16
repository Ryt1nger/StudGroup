"""Account-scoped university timetable, freshness and throttled refresh request."""

from datetime import UTC, date, datetime, timedelta
from typing import Annotated
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Header, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from studgroup.api import ApiError, active_group, database
from studgroup.legal import has_consent
from studgroup.models import Group, RanepaCrawlJob, UserProfile
from studgroup.profile import current
from studgroup.ranepa_crawl_jobs import REFRESH, enqueue_week
from studgroup.ranepa_crawl_plan import CrawlCandidate
from studgroup.ranepa_personal import personal_with_group_fallback

router = APIRouter(prefix="/v1")


@router.get("/ranepa-schedule")
async def my_ranepa_schedule(
    start: date,
    end: date,
    group: Annotated[Group, Depends(active_group)],
    db: Annotated[AsyncSession, Depends(database)],
    authorization: str | None = Header(None),
):
    user, _ = await current(db, authorization)
    try:
        response = await personal_with_group_fallback(db, group.id, user.id, start, end)
    except ValueError:
        raise ApiError("invalid_request", "Выберите период до 32 дней", 422) from None
    profile = await db.get(UserProfile, user.id)
    response["account_connected"] = bool(
        profile and profile.sdu_credential_id and group.academic_group_code
    )
    return response


@router.post("/ranepa-schedule/refresh")
async def request_ranepa_refresh(
    request: Request,
    group: Annotated[Group, Depends(active_group)],
    db: Annotated[AsyncSession, Depends(database)],
    authorization: str | None = Header(None),
):
    """Queue only this consenting account, never start a paid crawl from HTTP."""
    user, _ = await current(db, authorization)
    profile = await db.get(UserProfile, user.id)
    if not profile or not profile.sdu_credential_id or not group.academic_group_code:
        raise ApiError("action_rejected", "Подключите учётную запись СДО", 409)
    settings = request.app.state.settings
    if not await has_consent(db, user.id, "sdo", settings):
        raise ApiError("consent_required", "Требуется согласие на СДО", 403)
    now = datetime.now(UTC)
    local_day = now.astimezone(ZoneInfo(group.timezone)).date()
    monday = local_day - timedelta(days=local_day.weekday())
    existing = await db.scalar(
        select(RanepaCrawlJob)
        .where(
            RanepaCrawlJob.group_id == group.id,
            RanepaCrawlJob.user_id == user.id,
            RanepaCrawlJob.week_start == monday,
        )
        .with_for_update()
    )
    if existing is not None and existing.failure_code == "login_failed":
        return {
            "state": "credentials_required",
            "next_attempt_at": None,
            "queued": False,
        }
    if existing is not None and existing.state in {"queued", "running"}:
        return {
            "state": existing.state,
            "next_attempt_at": existing.next_attempt_at,
            "queued": True,
        }
    # A manual button cannot bypass the account-wide six-hour throttle.
    if existing is not None and existing.finished_at is not None:
        finished = existing.finished_at
        if finished.tzinfo is None:
            finished = finished.replace(tzinfo=UTC)
        next_at = existing.next_attempt_at
        if next_at is not None and next_at.tzinfo is None:
            next_at = next_at.replace(tzinfo=UTC)
        if finished > now - REFRESH or (next_at is not None and next_at > now):
            return {"state": "cooldown", "next_attempt_at": next_at, "queued": False}
    await enqueue_week(
        db, monday, [CrawlCandidate(group.id, user.id)], now=now, force_refresh=False
    )
    job = await db.scalar(
        select(RanepaCrawlJob).where(
            RanepaCrawlJob.group_id == group.id,
            RanepaCrawlJob.user_id == user.id,
            RanepaCrawlJob.week_start == monday,
        )
    )
    await db.commit()
    return {
        "state": job.state if job is not None else "unavailable",
        "next_attempt_at": job.next_attempt_at if job is not None else None,
        "queued": bool(job is not None and job.state == "queued"),
    }
