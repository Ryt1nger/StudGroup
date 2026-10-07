"""Explicit loopback-only real-data review, isolated from production Telegram auth/data."""

import argparse
import asyncio
import hmac
import ipaddress
import json
import os
import secrets
import uuid
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from typing import Annotated

from fastapi import Depends, Request
from fastapi.routing import APIRoute
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from studgroup.api import ApiError, BootstrapBody, database, schedule_data
from studgroup.chat_export import read_html_export
from studgroup.deadlines import ScheduleDeadlineContext, resolve_deadline
from studgroup.main import Settings, create_app
from studgroup.models import (
    Base,
    Group,
    Homework,
    Membership,
    RawMessage,
    SchedulePattern,
    User,
    WebSession,
)
from studgroup.security import issue_token


async def seed(directory: Path, archive: Path):
    """No overwrite: real completion marks survive restart; reseeding needs a fresh directory."""
    database_path = directory / "preview.sqlite"
    if database_path.exists():
        raise ValueError("preview_database_exists")
    report = json.loads((directory / "import-report.json").read_text(encoding="utf-8"))
    schedule = json.loads((directory / "schedule.json").read_text(encoding="utf-8"))
    messages = {
        row.message_id: row
        for row in read_html_export(archive, timezone=report["timezone_assumption"])
    }
    engine = create_async_engine(f"sqlite+aiosqlite:///{database_path}")
    now = datetime.now(UTC)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        os.chmod(database_path, 0o600)
        async with AsyncSession(engine) as db:
            group = Group(
                id=uuid.uuid4(),
                telegram_chat_id=-1,
                name="БИ 1.2 · тестовый импорт",
                timezone=report["timezone_assumption"],
                pilot_authorized=True,
            )
            user = User(id=uuid.uuid4(), telegram_user_id=1, display_name="Локальный просмотр")
            db.add_all([group, user])
            await db.flush()
            db.add(Membership(user_id=user.id, group_id=group.id, role="student", status="active"))
            sources = set()
            for candidate in report["assignments"]:
                if candidate.get("kind", "homework") != "homework":
                    continue
                if not candidate.get("subject") or not candidate.get("title"):
                    continue
                source_id = candidate["source_message_ids"][0]
                if source_id in sources:
                    continue  # Current one-card-per-source model: never silently duplicate a card.
                sources.add(source_id)
                source = messages[source_id]
                raw = RawMessage(
                    id=uuid.uuid4(),
                    group_id=group.id,
                    telegram_message_id=source_id,
                    text=source.text,
                    message_date=source.message_date.astimezone(UTC),
                    version_date=source.message_date.astimezone(UTC),
                    processing_state="completed",
                    imported=True,
                    delete_at=source.message_date + timedelta(days=30),
                )
                db.add(raw)
                deadline = (
                    datetime.fromisoformat(candidate["deadline_at"])
                    if candidate["deadline_at"]
                    else None
                )
                subject = candidate["subject"]
                db.add(
                    Homework(
                        group_id=group.id,
                        raw_message_id=raw.id,
                        subject_id=uuid.uuid5(group.id, subject.casefold()),
                        subject_name=subject,
                        title=candidate["title"],
                        description=candidate.get("description"),
                        deadline_at=deadline.astimezone(UTC) if deadline else None,
                        deadline_date_only=candidate["deadline_date_only"],
                        status="needs_clarification",
                        verification_state="needs_clarification",
                        urgency="normal",
                        created_at=now,
                        updated_at=now,
                        delete_at=max(
                            now + timedelta(days=90),
                            deadline.astimezone(UTC) + timedelta(days=7) if deadline else now,
                        ),
                    )
                )
            for weekday, start, end, subject, location in schedule["lessons"]:
                db.add(
                    SchedulePattern(
                        group_id=group.id,
                        subject=subject,
                        weekday=weekday,
                        starts=time.fromisoformat(start),
                        ends=time.fromisoformat(end),
                        location=location,
                        week="all",
                        valid_from=date.fromisoformat(schedule["valid_from"]),
                        valid_until=date.fromisoformat(schedule["valid_until"]),
                    )
                )
            await db.commit()
        print("Preview seeded: candidates", len(sources), "lessons", len(schedule["lessons"]))
    finally:
        await engine.dispose()


