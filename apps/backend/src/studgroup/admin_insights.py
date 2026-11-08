"""Owner-only analytics over live StudGroup records. No fake traffic or financial balances.

The billing journal can overlap with historical AI attempt/call ledgers: amounts from
different ledgers are NEVER added together. This endpoint is read-only and does not
contain Telegram message text, user identifiers or model output.
"""

from datetime import UTC, datetime, timedelta
from time import perf_counter
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from studgroup.admin_dashboard import admin_session
from studgroup.api import ApiError, database
from studgroup.models import (
    AIJob,
    AnalysisBudgetCall,
    BotOutbox,
    BotPanel,
    Group,
    GroupEvent,
    Homework,
    ImageAnalysisCall,
    ImageAnalysisJob,
    Membership,
    Notification,
    OwnerIncidentEpisode,
    RawMessage,
    ReceivedUpdate,
    ShadowEvaluationRun,
    User,
    WebSession,
)

router = APIRouter()
DATABASE = Annotated[AsyncSession, Depends(database)]
OWNER = Annotated[User, Depends(admin_session)]
BAD_STATES = ("failed", "error", "dead", "dead_letter")


def money(value):
    return round(float(value or 0), 6)


def stamp(value):
    return value.isoformat() if value else None


def scope(model, group_id):
    return (model.group_id == group_id,) if group_id else ()


async def ensure_group(db, group_id):
    if group_id and not await db.get(Group, group_id):
        raise ApiError("group_not_found", "Группа не найдена", 404)


async def total(db, model, criteria=(), value=None):
    measure = func.sum(value) if value is not None else func.count()
    result = await db.scalar(select(measure).select_from(model).where(*criteria))
    return money(result) if value is not None else int(result or 0)


async def distribution(db, model, state, criteria):
    found = (
        await db.execute(
            select(state, func.count()).select_from(model).where(*criteria).group_by(state)
        )
    ).all()
    return {str(key or "unknown"): int(qty) for key, qty in found}


async def daily(db, model, date_column, criteria, days, value=None):
    day = (
        func.date(date_column)
        if db.get_bind().dialect.name == "sqlite"
        else func.date_trunc("day", date_column)
    )
    aggregate = func.sum(value) if value is not None else func.count()
    found = (
        await db.execute(
            select(day, aggregate).select_from(model).where(*criteria).group_by(day).order_by(day)
        )
    ).all()
    values = {
        (
            day_value.date().isoformat() if hasattr(day_value, "date") else str(day_value)[:10]
        ): money(qty) if value is not None else int(qty)
        for day_value, qty in found
    }
    today = datetime.now(UTC).date()
    return [
        {
            "date": (today - timedelta(days=i)).isoformat(),
            "value": values.get((today - timedelta(days=i)).isoformat(), 0),
        }
        for i in range(days - 1, -1, -1)
    ]


