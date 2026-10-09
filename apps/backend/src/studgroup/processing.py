"""Durable, lease-based extraction. SQL is authoritative; Redis is only a wakeup channel."""

import asyncio
import json
import re
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from time import monotonic
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from sqlalchemy import and_, exists, func, or_, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from studgroup.academic_context import build_context, has_date_cue
from studgroup.ai import (
    BATCH_PROMPT_VERSION,
    SCREEN_PROMPT_VERSION,
    DeepSeekProvider,
    ProviderFailure,
)
from studgroup.api import schedule_data
from studgroup.deadlines import ScheduleDeadlineContext
from studgroup.homework_timing import lesson_deadline
from studgroup.models import (
    AcademicDeadline,
    AIAttempt,
    AICandidate,
    AIControl,
    AIJob,
    Group,
    GroupAIActivity,
    Homework,
    OwnerIncident,
    OwnerIncidentEpisode,
    RawMessage,
    SchedulePattern,
)
from studgroup.notifications import record_change

MAX_ATTEMPTS = 2
RECOVERABLE_FAILURES = {
    "provider_unreachable",
    "provider_error",
    "rate_limited",
    "processing_window_closed",
}
PROVIDER_INCIDENT_CODES = {
    "provider_unreachable",
    "provider_error",
    "rate_limited",
    "invalid_api_key",
    "insufficient_balance",
    "provider_not_configured",
    "invalid_provider_url",
    "invalid_provider_output",
    "incomplete_output",
    "invalid_date_only",
    "invalid_source_reference",
    "invalid_target_reference",
    "context_too_large",
    "invalid_message_timestamp",
    "pipeline_error",
}


def recovery_delay(attempts):
    """Back off transport recovery without abandoning the durable source version."""
    return min(30 * 2 ** min(max(attempts - 1, 0), 4), 300)


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


async def adopt_incident_episode(db, row):
    episode = (
        await db.get(OwnerIncidentEpisode, row.current_episode_id)
        if row.current_episode_id
        else None
    )
    if episode is None or (row.active and utc(row.opened_at) > utc(episode.opened_at)):
        episode = OwnerIncidentEpisode(
            code=row.code,
            opened_at=row.opened_at,
            last_seen_at=row.opened_at,
            recovered_at=row.recovered_at,
            occurrences=1,
            opening_pending=row.active and row.notification_pending,
            recovery_pending=not row.active and row.notification_pending,
            legacy=True,
        )
        db.add(episode)
        await db.flush()
        row.current_episode_id = episode.id
    elif not row.active and row.recovered_at and episode.recovered_at is None:
        episode.recovered_at = row.recovered_at
        episode.recovery_pending = row.notification_pending
    return episode


async def incident(db, code, now, recover=False, detail=None):
    inserted = None
    if not recover:
        inserted = (
            await db.execute(
                insert(OwnerIncident)
                .values(code=code, active=True, opened_at=now, notification_pending=True)
                .on_conflict_do_nothing(index_elements=[OwnerIncident.code])
                .returning(OwnerIncident.code)
            )
        ).scalar_one_or_none()
    row = await db.scalar(select(OwnerIncident).where(OwnerIncident.code == code).with_for_update())
    if row is None:
        return None
    episode = await adopt_incident_episode(db, row)
    if inserted is not None:
        episode.legacy = False
        episode.first_detail = detail
        episode.last_detail = detail
    if recover:
        if row and row.active:
            if utc(now) < utc(episode.last_seen_at):
                return episode.id
            row.active = False
            row.recovered_at = max(utc(now), utc(episode.last_seen_at))
            row.notification_pending = True
            episode.recovered_at = row.recovered_at
            episode.recovery_pending = True
    elif not row.active:
        row.active = True
        row.opened_at = now
        row.recovered_at = None
        row.notification_pending = True
        episode = OwnerIncidentEpisode(
            code=code,
            opened_at=now,
            last_seen_at=now,
            occurrences=1,
            first_detail=detail,
            last_detail=detail,
            opening_pending=True,
            recovery_pending=False,
            legacy=False,
        )
        db.add(episode)
        await db.flush()
        row.current_episode_id = episode.id
    elif inserted is None:
        episode.occurrences += 1
        episode.last_seen_at = max(utc(now), utc(episode.last_seen_at))
        if detail:
            episode.last_detail = detail
    return episode.id


