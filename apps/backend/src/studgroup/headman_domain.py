"""Group-scoped manual decisions. No provider calls; mutations, audit and inbox are atomic."""

import json
import secrets
import uuid
from datetime import UTC, date, datetime, time, timedelta
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import httpx
import sqlalchemy as sa
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from studgroup.academic_deadlines import archived
from studgroup.homework import archived_condition, utc
from studgroup.models import (
    AcademicDeadline,
    AICandidate,
    Group,
    GroupInvitation,
    HeadmanAudit,
    HeadmanDecision,
    Homework,
    MaterialLink,
    Membership,
    Notification,
    ScheduleException,
    SchedulePattern,
    User,
)
from studgroup.security import token_hash

MODELS = {
    "h": Homework,
    "e": AcademicDeadline,
    "p": SchedulePattern,
    "x": ScheduleException,
    "c": AICandidate,
}
FIELDS = {
    "c": ["state", "revision"],
    "h": [
        "subject_name",
        "subject_id",
        "title",
        "description",
        "deadline_at",
        "deadline_date_only",
        "status",
        "urgency",
        "verification_state",
        "cancelled_at",
        "updated_at",
        "significant_updated_at",
        "revision",
    ],
    "e": [
        "kind",
        "subject",
        "title",
        "description",
        "deadline_at",
        "date_only",
        "window_start",
        "window_end",
        "date_hint",
        "needs_clarification",
        "cancelled_at",
        "revision",
    ],
    "p": [
        "subject",
        "weekday",
        "starts",
        "ends",
        "valid_from",
        "valid_until",
        "week",
        "teacher",
        "location",
        "online_url",
        "cancelled",
        "revision",
    ],
    "x": [
        "pattern_id",
        "occurrence_date",
        "starts_at",
        "ends_at",
        "location",
        "online_url",
        "cancelled",
        "revision",
    ],
}


class StError(Exception):
    pass


def dumps(value):
    return json.dumps(
        value,
        ensure_ascii=False,
        default=lambda x: x.isoformat() if hasattr(x, "isoformat") else str(x),
    )


def safe_url(value):
    value = value.strip()
    p = urlsplit(value)
    if (
        len(value) > 2048
        or p.scheme != "https"
        or not p.hostname
        or p.username
        or p.password
        or any(c.isspace() for c in value)
    ):
        raise StError("Нужна обычная HTTPS-ссылка без логина и пароля.")
    return value


async def memberships(db, telegram_id):
    return (
        await db.execute(
            select(User, Membership, Group)
            .join(Membership, Membership.user_id == User.id)
            .join(Group, Group.id == Membership.group_id)
            .where(
                User.telegram_user_id == telegram_id,
                Membership.role.in_(["headman", "deputy"]),
                Membership.status == "active",
                Group.status == "active",
                Group.pilot_authorized.is_(True),
            )
            .order_by(Group.name)
        )
    ).all()


