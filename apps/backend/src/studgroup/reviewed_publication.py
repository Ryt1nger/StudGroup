"""Explicit private operator import: reviewed evidence, no model calls or public endpoint."""

import asyncio
import base64
import gzip
import hashlib
import hmac
import io
import json
import os
import uuid
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from studgroup.ai import SourcedExtraction
from studgroup.deadlines import canonical_subject, resolve_deadline
from studgroup.homework import archived_condition
from studgroup.ingestion import Chat, Message, ReplyReference, store_message
from studgroup.main import Settings
from studgroup.models import (
    AICandidate,
    AIJob,
    Group,
    Homework,
    Membership,
    RawMessage,
    SchedulePattern,
    User,
)
from studgroup.processing import context_for, publish, retain_until, utc


def unpack(packed, expected_hash):
    if len(packed) > 250_000 or not hmac.compare_digest(
        hashlib.sha256(packed.encode()).hexdigest(), expected_hash
    ):
        raise ValueError("publication_integrity_failed")
    with gzip.GzipFile(fileobj=io.BytesIO(base64.b64decode(packed, validate=True))) as source:
        decoded = source.read(2_000_001)
    if len(decoded) > 2_000_000:
        raise ValueError("publication_payload_too_large")
    payload = json.loads(decoded)
    if payload.get("version") != 1 or len(payload["sources"]) > 500 or len(payload["entries"]) > 50:
        raise ValueError("unsupported_publication_payload")
    return payload


