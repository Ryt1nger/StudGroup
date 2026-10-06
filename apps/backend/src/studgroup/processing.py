"""Durable, lease-based extraction. SQL is authoritative; Redis is only a wakeup channel."""

import json
import re
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from studgroup.academic_context import build_context, has_date_cue
from studgroup.ai import BATCH_PROMPT_VERSION, DeepSeekProvider, ProviderFailure
from studgroup.api import schedule_data
from studgroup.deadlines import ScheduleDeadlineContext
from studgroup.models import (
    AcademicDeadline,
    AIAttempt,
    AICandidate,
    AIControl,
    AIJob,
    Group,
    Homework,
    OwnerIncident,
    RawMessage,
    SchedulePattern,
)

MAX_ATTEMPTS = 2
# Upper bound for the byte-bounded request + system instructions and 3,000 output tokens.
# Flash peak prices; a model change requires explicit budget/pricing review.
RESERVATION = Decimal("0.012")
ACADEMIC_CUE = re.compile(
    r"\b(?:дз|кт)\b|домашн|контрольн|самостоят|срок|дедлайн|сдать|задан|тест|стр\.?\s*\d|номер|конспект|презентац|#[\w]+",
    re.IGNORECASE,
)


async def relevant(db, raw):
    if ACADEMIC_CUE.search(raw.text):
        return True
    if has_date_cue(raw.text):
        neighbors = (
            await db.scalars(
                select(RawMessage)
                .where(
                    RawMessage.group_id == raw.group_id,
                    RawMessage.id != raw.id,
                    RawMessage.message_date >= utc(raw.message_date) - timedelta(minutes=3),
                    RawMessage.message_date <= utc(raw.message_date),
                )
                .order_by(RawMessage.message_date.desc())
                .limit(5)
            )
        ).all()
        if any(ACADEMIC_CUE.search(row.text) for row in neighbors):
            return True
    if raw.reply_to_message_id is not None:
        parent = await db.scalar(
            select(RawMessage).where(
                RawMessage.group_id == raw.group_id,
                RawMessage.telegram_message_id == raw.reply_to_message_id,
            )
        )
        if parent:
            return bool(
                await db.scalar(select(Homework.id).where(Homework.raw_message_id == parent.id))
                or await db.scalar(
                    select(AcademicDeadline.id).where(AcademicDeadline.raw_message_id == parent.id)
                )
            )
    return False


def utc(stamp):
    return stamp.replace(tzinfo=UTC) if stamp.tzinfo is None else stamp.astimezone(UTC)


def retain_until(created, *event_dates):
    return max(
        [
            utc(created) + timedelta(days=90),
            *(utc(stamp) + timedelta(days=7) for stamp in event_dates if stamp is not None),
        ]
    )


async def incident(db, code, now, recover=False):
    row = await db.get(OwnerIncident, code)
    if recover:
        if row and row.active:
            row.active = False
            row.recovered_at = now
            row.notification_pending = True
    elif row is None:
        db.add(OwnerIncident(code=code, active=True, opened_at=now, notification_pending=True))
    elif not row.active:
        row.active = True
        row.opened_at = now
        row.recovered_at = None
        row.notification_pending = True


