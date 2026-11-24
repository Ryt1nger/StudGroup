"""Read-only, owner-scoped operational dashboard. No private message content leaves this API."""

import json
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from studgroup.api import ApiError, database
from studgroup.models import (
    AcademicDeadline,
    AIAttempt,
    AIJob,
    AnalysisBudgetCall,
    BillingPaymentOption,
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
    RanepaCrawlJob,
    RanepaPersonalSnapshot,
    RawMessage,
    ShadowEvaluationRun,
    User,
    WebSession,
)
from studgroup.process_control import ProcessAction, ProcessName
from studgroup.security import token_hash

router = APIRouter()
UI = Path(__file__).with_name("admin_ui")


class BillingOptionBody(BaseModel):
    label: str = Field(min_length=1, max_length=80)
    months: int = Field(ge=1, le=4)
    payment_url: str = Field(min_length=8, max_length=2000)
    enabled: bool = True
    position: int = Field(default=0, ge=0, le=100)


@router.get("/admin", include_in_schema=False)
@router.get("/admin/", include_in_schema=False)
async def admin_page():
    return FileResponse(UI / "index.html", headers={"Cache-Control": "no-store"})


@router.get("/admin/app.js", include_in_schema=False)
async def admin_script():
    return FileResponse(
        UI / "app.js", media_type="text/javascript", headers={"Cache-Control": "no-store"}
    )


