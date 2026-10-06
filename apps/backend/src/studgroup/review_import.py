"""Apply an evidence-reviewed import to the isolated preview; no provider requests."""

import argparse
import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from studgroup.chat_export import read_html_export
from studgroup.models import AcademicDeadline, Base, Group, Homework, RawMessage, SchedulePattern


async def run(directory: Path, archive: Path):
    decisions = json.loads((directory / "review-decisions.json").read_text())
    messages = {m.message_id: m for m in read_html_export(archive, timezone="Europe/Moscow")}
    engine = create_async_engine(f"sqlite+aiosqlite:///{directory / 'preview.sqlite'}")
    now = datetime.now(UTC)
    counts = {"homework": 0, "deadlines": 0, "moved_from_homework": 0, "homework_dates_updated": 0}
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with AsyncSession(engine) as db:
            group = await db.scalar(select(Group).where(Group.telegram_chat_id == -1))
            if group is None:
                raise ValueError("isolated_preview_required")
            imported_sources = (
                await db.scalars(
                    select(RawMessage).where(
                        RawMessage.group_id == group.id, RawMessage.imported.is_(True)
                    )
                )
            ).all()
            for raw_source in imported_sources:
                if raw_source.telegram_message_id in messages:
                    original = messages[raw_source.telegram_message_id].message_date.astimezone(UTC)
                    raw_source.message_date = original
                    raw_source.version_date = original
                    raw_source.delete_at = original + timedelta(days=30)
            aliases = {
                "Культура личности и…": "Культура личности и общества",
                "Культура личности": "Культура личности и общества",
                "Основы российской…": "Основы российской государственности",
                "История": "История России",
            }
            patterns = (
                await db.scalars(
                    select(SchedulePattern).where(SchedulePattern.group_id == group.id)
                )
            ).all()
            for pattern in patterns:
                pattern.subject = aliases.get(pattern.subject, pattern.subject)
            homeworks = (
                await db.scalars(select(Homework).where(Homework.group_id == group.id))
            ).all()
            for card in homeworks:
                if card.subject_name in aliases:
                    card.subject_name = aliases[card.subject_name]
                    card.subject_id = uuid.uuid5(group.id, card.subject_name.casefold())
                    card.revision += 1
                    card.updated_at = now

            def timestamp(value):
                if not value:
                    return None
                parsed = datetime.fromisoformat(value)
                if parsed.utcoffset() is None:
                    raise ValueError("deadline_requires_offset")
                return parsed.astimezone(UTC)

            for entry in decisions["entries"]:
                evidence = [messages[mid] for mid in entry["source_message_ids"]]
                raw = await db.scalar(
                    select(RawMessage).where(
                        RawMessage.group_id == group.id,
                        RawMessage.telegram_message_id == evidence[0].message_id,
                    )
                )
                if raw is None:
                    source = evidence[0]
                    raw = RawMessage(
                        group_id=group.id,
                        telegram_message_id=source.message_id,
                        text=source.text,
                        message_date=source.message_date.astimezone(UTC),
                        version_date=source.message_date.astimezone(UTC),
                        processing_state="completed",
                        imported=True,
                        delete_at=source.message_date.astimezone(UTC) + timedelta(days=30),
                    )
                    db.add(raw)
                    await db.flush()
                # Keep structured task facts, not a second 90-day copy of raw chat.
                description = entry.get("description") or entry["title"]
                if entry["kind"] == "homework":
                    existing = await db.scalar(
                        select(Homework).where(Homework.raw_message_id == raw.id)
                    )
                    if existing is None:
                        db.add(
                            Homework(
                                group_id=group.id,
                                raw_message_id=raw.id,
                                subject_id=uuid.uuid5(group.id, entry["subject"].casefold()),
                                subject_name=entry["subject"],
                                title=entry["title"],
                                description=description,
                                deadline_at=timestamp(entry.get("deadline_at")),
                                deadline_date_only=entry.get("date_only", False),
                                status="needs_clarification",
                                verification_state="needs_clarification",
                                created_at=evidence[0].message_date.astimezone(UTC),
                                updated_at=now,
                                delete_at=now + timedelta(days=90),
                            )
                        )
                        counts["homework"] += 1
                    elif entry.get("deadline_at"):
                        at = timestamp(entry["deadline_at"])
                        previous_at = existing.deadline_at
                        if previous_at and previous_at.utcoffset() is None:
                            previous_at = previous_at.replace(tzinfo=UTC)
                        if previous_at != at or existing.deadline_date_only != entry.get(
                            "date_only", False
                        ):
                            existing.deadline_at = at
                            existing.deadline_date_only = entry.get("date_only", False)
                            existing.description = description
                            existing.verification_state = (
                                "inferred" if entry.get("inferred") else "needs_clarification"
                            )
                            existing.revision += 1
                            existing.updated_at = now
                            counts["homework_dates_updated"] += 1
                    continue
                if entry["kind"] not in {"control_point", "assessment", "test"}:
                    raise ValueError("unsupported_review_kind")
                row = await db.scalar(
                    select(AcademicDeadline).where(
                        AcademicDeadline.group_id == group.id,
                        AcademicDeadline.import_key == entry["key"],
                    )
                )
                if row is None:
                    row = AcademicDeadline(
                        group_id=group.id, import_key=entry["key"], created_at=now
                    )
                    db.add(row)
                row.raw_message_id = raw.id
                row.kind = entry["kind"]
                row.subject = entry["subject"]
                row.title = entry["title"]
                row.description = description
                row.deadline_at = timestamp(entry.get("deadline_at"))
                row.date_only = entry.get("date_only", False)
                row.window_start = timestamp(entry.get("window_start"))
                row.window_end = timestamp(entry.get("window_end"))
                row.date_hint = entry.get("date_hint")
                row.needs_clarification = entry.get("needs_clarification", True)
                row.source_message_ids = json.dumps(entry["source_message_ids"])
                row.delete_at = now + timedelta(days=90)
                counts["deadlines"] += 1
                previous = await db.scalar(
                    select(Homework).where(Homework.raw_message_id == raw.id)
                )
                if previous and previous.status != "incomplete_hidden":
                    previous.status = "incomplete_hidden"  # Keep evidence/personal marks, no destructive deletion.
                    previous.revision += 1
                    previous.updated_at = now
                    counts["moved_from_homework"] += 1
            await db.commit()
        print("Reviewed import applied:", counts)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    parser.add_argument("archive", type=Path)
    args = parser.parse_args()
    asyncio.run(run(args.directory, args.archive))