@router.get("/v1/admin/analytics")
async def analytics(
    db: DATABASE,
    _owner: OWNER,
    days: int = Query(7, ge=1, le=90),
    group_id: UUID | None = None,
):
    await ensure_group(db, group_id)
    start = perf_counter()
    now = datetime.now(UTC)
    first_day = now.date() - timedelta(days=days - 1)
    since = datetime(first_day.year, first_day.month, first_day.day, tzinfo=UTC)
    month_start = datetime(now.year, now.month, 1, tzinfo=UTC)
    source = (*scope(RawMessage, group_id), RawMessage.message_date >= since)
    text = (*scope(ShadowEvaluationRun, group_id), ShadowEvaluationRun.created_at >= since)
    images = (*scope(ImageAnalysisJob, group_id), ImageAnalysisJob.created_at >= since)
    jobs = (*scope(AIJob, group_id), AIJob.created_at >= since)
    billing = (*scope(AnalysisBudgetCall, group_id), AnalysisBudgetCall.created_at >= since)
    home = (*scope(Homework, group_id), Homework.created_at >= since)
    events = (*scope(GroupEvent, group_id), GroupEvent.created_at >= since)
    members = (Membership.status == "active", *scope(Membership, group_id))
    active_incidents = await total(
        db, OwnerIncidentEpisode, (OwnerIncidentEpisode.recovered_at.is_(None),)
    )
    msg_count = await total(db, RawMessage, source)
    authors = (
        await db.scalar(
            select(func.count(func.distinct(RawMessage.sender_id))).where(
                *source, RawMessage.sender_id.is_not(None)
            )
        )
        or 0
    )
    text_count = await total(db, ShadowEvaluationRun, text)
    image_count = await total(db, ImageAnalysisJob, images)
    legacy_count = await total(db, AIJob, jobs)
    text_status = await distribution(db, ShadowEvaluationRun, ShadowEvaluationRun.state, text)
    image_status = await distribution(db, ImageAnalysisJob, ImageAnalysisJob.state, images)
    legacy_status = await distribution(db, AIJob, AIJob.state, jobs)
    charged = await total(db, AnalysisBudgetCall, billing, AnalysisBudgetCall.charged_usd)
    reserved = await total(
        db,
        AnalysisBudgetCall,
        (*scope(AnalysisBudgetCall, group_id), AnalysisBudgetCall.state != "settled"),
        AnalysisBudgetCall.reserved_usd,
    )
    month_cost = await total(
        db,
        AnalysisBudgetCall,
        (*scope(AnalysisBudgetCall, group_id), AnalysisBudgetCall.created_at >= month_start),
        AnalysisBudgetCall.charged_usd,
    )
    billed_by_kind = (
        await db.execute(
            select(AnalysisBudgetCall.kind, func.sum(AnalysisBudgetCall.charged_usd))
            .where(*billing)
            .group_by(AnalysisBudgetCall.kind)
        )
    ).all()
    image_calls = (*scope(ImageAnalysisCall, group_id), ImageAnalysisCall.created_at >= since)
    shadow_latency = await db.scalar(
        select(func.avg(ShadowEvaluationRun.latency_ms)).where(
            *text, ShadowEvaluationRun.latency_ms.is_not(None)
        )
    )
    image_latency = await db.scalar(
        select(func.avg(ImageAnalysisCall.latency_ms)).where(
            *image_calls, ImageAnalysisCall.latency_ms.is_not(None)
        )
    )
    groups = (await db.scalars(select(Group).order_by(Group.name))).all()
    last_message = await db.scalar(
        select(func.max(RawMessage.message_date)).where(*scope(RawMessage, group_id))
    )
    failed = sum(
        text_status.get(s, 0) + image_status.get(s, 0) + legacy_status.get(s, 0) for s in BAD_STATES
    )
    return {
        "as_of": stamp(now),
        "period_days": days,
        "selected_group_id": str(group_id) if group_id else None,
        "groups": [
            {"id": str(g.id), "name": g.name, "active": g.status == "active"} for g in groups
        ],
        "summary": {
            "members": await total(db, Membership, members),
            "groups": sum(
                g.status == "active" and g.pilot_authorized and (not group_id or g.id == group_id)
                for g in groups
            ),
            "messages": msg_count,
            "chat_authors": int(authors),
            "text_runs": text_count,
            "image_runs": image_count,
            "legacy_jobs": legacy_count,
            "errors": failed,
            "incidents_open": active_incidents,
            "homework": await total(db, Homework, home),
            "events": await total(db, GroupEvent, events),
            "spent_usd": charged,
            "reserved_usd": reserved,
            "month_spent_usd": month_cost,
            "text_latency_ms": round(float(shadow_latency)) if shadow_latency is not None else None,
            "image_latency_ms": round(float(image_latency)) if image_latency is not None else None,
            "last_message_at": stamp(last_message),
        },
        "states": {
            "text": text_status,
            "images": image_status,
            "legacy": legacy_status,
            "telegram": await distribution(db, BotOutbox, BotOutbox.state, ()),
        },
        "series": {
            "messages": await daily(db, RawMessage, RawMessage.message_date, source, days),
            "text": await daily(
                db, ShadowEvaluationRun, ShadowEvaluationRun.created_at, text, days
            ),
            "images": await daily(db, ImageAnalysisJob, ImageAnalysisJob.created_at, images, days),
            "spend": await daily(
                db,
                AnalysisBudgetCall,
                AnalysisBudgetCall.created_at,
                billing,
                days,
                AnalysisBudgetCall.charged_usd,
            ),
        },
        "finance": {
            "ledger_by_kind": [
                {"kind": str(kind), "usd": money(amount)} for kind, amount in billed_by_kind
            ],
            "period_charged_usd": charged,
            "month_charged_usd": month_cost,
            "unsettled_reserved_usd": reserved,
            "company_balance_usd": None,
            "company_revenue_usd": None,
            "provider_balances": None,
            "subscriptions": None,
            "coverage": "Только журнал списаний AI. Балансы, поступления и счета сервисов не подключены.",
            "accounting_note": "Суммы старых AI-попыток и фото-вызовов могут пересекаться с журналом бюджета и не прибавляются к нему.",
        },
        "availability": {
            "server_latency": "no_historical_http_metrics",
            "app_visits": "no_usage_event_collection",
            "provider_balances": "not_connected",
            "company_finances": "not_connected",
        },
        "diagnostics": {"query_duration_ms": round((perf_counter() - start) * 1000)},
    }


