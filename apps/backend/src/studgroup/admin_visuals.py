"""Additional read-only diagrams for the owner console.

All numbers come from existing operational records and are scoped before
aggregation. Missing instrumentation is represented as unavailable, never 0.
"""

from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import extract, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from studgroup.admin_dashboard import admin_session
from studgroup.admin_insights import BAD_STATES, ensure_group, money, scope
from studgroup.api import database
from studgroup.models import (
    AIJob,
    AnalysisBudgetCall,
    GroupEvent,
    Homework,
    ImageAnalysisCall,
    ImageAnalysisJob,
    Notification,
    RawMessage,
    ShadowEvaluationRun,
    User,
)

router = APIRouter()


def day_expression(db, column):
    return (
        func.date(column)
        if db.get_bind().dialect.name == "sqlite"
        else func.date_trunc("day", column)
    )


async def day_values(db, model, column, criteria, *, total_column=None):
    bucket = day_expression(db, column)
    aggregate = func.sum(total_column) if total_column is not None else func.count()
    rows = (
        await db.execute(
            select(bucket, aggregate)
            .select_from(model)
            .where(*criteria)
            .group_by(bucket)
            .order_by(bucket)
        )
    ).all()
    return {
        (value.date().isoformat() if hasattr(value, "date") else str(value)[:10]): money(amount)
        if total_column is not None
        else int(amount)
        for value, amount in rows
    }


async def grouped_errors(db, model, when, state, error, criteria, category):
    rows = (
        await db.execute(
            select(error, func.count())
            .select_from(model)
            .where(*criteria, state.in_(BAD_STATES), error.is_not(None))
            .group_by(error)
            .order_by(func.count().desc())
            .limit(12)
        )
    ).all()
    return [
        {"source": category, "code": str(code)[:70], "count": int(count)}
        for code, count in rows
        if code
    ]