async def repeat_next_week(db, group, start, end):
    """Explicit local-test assumption: one copied week, stable IDs, never a semester."""
    if (end - start).days != 6:
        raise ValueError("test_schedule_requires_one_full_week")
    rows = (
        await db.scalars(
            select(SchedulePattern).where(
                SchedulePattern.group_id == group.id,
                SchedulePattern.valid_from == start,
                SchedulePattern.valid_until == end,
            )
        )
    ).all()
    added = 0
    for original in rows:
        copied_id = uuid.uuid5(original.id, "local-test-next-week")
        if await db.get(SchedulePattern, copied_id):
            continue
        db.add(
            SchedulePattern(
                id=copied_id,
                group_id=group.id,
                subject=original.subject,
                weekday=original.weekday,
                starts=original.starts,
                ends=original.ends,
                teacher=original.teacher,
                location=original.location,
                week=original.week,
                valid_from=start + timedelta(days=7),
                valid_until=end + timedelta(days=7),
            )
        )
        added += 1
    await db.flush()
    return added


async def refresh_deadlines(
    directory: Path, archive: Path, copy_next_week=False, fill_missing=False
):
    """Apply approved policy to existing preview without reseeding or losing personal marks."""
    report = json.loads((directory / "import-report.json").read_text(encoding="utf-8"))
    coverage = json.loads((directory / "schedule.json").read_text(encoding="utf-8"))
    messages = {
        row.message_id: row
        for row in read_html_export(archive, timezone=report["timezone_assumption"])
    }
    candidates = {row["source_message_ids"][0]: row for row in report["assignments"]}
    engine = create_async_engine(f"sqlite+aiosqlite:///{directory / 'preview.sqlite'}")
    changed = 0
    reasons = {}
    try:
        async with AsyncSession(engine) as db:
            group = await db.scalar(select(Group))
            start = date.fromisoformat(coverage["valid_from"])
            end = date.fromisoformat(coverage["valid_until"])
            if copy_next_week:
                added = await repeat_next_week(db, group, start, end)
                print(
                    "Local test schedule copies added:",
                    added,
                    "week:",
                    start + timedelta(days=7),
                    end + timedelta(days=7),
                )
            patterns = (
                await db.scalars(
                    select(SchedulePattern).where(SchedulePattern.group_id == group.id)
                )
            ).all()
            end = max((pattern.valid_until for pattern in patterns), default=end)
            calendar = await schedule_data(start, end, group, db)
            schedule = ScheduleDeadlineContext(
                calendar["lessons"], start, end, calendar["week_state"] == "ready"
            )
            rows = (await db.scalars(select(Homework).where(Homework.group_id == group.id))).all()
            now = datetime.now(UTC)
            for row in rows:
                if fill_missing:
                    if row.deadline_at is not None or row.status == "incomplete_hidden":
                        continue
                    raw = (
                        await db.get(RawMessage, row.raw_message_id) if row.raw_message_id else None
                    )
                    if raw is None:
                        continue
                    resolved = resolve_deadline(
                        [
                            (
                                raw.text,
                                raw.message_date.replace(tzinfo=UTC)
                                if raw.message_date.tzinfo is None
                                else raw.message_date,
                            )
                        ],
                        group.timezone,
                        row.subject_name,
                        schedule=schedule,
                    )
                    if resolved.at is None:
                        continue
                    row.deadline_at = resolved.at.astimezone(UTC)
                    row.deadline_date_only = False
                    row.verification_state = "inferred"
                    row.revision += 1
                    row.updated_at = now
                    changed += 1
                    continue
                raw = await db.get(RawMessage, row.raw_message_id)
                candidate = candidates.get(raw.telegram_message_id) if raw else None
                if candidate is None:
                    continue
                sources = [
                    (messages[mid].text, messages[mid].message_date)
                    for mid in candidate["source_message_ids"]
                ]
                resolved = resolve_deadline(
                    sources,
                    group.timezone,
                    row.subject_name,
                    datetime.fromisoformat(candidate["deadline_at"])
                    if candidate["deadline_at"]
                    else None,
                    candidate["deadline_date_only"],
                    schedule,
                )
                reasons[resolved.basis] = reasons.get(resolved.basis, 0) + 1
                # SQLite drops offsets; normalize before persistence, as all backend
                # representations interpret naive stored timestamps as UTC.
                resolved_at = resolved.at.astimezone(UTC) if resolved.at else None
                existing = row.deadline_at
                if existing and existing.utcoffset() is None:
                    existing = existing.replace(tzinfo=UTC)
                if existing == resolved_at and row.deadline_date_only == resolved.date_only:
                    continue
                row.deadline_at = resolved_at
                row.deadline_date_only = resolved.date_only
                row.revision += 1
                row.updated_at = now
                row.significant_updated_at = now
                changed += 1
            await db.commit()
        print("Preview deadline updates:", changed, "resolution:", reasons)
    finally:
        await engine.dispose()


