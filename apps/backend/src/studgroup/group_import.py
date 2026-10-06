"""Insert-only export + reviewed-data import; no inferred Telegram identities or AI calls."""

import argparse
import asyncio
import base64
import gzip
import hashlib
import hmac
import json
import uuid
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path

from sqlalchemy import Date, DateTime, Time, Uuid, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import create_async_engine

from studgroup.chat_export import ExportMessage
from studgroup.main import Settings
from studgroup.models import (
    AcademicDeadline,
    Group,
    Homework,
    Membership,
    PersonalCompletion,
    RawMessage,
    SchedulePattern,
    User,
)
from studgroup.postgres_transfer import utc_values


async def import_group(db, messages, reviewed, chat_id, user_id, name):
    if chat_id >= 0 or user_id <= 0:
        raise ValueError("telegram_ids_invalid")
    group = (
        (await db.execute(select(Group.__table__).where(Group.telegram_chat_id == chat_id)))
        .mappings()
        .first()
    )
    group_id = (
        group["id"] if group else uuid.uuid5(uuid.NAMESPACE_URL, f"studgroup:telegram:{chat_id}")
    )
    if group and (not group["pilot_authorized"] or group["status"] != "active"):
        raise ValueError("existing_group_not_authorized")
    if not group:
        await db.execute(
            insert(Group).values(
                id=group_id,
                telegram_chat_id=chat_id,
                name=name,
                timezone="Europe/Moscow",
                status="active",
                pilot_authorized=True,
            )
        )
    user = (
        (await db.execute(select(User.__table__).where(User.telegram_user_id == user_id)))
        .mappings()
        .first()
    )
    account_id = user["id"] if user else uuid.uuid5(uuid.NAMESPACE_URL, f"studgroup:user:{user_id}")
    if not user:
        await db.execute(
            insert(User).values(
                id=account_id, telegram_user_id=user_id, display_name=f"Участник {name}"[:255]
            )
        )
    await db.execute(
        insert(Membership)
        .values(
            id=uuid.uuid5(group_id, f"member:{user_id}"),
            user_id=account_id,
            group_id=group_id,
            role="student",
            status="active",
        )
        .on_conflict_do_nothing(index_elements=[Membership.user_id, Membership.group_id])
    )
    existing = dict(
        (
            await db.execute(
                select(RawMessage.telegram_message_id, RawMessage.id).where(
                    RawMessage.group_id == group_id
                )
            )
        ).all()
    )
    originals = {row["telegram_message_id"]: row for row in reviewed.get("raw_messages", [])}
    raw_ids = dict(existing)
    rows = []
    for message in messages:
        if not message.text:
            continue
        mid = message.message_id
        raw_ids[mid] = existing.get(mid, uuid.uuid5(group_id, f"message:{mid}"))
        stamp = message.message_date.astimezone(UTC)
        rows.append(
            {
                "id": raw_ids[mid],
                "group_id": group_id,
                "telegram_message_id": mid,
                "sender_id": None,
                "reply_to_message_id": message.reply_to_message_id,
                "text": message.text,
                "message_date": stamp,
                "version_date": stamp,
                "revision": 1,
                "imported": True,
                "processing_state": "completed" if mid in originals else "needs_context",
                "delete_at": stamp + timedelta(days=30),
            }
        )
    if rows:
        statement = insert(RawMessage).on_conflict_do_nothing(
            index_elements=[RawMessage.group_id, RawMessage.telegram_message_id]
        )
        for offset in range(0, len(rows), 250):
            await db.execute(statement, rows[offset : offset + 250])
    source_ids = {row["id"]: raw_ids.get(row["telegram_message_id"]) for row in originals.values()}
    counts = {"text_messages": len(rows)}
    homework_ids = {}
    for model in [Homework, AcademicDeadline, SchedulePattern]:
        records = []
        for original in reviewed.get(model.__tablename__, []):
            row = utc_values(original)
            row["id"] = uuid.uuid5(group_id, f"reviewed:{model.__tablename__}:{original['id']}")
            row["group_id"] = group_id
            if "raw_message_id" in row and row["raw_message_id"] is not None:
                mapped = source_ids.get(row["raw_message_id"])
                if mapped is None:
                    raise ValueError("review_source_missing_from_export")
                row["raw_message_id"] = mapped
            if model is Homework:
                row["subject_id"] = uuid.uuid5(group_id, row["subject_name"].casefold())
                row["significant_updated_at"] = None  # baseline import is not a live amendment
                homework_ids[original["id"]] = row["id"]
            records.append(row)
        if records:
            await db.execute(
                insert(model).on_conflict_do_nothing(index_elements=[model.id]), records
            )
        counts[model.__tablename__] = len(records)
    marks = []
    for original in reviewed.get("personal_completions", []):
        row = utc_values(original)
        row["homework_id"] = homework_ids[row["homework_id"]]
        row["user_id"] = account_id
        row["id"] = uuid.uuid5(account_id, f"completion:{row['homework_id']}")
        marks.append(row)
    if marks:
        await db.execute(
            insert(PersonalCompletion).on_conflict_do_nothing(
                index_elements=[PersonalCompletion.homework_id, PersonalCompletion.user_id]
            ),
            marks,
        )
    counts["personal_completions"] = len(marks)
    return group_id, account_id, counts