async def apply(db, settings, payload, identity, now):
    if payload["owner_id"] != settings.owner_telegram_user_id or payload["chat_id"] >= 0:
        raise ValueError("publication_owner_or_chat_invalid")
    group = await db.scalar(
        select(Group)
        .where(
            Group.telegram_chat_id == payload["chat_id"],
            Group.pilot_authorized.is_(True),
            Group.status == "active",
        )
        .with_for_update()
    )
    if not group:
        raise ValueError("publication_group_not_authorized")
    member = await db.scalar(
        select(Membership.id)
        .join(User)
        .where(
            Membership.group_id == group.id,
            Membership.status == "active",
            User.telegram_user_id == payload["owner_id"],
        )
    )
    if not member:
        raise ValueError("publication_owner_not_group_member")
    job_id = uuid.uuid5(group.id, "reviewed-export:" + identity)
    if await db.get(AIJob, job_id):
        return {"already_committed": True}
    repeat = payload.get("repeat_schedule")
    extended = 0
    if repeat:
        low, high, end = (date.fromisoformat(repeat[k]) for k in ("from", "until", "next_until"))
        if high - low != timedelta(days=6) or end - high != timedelta(days=7):
            raise ValueError("only_one_approved_pilot_week_can_be_repeated")
        patterns = (
            await db.scalars(
                select(SchedulePattern)
                .where(
                    SchedulePattern.group_id == group.id,
                    SchedulePattern.valid_from <= low,
                    SchedulePattern.valid_until == high,
                    SchedulePattern.week == "all",
                )
                .with_for_update()
            )
        ).all()
        for pattern in patterns:
            pattern.valid_until = end
            pattern.revision += 1
            extended += 1
    source_map = {}
    for source in payload["sources"]:
        stamp = datetime.fromisoformat(source["message_date"])
        if stamp.utcoffset() is None or utc(stamp) + timedelta(days=30) <= now:
            raise ValueError("publication_source_expired_or_naive")
        message = Message(
            message_id=source["message_id"],
            date=int(stamp.timestamp()),
            chat=Chat(id=group.telegram_chat_id, type="supergroup"),
            text=source["text"],
            reply_to_message=ReplyReference(message_id=source["reply_to_message_id"])
            if source.get("reply_to_message_id")
            else None,
        )
        await store_message(db, group, message, imported=True)
        raw = await db.scalar(
            select(RawMessage)
            .where(
                RawMessage.group_id == group.id,
                RawMessage.telegram_message_id == source["message_id"],
            )
            .with_for_update()
        )
        if raw.text != source["text"] or utc(raw.message_date) != utc(stamp):
            raise ValueError("publication_source_has_newer_live_revision")
        source_map[source["message_id"]] = raw
    job = AIJob(
        id=job_id,
        group_id=group.id,
        raw_message_id=None,
        source_revision=1,
        state="done",
        attempts=0,
        available_at=now,
        created_at=now,
    )
    db.add(job)
    await db.flush()
    statistics = {"schedule_patterns_extended": extended, "published": 0, "review": 0}
    cards = []
    for ordinal, entry in enumerate(payload["entries"]):
        item = SourcedExtraction.model_validate_json(json.dumps(entry["assignment"]))
        if not set(item.source_message_ids) <= source_map.keys():
            raise ValueError("publication_reference_missing")
        raw = source_map[item.source_message_ids[0]]
        _, schedule = await context_for(db, raw, group, now)
        item.subject = canonical_subject(item.subject, schedule)
        if item.kind == "homework":
            resolved = resolve_deadline(
                [
                    (source_map[mid].text, utc(source_map[mid].message_date))
                    for mid in item.source_message_ids
                ],
                group.timezone,
                item.subject,
                item.deadline_at,
                item.deadline_date_only,
                schedule,
            )
            item.deadline_at, item.deadline_date_only, item._deadline_basis = (
                resolved.at,
                resolved.date_only,
                resolved.basis,
            )
        item._window_start = (
            datetime.fromisoformat(entry["window_start"]) if entry.get("window_start") else None
        )
        item._window_end = (
            datetime.fromisoformat(entry["window_end"]) if entry.get("window_end") else None
        )
        if entry.get("evidence_reviewed"):
            # Explicit operator review, not a blanket lowering of the AI confidence gate.
            item.confidence = max(85, item.confidence)
        accepted = await publish(db, raw, item, now, statistics=statistics)
        state = "published" if accepted else "review"
        statistics[state] += 1
        db.add(
            AICandidate(
                job_id=job.id,
                group_id=group.id,
                ordinal=ordinal,
                payload=json.dumps(
                    item.model_dump(mode="json")
                    | {
                        "deadline_basis": item._deadline_basis,
                        "window_start": entry.get("window_start"),
                        "window_end": entry.get("window_end"),
                    },
                    ensure_ascii=False,
                ),
                state=state,
                created_at=now,
                delete_at=retain_until(now, item.deadline_at, item._window_end),
            )
        )
        cards.append(
            {
                "subject": item.subject,
                "title": item.title,
                "deadline": item.deadline_at.isoformat() if item.deadline_at else None,
                "state": state,
                "kind": item.kind,
            }
        )
    statistics.pop("_used_source_ids", None)
    statistics["entries"] = cards
    await db.flush()
    # The same active predicate used by GET /homework?filter=all, not total row count.
    visible = (
        await db.scalars(
            select(Homework).where(
                Homework.group_id == group.id,
                Homework.status.in_(["published", "needs_clarification", "completed"]),
                Homework.delete_at > now,
                ~archived_condition(now, group.timezone),
            )
        )
    ).all()
    statistics["active_homework"] = [
        {
            "id": str(row.id),
            "subject": row.subject_name,
            "title": row.title,
            "deadline": utc(row.deadline_at).isoformat() if row.deadline_at else None,
        }
        for row in visible
    ]
    return statistics


async def startup(engine, settings):
    packed = os.environ.get("REVIEWED_PUBLICATION_PAYLOAD", "")
    if not packed:
        return
    identity = os.environ.get("REVIEWED_PUBLICATION_SHA256", "")
    payload = unpack(packed, identity)
    async with AsyncSession(engine) as db:
        result = await apply(db, settings, payload, identity, datetime.now(UTC))
        await db.commit()
    safe = {k: v for k, v in result.items() if k not in {"entries", "active_homework"}}
    safe["entry_states"] = [
        {"kind": r["kind"], "state": r["state"], "deadline": r["deadline"]}
        for r in result.get("entries", [])
    ]
    safe["active_homework"] = [
        {"id": r["id"], "deadline": r["deadline"]} for r in result.get("active_homework", [])
    ]
    print("REVIEWED_PUBLICATION_COMMITTED " + json.dumps(safe), flush=True)


async def main():
    engine = create_async_engine(Settings().database_url, hide_parameters=True)
    try:
        await startup(engine, Settings())
    finally:
        await engine.dispose()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except Exception as error:  # noqa: BLE001 -- never expose SQL parameters, keys or private source text
        print("REVIEWED_PUBLICATION_FAILED " + type(error).__name__, flush=True)
        raise SystemExit(1) from None