@router.get("/admin/visuals.js", include_in_schema=False)
async def admin_visual_script():
    return FileResponse(
        UI / "visuals.js", media_type="text/javascript", headers={"Cache-Control": "no-store"}
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


def billing_option_json(row):
    return {
        "id": row.id,
        "label": row.label,
        "months": row.months,
        "amount_rub": row.months * 800,
        "payment_url": row.payment_url,
        "enabled": row.enabled,
        "position": row.position,
        "updated_at": row.updated_at,
    }


def validated_payment_url(value):
    from studgroup.billing_manual import payment_link_value

    result = payment_link_value(value)
    if result is None:
        raise ApiError(
            "invalid_payment_url",
            "Нужна безопасная HTTPS-ссылка без логина, пароля и фрагмента",
            422,
        )
    return result


async def require_billing_options_schema(
    db: Annotated[AsyncSession, Depends(database)],
    _admin: Annotated[User, Depends(admin_session)],
):
    """Check schema only after authenticating the owner; never echo SQL or link data."""
    try:
        await db.execute(select(BillingPaymentOption.id).limit(0))
    except DBAPIError as error:
        await db.rollback()
        original = error.orig
        missing = getattr(original, "sqlstate", None) == "42P01" or (
            db.get_bind().dialect.name == "sqlite"
            and str(original) == "no such table: billing_payment_options"
        )
        if missing:
            raise ApiError(
                "billing_schema_missing",
                "Платёжные кнопки недоступны: требуется миграция базы 0069.",
                503,
            ) from None
        raise ApiError(
            "billing_storage_unavailable", "Хранилище платёжных кнопок недоступно", 503
        ) from None


@router.get("/v1/admin/billing-options", dependencies=[Depends(require_billing_options_schema)])
async def billing_options(
    db: Annotated[AsyncSession, Depends(database)],
    _admin: Annotated[User, Depends(admin_session)],
):
    rows = (
        await db.scalars(
            select(BillingPaymentOption).order_by(
                BillingPaymentOption.position, BillingPaymentOption.months
            )
        )
    ).all()
    return {"items": [billing_option_json(row) for row in rows]}


@router.post("/v1/admin/billing-options", dependencies=[Depends(require_billing_options_schema)])
async def create_billing_option(
    body: BillingOptionBody,
    db: Annotated[AsyncSession, Depends(database)],
    _admin: Annotated[User, Depends(admin_session)],
):
    now = datetime.now(UTC)
    row = BillingPaymentOption(
        label=" ".join(body.label.split()),
        months=body.months,
        payment_url=validated_payment_url(body.payment_url),
        enabled=body.enabled,
        position=body.position,
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise ApiError(
            "billing_option_exists", "Кнопка для этого срока уже существует", 409
        ) from None
    return billing_option_json(row)


@router.put(
    "/v1/admin/billing-options/{option_id}", dependencies=[Depends(require_billing_options_schema)]
)
async def update_billing_option(
    option_id: UUID,
    body: BillingOptionBody,
    db: Annotated[AsyncSession, Depends(database)],
    _admin: Annotated[User, Depends(admin_session)],
):
    row = await db.get(BillingPaymentOption, option_id)
    if row is None:
        raise ApiError("billing_option_not_found", "Кнопка не найдена", 404)
    row.label = " ".join(body.label.split())
    row.months = body.months
    row.payment_url = validated_payment_url(body.payment_url)
    row.enabled = body.enabled
    row.position = body.position
    row.updated_at = datetime.now(UTC)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise ApiError(
            "billing_option_exists", "Кнопка для этого срока уже существует", 409
        ) from None
    return billing_option_json(row)


@router.delete(
    "/v1/admin/billing-options/{option_id}", dependencies=[Depends(require_billing_options_schema)]
)
async def delete_billing_option(
    option_id: UUID,
    db: Annotated[AsyncSession, Depends(database)],
    _admin: Annotated[User, Depends(admin_session)],
):
    row = await db.get(BillingPaymentOption, option_id)
    if row is None:
        raise ApiError("billing_option_not_found", "Кнопка не найдена", 404)
    await db.delete(row)
    await db.commit()
    return {"deleted": True}


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
    # Read-only, owner-scoped reliability counters. No credentials, URLs,
    # screenshots, target IDs, source content or student identifiers are returned.
    crawl_states = await states(db, RanepaCrawlJob, RanepaCrawlJob.state, *scope(RanepaCrawlJob))
    crawl_reasons = await states(
        db,
        RanepaCrawlJob,
        RanepaCrawlJob.retry_reason,
        *scope(RanepaCrawlJob),
        RanepaCrawlJob.retry_reason.is_not(None),
    )
    snapshots = (
        await db.scalars(select(RanepaPersonalSnapshot).where(*scope(RanepaPersonalSnapshot)))
    ).all()
    fresh = 0
    old = 0
    sdo_links = Counter()
    for snapshot in snapshots:
        checked = snapshot.checked_at
        if checked.tzinfo is None:
            checked = checked.replace(tzinfo=UTC)
        if checked >= now - timedelta(hours=6):
            fresh += 1
        else:
            old += 1
        for lesson in json.loads(snapshot.rows_json):
            if lesson.get("room") == "СДО":
                sdo_links[str(lesson.get("link_state") or "not_checked")] += 1
    recent_runs = (
        await db.scalars(
            select(ShadowEvaluationRun).where(
                *scope(ShadowEvaluationRun),
                ShadowEvaluationRun.raw_message_id.is_(None),
                ShadowEvaluationRun.created_at >= since,
            )
        )
    ).all()
    outcome_counts = Counter()
    package_outcomes_missing = 0
    empty_packages = 0
    useful_packages = 0
    for package in recent_runs:
        metrics = json.loads(package.metrics_json or "{}")
        product = (metrics.get("product") or {}).get("summary")
        if product is None:
            package_outcomes_missing += 1
            continue
        counts_by_outcome = product.get("by_outcome") or {}
        outcome_counts.update(counts_by_outcome)
        if product.get("empty") == product.get("targets") and product.get("targets", 0) > 0:
            empty_packages += 1
        if product.get("useful", 0) > 0:
            useful_packages += 1
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
        "reliability": {
            "sdo": {
                "queue_states": crawl_states,
                "retry_reasons": crawl_reasons,
                "snapshot_fresh_under_6h": fresh,
                "snapshot_stale_over_6h": old,
                "link_states": dict(sorted(sdo_links.items())),
            },
            "ai_products": {
                "packages_7d": len(recent_runs),
                "outcomes_7d": dict(sorted(outcome_counts.items())),
                "empty_packages_7d": empty_packages,
                "useful_packages_7d": useful_packages,
                "legacy_packages_without_outcomes_7d": package_outcomes_missing,
            },
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
            "new_engine_enabled": request.app.state.settings.new_engine_enabled
            or request.app.state.settings.owner_funded_pilot,
            "shadow_enabled": request.app.state.settings.ai_shadow_enabled,
            "image_enabled": request.app.state.settings.image_analysis_enabled
            or request.app.state.settings.owner_funded_pilot,
            "owner_funded_pilot": request.app.state.settings.owner_funded_pilot,
        },
    }


@router.get("/v1/admin/processes")
async def background_processes(
    request: Request,
    db: Annotated[AsyncSession, Depends(database)],
    owner: Annotated[User, Depends(admin_session)],
):
    from studgroup.process_control import process_snapshot

    settings = request.app.state.settings
    delivery_configured = bool(
        settings.bot_admin_user_id
        or settings.owner_update_notifications_enabled
        or settings.billing_enabled
    )
    defaults = {
        "ai": settings.processing_mode == "embedded"
        or settings.cabinet_schedule_background_enabled,
        "sdo": settings.ranepa_crawl_enabled,
        "telegram_outbox": delivery_configured,
        "scheduler": delivery_configured or settings.billing_enabled,
    }
    processes = await process_snapshot(db)
    for row in processes:
        enabled = row.get("enabled")
        row["effective_enabled"] = defaults[row["name"]] if enabled is None else enabled
    return {
        "processes": processes,
        "queues": {
            "ai_messages": await states(db, RawMessage, RawMessage.processing_state),
            "ai_jobs": await states(db, AIJob, AIJob.state),
            "ai_images": await states(db, ImageAnalysisJob, ImageAnalysisJob.state),
            "sdo": await states(db, RanepaCrawlJob, RanepaCrawlJob.state),
            "telegram_outbox": await states(db, BotOutbox, BotOutbox.state),
        },
    }


@router.post("/v1/admin/processes/{name}/{action}", status_code=202)
async def change_background_process(
    name: ProcessName,
    action: ProcessAction,
    db: Annotated[AsyncSession, Depends(database)],
    owner: Annotated[User, Depends(admin_session)],
):
    from studgroup.process_control import process_json, request_process

    row = await request_process(db, name, action)
    result = process_json(row)
    result["effective_enabled"] = row.enabled
    await db.commit()
    return result


@router.get("/v1/admin/sdo-runs")
async def urgent_sdo_status(
    db: Annotated[AsyncSession, Depends(database)],
    owner: Annotated[User, Depends(admin_session)],
):
    from studgroup.urgent_sdo import latest_run

    return {"run": await latest_run(db)}


@router.post("/v1/admin/sdo-runs", status_code=202)
async def urgent_sdo_request(
    db: Annotated[AsyncSession, Depends(database)],
    owner: Annotated[User, Depends(admin_session)],
):
    from studgroup.urgent_sdo import request_run, run_json

    row = await request_run(db)
    result = await run_json(db, row)
    await db.commit()
    return result