async def claim(engine, settings, now):
    async with AsyncSession(engine, expire_on_commit=False) as db:
        # Singleton lock serializes budget reservations across groups/workers.
        control = await db.scalar(select(AIControl).where(AIControl.id == 1).with_for_update())
        if control is None:
            # create_all tests/local previews; production migration seeds it.
            control = AIControl(id=1, spent_usd=Decimal(0))
            db.add(control)
            await db.flush()
        raws = (
            await db.scalars(
                select(RawMessage)
                .join(Group)
                .where(
                    RawMessage.processing_state == "pending",
                    RawMessage.delete_at > now,
                    Group.pilot_authorized.is_(True),
                    Group.status == "active",
                )
                .order_by(RawMessage.version_date)
                .limit(50)
                .with_for_update(skip_locked=True)
            )
        ).all()
        for raw in raws:
            if not await relevant(db, raw):
                raw.processing_state = "completed"
                continue
            job = await db.scalar(
                select(AIJob)
                .where(
                    AIJob.raw_message_id == raw.id,
                    AIJob.source_revision == raw.revision,
                )
                .with_for_update()
            )
            if job and (
                job.state in {"done", "failed", "superseded"}
                or utc(job.available_at) > now
                or (job.lease_until and utc(job.lease_until) > now)
            ):
                continue
            if job and job.attempts >= MAX_ATTEMPTS:
                job.state = "failed"
                raw.processing_state = "failed"
                await incident(db, "processing_exhausted", now)
                continue
            day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
            daily = await db.scalar(
                select(func.coalesce(func.sum(AIAttempt.charged_usd), 0)).where(
                    AIAttempt.group_id == raw.group_id,
                    AIAttempt.created_at >= day_start,
                )
            )
            if control.spent_usd + RESERVATION > Decimal(
                str(settings.ai_total_budget_usd)
            ) or Decimal(daily) + RESERVATION > Decimal(str(settings.ai_daily_group_budget_usd)):
                await incident(db, "ai_budget_limit", now)
                continue
            if job is None:
                job = AIJob(
                    raw_message_id=raw.id,
                    group_id=raw.group_id,
                    source_revision=raw.revision,
                    available_at=now,
                    created_at=now,
                    attempts=0,
                )
                db.add(job)
                await db.flush()
            job.state = "running"
            job.attempts += 1
            job.lease_until = now + timedelta(minutes=3)
            control.spent_usd += RESERVATION
            attempt = AIAttempt(
                job_id=job.id,
                group_id=raw.group_id,
                charged_usd=RESERVATION,
                model=settings.deepseek_model,
                prompt_version=BATCH_PROMPT_VERSION,
                created_at=now,
            )
            db.add(attempt)
            await db.flush()
            ids = job.id, attempt.id, raw.id, raw.revision, job.attempts
            await db.commit()
            return ids
        await db.commit()
    return None


async def context_for(db, raw, group, now):
    rows = (
        await db.scalars(
            select(RawMessage)
            .where(
                RawMessage.group_id == raw.group_id,
                RawMessage.delete_at > now,
                RawMessage.message_date >= utc(raw.message_date) - timedelta(days=7),
            )
            .order_by(RawMessage.message_date.desc())
            .limit(250)
        )
    ).all()
    indexed = {row.id: row for row in rows}
    indexed[raw.id] = raw
    messages = [
        SimpleNamespace(
            message_id=row.telegram_message_id,
            reply_to_message_id=row.reply_to_message_id,
            message_date=utc(row.message_date),
            text=row.text,
        )
        for row in indexed.values()
    ]
    context = build_context(messages, raw.telegram_message_id, max_bytes=12500)
    patterns = (
        await db.scalars(select(SchedulePattern).where(SchedulePattern.group_id == group.id))
    ).all()
    today = now.astimezone(ZoneInfo(group.timezone)).date()
    source_day = utc(raw.message_date).astimezone(ZoneInfo(group.timezone)).date()
    intervals = sorted({(p.valid_from, p.valid_until) for p in patterns})
    merged = []
    for low, high in intervals:
        if merged and low <= merged[-1][1] + timedelta(days=1):
            merged[-1] = (merged[-1][0], max(merged[-1][1], high))
        else:
            merged.append((low, high))
    span = next(
        (span for span in merged if span[0] <= source_day <= span[1]),
        next((span for span in merged if span[0] <= today <= span[1]), None),
    )
    if span is None:
        return context, None
    start = max(span[0], min(source_day, today))
    end = min(span[1], today + timedelta(days=14))
    calendar = await schedule_data(start, end, group, db)
    schedule = ScheduleDeadlineContext(
        calendar["lessons"], start, end, calendar["week_state"] == "ready"
    )
    return context, schedule


def cost(usage):
    hit = min(usage.prompt_cache_hit_tokens, usage.prompt_tokens)
    miss = usage.prompt_tokens - hit
    return (
        Decimal(miss) * Decimal("0.30")
        + Decimal(hit) * Decimal("0.006")
        + Decimal(usage.completion_tokens) * Decimal("1.20")
    ) / Decimal(1000000)