def create_preview_app():
    directory = Path(os.environ["STUDGROUP_PREVIEW_DIR"]).resolve()
    secret = (directory / "preview.secret").read_text().strip()
    if len(secret) < 40 or not (directory / "preview.sqlite").exists():
        raise RuntimeError("preview_not_prepared")
    settings = Settings(
        database_url=os.environ.get("STUDGROUP_PREVIEW_DATABASE_URL")
        or f"sqlite+aiosqlite:///{directory / 'preview.sqlite'}",
        webapp_origin="http://localhost:5173",
        telegram_bot_token="",
        ai_enabled=False,
        processing_mode="external",
    )
    app = create_app(settings)

    async def preview_bootstrap(
        body: BootstrapBody, request: Request, db: Annotated[AsyncSession, Depends(database)]
    ):
        try:
            loopback = request.client and ipaddress.ip_address(request.client.host).is_loopback
        except ValueError:
            loopback = False
        if not loopback:
            raise ApiError("permission_denied", "Только локальный просмотр", 403)
        if not hmac.compare_digest(body.init_data, secret):
            raise ApiError("unauthenticated", "Локальный доступ не подтверждён")
        member = await db.scalar(select(Membership).where(Membership.status == "active"))
        user = await db.get(User, member.user_id)
        group = await db.get(Group, member.group_id)
        token, digest = issue_token()
        now = datetime.now(UTC)
        expires = now + timedelta(hours=12)
        db.add(
            WebSession(
                token_hash=digest, user_id=user.id, membership_id=member.id, expires_at=expires
            )
        )
        await db.commit()
        return {
            "access_token": token,
            "token_type": "Bearer",
            "expires_at": expires,
            "session": {
                "user": {
                    "id": user.id,
                    "telegram_user_id": user.telegram_user_id,
                    "display_name": user.display_name,
                    "username": None,
                },
                "access_state": "active",
                "membership": {
                    "id": member.id,
                    "role": member.role,
                    "status": member.status,
                    "can_recheck": False,
                },
                "group": {
                    "id": group.id,
                    "name": group.name,
                    "timezone": group.timezone,
                    "status": group.status,
                    "pilot_authorized": True,
                },
                "permissions": [
                    "today.read",
                    "homework.read",
                    "homework.completion.write",
                    "schedule.read",
                ],
                "server_time": now,
            },
            "start_param_policy": "navigation_hint_only",
        }

    app.router.routes.insert(
        0,
        APIRoute(
            "/v1/session/bootstrap", preview_bootstrap, methods=["POST"], include_in_schema=False
        ),
    )
    return app


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    parser.add_argument("archive", type=Path)
    parser.add_argument("--refresh-deadlines", action="store_true")
    parser.add_argument("--fill-missing-deadlines", action="store_true")
    parser.add_argument(
        "--repeat-next-week",
        action="store_true",
        help="Explicit local test copy, requires --refresh-deadlines",
    )
    args = parser.parse_args()
    if args.refresh_deadlines or args.fill_missing_deadlines:
        asyncio.run(
            refresh_deadlines(
                args.directory, args.archive, args.repeat_next_week, args.fill_missing_deadlines
            )
        )
    else:
        if args.repeat_next_week:
            parser.error("--repeat-next-week requires --refresh-deadlines")
        asyncio.run(seed(args.directory, args.archive))
        with (args.directory / "preview.secret").open("x") as file:
            os.chmod(args.directory / "preview.secret", 0o600)
            file.write(secrets.token_urlsafe(48))
