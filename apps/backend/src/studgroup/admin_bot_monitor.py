"""Owner-only bot telemetry backed by existing Telegram and application tables.

Webhook updates, outbox and panels are system-wide (no group_id in those tables).
A live session is not a historical login. No private payloads or user IDs leave
this endpoint. All reads are bounded and do not incur AI provider costs.
"""

from datetime import UTC, datetime, timedelta
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from studgroup.admin_dashboard import admin_session
from studgroup.admin_insights import ensure_group, scope, stamp, total
from studgroup.admin_visuals import day_values
from studgroup.api import database
from studgroup.models import (
    BotOutbox,
    BotPanel,
    Group,
    Membership,
    RawMessage,
    ReceivedUpdate,
    User,
    WebSession,
)

router = APIRouter()


@router.get("/v1/admin/bot-monitor")
async def bot_monitor(
    db: Annotated[AsyncSession, Depends(database)],
    _owner: Annotated[User, Depends(admin_session)],
    days: int = Query(7, ge=1, le=90),
    group_id: UUID | None = None,
):
    await ensure_group(db, group_id)
    now = datetime.now(UTC)
    start_date = now.date() - timedelta(days=days - 1)
    since = datetime.combine(start_date, datetime.min.time(), tzinfo=UTC)
    message_scope = (*scope(RawMessage, group_id), RawMessage.message_date >= since)
    received_scope = (ReceivedUpdate.received_at >= since,)

    webhook_count = await total(db, ReceivedUpdate, received_scope)
    incoming_latest = await db.scalar(select(func.max(ReceivedUpdate.received_at)))
    messages_latest = await db.scalar(
        select(func.max(RawMessage.message_date)).where(*scope(RawMessage, group_id))
    )
    imported = await total(db, RawMessage, (*message_scope, RawMessage.imported.is_(True)))
    unique_senders = await db.scalar(
        select(func.count(func.distinct(RawMessage.sender_id)))
        .where(*message_scope, RawMessage.sender_id.is_not(None))
    ) or 0
    active_memberships = await total(
        db, Membership, (Membership.status == "active", *scope(Membership, group_id))
    )

    outgoing = (await db.execute(
        select(BotOutbox.state, func.count())
        .group_by(BotOutbox.state)
        .order_by(func.count().desc())
    )).all()
    outbox = {str(state or "unknown"): int(count) for state, count in outgoing}
    pending = sum(value for key, value in outbox.items() if key in ("pending", "retry", "queued", "sending"))
    failed = sum(value for key, value in outbox.items() if key in ("failed", "dead", "dead_letter"))
    sent = outbox.get("sent", 0)
    panel_counts = (await db.execute(
        select(BotPanel.mode, func.count()).group_by(BotPanel.mode)
    )).all()
    panel_states = {str(mode or "unknown"): int(count) for mode, count in panel_counts}
    sessions = (WebSession.expires_at > now,)
    active_sessions = await total(db, WebSession, sessions)
    session_users = await db.scalar(
        select(func.count(func.distinct(WebSession.user_id))).where(*sessions)
    ) or 0
    users_total = await total(db, User)
    all_groups = (await db.scalars(select(Group).order_by(Group.name))).all()

    webhook_daily = await day_values(
        db, ReceivedUpdate, ReceivedUpdate.received_at, received_scope
    )
    messages_daily = await day_values(
        db, RawMessage, RawMessage.message_date, message_scope
    )
    dates = [(start_date + timedelta(days=i)).isoformat() for i in range(days)]

    return {
        "as_of": stamp(now),
        "period_days": days,
        "source_notes": {
            "webhook": "Все Telegram-обновления всего бота за выбранный период. Не число сообщений.",
            "outbox": "Состояния всей очереди отправки за всё время: таблица не содержит даты отправки.",
            "panels": "Текущее количество панелей бота по типам, не число их открытий.",
            "sessions": "Только действующие сессии WebApp, не история входов.",
            "messages": "Сообщения подключённых групп за период, включая импортированные.",
        },
        "bot": {
            "webhook_updates": webhook_count,
            "last_webhook_at": stamp(incoming_latest),
            "outbox_total": sum(outbox.values()),
            "sent": sent,
            "failed": failed,
            "pending": pending,
            "outbox_states": outbox,
            "panel_total": sum(panel_states.values()),
            "panel_modes": panel_states,
        },
        "people": {
            "users_total": users_total,
            "active_memberships": active_memberships,
            "active_sessions": active_sessions,
            "session_users": int(session_users),
            "unique_senders": int(unique_senders),
        },
        "messages": {
            "period_count": await total(db, RawMessage, message_scope),
            "imported": imported,
            "new": await total(db, RawMessage, (*message_scope, RawMessage.imported.is_(False))),
            "last_at": stamp(messages_latest),
        },
        "daily": [
            {"date": date, "updates": webhook_daily.get(date, 0), "messages": messages_daily.get(date, 0)}
            for date in dates
        ],
        "groups": [
            {"id": str(g.id), "name": g.name, "active": g.status == "active",
             "pilot": bool(g.pilot_authorized)}
            for g in all_groups
            if group_id is None or g.id == group_id
        ],
    }