async def read_reviewed(engine):
    """Read the isolated review only; never copy local preview users or sessions."""
    async with engine.connect() as db:
        groups = (
            (await db.execute(select(Group.__table__).where(Group.telegram_chat_id == -1)))
            .mappings()
            .all()
        )
        if len(groups) != 1:
            raise ValueError("isolated_review_group_missing")
        group_id = groups[0]["id"]
        result = {}
        for model in [RawMessage, Homework, AcademicDeadline, SchedulePattern]:
            result[model.__tablename__] = [
                dict(row)
                for row in (
                    await db.execute(select(model.__table__).where(model.group_id == group_id))
                )
                .mappings()
                .all()
            ]
        result["personal_completions"] = [
            dict(row)
            for row in (
                await db.execute(
                    select(PersonalCompletion.__table__)
                    .join(Homework, Homework.id == PersonalCompletion.homework_id)
                    .join(User, User.id == PersonalCompletion.user_id)
                    .where(Homework.group_id == group_id, User.telegram_user_id == 1)
                )
            )
            .mappings()
            .all()
        ]
        return result


def pack_payload(messages, reviewed):
    data = {
        "version": 1,
        "messages": [
            {
                "message_id": m.message_id,
                "message_date": m.message_date.isoformat(),
                "text": m.text,
                "reply_to_message_id": m.reply_to_message_id,
                "has_media": m.has_media,
            }
            for m in messages
        ],
        "reviewed": reviewed,
    }

    def encode(value):
        if isinstance(value, (datetime, date, time)):
            return value.isoformat()
        if isinstance(value, uuid.UUID):
            return str(value)
        raise TypeError("unsupported_payload_value")

    raw = json.dumps(data, ensure_ascii=False, default=encode).encode()
    if len(raw) > 32_000_000:
        raise ValueError("import_payload_too_large")
    packed = base64.b64encode(gzip.compress(raw, mtime=0))
    if len(packed) > 750_000:
        raise ValueError("render_secret_payload_too_large")
    return packed, hashlib.sha256(packed).hexdigest()


def unpack_payload(packed, expected_hash):
    if len(packed) > 750_000 or not hmac.compare_digest(
        hashlib.sha256(packed).hexdigest(), expected_hash
    ):
        raise ValueError("payload_integrity_failed")
    import io

    with gzip.GzipFile(fileobj=io.BytesIO(base64.b64decode(packed, validate=True))) as file:
        raw = file.read(32_000_001)
    if len(raw) > 32_000_000:
        raise ValueError("import_payload_too_large")
    data = json.loads(raw)
    if data.get("version") != 1:
        raise ValueError("unsupported_import_version")
    messages = [
        ExportMessage(
            m["message_id"],
            datetime.fromisoformat(m["message_date"]),
            m["text"],
            m["reply_to_message_id"],
            m["has_media"],
        )
        for m in data["messages"]
    ]
    models = {
        model.__tablename__: model
        for model in [RawMessage, Homework, AcademicDeadline, SchedulePattern, PersonalCompletion]
    }
    reviewed = data["reviewed"]
    for table, rows in reviewed.items():
        if table not in models:
            raise ValueError("unsupported_import_table")
        for row in rows:
            for column in models[table].__table__.columns:
                value = row.get(column.name)
                if value is None:
                    continue
                if isinstance(column.type, Uuid):
                    row[column.name] = uuid.UUID(value)
                elif isinstance(column.type, DateTime):
                    row[column.name] = datetime.fromisoformat(value)
                elif isinstance(column.type, Date):
                    row[column.name] = date.fromisoformat(value)
                elif isinstance(column.type, Time):
                    row[column.name] = time.fromisoformat(value)
    return messages, reviewed


async def run_import(args):
    messages, reviewed = unpack_payload(args.payload.read_bytes(), args.sha256)
    engine = create_async_engine(Settings().database_url, hide_parameters=True)
    try:
        async with engine.begin() as db:
            group, account, counts = await import_group(
                db, messages, reviewed, args.chat_id, args.user_id, args.name
            )
        async with engine.connect() as db:
            verified = {}
            from sqlalchemy import func

            for model in [RawMessage, Homework, AcademicDeadline, SchedulePattern]:
                verified[model.__tablename__] = await db.scalar(
                    select(func.count()).select_from(model).where(model.group_id == group)
                )
            member = (
                await db.execute(
                    select(Membership.role, Membership.status).where(
                        Membership.group_id == group, Membership.user_id == account
                    )
                )
            ).one()
        print(
            "GROUP_IMPORT_COMMITTED",
            json.dumps(
                {
                    "source": counts,
                    "verified": verified,
                    "membership_role": member.role,
                    "membership_status": member.status,
                }
            ),
            flush=True,
        )
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--payload", type=Path, required=True)
    parser.add_argument("--sha256", required=True)
    parser.add_argument("--chat-id", type=int, required=True)
    parser.add_argument("--user-id", type=int, required=True)
    parser.add_argument("--name", required=True)
    args = parser.parse_args()
    try:
        asyncio.run(run_import(args))
    except Exception as error:  # noqa: BLE001 -- CLI errors must not expose private payloads or SQL parameters
        print("GROUP_IMPORT_FAILED", type(error).__name__, flush=True)
        raise SystemExit(1) from None