async def claim(engine, settings, now):
    filter_started = monotonic()
    filtered_sources = {}
    scheduled = settings.ai_schedule_enabled
    slot = None
    extended = False
    if scheduled:
        from studgroup.ai_schedule import window

        timing = window(now, settings.ai_schedule_timezone)
        if timing is None:
            return None
        slot, _close, extended = timing
    async with AsyncSession(engine, expire_on_commit=False) as db:
        # Singleton lock serializes budget reservations across groups/workers.
        control = await db.scalar(select(AIControl).where(AIControl.id == 1).with_for_update())
        if control is None:
            # create_all tests/local previews; production migration seeds it.
            control = AIControl(id=1, spent_usd=Decimal(0))
            db.add(control)
            await db.flush()
        if settings.ai_live_two_pass:
            # Old and new instances may overlap during deploy. Restore unpaid local
            # discards under the same singleton lock used by both worker generations.
            # Once a model job exists this never reopens the source again.
            await db.execute(
                update(RawMessage)
                .where(
                    RawMessage.imported.is_(False),
                    RawMessage.processing_state == "completed",
                    RawMessage.live_received_at.is_not(None),
                    RawMessage.message_date >= now - timedelta(days=7),
                    RawMessage.delete_at > now,
                    RawMessage.group_id.in_(
                        select(Group.id).where(
                            Group.pilot_authorized.is_(True), Group.status == "active"
                        )
                    ),
                    ~exists(select(AIJob.id).where(AIJob.raw_message_id == RawMessage.id)),
                )
                .values(processing_state="pending")
            )
        # Recover transport jobs abandoned by the previous two-attempt policy.
        # Do not reopen invalid-output/rejected requests or obsolete source revisions.
        abandoned = (
            await db.execute(
                select(AIJob, RawMessage)
                .join(RawMessage, AIJob.raw_message_id == RawMessage.id)
                .join(Group, RawMessage.group_id == Group.id)
                .where(
                    AIJob.state == "failed",
                    AIJob.last_error.in_(
                        ["provider_unreachable", "rate_limited", "processing_window_closed"]
                    ),
                    AIJob.source_revision == RawMessage.revision,
                    RawMessage.processing_state == "failed",
                    RawMessage.delete_at > now,
                    Group.status == "active",
                    Group.pilot_authorized.is_(True),
                )
                .limit(50)
                .with_for_update(skip_locked=True)
            )
        ).all()
        for abandoned_job, abandoned_raw in abandoned:
            abandoned_job.state = "retry"
            abandoned_job.available_at = now
            abandoned_raw.processing_state = "pending"
        if abandoned:
            await db.flush()
        import_scope = (
            and_(
                RawMessage.imported.is_(True), Group.telegram_chat_id == settings.ai_import_chat_id
            )
            if settings.ai_import_chat_id is not None and not scheduled
            else False
        )
        already_processed = exists(
            select(AIJob.id).where(
                AIJob.raw_message_id == RawMessage.id,
                AIJob.source_revision == RawMessage.revision,
                AIJob.state.in_(["done", "failed", "superseded"]),
            )
        )
        raws = (
            await db.scalars(
                select(RawMessage)
                .join(Group)
                .where(
                    or_(
                        RawMessage.processing_state == "pending",
                        and_(import_scope, RawMessage.processing_state == "needs_context"),
                    ),
                    or_(RawMessage.delete_at > now, import_scope),
                    Group.pilot_authorized.is_(True),
                    Group.status == "active",
                    ~already_processed,
                    *(
                        [
                            RawMessage.imported.is_(False),
                            RawMessage.live_received_at.is_not(None),
                            RawMessage.live_received_at <= slot,
                            exists(
                                select(GroupAIActivity.group_id).where(
                                    GroupAIActivity.group_id == RawMessage.group_id,
                                    GroupAIActivity.last_signal_at >= slot - timedelta(minutes=30),
                                )
                            )
                            if extended
                            else True,
                        ]
                        if scheduled
                        else []
                    ),
                )
                .order_by(RawMessage.version_date)
                .limit(50)
                .with_for_update(skip_locked=True)
            )
        ).all()
        for raw in raws:
            if not settings.ai_live_two_pass and not await relevant(db, raw):
                raw.processing_state = "completed"
                filtered_sources.setdefault(raw.group_id, []).append(
                    f"{raw.id}:{raw.revision}:{raw.telegram_message_id}"
                )
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
            recovering = job and (
                (job.state == "retry" and job.last_error in RECOVERABLE_FAILURES)
                or (job.state == "running" and job.lease_until and utc(job.lease_until) <= now)
                or (settings.ai_live_two_pass and job.screen_checkpoint and job.state == "queued")
            )
            if job and job.state == "running" and job.lease_until and utc(job.lease_until) <= now:
                from studgroup.run_reports import finish as finish_report

                unfinished = (
                    await db.scalars(
                        select(AIAttempt).where(
                            AIAttempt.job_id == job.id, AIAttempt.finished_at.is_(None)
                        )
                    )
                ).all()
                for unfinished_attempt in unfinished:
                    await finish_report(
                        db,
                        settings,
                        await db.get(Group, raw.group_id),
                        job,
                        unfinished_attempt,
                        "interrupted",
                        now,
                        error="lease_expired",
                    )
            if (
                scheduled
                and job
                and job.schedule_slot
                and utc(job.schedule_slot) >= slot
                and not recovering
            ):
                continue
            screened_generation = (
                json.loads(job.screen_checkpoint).get("generation", 0)
                if job and job.screen_checkpoint
                else 0
            )
            if job and job.attempts - screened_generation >= MAX_ATTEMPTS and not recovering:
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
                await incident(
                    db, "ai_budget_limit", now + timedelta(seconds=monotonic() - filter_started)
                )
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
            if scheduled:
                job.schedule_slot = slot
                activity = await db.get(GroupAIActivity, raw.group_id)
                if activity:
                    activity.last_batch_slot = slot
            job.attempts += 1
            job.lease_until = now + timedelta(minutes=3)
            control.spent_usd += RESERVATION
            attempt = AIAttempt(
                job_id=job.id,
                group_id=raw.group_id,
                charged_usd=RESERVATION,
                model=settings.deepseek_model,
                prompt_version=SCREEN_PROMPT_VERSION
                if settings.ai_live_two_pass and not job.screen_checkpoint
                else BATCH_PROMPT_VERSION,
                created_at=now,
            )
            db.add(attempt)
            await db.flush()
            from studgroup.run_reports import filtered as filtered_report
            from studgroup.run_reports import start as start_report

            await filtered_report(db, settings, filtered_sources, now, monotonic() - filter_started)
            await start_report(db, settings, await db.get(Group, raw.group_id), job, attempt, raw)
            ids = job.id, attempt.id, raw.id, raw.revision, job.attempts
            await db.commit()
            return ids
        from studgroup.run_reports import filtered as filtered_report

        await filtered_report(db, settings, filtered_sources, now, monotonic() - filter_started)
        if settings.ai_schedule_enabled:
            from studgroup.batch_reports import finish_ready

            await finish_ready(db, settings, now)
        await db.commit()
        return None


