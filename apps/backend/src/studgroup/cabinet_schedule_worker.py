"""Durable, staged screenshot reading for manual staff timetable uploads.

One server-side job per sweep. The expensive A and B model passes run in
separate invocations, checkpointed in SQL. Never repurchase an ambiguous
in-flight AI call after a worker crash; an explicit user retry is required.
"""

import asyncio
import json
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from studgroup.ai import ProviderFailure
from studgroup.analysis.image_contracts import ScheduleInterpretation, VisualReading
from studgroup.analysis.image_reading import read_pass
from studgroup.analysis.release_budget import provider_for, utc
from studgroup.analysis.schedule_scope import suggest_review_rows
from studgroup.api import ApiError
from studgroup.models import CabinetScheduleUpload, Group, SchedulePattern

LEASE = timedelta(minutes=6)


async def claim(engine, now):
    """Claim one stage with a short SQL lock; never hold locks during provider HTTP."""
    async with AsyncSession(engine, expire_on_commit=False) as db:
        stale = await db.scalar(
            select(CabinetScheduleUpload)
            .where(
                CabinetScheduleUpload.state == "processing",
                CabinetScheduleUpload.claimed_at.is_not(None),
                CabinetScheduleUpload.claimed_at < now - LEASE,
            )
            .order_by(CabinetScheduleUpload.claimed_at)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if stale is not None:
            # An old invocation may have reached the provider. Automatic retry can
            # charge twice, so fail closed; the headman can request one explicit retry.
            stale.state = "failed"
            stale.progress_stage = "failed"
            stale.claimed_at = None
            await db.commit()
            return "stale", None
        row = await db.scalar(
            select(CabinetScheduleUpload)
            .where(
                CabinetScheduleUpload.state == "queued",
                CabinetScheduleUpload.delete_at > now,
            )
            .order_by(CabinetScheduleUpload.created_at, CabinetScheduleUpload.id)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if row is None:
            return "idle", None
        group = await db.get(Group, row.group_id)
        if group is None or group.status != "active" or not group.pilot_authorized:
            row.state = "failed"
            row.progress_stage = "failed"
            await db.commit()
            return "failed", None
        phase = row.progress_stage
        if phase not in {"queued_a", "queued_b"} or (
            phase == "queued_b" and not row.first_pass_json
        ):
            row.state = "failed"
            row.progress_stage = "failed"
            await db.commit()
            return "failed", None
        row.state = "processing"
        row.progress_stage = "reading_a" if phase == "queued_a" else "reading_b"
        row.progress_percent = 20 if phase == "queued_a" else 65
        row.claimed_at = now
        item = {
            "id": row.id,
            "group_id": row.group_id,
            "week_start": row.week_start,
            "duration": row.duration,
            "image": bytes(row.image_bytes),
            "mime": row.image_mime,
            "first": row.first_pass_json,
            "anchor": group.first_week_anchor,
            "timezone": group.timezone,
            "phase": phase,
        }
        await db.commit()
        return "claimed", item


async def checkpoint(engine, ident, *, stage, state, progress, first=None, rows=None):
    async with AsyncSession(engine, expire_on_commit=False) as db:
        row = await db.get(CabinetScheduleUpload, ident, with_for_update=True)
        if row is None or row.state != "processing":
            return False
        row.state = state
        row.progress_stage = stage
        row.progress_percent = progress
        row.claimed_at = None if state != "processing" else row.claimed_at
        if first is not None:
            row.first_pass_json = first
        if rows is not None:
            row.rows_json = json.dumps(rows, ensure_ascii=False)
        await db.commit()
        return True


async def fail(engine, ident):
    async with AsyncSession(engine) as db:
        row = await db.get(CabinetScheduleUpload, ident, with_for_update=True)
        if row is not None and row.state == "processing":
            row.state = "failed"
            row.progress_stage = "failed"
            row.claimed_at = None
            await db.commit()


async def process_next(engine, settings, provider=None, now=None):
    now = utc(now or datetime.now(UTC))
    status, task = await claim(engine, now)
    if task is None:
        return status
    try:
        port = provider_for(
            engine,
            settings,
            task["group_id"],
            provider,
            kind="cabinet_image",
            attempt_key=f"cabinet-schedule:{task['id']}:{task['phase']}",
        )
        if task["phase"] == "queued_a":
            first, _ = await read_pass(port, task["image"], task["mime"], 1, context={})
            if not first.complete:
                raise ApiError("image_unclear", "Не удалось полностью прочитать изображение", 422)
            ok = await checkpoint(
                engine,
                task["id"],
                stage="queued_b",
                state="queued",
                progress=50,
                first=first.model_dump_json(),
            )
            return "pass_a_ready" if ok else "stale"

        first = VisualReading.model_validate_json(task["first"])
        hint = f"Староста явно выбрал расписание с {task['week_start'].isoformat()} " + (
            "только на эту неделю."
            if task["duration"] == "one_week"
            else "на постоянной основе для этой чётности."
        )
        second, _ = await read_pass(
            port,
            task["image"],
            task["mime"],
            2,
            first=first,
            context={
                "messages": [{"telegram_message_id": 0, "text": hint}],
                "image_source_message_id": 0,
                "group_timezone": task["timezone"],
            },
        )
        if (
            not second.complete
            or not second.schedule
            or not {"schedule", "schedule_change"}.intersection(second.semantic_types)
        ):
            raise ApiError("not_a_schedule", "Не удалось надёжно распознать пары", 422)
        await checkpoint(
            engine,
            task["id"],
            stage="preparing",
            state="processing",
            progress=90,
        )
        scope = ScheduleInterpretation(
            coverage=(
                second.schedule_interpretation.coverage
                if second.schedule_interpretation is not None
                and second.schedule_interpretation.coverage != "unknown"
                else "partial"
            ),
            duration=task["duration"],
            week_start=task["week_start"],
            explanation="Период подтверждён старостой при загрузке",
            source_message_ids=[],
        )
        second = second.model_copy(update={"schedule_interpretation": scope})
        async with AsyncSession(engine) as db:
            horizon = await db.scalar(
                select(func.max(SchedulePattern.valid_until)).where(
                    SchedulePattern.group_id == task["group_id"],
                    SchedulePattern.cancelled.is_(False),
                )
            )
        rows = suggest_review_rows(second, task["anchor"], horizon)
        if not rows:
            raise ApiError("not_a_schedule", "На скриншоте не найдены пары", 422)
        saved = await checkpoint(
            engine,
            task["id"],
            stage="ready",
            state="pending",
            progress=100,
            rows=rows,
        )
        return "ready" if saved else "stale"
    except asyncio.CancelledError:
        # Never automatically retry a potentially billable interrupted invocation.
        raise
    except (ProviderFailure, ApiError, ValueError, TypeError, KeyError):
        await fail(engine, task["id"])
        return "failed"
    except Exception:  # noqa: BLE001 -- redact all provider and image details
        await fail(engine, task["id"])
        return "failed"


async def processing_loop(engine, settings, *, pause_seconds=3):
    """Runs in the deployed web process, which has no separate Celery worker."""
    while True:
        try:
            status = await process_next(engine, settings)
        except asyncio.CancelledError:
            raise
        except Exception as error:  # noqa: BLE001 -- keep web ready, redact private details
            print("CABINET_SCHEDULE_WORKER_UNAVAILABLE " + type(error).__name__, flush=True)
            status = "failed"
        await asyncio.sleep(0.5 if status in {"pass_a_ready", "ready"} else pause_seconds)
