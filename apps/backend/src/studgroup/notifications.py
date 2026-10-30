"""Group-scoped real changes and personal read receipts. No fake import announcements."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Header
from sqlalchemy import func, select, true
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from studgroup import subgroups
from studgroup.api import ApiError, active_group, database
from studgroup.homework import user_id
from studgroup.models import AcademicDeadline, Group, Homework, Notification, NotificationRead

router = APIRouter(prefix="/v1")


async def record_change(db, raw, entity, kind, now):
    if raw.imported and raw.revision == 1:
        return
    key = f"{raw.id}:{raw.revision}:{kind}:{entity.id}"
    await db.execute(
        insert(Notification)
        .values(
            id=uuid.uuid5(uuid.NAMESPACE_URL, key),
            group_id=raw.group_id,
            event_key=key,
            title=entity.subject_name if kind == "homework" else entity.subject,
            body=entity.title[:1000],
            entity_type=kind,
            entity_id=entity.id,
            created_at=now,
            delete_at=now + timedelta(days=90),
        )
        .on_conflict_do_nothing(index_elements=[Notification.event_key])
    )


async def own_notifications(db, group_id, user):
    """Condition that drops notifications about another subgroup's cards."""
    hidden = await subgroups.hidden(db, group_id, user)
    if not hidden:
        return true()
    cards = [
        *(await db.scalars(select(Homework.id).where(Homework.subgroup_id.in_(hidden)))).all(),
        *(
            await db.scalars(
                select(AcademicDeadline.id).where(AcademicDeadline.subgroup_id.in_(hidden))
            )
        ).all(),
    ]
    return (
        Notification.entity_id.is_(None) | Notification.entity_id.not_in(cards) if cards else true()
    )


async def inbox(db, group, user):
    now = datetime.now(UTC)
    mine = await own_notifications(db, group.id, user)
    rows = (
        await db.scalars(
            select(Notification)
            .where(Notification.group_id == group.id, Notification.delete_at > now, mine)
            .order_by(Notification.created_at.desc())
            .limit(50)
        )
    ).all()
    read = set(
        (
            await db.scalars(
                select(NotificationRead.notification_id).where(NotificationRead.user_id == user)
            )
        ).all()
    )
    return {
        "generated_at": now,
        "unread_count": await db.scalar(
            select(func.count(Notification.id)).where(
                Notification.group_id == group.id,
                Notification.delete_at > now,
                mine,
                Notification.id.not_in(
                    select(NotificationRead.notification_id).where(NotificationRead.user_id == user)
                ),
            )
        ),
        "items": [
            {
                "id": r.id,
                "title": r.title,
                "body": r.body,
                "entity_type": r.entity_type,
                "entity_id": r.entity_id,
                "created_at": r.created_at,
                "read": r.id in read,
            }
            for r in rows
        ],
    }


@router.get("/notifications")
async def list_notifications(
    db: Annotated[AsyncSession, Depends(database)],
    group: Annotated[Group, Depends(active_group)],
    authorization: Annotated[str | None, Header()] = None,
):
    return await inbox(db, group, await user_id(db, authorization))


@router.post("/notifications/{notification_id}/read")
async def read_one(
    notification_id: uuid.UUID,
    db: Annotated[AsyncSession, Depends(database)],
    group: Annotated[Group, Depends(active_group)],
    authorization: Annotated[str | None, Header()] = None,
):
    """Personal read receipt for one notification of the caller's group."""
    user = await user_id(db, authorization)
    mine = await own_notifications(db, group.id, user)
    exists = await db.scalar(
        select(Notification.id).where(
            Notification.id == notification_id,
            Notification.group_id == group.id,
            Notification.delete_at > datetime.now(UTC),
            mine,
        )
    )
    if exists is None:
        raise ApiError("not_found", "Уведомление не найдено", 404)
    await db.execute(
        insert(NotificationRead)
        .values(id=uuid.uuid4(), notification_id=exists, user_id=user, read_at=datetime.now(UTC))
        .on_conflict_do_nothing(
            index_elements=[NotificationRead.notification_id, NotificationRead.user_id]
        )
    )
    await db.commit()
    return await inbox(db, group, user)


@router.post("/notifications/read-all")
async def read_all(
    db: Annotated[AsyncSession, Depends(database)],
    group: Annotated[Group, Depends(active_group)],
    authorization: Annotated[str | None, Header()] = None,
):
    user = await user_id(db, authorization)
    snapshot = await inbox(db, group, user)
    ids = (
        await db.scalars(
            select(Notification.id).where(
                Notification.group_id == group.id,
                Notification.delete_at > snapshot["generated_at"],
                Notification.created_at <= snapshot["generated_at"],
                await own_notifications(db, group.id, user),
            )
        )
    ).all()
    for id in ids:
        await db.execute(
            insert(NotificationRead)
            .values(id=uuid.uuid4(), notification_id=id, user_id=user, read_at=datetime.now(UTC))
            .on_conflict_do_nothing(
                index_elements=[NotificationRead.notification_id, NotificationRead.user_id]
            )
        )
    await db.commit()
    return await inbox(db, group, user)