async def context_for(db, raw, group, now, *, semantic_neighborhood=False):
    base = select(RawMessage).where(
        RawMessage.group_id == raw.group_id,
        or_(
            RawMessage.delete_at > now,
            RawMessage.imported.is_(True) if raw.imported else False,
        ),
        RawMessage.message_date >= utc(raw.message_date) - timedelta(days=7),
        RawMessage.message_date <= utc(raw.message_date) + timedelta(days=7),
    )
    before = (
        await db.scalars(
            base.where(RawMessage.message_date <= utc(raw.message_date))
            .order_by(RawMessage.message_date.desc())
            .limit(125)
        )
    ).all()
    after = (
        await db.scalars(
            base.where(RawMessage.message_date > utc(raw.message_date))
            .order_by(RawMessage.message_date)
            .limit(125)
        )
    ).all()
    rows = [*before, *after]
    if raw.reply_to_message_id is not None:
        parent = await db.scalar(
            select(RawMessage).where(
                RawMessage.group_id == raw.group_id,
                RawMessage.telegram_message_id == raw.reply_to_message_id,
                or_(
                    RawMessage.delete_at > now,
                    RawMessage.imported.is_(True) if raw.imported else False,
                ),
            )
        )
        if parent:
            rows.append(parent)
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
    context = build_context(
        messages,
        raw.telegram_message_id,
        max_bytes=12500,
        neighborhood=8 if semantic_neighborhood else 0,
    )
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


