"""Opt-in private testing feed. Sources are persisted events, never chat instructions."""

import json
import uuid
from datetime import UTC, datetime
from urllib.parse import urlsplit

from sqlalchemy import String, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from studgroup.models import AICandidate, BotOutbox, Group, HeadmanAudit, Notification

LABELS = {"homework": "ДЗ", "deadline": "КТ / событие", "schedule": "Расписание"}
OPERATIONS = {
    "create": "Создана карточка",
    "material": "Изменены материалы",
    "description": "Изменено описание",
    "cancel": "Отменено",
    "confirm": "Подтверждено",
    "undo": "Изменение отменено",
    "convert": "Изменён тип карточки",
    "merge": "Карточки объединены",
    "defer": "Отложена проверка",
    "schedule": "Изменено расписание",
    "schedule_add": "Добавлена пара",
    "edit": "Карточка изменена",
}


async def queue(db, settings, event_key, group, text, kind=None, entity_id=None):
    payload = {
        "chat_id": settings.owner_telegram_user_id,
        "text": f"Тестирование StudGroup · {group.name}\n{text}"[:3900],
        "link_preview_options": {"is_disabled": True},
    }
    origin = urlsplit(settings.webapp_origin)
    if (
        origin.scheme == "https"
        and origin.hostname
        and not origin.username
        and not origin.password
        and not origin.query
        and not origin.fragment
    ):
        path = (
            f"/homework/{entity_id}"
            if kind == "homework"
            else f"/deadlines/{entity_id}"
            if kind == "deadline"
            else "/schedule"
            if kind == "schedule"
            else "/today"
        )
        payload["reply_markup"] = {
            "inline_keyboard": [
                [
                    {
                        "text": "Открыть приложение",
                        "web_app": {"url": settings.webapp_origin.rstrip("/") + path},
                    }
                ]
            ]
        }
    key = f"owner-test:{settings.owner_telegram_user_id}:{event_key}"
    await db.execute(
        insert(BotOutbox)
        .values(
            id=uuid.uuid5(uuid.NAMESPACE_URL, key),
            method="sendMessage",
            payload=json.dumps(payload, ensure_ascii=False),
            state="pending",
            attempts=0,
            available_at=datetime.now(UTC),
            dedup_key=key,
        )
        .on_conflict_do_nothing(index_elements=[BotOutbox.dedup_key])
    )


async def tick(engine, settings):
    owner = settings.owner_telegram_user_id
    since = settings.owner_update_notifications_since
    if not settings.owner_update_notifications_enabled or not owner or owner <= 0 or since is None:
        return
    async with AsyncSession(engine) as db:
        groups = {
            g.id: g
            for g in (
                await db.scalars(
                    select(Group).where(Group.status == "active", Group.pilot_authorized.is_(True))
                )
            ).all()
        }
        if not groups:
            return

        # Limit unsent sources, not the first N events forever. Sent/queued keys
        # are excluded so large bursts drain without replay after restart.
        def unseen(prefix, id_column):
            return (
                ~select(BotOutbox.id)
                .where(
                    func.lower(func.replace(BotOutbox.dedup_key, "-", ""))
                    == f"ownertest:{owner}:{prefix}:"
                    + func.lower(func.replace(id_column.cast(String), "-", ""))
                )
                .exists()
            )

        events = (
            await db.scalars(
                select(Notification)
                .where(
                    Notification.group_id.in_(groups),
                    Notification.created_at >= since,
                    Notification.delete_at > datetime.now(UTC),
                    ~Notification.event_key.like("manual:%"),
                    unseen("notice", Notification.id),
                )
                .order_by(Notification.created_at, Notification.id)
                .limit(100)
            )
        ).all()
        for event in events:
            await queue(
                db,
                settings,
                f"notice:{event.id}",
                groups[event.group_id],
                f"{LABELS.get(event.entity_type, 'Обновление')} · новая или изменённая карточка\n{event.title}\n{event.body}",
                event.entity_type,
                event.entity_id,
            )
        audits = (
            await db.scalars(
                select(HeadmanAudit)
                .where(
                    HeadmanAudit.group_id.in_(groups),
                    HeadmanAudit.created_at >= since,
                    unseen("audit", HeadmanAudit.id),
                )
                .order_by(HeadmanAudit.created_at, HeadmanAudit.id)
                .limit(100)
            )
        ).all()
        for audit in audits:
            after = json.loads(audit.after)
            lines = [OPERATIONS.get(audit.operation, f"Изменение: {audit.operation}")]
            for key, snapshot in after.items():
                values = snapshot.get("fields", snapshot) if snapshot else {}
                lines.append(str(values.get("subject_name") or values.get("subject") or key))
                if values.get("title"):
                    lines.append(str(values["title"]))
                if values.get("description"):
                    lines.append(str(values["description"])[:600])
                if values.get("deadline_at"):
                    lines.append(f"Срок: {values['deadline_at']}")
                if values.get("window_start") or values.get("window_end"):
                    lines.append(
                        f"Период: {values.get('window_start')} — {values.get('window_end')}"
                    )
                for material in (snapshot or {}).get("materials", []):
                    lines.append(f"Материал: {material.get('title', '')}")
            kind = (
                "homework"
                if audit.entity_key.startswith("h:")
                else "deadline"
                if audit.entity_key.startswith("e:")
                else "schedule"
                if audit.entity_key.startswith(("p:", "x:"))
                else None
            )
            entity_id = audit.entity_key.split(":", 1)[1] if ":" in audit.entity_key else None
            await queue(
                db,
                settings,
                f"audit:{audit.id}",
                groups[audit.group_id],
                "\n".join(lines),
                kind,
                entity_id,
            )
        candidates = (
            await db.scalars(
                select(AICandidate)
                .where(
                    AICandidate.group_id.in_(groups),
                    AICandidate.created_at >= since,
                    AICandidate.state == "review",
                    AICandidate.delete_at > datetime.now(UTC),
                    unseen("candidate", AICandidate.id),
                )
                .order_by(AICandidate.created_at, AICandidate.id)
                .limit(100)
            )
        ).all()
        for candidate in candidates:
            data = json.loads(candidate.payload)
            await queue(
                db,
                settings,
                f"candidate:{candidate.id}",
                groups[candidate.group_id],
                f"ИИ нашёл данные — требует проверки\n{data.get('subject') or 'Предмет не определён'}\n{data.get('title') or 'Новая находка'}\n{str(data.get('description') or '')[:1000]}",
            )
        await db.commit()
