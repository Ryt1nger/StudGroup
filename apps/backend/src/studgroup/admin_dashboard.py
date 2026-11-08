"""Read-only, owner-scoped operational dashboard. No private message content leaves this API."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from studgroup.api import ApiError, database
from studgroup.models import (
    AcademicDeadline,
    AIAttempt,
    AIJob,
    AnalysisBudgetCall,
    BotOutbox,
    CanonicalAcademicItem,
    Group,
    GroupAnalysisSettings,
    GroupEvent,
    Homework,
    ImageAnalysisCall,
    ImageAnalysisJob,
    Membership,
    OwnerIncidentEpisode,
    RawMessage,
    ShadowEvaluationRun,
    User,
    WebSession,
)
from studgroup.security import token_hash

router = APIRouter()
UI = Path(__file__).with_name("admin_ui")


@router.get("/admin", include_in_schema=False)
@router.get("/admin/", include_in_schema=False)
async def admin_page():
    return FileResponse(UI / "index.html", headers={"Cache-Control": "no-store"})


@router.get("/admin/app.js", include_in_schema=False)
async def admin_script():
    return FileResponse(
        UI / "app.js", media_type="text/javascript", headers={"Cache-Control": "no-store"}
    )


@router.get("/admin/style.css", include_in_schema=False)
async def admin_style():
    return FileResponse(
        UI / "style.css", media_type="text/css", headers={"Cache-Control": "no-store"}
    )


async def admin_session(
    request: Request,
    db: Annotated[AsyncSession, Depends(database)],
    authorization: str | None = Header(None),
) -> User:
    if not authorization or not authorization.startswith("Bearer "):
        raise ApiError("unauthenticated", "Откройте админку из Telegram")
    credential = await db.get(WebSession, token_hash(authorization[7:]))
    expiry = credential.expires_at if credential else None
    if expiry is not None and expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=UTC)
    if credential is None or expiry <= datetime.now(UTC):
        raise ApiError("session_expired", "Откройте админку заново из Telegram")
    user = await db.get(User, credential.user_id)
    if (
        user is None
        or request.app.state.settings.bot_admin_user_id is None
        or (user.telegram_user_id != request.app.state.settings.bot_admin_user_id)
    ):
        raise ApiError("permission_denied", "Доступ запрещён", 403)
    return user


async def count(db, model, *criteria):
    return await db.scalar(select(func.count()).select_from(model).where(*criteria)) or 0


async def states(db, model, column, *criteria):
    rows = (
        await db.execute(
            select(column, func.count()).select_from(model).where(*criteria).group_by(column)
        )
    ).all()
    return {str(state or "unknown"): total for state, total in rows}


@router.get("/v1/admin/dashboard")
async def dashboard(
    request: Request,
    db: Annotated[AsyncSession, Depends(database)],
    _admin: Annotated[User, Depends(admin_session)],
    group_id: UUID | None = None,
):
    now = datetime.now(UTC)
    since = now - timedelta(days=7)
    groups = (await db.scalars(select(Group).order_by(Group.name))).all()
    if group_id is not None and all(group.id != group_id for group in groups):
        raise ApiError("group_not_found", "Группа не найдена", 404)

    def scope(model):
        return (model.group_id == group_id,) if group_id else ()

    member_query = select(func.count()).select_from(Membership).where(Membership.status == "active")
    if group_id:
        member_query = member_query.where(Membership.group_id == group_id)
    members = await db.scalar(member_query) or 0
    sources = await states(db, RawMessage, RawMessage.processing_state, *scope(RawMessage))
    ai_jobs = await states(db, AIJob, AIJob.state, *scope(AIJob))
    recent_messages = await count(
        db, RawMessage, *scope(RawMessage), RawMessage.message_date >= since
    )
    latest_message = await db.scalar(
        select(func.max(RawMessage.message_date)).where(*scope(RawMessage))
    )
    shadow = await states(
        db, ShadowEvaluationRun, ShadowEvaluationRun.state, *scope(ShadowEvaluationRun)
    )
    image = await states(db, ImageAnalysisJob, ImageAnalysisJob.state, *scope(ImageAnalysisJob))
    outbox = await states(db, BotOutbox, BotOutbox.state)  # system-wide, no group_id
    spent = await db.scalar(
        select(func.sum(AnalysisBudgetCall.charged_usd)).where(*scope(AnalysisBudgetCall))
    )
    legacy_spent = await db.scalar(select(func.sum(AIAttempt.charged_usd)).where(*scope(AIAttempt)))
    image_spent = await db.scalar(
        select(func.sum(ImageAnalysisCall.charged_usd)).where(*scope(ImageAnalysisCall))
    )
    reserved = await db.scalar(
        select(func.sum(AnalysisBudgetCall.reserved_usd)).where(
            *scope(AnalysisBudgetCall), AnalysisBudgetCall.state != "settled"
        )
    )
    active_incidents = (
        await db.execute(
            select(
                OwnerIncidentEpisode.id,
                OwnerIncidentEpisode.code,
                OwnerIncidentEpisode.opened_at,
                OwnerIncidentEpisode.last_seen_at,
                OwnerIncidentEpisode.occurrences,
            )
            .where(OwnerIncidentEpisode.recovered_at.is_(None))
            .order_by(OwnerIncidentEpisode.opened_at.desc())
            .limit(20)
        )
    ).all()
    modes_query = select(
        GroupAnalysisSettings.group_id,
        GroupAnalysisSettings.analysis_mode,
        GroupAnalysisSettings.paid_outcome_hold,
    )
    if group_id:
        modes_query = modes_query.where(GroupAnalysisSettings.group_id == group_id)
    modes = (await db.execute(modes_query)).all()
    return {
        "as_of": now,
        "period_days": 7,
        "selected_group_id": group_id,
        "groups": [
            {
                "id": group.id,
                "name": group.name,
                "status": group.status,
                "pilot_authorized": group.pilot_authorized,
            }
            for group in groups
        ],
        "summary": {
            "active_groups": sum(
                g.status == "active" and g.pilot_authorized and (not group_id or g.id == group_id)
                for g in groups
            ),
            "active_members": members,
            "messages_7d": recent_messages,
            "latest_message_at": latest_message,
            "published_homework": await count(
                db, Homework, *scope(Homework), Homework.status == "published"
            ),
            "active_deadlines": await count(
                db,
                AcademicDeadline,
                *scope(AcademicDeadline),
                AcademicDeadline.cancelled_at.is_(None),
                AcademicDeadline.delete_at > now,
            ),
            "needs_clarification": await count(
                db,
                AcademicDeadline,
                *scope(AcademicDeadline),
                AcademicDeadline.needs_clarification.is_(True),
                AcademicDeadline.cancelled_at.is_(None),
                AcademicDeadline.delete_at > now,
            ),
            "canonical_items": await count(
                db,
                CanonicalAcademicItem,
                *scope(CanonicalAcademicItem),
                CanonicalAcademicItem.state == "active",
            ),
            "published_events": await count(
                db, GroupEvent, *scope(GroupEvent), GroupEvent.state == "published"
            ),
            "active_incidents": len(active_incidents),
            "charged_usd": float(spent or 0),
            "legacy_attempts_charged_usd": float(legacy_spent or 0),
            "image_calls_charged_usd": float(image_spent or 0),
            "unsettled_reserved_usd": float(reserved or 0),
        },
        "queues": {
            "messages": sources,
            "legacy_ai_jobs": ai_jobs,
            "shadow": shadow,
            "images": image,
            "bot_outbox": outbox,
        },
        "analysis_modes": [
            {"group_id": gid, "mode": mode, "hold": hold} for gid, mode, hold in modes
        ],
        "schedule_versions": [
            {"group_id": group.id, "group_name": group.name, "version": group.schedule_version}
            for group in groups
            if not group_id or group.id == group_id
        ],
        "incidents": [
            {
                "id": ident,
                "code": code,
                "opened_at": opened,
                "last_seen_at": last,
                "occurrences": occurrences,
            }
            for ident, code, opened, last, occurrences in active_incidents
        ],
        "runtime": {
            "processing_mode": request.app.state.settings.processing_mode,
            "legacy_ai_enabled": request.app.state.settings.ai_enabled
            and request.app.state.settings.legacy_engine_enabled,
            "new_engine_enabled": request.app.state.settings.new_engine_enabled,
            "shadow_enabled": request.app.state.settings.ai_shadow_enabled,
            "image_enabled": request.app.state.settings.image_analysis_enabled,
        },
    }