async def publish(db, raw, extraction, now, statistics=None):
    state = extraction.publication_state
    if state not in {"published", "needs_clarification"}:
        return False
    if not all((extraction.subject, extraction.title, extraction.description)):
        return False

    def track(created, changed):
        if statistics is None:
            return
        field = "created_cards" if created else "updated_cards" if changed else "unchanged_cards"
        statistics[field] = statistics.get(field, 0) + 1
        if created or changed:
            statistics["applied_fragments"] = statistics.get("applied_fragments", 0) + 1
            statistics.setdefault("_used_source_ids", set()).update(extraction.source_message_ids)

    is_inferred = extraction._deadline_basis in {
        "next_subject_lesson",
        "provisional_next_subject_lesson",
        "relative_message_date_inferred_clock",
        "explicit_source_date_inferred_clock",
        "source_day_inferred_clock",
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
    # A manual candidate publication and an AI result for the same source serialize.
    await db.scalar(
        select(RawMessage.id)
        .where(RawMessage.id == original.id, RawMessage.group_id == raw.group_id)
        .with_for_update()
    )
    if extraction.kind == "homework":
        from studgroup.headman_domain import ai_locked

        if await ai_locked(db, raw.group_id, original.id):
            return False
        group = await db.get(Group, raw.group_id)
        deadline, date_only = await lesson_deadline(
            db,
            group,
            extraction.subject,
            deadline,
            extraction.deadline_date_only,
            raw.text + "\n" + original.text,
        )
        changed = True
        existing = await db.scalar(
            select(Homework).where(Homework.raw_message_id == original.id).with_for_update()
        )
        new_card = existing is None
        if existing and await ai_locked(db, raw.group_id, original.id):
            return False
        values = {
            "subject_name": extraction.subject,
            "subject_id": uuid.uuid5(raw.group_id, extraction.subject.casefold()),
            "title": extraction.title,
            "description": extraction.description,
            "deadline_at": deadline,
            "deadline_date_only": date_only,
            "status": state,
            "urgency": extraction.urgency,
            "verification_state": "inferred" if is_inferred else "from_group_message",
            "source_message_at": utc(original.message_date),
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
                    if key in {"deadline_at", "source_message_at"} and getattr(existing, key)
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
            existing = Homework(
                group_id=raw.group_id,
                raw_message_id=original.id,
                **values,
                created_at=now,
                updated_at=now,
                delete_at=retain_until(now, deadline),
            )
            db.add(existing)
        await db.flush()
        if changed:
            await record_change(db, raw, existing, "homework", now)
        track(new_card, changed)
        other = await db.scalar(
            select(AcademicDeadline).where(AcademicDeadline.raw_message_id == original.id)
        )
        if other:
            # Classification correction: retained old event becomes inaccessible, no duplicate list entry.
            if statistics is not None and utc(other.delete_at) > now:
                statistics["updated_cards"] = statistics.get("updated_cards", 0) + 1
                statistics["reclassified_cards"] = statistics.get("reclassified_cards", 0) + 1
                if not (new_card or changed):
                    statistics["applied_fragments"] = statistics.get("applied_fragments", 0) + 1
                    statistics.setdefault("_used_source_ids", set()).update(
                        extraction.source_message_ids
                    )
            other.delete_at = now
    else:
        from studgroup.headman_domain import ai_locked

        if await ai_locked(db, raw.group_id, original.id):
            return False
        key = f"telegram:{original.telegram_message_id}"
        row = await db.scalar(
            select(AcademicDeadline)
            .where(AcademicDeadline.group_id == raw.group_id, AcademicDeadline.import_key == key)
            .with_for_update()
        )
        if row is None:
            # A reviewed baseline may use a non-Telegram import key. Reuse its
            # unique source-linked event instead of duplicating it during replay.
            legacy = (
                await db.scalars(
                    select(AcademicDeadline)
                    .where(
                        AcademicDeadline.group_id == raw.group_id,
                        AcademicDeadline.raw_message_id == original.id,
                        AcademicDeadline.delete_at > now,
                    )
                    .with_for_update()
                )
            ).all()
            if len(legacy) > 1:
                return False
            if legacy:
                row = legacy[0]
        new_card = row is None
        if row and await ai_locked(db, raw.group_id, original.id):
            return False
        if row is None:
            row = AcademicDeadline(
                group_id=raw.group_id,
                raw_message_id=original.id,
                import_key=key,
                created_at=now,
                delete_at=now + timedelta(days=90),
            )
            db.add(row)
        before = (
            row.kind,
            row.subject,
            row.title,
            row.description,
            row.deadline_at,
            row.window_start,
            row.window_end,
        )
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
            if statistics is not None and old.status != "incomplete_hidden":
                statistics["updated_cards"] = statistics.get("updated_cards", 0) + 1
                statistics["reclassified_cards"] = statistics.get("reclassified_cards", 0) + 1
                if not new_card and before == (
                    row.kind,
                    row.subject,
                    row.title,
                    row.description,
                    row.deadline_at,
                    row.window_start,
                    row.window_end,
                ):
                    statistics["applied_fragments"] = statistics.get("applied_fragments", 0) + 1
                    statistics.setdefault("_used_source_ids", set()).update(
                        extraction.source_message_ids
                    )
            old.status = "incomplete_hidden"
        await db.flush()
        if before != (
            row.kind,
            row.subject,
            row.title,
            row.description,
            row.deadline_at,
            row.window_start,
            row.window_end,
        ):
            row.revision += 1
        if before != (
            row.kind,
            row.subject,
            row.title,
            row.description,
            row.deadline_at,
            row.window_start,
            row.window_end,
        ):
            await record_change(db, raw, row, "deadline", now)
        track(
            new_card,
            before
            != (
                row.kind,
                row.subject,
                row.title,
                row.description,
                row.deadline_at,
                row.window_start,
                row.window_end,
            ),
        )
    return True


async def process_next(engine, settings, provider=None, now=None):
    now = utc(now or datetime.now(UTC))
    started = monotonic()

    def clock():
        return now + timedelta(seconds=monotonic() - started)

    if not settings.ai_enabled:
        return "disabled"
    if settings.ai_enabled_until is not None and utc(settings.ai_enabled_until) <= clock():
        return "scheduled_off"
    daily_close = None
    if settings.ai_schedule_enabled:
        from studgroup.ai_schedule import window

        timing = window(clock(), settings.ai_schedule_timezone)
        if timing is None:
            return "outside_hours"
        _slot, daily_close, _extended = timing
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
        job = await db.get(AIJob, job_id)
        screening = settings.ai_live_two_pass and not job.screen_checkpoint
        context, schedule = await context_for(
            db, raw, group, now, semantic_neighborhood=settings.ai_live_two_pass
        )
        timezone, message_id = group.timezone, raw.telegram_message_id
        attempt = await db.get(AIAttempt, attempt_id)
        initial_metrics = json.loads(attempt.metrics or "{}")
        initial_metrics["context_messages"] = max(0, len(context) - 1)
        initial_metrics["submitted_messages"] = len(context)
        attempt.metrics = json.dumps(initial_metrics)
        await db.commit()
    result = None
    screen_result = None
    failure = None
    failure_at = None
    try:
        provider = provider or DeepSeekProvider(
            settings.deepseek_api_key.get_secret_value(),
            model=settings.deepseek_model,
            base_url=settings.deepseek_base_url,
        )
        cutoffs = [
            utc(value) for value in [settings.ai_enabled_until, daily_close] if value is not None
        ]
        remaining = (min(cutoffs) - clock()).total_seconds() if cutoffs else None
        if remaining is not None and remaining <= 0:
            failure = ProviderFailure("processing_window_closed", True, reservation_releasable=True)
        else:
            async with asyncio.timeout(remaining):
                if screening:
                    screen_result = await provider.screen_batch(context, timezone, message_id)
                else:
                    result = await provider.extract_batch(
                        context, timezone, schedule, target_message_id=message_id
                    )
    except TimeoutError:
        failure = ProviderFailure("processing_window_closed", True)
        failure_at = clock()
    except ProviderFailure as error:
        failure = error
        failure_at = clock()
    response_at = clock()
    failure_at = failure_at or response_at
    async with AsyncSession(engine) as db:
        control = await db.scalar(select(AIControl).where(AIControl.id == 1).with_for_update())
        job = await db.get(AIJob, job_id)
        raw = await db.get(RawMessage, raw_id)
        attempt = await db.get(AIAttempt, attempt_id)
        response = screen_result or result
        reported_usage = response.usage if response else failure.usage if failure else None
        if reported_usage is not None:
            actual = cost(reported_usage)
            control.spent_usd += actual - attempt.charged_usd
            attempt.charged_usd = actual
            attempt.prompt_tokens = reported_usage.prompt_tokens
            attempt.completion_tokens = reported_usage.completion_tokens
            if response:
                attempt.prompt_version = response.prompt_version
        elif failure and failure.reservation_releasable:
            # Settle this attempt under the same singleton lock used to reserve funds.
            # This also adjusts daily sums; a superseded job still owns its own charge.
            control.spent_usd -= attempt.charged_usd
            attempt.charged_usd = Decimal(0)
        # A timed-out worker must not overwrite the result or lease of a newer attempt.
        if job.attempts != generation or job.state != "running":
            from studgroup.run_reports import finish as finish_report

            incident_number = (
                await incident(db, failure.code, failure_at, detail=failure.detail)
                if failure
                else None
            )

            await finish_report(
                db,
                settings,
                await db.get(Group, job.group_id),
                job,
                attempt,
                "superseded",
                clock(),
                metrics={
                    "context_messages": max(0, len(context) - 1),
                    "submitted_messages": len(context),
                    "analyzed_fragments": int(result is not None),
                    "incident_number": incident_number,
                    "important_proposals": len(result.batch.assignments)
                    + len(result.batch.online_lessons)
                    if result
                    else 0,
                    "important_fragments": len(
                        {
                            mid
                            for assignment in [
                                *result.batch.assignments,
                                *result.batch.online_lessons,
                            ]
                            for mid in assignment.source_message_ids
                        }
                    )
                    if result
                    else 0,
                },
                error=failure.code if failure else "newer_attempt_owns_job",
            )
            await db.commit()
            return "superseded"
        job.lease_until = None
        outcome = "completed"
        statistics = {}
        candidates = []
        if screen_result:
            if raw and raw.revision == revision:
                job.screen_checkpoint = json.dumps(
                    {
                        "generation": generation,
                        "decision": screen_result.decision.model_dump(),
                    }
                )
                if screen_result.decision.signal:
                    job.state = "queued"
                    job.available_at = response_at
                    raw.processing_state = "pending"
                    outcome = "screened"
                else:
                    job.state = "done"
                    raw.processing_state = "completed"
                job.last_error = None
                statistics["screened_messages"] = 1
                statistics["screen_signals"] = int(screen_result.decision.signal)
                for code in PROVIDER_INCIDENT_CODES:
                    await incident(db, code, response_at, recover=True)
            else:
                job.state = "superseded"
        elif result:
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
                        accepted = await publish(db, raw, assignment, now, statistics=statistics)
                        candidate.state = "published" if accepted else "review"
                        published = accepted and published
                    raw.processing_state = "completed" if published else "needs_context"
                if result.batch.online_lessons:
                    from studgroup.online_lessons import discover

                    for proposal in result.batch.online_lessons:
                        cited = (
                            await db.scalars(
                                select(RawMessage).where(
                                    RawMessage.group_id == raw.group_id,
                                    RawMessage.telegram_message_id.in_(proposal.source_message_ids),
                                )
                            )
                        ).all()
                        link_source = next((r for r in cited if proposal.url in r.text), None)
                        if link_source:
                            bound = await discover(
                                db,
                                link_source,
                                subject=proposal.subject,
                                lesson_date=proposal.lesson_date,
                                permanent=proposal.permanent,
                                confidence=proposal.confidence,
                                only_url=proposal.url,
                                lesson_time=proposal.lesson_time,
                            )
                            statistics["online_links_bound"] = (
                                statistics.get("online_links_bound", 0) + bound
                            )
                job.state = "done"
            else:
                job.state = "superseded"
            for code in PROVIDER_INCIDENT_CODES:
                await incident(db, code, response_at, recover=True)
        elif failure:
            job.last_error = failure.code
            recoverable = failure.retryable and failure.code in RECOVERABLE_FAILURES
            job.state = (
                "retry"
                if failure.retryable
                and (
                    recoverable
                    or job.attempts
                    - (
                        json.loads(job.screen_checkpoint).get("generation", 0)
                        if job.screen_checkpoint
                        else 0
                    )
                    < MAX_ATTEMPTS
                )
                else "failed"
            )
            outcome = job.state
            job.available_at = (
                now + timedelta(seconds=recovery_delay(job.attempts))
                if recoverable
                else now + timedelta(minutes=5)
            )
            if raw and raw.revision == revision:
                raw.processing_state = "pending" if job.state == "retry" else "failed"
            statistics["incident_number"] = await incident(
                db, failure.code, failure_at, detail=failure.detail
            )
            # Ambiguous usage (read/write timeout, HTTP 5xx, malformed 200) stays reserved.
        from studgroup.run_reports import finish as finish_report

        source_ids = (
            {
                mid
                for assignment in [*result.batch.assignments, *result.batch.online_lessons]
                for mid in assignment.source_message_ids
            }
            if result
            else set()
        )
        used_source_ids = statistics.pop("_used_source_ids", set())
        statistics["used_source_messages"] = len(used_source_ids)
        statistics["used_source_ids"] = sorted(used_source_ids)
        statistics["important_source_ids"] = sorted(source_ids)
        await finish_report(
            db,
            settings,
            await db.get(Group, job.group_id),
            job,
            attempt,
            "superseded" if job.state == "superseded" else outcome,
            clock(),
            metrics={
                **statistics,
                "stage": "screen" if screening else "deep",
                "context_messages": max(0, len(context) - 1),
                "submitted_messages": len(context),
                "analyzed_fragments": int(response is not None),
                "important_proposals": len(result.batch.assignments)
                + len(result.batch.online_lessons)
                if result
                else 0,
                "important_fragments": len(source_ids),
                "review_proposals": sum(c.state == "review" for c in candidates),
            },
            error=failure.code
            if failure
            else job.last_error
            if candidates and any(c.state == "review" for c in candidates)
            else None,
        )
        telemetry = {
            "job": str(job_id),
            "attempt": str(attempt_id),
            "stage": "screen" if screening else "deep",
            "outcome": outcome,
            "signals": statistics.get("screen_signals"),
            "created_cards": statistics.get("created_cards", 0),
            "updated_cards": statistics.get("updated_cards", 0),
            "review": sum(c.state == "review" for c in candidates),
            "prompt_tokens": attempt.prompt_tokens,
            "completion_tokens": attempt.completion_tokens,
            "charged_usd": str(attempt.charged_usd),
        }
        await db.commit()
        if settings.ai_live_two_pass and settings.owner_update_notifications_enabled:
            print("AI_STAGE_RESULT " + json.dumps(telemetry), flush=True)
    return outcome
