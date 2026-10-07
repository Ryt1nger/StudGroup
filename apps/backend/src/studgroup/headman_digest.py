"""At most two non-empty personal digests; SQL receipts prevent duplicate questions."""

import uuid
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from studgroup import headman_domain as domain
from studgroup.bot_admin import enqueue
from studgroup.homework import utc
from studgroup.models import (
    BotOutbox,
    Group,
    HeadmanQuestionDelivery,
    HeadmanSettings,
    Membership,
    User,
)
from studgroup.security import token_hash


def waiting_minutes(at, now):
    if at is None:
        return 30
    remaining = (utc(at) - now).total_seconds()
    if remaining <= 3 * 3600:
        return 15
    if remaining <= 12 * 3600:
        return 30
    if remaining <= 24 * 3600:
        return 60
    return None


async def tick(engine, now=None):
    now = now or datetime.now(UTC)
    async with AsyncSession(engine, expire_on_commit=False) as db:
        configs = (
            await db.execute(
                select(HeadmanSettings, Membership, User, Group)
                .join(Membership, Membership.id == HeadmanSettings.membership_id)
                .join(User, User.id == Membership.user_id)
                .join(Group, Group.id == Membership.group_id)
                .where(
                    HeadmanSettings.questions_enabled.is_(True),
                    Membership.status == "active",
                    Membership.role.in_(["headman", "deputy"]),
                    Group.pilot_authorized.is_(True),
                    Group.status == "active",
                )
                .order_by(Membership.id)
                .with_for_update(skip_locked=True)
            )
        ).all()
        by_group = {}
        for config, member, user, group in configs:
            by_group.setdefault(group.id, []).append((config, member, user, group))
        for entries in by_group.values():
            group = entries[0][3]
            local = now.astimezone(ZoneInfo(group.timezone))
            if not 7 <= local.hour < 23:
                continue
            from studgroup.headman_bot import candidates

            questions = await domain.cards(db, group, "review", now=now) + await candidates(
                db, group, now
            )
            if not questions:
                continue
            questions.sort(
                key=lambda item: (
                    utc(item[2].deadline_at)
                    if getattr(item[2], "deadline_at", None)
                    else now + timedelta(days=365),
                    item[0],
                )
            )
            deputies = [entry for entry in entries if entry[1].role == "deputy"]
            heads = [entry for entry in entries if entry[1].role == "headman"]
            for config, member, user, _ in entries:
                times = config.digest_times.split(",")[:2]
                slots = [t for t in times if local.strftime("%H:%M") >= t]
                # After a server sleep, catch up only the latest due slot, never send two at once.
                slot = slots[-1] if slots else "none"
                batch_key = f"st-digest:{member.id}:{local.date()}:{slot}"
                regular = bool(
                    slots
                    and local.strftime("%H:%M") <= "22:00"
                    and not await db.scalar(
                        select(BotOutbox.id).where(BotOutbox.dedup_key == batch_key)
                    )
                )
                selected = []
                for index, (key, kind, row) in enumerate(questions):
                    at = getattr(row, "deadline_at", None)
                    urgent = bool(
                        at
                        and now < utc(at) <= now + timedelta(hours=24)
                        and (kind == "e" or getattr(row, "urgency", "normal") == "urgent")
                    )
                    if not regular and not urgent:
                        continue
                    delivered = await db.scalar(
                        select(HeadmanQuestionDelivery)
                        .where(
                            HeadmanQuestionDelivery.entity_key == key,
                            HeadmanQuestionDelivery.revision == row.revision,
                        )
                        .with_for_update()
                    )
                    if delivered:
                        original = await db.get(Membership, delivered.membership_id)
                        wait = waiting_minutes(getattr(row, "deadline_at", None), now)
                        if (
                            not heads
                            or member.role != "headman"
                            or delivered.escalated
                            or not original
                            or original.role != "deputy"
                            or wait is not None
                            and utc(delivered.sent_at) + timedelta(minutes=wait) > now
                        ):
                            continue
                        delivered.escalated = True
                    else:
                        # Across each three questions, one goes to the headman and two to deputies.
                        recipients = (
                            deputies
                            if deputies and (not heads or index % 3 != 0)
                            else heads or entries
                        )
                        recipient = recipients[(index // 3) % len(recipients)]
                        if recipient[1].id != member.id:
                            continue
                        outcome = await db.execute(
                            insert(HeadmanQuestionDelivery)
                            .values(
                                id=uuid.uuid4(),
                                group_id=group.id,
                                entity_key=key,
                                revision=row.revision,
                                membership_id=member.id,
                                sent_at=now,
                                escalated=False,
                            )
                            .on_conflict_do_nothing(
                                index_elements=[
                                    HeadmanQuestionDelivery.entity_key,
                                    HeadmanQuestionDelivery.revision,
                                ]
                            )
                            .returning(HeadmanQuestionDelivery.id)
                        )
                        if outcome.scalar_one_or_none() is None:
                            continue
                    selected.append((key, kind, row))
                    if len(selected) >= 4:
                        break
                if not selected:
                    continue
                if not regular:
                    fingerprint = token_hash(
                        "|".join(f"{key}:{row.revision}" for key, _, row in selected)
                    )[:32]
                    batch_key = f"st-urgent:{member.id}:{fingerprint}"
                lines = []
                for _, kind, row in selected:
                    if kind == "c":
                        lines.append(
                            "• Неопубликованный черновик: нужно проверить предмет/задачу/срок."
                        )
                    else:
                        subject = row.subject_name if kind == "h" else row.subject
                        lines.append(f"• {subject}: {row.title[:160]}")
                enqueue(
                    db,
                    "sendMessage",
                    {
                        "chat_id": user.telegram_user_id,
                        "text": f"{group.name} · вопросы для проверки\n\n"
                        + "\n".join(lines)
                        + "\n\nОткрой /st → Требует проверки.",
                    },
                    dedup_key=batch_key,
                )
        await db.commit()
