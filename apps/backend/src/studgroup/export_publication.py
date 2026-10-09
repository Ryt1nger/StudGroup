"""Explicit export sink: commit each analyzed fragment, never await the export tail."""

import json
import uuid
from collections import Counter
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from studgroup.ai import SourcedExtraction
from studgroup.deadlines import canonical_subject, resolve_deadline
from studgroup.models import AICandidate, AIJob, Group, RawMessage
from studgroup.processing import context_for, publish, retain_until, utc


def fragment_sink(engine, group_id):
    async def apply(key, fragment):
        now = datetime.now(UTC)
        job_id = uuid.uuid5(group_id, "export-fragment:" + key)
        async with AsyncSession(engine, expire_on_commit=False) as db:
            # Serializes retries/concurrent sinks and validates the explicitly selected group.
            group = await db.scalar(select(Group).where(Group.id == group_id).with_for_update())
            if not group or not group.pilot_authorized or group.status != "active":
                raise ValueError("export_publication_group_not_authorized")
            existing = await db.get(AIJob, job_id)
            if existing:
                return {"already_committed": True}
            job = AIJob(
                id=job_id,
                group_id=group_id,
                raw_message_id=None,
                source_revision=1,
                state="done",
                attempts=0,
                available_at=now,
                created_at=now,
            )
            db.add(job)
            await db.flush()
            sources = {s["message_id"]: s for s in fragment["sources"]}
            counts = Counter()
            # Validate all references before any publication. Never mutate stale source versions.
            raw_by_id = {}
            for data in fragment["assignments"]:
                for mid in data["source_message_ids"]:
                    source = sources.get(mid)
                    raw = await db.scalar(
                        select(RawMessage)
                        .where(
                            RawMessage.group_id == group_id, RawMessage.telegram_message_id == mid
                        )
                        .with_for_update()
                    )
                    if (
                        not source
                        or not raw
                        or raw.text != source["text"]
                        or utc(raw.message_date)
                        != utc(datetime.fromisoformat(source["message_date"]))
                        or utc(raw.delete_at) <= now
                    ):
                        raise ValueError("export_publication_source_missing_stale_or_expired")
                    raw_by_id[mid] = raw
            anchors = Counter(data["source_message_ids"][0] for data in fragment["assignments"])
            for ordinal, data in enumerate(fragment["assignments"]):
                item = SourcedExtraction.model_validate_json(
                    json.dumps(
                        {k: v for k, v in data.items() if k in SourcedExtraction.model_fields}
                    )
                )
                raw = raw_by_id[item.source_message_ids[0]]
                _, schedule = await context_for(db, raw, group, now)
                item.subject = canonical_subject(item.subject, schedule)
                evidence = [
                    (raw_by_id[mid].text, utc(raw_by_id[mid].message_date))
                    for mid in item.source_message_ids
                ]
                resolved = resolve_deadline(
                    evidence,
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
                    datetime.fromisoformat(data["window_start"])
                    if data.get("window_start")
                    else None
                )
                item._window_end = (
                    datetime.fromisoformat(data["window_end"]) if data.get("window_end") else None
                )
                raw = raw_by_id[item.source_message_ids[0]]
                # Current schema has one task per original source; never overwrite a sibling.
                accepted = False
                if anchors[raw.telegram_message_id] == 1:
                    accepted = await publish(db, raw, item, now)
                state = "published" if accepted else "review"
                counts[state] += 1
                db.add(
                    AICandidate(
                        job_id=job.id,
                        group_id=group_id,
                        ordinal=ordinal,
                        payload=json.dumps(
                            item.model_dump(mode="json")
                            | {
                                "deadline_basis": item._deadline_basis,
                                "window_start": data.get("window_start"),
                                "window_end": data.get("window_end"),
                            },
                            ensure_ascii=False,
                        ),
                        state=state,
                        created_at=now,
                        delete_at=retain_until(now, item.deadline_at, item._window_end),
                    )
                )
            await db.commit()
            return dict(counts)

    return apply