def item(ident, title, status, at, **fields):
    return {
        "id": str(ident),
        "title": str(title),
        "status": str(status or "—"),
        "at": stamp(at),
        "fields": {
            key: stamp(value)
            if isinstance(value, datetime)
            else money(value)
            if key.endswith("_usd")
            else str(value)
            if isinstance(value, UUID)
            else value
            for key, value in fields.items()
            if value is not None
        },
    }


@router.get("/v1/admin/details/{kind}")
async def details(
    kind: str,
    db: DATABASE,
    _owner: OWNER,
    days: int = Query(7, ge=1, le=90),
    group_id: UUID | None = None,
    offset: int = Query(0, ge=0, le=10000),
    limit: int = Query(40, ge=1, le=100),
):
    await ensure_group(db, group_id)
    first_day = datetime.now(UTC).date() - timedelta(days=days - 1)
    since = datetime(first_day.year, first_day.month, first_day.day, tzinfo=UTC)
    # Each detail row is selected from the same live source as its summary.
    # Never return raw Telegram text, model outputs, payloads or credentials.
    rows = []
    has_more = False
    if kind == "groups":
        query = select(Group).where(Group.status == "active", Group.pilot_authorized.is_(True))
        if group_id:
            query = query.where(Group.id == group_id)
        query = query.order_by(Group.name)
        found = (await db.scalars(query.offset(offset).limit(limit + 1))).all()
        rows = [
            item(
                x.id,
                x.name,
                x.status,
                x.bot_added_at,
                pilot_authorized=x.pilot_authorized,
                schedule_version=x.schedule_version,
            )
            for x in found
        ]
    elif kind == "members":
        query = (
            select(Membership)
            .where(Membership.status == "active", *scope(Membership, group_id))
            .order_by(Membership.id)
        )
        found = (await db.scalars(query.offset(offset).limit(limit + 1))).all()
        rows = [
            item(x.id, "Участник группы", x.role, None, group_id=x.group_id, role=x.role)
            for x in found
        ]
    elif kind == "messages":
        query = (
            select(RawMessage)
            .where(*scope(RawMessage, group_id), RawMessage.message_date >= since)
            .order_by(RawMessage.message_date.desc(), RawMessage.id.desc())
        )
        found = (await db.scalars(query.offset(offset).limit(limit + 1))).all()
        rows = [
            item(
                x.id,
                "Сообщение Telegram",
                x.processing_state,
                x.message_date,
                group_id=x.group_id,
                imported=x.imported,
                revision=x.revision,
            )
            for x in found
        ]
    elif kind == "text":
        query = (
            select(ShadowEvaluationRun)
            .where(*scope(ShadowEvaluationRun, group_id), ShadowEvaluationRun.created_at >= since)
            .order_by(ShadowEvaluationRun.created_at.desc(), ShadowEvaluationRun.id.desc())
        )
        found = (await db.scalars(query.offset(offset).limit(limit + 1))).all()
        rows = [
            item(
                x.id,
                "Анализ текста",
                x.state,
                x.created_at,
                group_id=x.group_id,
                engine=x.engine_version,
                attempts=x.attempts,
                latency_ms=x.latency_ms,
                charged_usd=x.charged_usd,
                tokens_in=x.prompt_tokens,
                tokens_out=x.completion_tokens,
                error_code=x.last_error,
            )
            for x in found
        ]
    elif kind == "images":
        query = (
            select(ImageAnalysisJob)
            .where(*scope(ImageAnalysisJob, group_id), ImageAnalysisJob.created_at >= since)
            .order_by(ImageAnalysisJob.created_at.desc(), ImageAnalysisJob.id.desc())
        )
        found = (await db.scalars(query.offset(offset).limit(limit + 1))).all()
        rows = [
            item(
                x.id,
                "Анализ фото",
                x.state,
                x.created_at,
                group_id=x.group_id,
                attempts=x.attempts,
                pass_number=x.pass_number,
                model=x.model_key,
                reused=x.reused_from_job_id is not None,
                error_code=x.error,
            )
            for x in found
        ]
    elif kind == "legacy":
        query = (
            select(AIJob)
            .where(*scope(AIJob, group_id), AIJob.created_at >= since)
            .order_by(AIJob.created_at.desc(), AIJob.id.desc())
        )
        found = (await db.scalars(query.offset(offset).limit(limit + 1))).all()
        rows = [
            item(
                x.id,
                "Задание AI",
                x.state,
                x.created_at,
                group_id=x.group_id,
                attempts=x.attempts,
                error_code=x.last_error,
            )
            for x in found
        ]
    elif kind in ("expenses", "month_expenses", "reserves"):
        financial_since = (
            datetime.now(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            if kind == "month_expenses"
            else since
        )
        criteria = scope(AnalysisBudgetCall, group_id)
        if kind == "reserves":
            criteria += (AnalysisBudgetCall.state != "settled",)
        else:
            criteria += (AnalysisBudgetCall.created_at >= financial_since,)
        query = (
            select(AnalysisBudgetCall)
            .where(*criteria)
            .order_by(AnalysisBudgetCall.created_at.desc(), AnalysisBudgetCall.id.desc())
        )
        found = (await db.scalars(query.offset(offset).limit(limit + 1))).all()
        rows = [
            item(
                x.id,
                "AI · " + x.kind,
                x.state,
                x.created_at,
                group_id=x.group_id,
                charged_usd=x.charged_usd,
                reserved_usd=x.reserved_usd,
                settled_at=x.settled_at,
            )
            for x in found
        ]
    elif kind == "homework":
        query = (
            select(Homework)
            .where(*scope(Homework, group_id), Homework.created_at >= since)
            .order_by(Homework.created_at.desc(), Homework.id.desc())
        )
        found = (await db.scalars(query.offset(offset).limit(limit + 1))).all()
        rows = [
            item(
                x.id,
                x.subject_name or "Домашнее задание",
                x.status,
                x.created_at,
                group_id=x.group_id,
                verification=x.verification_state,
                deadline=x.deadline_at,
            )
            for x in found
        ]
    elif kind == "events":
        query = (
            select(GroupEvent)
            .where(*scope(GroupEvent, group_id), GroupEvent.created_at >= since)
            .order_by(GroupEvent.created_at.desc(), GroupEvent.id.desc())
        )
        found = (await db.scalars(query.offset(offset).limit(limit + 1))).all()
        rows = [
            item(
                x.id,
                "Событие",
                x.state,
                x.created_at,
                group_id=x.group_id,
                published_at=x.published_at,
                revision=x.revision,
            )
            for x in found
        ]
    elif kind == "notifications":
        query = (
            select(Notification)
            .where(*scope(Notification, group_id), Notification.created_at >= since)
            .order_by(Notification.created_at.desc(), Notification.id.desc())
        )
        found = (await db.scalars(query.offset(offset).limit(limit + 1))).all()
        rows = [
            item(
                x.id,
                "Уведомление",
                "создано",
                x.created_at,
                group_id=x.group_id,
                entity_type=x.entity_type,
            )
            for x in found
        ]
    elif kind == "incidents":
        query = (
            select(OwnerIncidentEpisode)
            .where(OwnerIncidentEpisode.recovered_at.is_(None))
            .order_by(OwnerIncidentEpisode.opened_at.desc(), OwnerIncidentEpisode.id.desc())
        )
        found = (await db.scalars(query.offset(offset).limit(limit + 1))).all()
        rows = [
            item(
                x.id,
                x.code,
                "восстановлено" if x.recovered_at else "открыто",
                x.opened_at,
                occurrences=x.occurrences,
                last_seen_at=x.last_seen_at,
                recovered_at=x.recovered_at,
                detail_code=x.last_detail,
            )
            for x in found
        ]
    elif kind == "bot_updates":
        query = (
            select(ReceivedUpdate)
            .where(ReceivedUpdate.received_at >= since)
            .order_by(ReceivedUpdate.received_at.desc(), ReceivedUpdate.update_id.desc())
        )
        found = (await db.scalars(query.offset(offset).limit(limit + 1))).all()
        rows = [
            item(x.update_id, "Обновление от Telegram", "получено", x.received_at)
            for x in found
        ]
    elif kind == "bot_panels":
        query = select(BotPanel).order_by(BotPanel.mode, BotPanel.id)
        found = (await db.scalars(query.offset(offset).limit(limit + 1))).all()
        rows = [
            item(x.id, "Панель бота", x.mode, None,
                 mode=x.mode, generation=x.generation, revision=x.revision)
            for x in found
        ]
    elif kind == "bot_sessions":
        now = datetime.now(UTC)
        query = (
            select(WebSession)
            .where(WebSession.expires_at > now)
            .order_by(WebSession.expires_at.desc(), WebSession.token_hash)
        )
        found = (await db.scalars(query.offset(offset).limit(limit + 1))).all()
        rows = [
            item(i + offset + 1, "Действующая сессия WebApp", "активна", None,
                 expires_at=x.expires_at)
            for i, x in enumerate(found)
        ]
    elif kind == "telegram":
        query = select(BotOutbox).order_by(BotOutbox.available_at.desc(), BotOutbox.id.desc())
        found = (await db.scalars(query.offset(offset).limit(limit + 1))).all()
        rows = [
            item(x.id, "Telegram · " + x.method, x.state, x.available_at, attempts=x.attempts)
            for x in found
        ]
    elif kind == "errors":
        # Independent error sources: do not sum them as distinct incidents.
        merged = []
        for model, timestamp, col, name, extra in [
            (
                ShadowEvaluationRun,
                ShadowEvaluationRun.created_at,
                ShadowEvaluationRun.state,
                "AI · текст",
                ShadowEvaluationRun.last_error,
            ),
            (
                ImageAnalysisJob,
                ImageAnalysisJob.created_at,
                ImageAnalysisJob.state,
                "AI · фото",
                ImageAnalysisJob.error,
            ),
            (AIJob, AIJob.created_at, AIJob.state, "AI · очередь", AIJob.last_error),
        ]:
            criteria = (timestamp >= since, col.in_(BAD_STATES), *scope(model, group_id))
            found = (
                await db.scalars(
                    select(model)
                    .where(*criteria)
                    .order_by(timestamp.desc())
                    .limit(offset + limit + 1)
                )
            ).all()
            merged.extend(
                item(
                    x.id,
                    name,
                    x.state,
                    x.created_at,
                    group_id=x.group_id,
                    error_code=getattr(x, extra.key),
                )
                for x in found
            )
        merged.sort(key=lambda record: record["at"] or "", reverse=True)
        rows = merged[offset : offset + limit + 1]
    else:
        raise ApiError("metric_not_found", "Такой метрики нет", 404)
    has_more = len(rows) > limit
    return {
        "kind": kind,
        "items": rows[:limit],
        "offset": offset,
        "has_more": has_more,
        "period_days": days,
        "note": "Инциденты и исходящий Telegram не ограничены выбранной группой."
        if kind in ("incidents", "telegram", "bot_updates", "bot_panels", "bot_sessions")
        else None,
    }