async def publish(db, raw, extraction, now):
    state = extraction.publication_state
    if state not in {"published", "needs_clarification"}:
        return False
    if not all((extraction.subject, extraction.title, extraction.description)):
        return False
    is_inferred = extraction._deadline_basis in {
        "next_subject_lesson",
        "provisional_next_subject_lesson",
    }
    deadline = utc(extraction.deadline_at) if extraction.deadline_at else None
    # An explicit reply/amendment updates the referenced task, not a duplicate task.
    original = raw
    if (
        has_date_cue(raw.text)
        and not ACADEMIC_CUE.search(raw.text)
        and raw.reply_to_message_id is None
    ):
        # A neighboring deadline-only fragment can amend exactly one cited task.
        # Never choose a random subject/task merely because it was posted nearby.
        candidates = []
        parents = (
            await db.scalars(
                select(RawMessage).where(
                    RawMessage.group_id == raw.group_id,
                    RawMessage.telegram_message_id.in_(extraction.source_message_ids),
                    RawMessage.id != raw.id,
                )
            )
        ).all()
        for parent in parents:
            card = await db.scalar(select(Homework).where(Homework.raw_message_id == parent.id))
            event = await db.scalar(
                select(AcademicDeadline).where(AcademicDeadline.raw_message_id == parent.id)
            )
            name = card.subject_name if card else event.subject if event else None
            if name and name.casefold().strip() == extraction.subject.casefold().strip():
                candidates.append(parent)
        if len(candidates) != 1:
            return False
        original = candidates[0]
    if raw.reply_to_message_id in extraction.source_message_ids:
        parent = await db.scalar(
            select(RawMessage).where(
                RawMessage.group_id == raw.group_id,
                RawMessage.telegram_message_id == raw.reply_to_message_id,
            )
        )
        if parent:
            card = await db.scalar(select(Homework).where(Homework.raw_message_id == parent.id))
            event = await db.scalar(
                select(AcademicDeadline).where(AcademicDeadline.raw_message_id == parent.id)
            )
            name = card.subject_name if card else event.subject if event else None
            if name and name.casefold().strip() == extraction.subject.casefold().strip():
                original = parent
    if extraction.kind == "homework":
        existing = await db.scalar(
            select(Homework).where(Homework.raw_message_id == original.id).with_for_update()
        )
        values = {
            "subject_name": extraction.subject,
            "subject_id": uuid.uuid5(raw.group_id, extraction.subject.casefold()),
            "title": extraction.title,
            "description": extraction.description,
            "deadline_at": deadline,
            "deadline_date_only": extraction.deadline_date_only,
            "status": state,
            "urgency": extraction.urgency,
            "verification_state": "inferred" if is_inferred else "from_group_message",
        }
        if existing:
            # A missing/model-assumed date never erases a previously explicit deadline.
            if existing.deadline_at and (deadline is None or is_inferred):
                values["deadline_at"] = existing.deadline_at
                values["deadline_date_only"] = existing.deadline_date_only
                values["verification_state"] = existing.verification_state
            changed = any(
                (
                    utc(getattr(existing, key))
                    if key == "deadline_at" and getattr(existing, key)
                    else getattr(existing, key)
                )
                != value
                for key, value in values.items()
            )
            if changed:
                for key, value in values.items():
                    setattr(existing, key, value)
                existing.revision += 1
                existing.updated_at = now
                existing.significant_updated_at = now
                existing.delete_at = max(
                    utc(existing.delete_at), retain_until(existing.created_at, existing.deadline_at)
                )
        else:
            db.add(
                Homework(
                    group_id=raw.group_id,
                    raw_message_id=original.id,
                    **values,
                    created_at=now,
                    updated_at=now,
                    delete_at=retain_until(now, deadline),
                )
            )
        other = await db.scalar(
            select(AcademicDeadline).where(AcademicDeadline.raw_message_id == original.id)
        )
        if other:
            # Classification correction: retained old event becomes inaccessible, no duplicate list entry.
            other.delete_at = now
    else:
        key = f"telegram:{original.telegram_message_id}"
        row = await db.scalar(
            select(AcademicDeadline).where(
                AcademicDeadline.group_id == raw.group_id, AcademicDeadline.import_key == key
            )
        )
        if row is None:
            row = AcademicDeadline(
                group_id=raw.group_id,
                raw_message_id=original.id,
                import_key=key,
                created_at=now,
                delete_at=now + timedelta(days=90),
            )
            db.add(row)
        row.kind = extraction.kind
        row.subject = extraction.subject
        row.title = extraction.title
        row.description = extraction.description
        if (
            deadline is not None
            or extraction._window_start is not None
            or (row.deadline_at is None and row.window_start is None)
        ):
            row.deadline_at = deadline
            row.date_only = extraction.deadline_date_only
            row.window_start = utc(extraction._window_start) if extraction._window_start else None
            row.window_end = utc(extraction._window_end) if extraction._window_end else None
        row.needs_clarification = row.deadline_at is None and row.window_start is None
        row.delete_at = max(
            utc(row.delete_at), retain_until(row.created_at, row.deadline_at, row.window_end)
        )
        row.source_message_ids = json.dumps(extraction.source_message_ids)
        old = await db.scalar(select(Homework).where(Homework.raw_message_id == original.id))
        if old:
            old.status = "incomplete_hidden"
    return True