@router.get("/v1/admin/visuals")
async def visual_analytics(
    request: Request,
    db: Annotated[AsyncSession, Depends(database)],
    _owner: Annotated[User, Depends(admin_session)],
    days: int = Query(7, ge=1, le=90),
    group_id: UUID | None = None,
):
    await ensure_group(db, group_id)
    now = datetime.now(UTC)
    first_day = now.date() - timedelta(days=days - 1)
    since = datetime(first_day.year, first_day.month, first_day.day, tzinfo=UTC)
    month_since = datetime(now.year, now.month, 1, tzinfo=UTC)

    message_filter = (*scope(RawMessage, group_id), RawMessage.message_date >= since)
    text_filter = (*scope(ShadowEvaluationRun, group_id), ShadowEvaluationRun.created_at >= since)
    image_filter = (*scope(ImageAnalysisJob, group_id), ImageAnalysisJob.created_at >= since)
    legacy_filter = (*scope(AIJob, group_id), AIJob.created_at >= since)
    homework_filter = (*scope(Homework, group_id), Homework.created_at >= since)
    events_filter = (*scope(GroupEvent, group_id), GroupEvent.created_at >= since)
    notices_filter = (*scope(Notification, group_id), Notification.created_at >= since)
    image_calls_filter = (
        *scope(ImageAnalysisCall, group_id),
        ImageAnalysisCall.created_at >= since,
    )
    month_filter = (
        *scope(AnalysisBudgetCall, group_id),
        AnalysisBudgetCall.created_at >= month_since,
    )

    daily_sources = {
        "messages": await day_values(db, RawMessage, RawMessage.message_date, message_filter),
        "text": await day_values(
            db, ShadowEvaluationRun, ShadowEvaluationRun.created_at, text_filter
        ),
        "images": await day_values(db, ImageAnalysisJob, ImageAnalysisJob.created_at, image_filter),
        "homework": await day_values(db, Homework, Homework.created_at, homework_filter),
        "events": await day_values(db, GroupEvent, GroupEvent.created_at, events_filter),
        "notifications": await day_values(
            db, Notification, Notification.created_at, notices_filter
        ),
        "text_errors": await day_values(
            db,
            ShadowEvaluationRun,
            ShadowEvaluationRun.created_at,
            (*text_filter, ShadowEvaluationRun.state.in_(BAD_STATES)),
        ),
        "image_errors": await day_values(
            db,
            ImageAnalysisJob,
            ImageAnalysisJob.created_at,
            (*image_filter, ImageAnalysisJob.state.in_(BAD_STATES)),
        ),
        "legacy_errors": await day_values(
            db,
            AIJob,
            AIJob.created_at,
            (*legacy_filter, AIJob.state.in_(BAD_STATES)),
        ),
    }

    # Means are calculated from SUM/COUNT, not from the sum of durations.
    async def latency_means(model, when, latency, criteria):
        bucket = day_expression(db, when)
        rows = (
            await db.execute(
                select(bucket, func.avg(latency))
                .select_from(model)
                .where(*criteria, latency.is_not(None))
                .group_by(bucket)
                .order_by(bucket)
            )
        ).all()
        return {
            date.date().isoformat() if hasattr(date, "date") else str(date)[:10]: round(
                float(value)
            )
            for date, value in rows
            if value is not None
        }

    latency_text = await latency_means(
        ShadowEvaluationRun,
        ShadowEvaluationRun.created_at,
        ShadowEvaluationRun.latency_ms,
        text_filter,
    )
    latency_photo = await latency_means(
        ImageAnalysisCall,
        ImageAnalysisCall.created_at,
        ImageAnalysisCall.latency_ms,
        image_calls_filter,
    )

    hours = (
        await db.execute(
            select(extract("hour", RawMessage.message_date), func.count())
            .where(*message_filter)
            .group_by(extract("hour", RawMessage.message_date))
        )
    ).all()
    by_hour = {int(hour): int(count) for hour, count in hours}
    spend_rows = (
        await db.execute(
            select(AnalysisBudgetCall.kind, func.sum(AnalysisBudgetCall.charged_usd))
            .where(*month_filter)
            .group_by(AnalysisBudgetCall.kind)
            .order_by(func.sum(AnalysisBudgetCall.charged_usd).desc())
        )
    ).all()

    errors = (
        await grouped_errors(
            db,
            ShadowEvaluationRun,
            ShadowEvaluationRun.created_at,
            ShadowEvaluationRun.state,
            ShadowEvaluationRun.last_error,
            text_filter,
            "Текст",
        )
        + await grouped_errors(
            db,
            ImageAnalysisJob,
            ImageAnalysisJob.created_at,
            ImageAnalysisJob.state,
            ImageAnalysisJob.error,
            image_filter,
            "Фото",
        )
        + await grouped_errors(
            db,
            AIJob,
            AIJob.created_at,
            AIJob.state,
            AIJob.last_error,
            legacy_filter,
            "Старая очередь",
        )
    )
    dates = [first_day + timedelta(days=i) for i in range(days)]
    daily = []
    for date in dates:
        key = date.isoformat()
        point = {"date": key}
        for name, values in daily_sources.items():
            if name not in ("text_latency_ms", "photo_latency_ms"):
                point[name] = values.get(key, 0)
        point["text_latency_ms"] = latency_text.get(key)
        point["photo_latency_ms"] = latency_photo.get(key)
        daily.append(point)

    month_spend = await day_values(
        db,
        AnalysisBudgetCall,
        AnalysisBudgetCall.created_at,
        month_filter,
        total_column=AnalysisBudgetCall.charged_usd,
    )
    month_dates = (now.date() - month_since.date()).days + 1
    month_series = [
        {
            "date": (month_since.date() + timedelta(days=i)).isoformat(),
            "value": month_spend.get((month_since.date() + timedelta(days=i)).isoformat(), 0),
        }
        for i in range(month_dates)
    ]
    previous_since = since - timedelta(days=days)

    async def preceding_count(model, timestamp, *extra):
        result = await db.scalar(
            select(func.count())
            .select_from(model)
            .where(
                *scope(model, group_id),
                timestamp >= previous_since,
                timestamp < since,
                *extra,
            )
        )
        return int(result or 0)

    comparison = {}
    for name, model, timestamp in [
        ("messages", RawMessage, RawMessage.message_date),
        ("text", ShadowEvaluationRun, ShadowEvaluationRun.created_at),
        ("images", ImageAnalysisJob, ImageAnalysisJob.created_at),
        ("homework", Homework, Homework.created_at),
    ]:
        comparison[name] = {
            "current": sum(int(row[name]) for row in daily),
            "previous": await preceding_count(model, timestamp),
        }
    comparison["errors"] = {
        "current": sum(
            int(row["text_errors"] + row["image_errors"] + row["legacy_errors"]) for row in daily
        ),
        "previous": (
            await preceding_count(
                ShadowEvaluationRun,
                ShadowEvaluationRun.created_at,
                ShadowEvaluationRun.state.in_(BAD_STATES),
            )
            + await preceding_count(
                ImageAnalysisJob,
                ImageAnalysisJob.created_at,
                ImageAnalysisJob.state.in_(BAD_STATES),
            )
            + await preceding_count(
                AIJob,
                AIJob.created_at,
                AIJob.state.in_(BAD_STATES),
            )
        ),
    }

    return {
        "as_of": now.isoformat(),
        "period_days": days,
        "comparison": comparison,
        "daily": daily,
        "by_hour_utc": [{"hour": i, "value": by_hour.get(i, 0)} for i in range(24)],
        "error_reasons": sorted(errors, key=lambda r: r["count"], reverse=True)[:10],
        "finance_month": {
            "series": month_series,
            "by_kind": [{"kind": kind, "usd": money(total)} for kind, total in spend_rows],
            "label": "Текущий календарный месяц · UTC · только журнал AI",
        },
        "server": request.app.state.owner_http_telemetry.snapshot(),
        "definitions": {
            "images": "Количество проходов обработки фото (1 фото может иметь два прохода).",
            "stale": "Устаревший результат фото после обновления контекста; не ошибка.",
            "activity": "Сообщения Telegram-чата. Не измеряет открытия WebApp.",
            "latency": "Время выполнения AI/провайдерских вызовов, не скорость WebApp.",
        },
    }