async def access(db, telegram_id, group_id):
    rows = await memberships(db, telegram_id)
    row = next((r for r in rows if r[2].id == group_id), None)
    if row is None:
        raise StError("Нет прав старосты/помощника в этой группе. Доступ мог измениться.")
    # Serialize with role revocation. Re-read locked state rather than trusting the menu.
    member = await db.scalar(
        select(Membership)
        .where(Membership.id == row[1].id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if member.status != "active" or member.role not in {"headman", "deputy"}:
        raise StError("Доступ к группе изменился.")
    group = await db.scalar(
        select(Group)
        .where(Group.id == group_id)
        .with_for_update(key_share=True)
        .execution_options(populate_existing=True)
    )
    if not group or group.status != "active" or not group.pilot_authorized:
        raise StError("Группа больше недоступна.")
    return row[0], member, group


def headman_only(member):
    if member.role != "headman":
        raise StError("Участниками и настройками группы управляет староста.")


async def entity(db, group, key):
    try:
        kind, ident = key.split(":", 1)
        cls = MODELS[kind]
        row = await db.scalar(
            select(cls)
            .where(cls.id == uuid.UUID(ident), cls.group_id == group.id)
            .with_for_update()
        )
    except (ValueError, KeyError):
        row = None
    if row is None or (hasattr(row, "delete_at") and utc(row.delete_at) <= datetime.now(UTC)):
        raise StError("Карточка больше недоступна. Обнови список.")
    return kind, row


async def guard(db, group, member, key, row, expected):
    if row.revision != expected:
        raise StError("Карточка уже изменилась. Открой её заново — старую правку не применяю.")
    decision = await db.get(HeadmanDecision, key)
    if decision and decision.group_id != group.id:
        raise StError("Недоступная карточка.")
    if (
        decision
        and decision.locked
        and decision.actor_role == "headman"
        and member.role == "deputy"
    ):
        raise StError("Это подтверждённое решение старосты. Изменить его может староста.")


async def snapshot(db, key, kind, row):
    if row is None:
        return None
    decision = await db.get(HeadmanDecision, key, populate_existing=True)
    material = (
        await db.scalars(
            select(MaterialLink).where(
                MaterialLink.group_id == row.group_id, MaterialLink.entity_key == key
            )
        )
    ).all()
    return {
        "fields": {field: getattr(row, field) for field in FIELDS[kind]},
        "materials": [{"id": str(m.id), "title": m.title, "url": m.url} for m in material],
        "decision": {
            "actor_id": str(decision.actor_id),
            "actor_role": decision.actor_role,
            "locked": decision.locked,
            "deferred_until": decision.deferred_until,
        }
        if decision
        else None,
    }


async def lock_decision(db, group, user, member, key, row, deferred=None):
    await db.execute(
        insert(HeadmanDecision)
        .values(
            entity_key=key,
            group_id=group.id,
            actor_id=user.id,
            actor_role=member.role,
            locked=deferred is None,
            deferred_until=deferred,
            revision=row.revision,
        )
        .on_conflict_do_update(
            index_elements=[HeadmanDecision.entity_key],
            set_={
                "actor_id": user.id,
                "actor_role": member.role,
                "locked": deferred is None,
                "deferred_until": deferred,
                "revision": row.revision,
            },
        )
    )


async def save_audit(db, group, user, operation, before, rows, notify=True):
    after = {}
    aid = uuid.uuid4()
    for key, kind, row in rows:
        after[key] = await snapshot(db, key, kind, row)
        if notify and kind != "c":
            db.add(
                Notification(
                    id=uuid.uuid5(aid, key),
                    group_id=group.id,
                    event_key=f"manual:{aid}:{key}",
                    title=row.subject_name
                    if kind == "h"
                    else row.subject
                    if kind in {"e", "p"}
                    else "Расписание",
                    body=(row.title if kind in {"h", "e"} else "Изменение пары")[:1000],
                    entity_type="homework"
                    if kind == "h"
                    else "deadline"
                    if kind == "e"
                    else "schedule",
                    entity_id=row.id,
                    created_at=datetime.now(UTC),
                    delete_at=datetime.now(UTC) + timedelta(days=90),
                )
            )
    audit = HeadmanAudit(
        id=aid,
        group_id=group.id,
        actor_id=user.id,
        entity_key=rows[0][0],
        operation=operation,
        before=dumps(before),
        after=dumps(after),
        created_at=datetime.now(UTC),
    )
    db.add(audit)
    await db.flush()
    return aid


async def cards(db, group, mode="active", search="", now=None):
    now = now or datetime.now(UTC)
    hw = (
        await db.scalars(
            select(Homework)
            .where(
                Homework.group_id == group.id,
                Homework.delete_at > now,
                Homework.status.in_(["published", "needs_clarification", "completed", "cancelled"]),
                archived_condition(now, group.timezone)
                if mode == "archive"
                else ~archived_condition(now, group.timezone),
            )
            .order_by(Homework.created_at.desc())
        )
    ).all()
    ev = (
        await db.scalars(
            select(AcademicDeadline)
            .where(AcademicDeadline.group_id == group.id, AcademicDeadline.delete_at > now)
            .order_by(AcademicDeadline.created_at.desc())
        )
    ).all()
    rows = [(f"h:{r.id}", "h", r) for r in hw] + [
        (f"e:{r.id}", "e", r) for r in ev if archived(r, now, group.timezone) == (mode == "archive")
    ]
    result = []
    for key, kind, row in rows:
        if (
            search
            and search.casefold()
            not in ((row.subject_name if kind == "h" else row.subject) + " " + row.title).casefold()
        ):
            continue
        decision = await db.get(HeadmanDecision, key)
        if mode == "review":
            if decision and (
                decision.locked or decision.deferred_until and utc(decision.deferred_until) > now
            ):
                continue
            if (
                kind == "h"
                and row.status != "needs_clarification"
                and row.verification_state != "inferred"
                and row.verification_state != "needs_clarification"
                and row.deadline_at is not None
            ):
                continue
            if kind == "e" and not row.needs_clarification:
                continue
        result.append((key, kind, row))
    return result


async def create_card(db, group, user, member, kind, values, source_date=None):
    now = datetime.now(UTC)
    if kind == "h" and values.get("date_only"):
        from studgroup.homework_timing import lesson_deadline

        values["deadline_at"], values["date_only"] = await lesson_deadline(
            db, group, values["subject"], values.get("deadline_at"), True, ""
        )
    if kind == "h":
        row = Homework(
            group_id=group.id,
            subject_id=uuid.uuid5(group.id, values["subject"].casefold()),
            subject_name=values["subject"],
            title=values["title"],
            description=values["description"],
            deadline_at=values.get("deadline_at"),
            deadline_date_only=values.get("date_only", False),
            verification_state="manual_confirmed",
            status="published",
            created_at=now,
            updated_at=now,
            source_message_at=source_date or now,
            delete_at=now + timedelta(days=90),
            revision=1,
        )
    else:
        row = AcademicDeadline(
            group_id=group.id,
            import_key=f"manual:{uuid.uuid4()}",
            kind=values.get("kind", "control_point"),
            subject=values["subject"],
            title=values["title"],
            description=values["description"],
            deadline_at=values.get("deadline_at"),
            date_only=values.get("date_only", False),
            window_start=values.get("window_start"),
            window_end=values.get("window_end"),
            needs_clarification=False,
            source_message_ids="[]",
            created_at=now,
            delete_at=now + timedelta(days=90),
            revision=1,
        )
    db.add(row)
    await db.flush()
    key = f"{kind}:{row.id}"
    await lock_decision(db, group, user, member, key, row)
    aid = await save_audit(db, group, user, "create", {key: None}, [(key, kind, row)])
    return key, aid


async def mutate(db, group, user, member, key, expected, operation, values=None):
    kind, row = await entity(db, group, key)
    await guard(db, group, member, key, row, expected)
    before = {key: await snapshot(db, key, kind, row)}
    values = values or {}
    for field, value in values.items():
        if field not in FIELDS[kind] or field == "revision":
            raise StError("Недоступное поле.")
        setattr(row, field, value)
    now = datetime.now(UTC)
    if operation in {"cancel", "reject"}:
        if kind == "h":
            row.status = "cancelled"
            row.cancelled_at = now
        elif kind == "e":
            row.cancelled_at = now
        elif kind == "c":
            row.state = "dismissed"
        else:
            raise StError("Для пары используется изменение расписания.")
    if kind == "h" and operation != "defer":
        row.verification_state = "manual_confirmed"
        row.updated_at = now
        if operation not in {"confirm", "material"}:
            row.significant_updated_at = now
    if kind == "e" and operation != "defer":
        row.needs_clarification = False
    row.revision += 1
    await db.flush()
    await lock_decision(
        db,
        group,
        user,
        member,
        key,
        row,
        now + timedelta(hours=12) if operation == "defer" else None,
    )
    return await save_audit(
        db,
        group,
        user,
        operation,
        before,
        [(key, kind, row)],
        notify=operation not in {"confirm", "defer", "material", "description"},
    )


async def add_material(db, group, user, member, key, expected, title, url):
    kind, row = await entity(db, group, key)
    await guard(db, group, member, key, row, expected)
    if kind not in {"h", "e"}:
        raise StError("Материал добавляется к ДЗ или КТ.")
    before = {key: await snapshot(db, key, kind, row)}
    if (
        await db.scalar(
            select(func.count())
            .select_from(MaterialLink)
            .where(MaterialLink.group_id == group.id, MaterialLink.entity_key == key)
        )
        >= 20
    ):
        raise StError("В карточке уже 20 ссылок.")
    db.add(MaterialLink(group_id=group.id, entity_key=key, title=title[:255], url=safe_url(url)))
    row.revision += 1
    await db.flush()
    return await save_audit(db, group, user, "material", before, [(key, kind, row)], False)


async def convert(db, group, user, member, key, expected):
    kind, row = await entity(db, group, key)
    await guard(db, group, member, key, row, expected)
    if kind not in {"h", "e"}:
        raise StError("Можно преобразовать только ДЗ и КТ.")
    before = {key: await snapshot(db, key, kind, row)}
    newkind = "e" if kind == "h" else "h"
    _, aid = await create_card(
        db,
        group,
        user,
        member,
        newkind,
        {
            "subject": row.subject_name if kind == "h" else row.subject,
            "title": row.title,
            "description": row.description or row.title,
            "deadline_at": row.deadline_at,
            "date_only": row.deadline_date_only if kind == "h" else row.date_only,
        },
    )
    audit = await db.get(HeadmanAudit, aid)
    newkey = audit.entity_key
    _, newrow = await entity(db, group, newkey)
    newrow.raw_message_id = row.raw_message_id if newkind == "e" else None
    for link in (
        await db.scalars(
            select(MaterialLink).where(
                MaterialLink.group_id == group.id, MaterialLink.entity_key == key
            )
        )
    ).all():
        db.add(MaterialLink(group_id=group.id, entity_key=newkey, title=link.title, url=link.url))
    if kind == "h":
        row.status, row.cancelled_at = "cancelled", datetime.now(UTC)
    else:
        row.cancelled_at = datetime.now(UTC)
    row.revision += 1
    await lock_decision(db, group, user, member, key, row)
    await db.flush()
    before[newkey] = None
    audit.operation = "convert"
    audit.before = dumps(before)
    audit.after = dumps(
        {
            key: await snapshot(db, key, kind, row),
            newkey: await snapshot(db, newkey, newkind, newrow),
        }
    )
    return aid


async def merge(db, group, user, member, key, expected, target, target_revision):
    kind, row = await entity(db, group, key)
    tk, other = await entity(db, group, target)
    if key == target or kind != tk or kind not in {"h", "e"}:
        raise StError("Выбери другую карточку того же типа.")
    await guard(db, group, member, key, row, expected)
    await guard(db, group, member, target, other, target_revision)
    before = {
        key: await snapshot(db, key, kind, row),
        target: await snapshot(db, target, tk, other),
    }
    other.description = (
        (other.description or "") + "\n\nДополнение: " + (row.description or row.title)
    )[:12000]
    if kind == "h":
        row.status = "cancelled"
    row.cancelled_at = datetime.now(UTC)
    row.revision += 1
    other.revision += 1
    for link in (
        await db.scalars(
            select(MaterialLink).where(
                MaterialLink.group_id == group.id, MaterialLink.entity_key == key
            )
        )
    ).all():
        db.add(MaterialLink(group_id=group.id, entity_key=target, title=link.title, url=link.url))
    await db.flush()
    for k, r in [(key, row), (target, other)]:
        await lock_decision(db, group, user, member, k, r)
    return await save_audit(
        db, group, user, "merge", before, [(key, kind, row), (target, tk, other)]
    )


def decode_field(row, name, value):
    if value is None:
        return None
    column = sa.inspect(type(row)).columns[name]
    if isinstance(column.type, sa.DateTime):
        return datetime.fromisoformat(value)
    if isinstance(column.type, sa.Date):
        return date.fromisoformat(value)
    if isinstance(column.type, sa.Time):
        return time.fromisoformat(value)
    if isinstance(column.type, sa.Uuid):
        return uuid.UUID(value)
    return value


async def undo(db, group, user, member, audit_id):
    audit = await db.scalar(
        select(HeadmanAudit)
        .where(HeadmanAudit.id == audit_id, HeadmanAudit.group_id == group.id)
        .with_for_update()
    )
    if not audit or audit.undone or utc(audit.created_at) < datetime.now(UTC) - timedelta(days=90):
        raise StError("Эта правка уже отменена или недоступна.")
    before, after = json.loads(audit.before), json.loads(audit.after)
    rows = []
    for key, snapshot_after in sorted(after.items()):
        kind, row = await entity(db, group, key)
        await guard(db, group, member, key, row, snapshot_after["fields"]["revision"])
        rows.append((key, kind, row))
    inverse = {key: await snapshot(db, key, kind, row) for key, kind, row in rows}
    for key, kind, row in rows:
        old = before[key]
        if old:
            for field, value in old["fields"].items():
                if field != "revision":
                    setattr(row, field, decode_field(row, field, value))
        elif kind in {"h", "e"}:
            if kind == "h":
                row.status = "cancelled"
            row.cancelled_at = datetime.now(UTC)
        elif kind == "x":
            # Preserve this record and restore the unmodified base occurrence.
            pattern = await db.get(SchedulePattern, row.pattern_id)
            zone = ZoneInfo(group.timezone)
            row.starts_at = datetime.combine(row.occurrence_date, pattern.starts, zone)
            row.ends_at = datetime.combine(row.occurrence_date, pattern.ends, zone)
            row.location, row.online_url, row.cancelled = (
                pattern.location,
                pattern.online_url,
                False,
            )
        elif kind == "p":
            row.cancelled = True
        row.revision += 1
        for link in (
            await db.scalars(
                select(MaterialLink).where(
                    MaterialLink.group_id == group.id, MaterialLink.entity_key == key
                )
            )
        ).all():
            await db.delete(link)
        await db.flush()
        for link in old["materials"] if old else []:
            db.add(
                MaterialLink(
                    id=uuid.UUID(link["id"]),
                    group_id=group.id,
                    entity_key=key,
                    title=link["title"],
                    url=link["url"],
                )
            )
        await lock_decision(db, group, user, member, key, row)
        if old:
            decision = await db.get(HeadmanDecision, key, populate_existing=True)
            previous = old["decision"]
            decision.locked = bool(previous and previous["locked"])
            decision.deferred_until = (
                datetime.fromisoformat(previous["deferred_until"])
                if previous and previous["deferred_until"]
                else None
            )
    audit.undone = True
    await db.flush()
    return await save_audit(db, group, user, "undo", inverse, rows)


async def ai_locked(db, group_id, raw_id):
    if await db.scalar(
        select(HeadmanDecision.entity_key).where(
            HeadmanDecision.entity_key == f"r:{raw_id}",
            HeadmanDecision.group_id == group_id,
            HeadmanDecision.locked.is_(True),
        )
    ):
        return True
    keys = [
        f"h:{i}"
        for i in (
            await db.scalars(
                select(Homework.id).where(
                    Homework.group_id == group_id, Homework.raw_message_id == raw_id
                )
            )
        ).all()
    ]
    keys += [
        f"e:{i}"
        for i in (
            await db.scalars(
                select(AcademicDeadline.id).where(
                    AcademicDeadline.group_id == group_id, AcademicDeadline.raw_message_id == raw_id
                )
            )
        ).all()
    ]
    return bool(
        keys
        and await db.scalar(
            select(HeadmanDecision.entity_key)
            .where(
                HeadmanDecision.group_id == group_id,
                HeadmanDecision.entity_key.in_(keys),
                HeadmanDecision.locked.is_(True),
            )
            .limit(1)
        )
    )


async def telegram_member(settings, group, uid):
    if not settings.telegram_bot_token:
        return False
    try:
        async with httpx.AsyncClient(timeout=10, trust_env=False, follow_redirects=False) as client:
            response = await client.post(
                f"https://api.telegram.org/bot{settings.telegram_bot_token}/getChatMember",
                json={"chat_id": group.telegram_chat_id, "user_id": uid},
            )
            data = response.json()
        member = data.get("result", {})
        return (
            response.status_code == 200
            and data.get("ok") is True
            and (
                member.get("status") in {"creator", "administrator", "member"}
                or member.get("status") == "restricted"
                and member.get("is_member") is True
            )
        )
    except (httpx.HTTPError, ValueError):
        return False


async def issue_invites(db, group, member, count):
    headman_only(member)
    if not 1 <= count <= 10:
        raise StError("За один раз — от 1 до 10 приглашений.")
    if (
        await db.scalar(
            select(func.count())
            .select_from(GroupInvitation)
            .where(
                GroupInvitation.group_id == group.id,
                GroupInvitation.expires_at > datetime.now(UTC),
                GroupInvitation.revoked.is_(False),
                GroupInvitation.used_by.is_(None),
            )
        )
        + count
        > 50
    ):
        raise StError("У группы уже много действующих приглашений. Отзови старые.")
    result = []
    for _ in range(count):
        code = secrets.token_urlsafe(24)
        db.add(
            GroupInvitation(
                token_hash=token_hash(code),
                group_id=group.id,
                issuer_id=member.id,
                expires_at=datetime.now(UTC) + timedelta(hours=24),
            )
        )
        result.append(code)
    await db.flush()
    return result


async def redeem_invite(db, settings, telegram_id, code):
    invitation = await db.scalar(
        select(GroupInvitation)
        .where(GroupInvitation.token_hash == token_hash(code))
        .with_for_update()
    )
    now = datetime.now(UTC)
    if (
        not invitation
        or invitation.revoked
        or invitation.used_by
        or utc(invitation.expires_at) <= now
    ):
        raise StError("Приглашение использовано, отозвано или истекло.")
    issuer = await db.get(Membership, invitation.issuer_id)
    group = await db.get(Group, invitation.group_id)
    if (
        not issuer
        or issuer.status != "active"
        or issuer.role != "headman"
        or group.status != "active"
        or not group.pilot_authorized
    ):
        raise StError("Приглашение больше недействительно.")
    if not await telegram_member(settings, group, telegram_id):
        raise StError(
            "Не удалось подтвердить участие в Telegram-группе. Вступи в неё; бот должен иметь права администратора для проверки."
        )
    await db.execute(
        insert(User)
        .values(id=uuid.uuid4(), telegram_user_id=telegram_id, display_name="Участник")
        .on_conflict_do_nothing(index_elements=[User.telegram_user_id])
    )
    user = await db.scalar(select(User).where(User.telegram_user_id == telegram_id))
    await db.execute(
        insert(Membership)
        .values(
            id=uuid.uuid4(), user_id=user.id, group_id=group.id, role="student", status="active"
        )
        .on_conflict_do_nothing(index_elements=[Membership.user_id, Membership.group_id])
    )
    existing = await db.scalar(
        select(Membership)
        .where(Membership.user_id == user.id, Membership.group_id == group.id)
        .with_for_update()
    )
    if existing.status != "active":
        raise StError("Доступ был отозван. Обратись к старосте; приглашение не обходит отзыв.")
    invitation.used_by = user.id
    return group