async def process_next(engine, settings, provider=None, now=None):
    now = now or datetime.now(UTC)
    if not settings.ai_enabled:
        return "disabled"
    if settings.deepseek_model != "deepseek-flash":
        return "model_budget_not_reviewed"
    if not settings.deepseek_api_key.get_secret_value() and provider is None:
        async with AsyncSession(engine) as db:
            await incident(db, "invalid_api_key", now)
            await db.commit()
        return "not_configured"
    claimed = await claim(engine, settings, now)
    if claimed is None:
        return "idle"
    job_id, attempt_id, raw_id, revision, generation = claimed
    async with AsyncSession(engine) as db:
        raw = await db.get(RawMessage, raw_id)
        group = await db.get(Group, raw.group_id)
        context, schedule = await context_for(db, raw, group, now)
        timezone, message_id = group.timezone, raw.telegram_message_id
    provider = provider or DeepSeekProvider(
        settings.deepseek_api_key.get_secret_value(),
        model=settings.deepseek_model,
        base_url=settings.deepseek_base_url,
    )
    result = None
    failure = None
    try:
        result = await provider.extract_batch(
            context, timezone, schedule, target_message_id=message_id
        )
    except ProviderFailure as error:
        failure = error
    async with AsyncSession(engine) as db:
        control = await db.scalar(select(AIControl).where(AIControl.id == 1).with_for_update())
        job = await db.get(AIJob, job_id)
        raw = await db.get(RawMessage, raw_id)
        attempt = await db.get(AIAttempt, attempt_id)
        if result:
            actual = cost(result.usage)
            control.spent_usd += actual - attempt.charged_usd
            attempt.charged_usd = actual
            attempt.prompt_tokens = result.usage.prompt_tokens
            attempt.completion_tokens = result.usage.completion_tokens
            attempt.prompt_version = result.prompt_version
        # A timed-out worker must not overwrite the result or lease of a newer attempt.
        if job.attempts != generation or job.state != "running":
            await db.commit()
            return "superseded"
        job.lease_until = None
        outcome = "completed"
        if result:
            # A stale reply never overwrites a concurrently edited Telegram message.
            if raw and raw.revision == revision:
                candidates = []
                for ordinal, assignment in enumerate(result.batch.assignments):
                    payload = assignment.model_dump(mode="json") | {
                        "deadline_basis": assignment._deadline_basis,
                        "window_start": assignment._window_start.isoformat()
                        if assignment._window_start
                        else None,
                        "window_end": assignment._window_end.isoformat()
                        if assignment._window_end
                        else None,
                    }
                    candidate = AICandidate(
                        job_id=job.id,
                        group_id=raw.group_id,
                        ordinal=ordinal,
                        payload=json.dumps(payload, ensure_ascii=False),
                        state="review",
                        created_at=now,
                        delete_at=retain_until(now, assignment.deadline_at, assignment._window_end),
                    )
                    db.add(candidate)
                    candidates.append(candidate)
                if len(result.batch.assignments) > 1:
                    # One raw-to-homework row cannot silently discard a second task.
                    raw.processing_state = "needs_context"
                    job.last_error = "multiple_tasks_require_review"
                else:
                    published = True
                    for assignment, candidate in zip(
                        result.batch.assignments, candidates, strict=True
                    ):
                        accepted = await publish(db, raw, assignment, now)
                        candidate.state = "published" if accepted else "review"
                        published = accepted and published
                    raw.processing_state = "completed" if published else "needs_context"
                job.state = "done"
            else:
                job.state = "superseded"
            for code in ["provider_unreachable", "provider_error", "rate_limited"]:
                await incident(db, code, now, recover=True)
        elif failure:
            job.last_error = failure.code
            job.state = "retry" if failure.retryable and job.attempts < MAX_ATTEMPTS else "failed"
            outcome = job.state
            job.available_at = now + timedelta(minutes=5)
            if raw and raw.revision == revision:
                raw.processing_state = "pending" if job.state == "retry" else "failed"
            await incident(db, failure.code, now)
            # Unknown provider usage keeps the full reservation, never charges zero on timeout.
        await db.commit()
    return outcome
