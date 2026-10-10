"""One durable start/finish per group-slot; attempts keep their own telemetry."""

import json
import uuid
from datetime import UTC
from decimal import Decimal
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from sqlalchemy import exists, select

from studgroup.models import (
    AIAttempt,
    AICandidate,
    AIJob,
    AIRun,
    Group,
    GroupAIProfile,
    RawMessage,
)
from studgroup.owner_updates import queue


def utc(value):
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def target_key(raw_id, revision, generation=1):
    return f"{raw_id}:{revision}:{generation}"


def parse_target_key(key, default_generation=1):
    parts = key.split(":")
    if len(parts) == 2:
        raw_id, revision = parts
        return raw_id, revision, default_generation
    if len(parts) == 3:
        raw_id, revision, generation = parts
        return raw_id, revision, int(generation)
    raise ValueError("invalid_run_target_key")


async def attach(db, settings, group, job, raw, attempt):
    from studgroup.ai_schedule import window
    from studgroup.run_reports import enabled

    timing = window(utc(attempt.created_at), settings.ai_schedule_timezone)
    if not timing:
        return
    slot = timing[0]
    key = target_key(raw.id, job.source_revision, job.analysis_generation)
    active = (
        await db.scalars(
            select(AIRun)
            .where(
                AIRun.group_id == group.id,
                AIRun.analysis_generation == job.analysis_generation,
                AIRun.finished_at.is_(None),
            )
            .order_by(AIRun.started_at)
            .with_for_update()
        )
    ).all()
    run = next((r for r in active if key in json.loads(r.targets)), None)
    if run is None:
        run = await db.scalar(
            select(AIRun)
            .where(
                AIRun.group_id == group.id,
                AIRun.slot == slot,
                AIRun.analysis_generation == job.analysis_generation,
            )
            .with_for_update()
        )
    if run is None:
        # Snapshot ALL eligible targets, not the worker's first 50-item claim page.
        previous_keys = {k for r in active for k in json.loads(r.targets)}
        profile = await db.get(GroupAIProfile, group.id)
        backfill = bool(
            profile
            and profile.state == "backfill"
            and profile.generation == job.analysis_generation
        )
        conditions = [
            RawMessage.group_id == group.id,
            RawMessage.analysis_generation == job.analysis_generation,
            RawMessage.processing_state == "pending",
            ~exists(
                select(AIJob.id).where(
                    AIJob.raw_message_id == RawMessage.id,
                    AIJob.source_revision == RawMessage.revision,
                    AIJob.analysis_generation == RawMessage.analysis_generation,
                    AIJob.state.in_(["done", "failed", "superseded"]),
                )
            ),
        ]
        if not backfill:
            conditions.extend(
                [
                    RawMessage.imported.is_(False),
                    RawMessage.delete_at > attempt.created_at,
                    RawMessage.live_received_at.is_not(None),
                    RawMessage.live_received_at <= slot,
                ]
            )
        else:
            conditions.extend(
                [
                    RawMessage.message_date >= group.bot_added_at
                    if group.bot_added_at is not None
                    else False,
                ]
            )
        sources = (
            await db.execute(
                select(
                    RawMessage.id,
                    RawMessage.revision,
                    RawMessage.analysis_generation,
                ).where(*conditions)
            )
        ).all()
        keys = sorted(
            {target_key(mid, revision, generation) for mid, revision, generation in sources}
            - previous_keys
            | {key}
        )
        run = AIRun(
            id=uuid.uuid5(group.id, f"planned-run:{slot.isoformat()}:{job.analysis_generation}"),
            group_id=group.id,
            slot=slot,
            analysis_generation=job.analysis_generation,
            targets=json.dumps(keys),
            started_at=attempt.created_at,
        )
        db.add(run)
        await db.flush()
        if enabled(settings, attempt):
            zone = ZoneInfo(group.timezone)
            at = utc(run.started_at).astimezone(zone)
            boundary = utc(slot).astimezone(zone)
            await queue(
                db,
                settings,
                f"batch-start:{run.id}",
                group,
                f"▶ Начало прогона {str(run.id)[:8]}\n{at:%d.%m.%Y %H:%M:%S} МСК\n"
                f"Новых/изменённых сообщений в пакете: {len(keys)}. Граница пакета: {boundary:%H:%M}.\n"
                "Лёгкий и глубокий анализ, а также повторы входят в этот общий прогон.",
            )
    values = json.loads(attempt.metrics or "{}")
    values["run_id"] = str(run.id)
    attempt.metrics = json.dumps(values)


async def prune_pre_install_targets(db, runs):
    """Drop never-started targets older than the bot installation from open runs.

    A package snapshotted before the bootstrap bound existed may list the whole
    imported history. Those sources have no job and will never be claimed, yet
    they keep the group's light/deep gate closed and the run silent forever.
    Sources that already own a job are kept untouched.
    """
    for run in runs:
        group = await db.get(Group, run.group_id)
        if group is None or group.bot_added_at is None:
            continue
        keys = json.loads(run.targets)
        parsed = {}
        for key in keys:
            try:
                raw_id, _revision, _generation = parse_target_key(key, run.analysis_generation)
                parsed[key] = uuid.UUID(raw_id)
            except (TypeError, ValueError):
                continue
        if not parsed:
            continue
        ids = set(parsed.values())
        old = {
            row.id
            for row in (
                await db.execute(
                    select(RawMessage.id).where(
                        RawMessage.id.in_(ids),
                        RawMessage.message_date < group.bot_added_at,
                        ~exists(select(AIJob.id).where(AIJob.raw_message_id == RawMessage.id)),
                    )
                )
            ).all()
        }
        if not old:
            continue
        kept = [key for key in keys if parsed.get(key) not in old]
        run.targets = json.dumps(kept)


async def finish_ready(db, settings, now, idle=False):
    from studgroup.run_reports import enabled

    runs = (
        await db.scalars(select(AIRun).where(AIRun.finished_at.is_(None)).with_for_update())
    ).all()
    for run in runs:
        group = await db.get(Group, run.group_id)
        keys = set(json.loads(run.targets))
        profile = await db.get(GroupAIProfile, run.group_id)
        backfill = bool(
            profile
            and profile.state == "backfill"
            and profile.generation == run.analysis_generation
        )
        raw_ids = set()
        for key in keys:
            try:
                raw_id, _revision, _generation = parse_target_key(key, run.analysis_generation)
                raw_ids.add(uuid.UUID(raw_id))
            except (TypeError, ValueError):
                continue
        jobs = (
            await db.scalars(
                select(AIJob).where(
                    AIJob.group_id == run.group_id, AIJob.raw_message_id.in_(raw_ids)
                )
            )
        ).all()
        indexed = {
            target_key(j.raw_message_id, j.source_revision, j.analysis_generation): j for j in jobs
        }
        raws = {
            r.id: r
            for r in (await db.scalars(select(RawMessage).where(RawMessage.id.in_(raw_ids)))).all()
        }
        pending, failed, skipped = 0, 0, 0
        waiting, unclaimed = 0, 0
        for key in keys:
            job = indexed.get(key)
            if job:
                is_open = job.state not in {"done", "failed", "superseded"}
                pending += is_open
                waiting += is_open
                failed += job.state == "failed"
                skipped += job.state == "superseded"
            else:
                try:
                    raw_id, revision, generation = parse_target_key(key, run.analysis_generation)
                    parsed_raw_id = uuid.UUID(raw_id)
                except (TypeError, ValueError):
                    skipped += 1
                    continue
                raw = raws.get(parsed_raw_id)
                obsolete = (
                    raw is None
                    or raw.revision != int(revision)
                    or raw.analysis_generation != generation
                    or (
                        backfill
                        and (
                            group.bot_added_at is None
                            or utc(raw.message_date) < utc(group.bot_added_at)
                        )
                    )
                    or (utc(raw.delete_at) <= utc(now) and not backfill)
                )
                pending += not obsolete
                unclaimed += not obsolete
                skipped += obsolete
        attempts = (
            await db.scalars(
                select(AIAttempt).where(
                    AIAttempt.group_id == run.group_id, AIAttempt.created_at >= run.started_at
                )
            )
        ).all()
        pairs = [(a, json.loads(a.metrics or "{}")) for a in attempts]
        pairs = [(a, m) for a, m in pairs if m.get("run_id") == str(run.id)]
        if any(a.finished_at is None for a, _ in pairs):
            continue
        # The worker is idle inside the window and no job is queued, retrying or
        # running: the targets that never got a job cannot progress in this slot.
        # Close the run honestly instead of keeping its report silent forever;
        # those sources stay pending and join the next slot's snapshot.
        abandoned = unclaimed if idle and pending and not waiting else 0
        if pending and not abandoned:
            continue
        run.finished_at = now
        run.outcome = "failed" if failed else "incomplete" if abandoned else "completed"
        if not enabled(settings, SimpleNamespace(created_at=run.started_at)):
            continue
        await emit(db, settings, run, pairs, failed, skipped, now, abandoned)


def review_reason(payload):
    """Plain-language reason a proposal was not published, from its stored facts."""
    reasons = []
    kind = payload.get("kind")
    confidence = payload.get("confidence")
    if kind == "needs_context":
        reasons.append("модель просит контекст")
    if isinstance(confidence, int) and confidence < 85:
        reasons.append(f"уверенность {confidence} (нужно от 85)")
    missing = [
        label
        for field, label in (
            ("subject", "предмет"),
            ("title", "название"),
            ("description", "описание"),
            ("deadline_at", "срок"),
        )
        if not payload.get(field)
    ]
    if missing:
        reasons.append("не хватило: " + ", ".join(missing))
    if not reasons:
        reasons.append("не прошло проверку публикации")
    return "; ".join(reasons)


async def review_section(db, job_ids, limit=12):
    if not job_ids:
        return ""
    rows = (
        await db.execute(
            select(AICandidate, RawMessage)
            .join(AIJob, AIJob.id == AICandidate.job_id)
            .join(RawMessage, RawMessage.id == AIJob.raw_message_id, isouter=True)
            .where(AICandidate.job_id.in_(job_ids), AICandidate.state == "review")
            .order_by(AICandidate.created_at)
        )
    ).all()
    if not rows:
        return ""
    lines = [f"\nНа проверку ({len(rows)}):"]
    for candidate, raw in rows[:limit]:
        try:
            payload = json.loads(candidate.payload)
        except (TypeError, ValueError):
            payload = {}
        number = f"#{raw.telegram_message_id}" if raw is not None else "#?"
        text = " ".join((raw.text or "").split())[:70] if raw is not None else ""
        lines.append(f"• {number} «{text}» — {review_reason(payload)}")
    if len(rows) > limit:
        lines.append(f"… и ещё {len(rows) - limit}")
    return "\n".join(lines)


async def emit(db, settings, run, pairs, failed, skipped, now, abandoned=0):
    group = await db.get(Group, run.group_id)
    known = [
        (a, m) for a, m in pairs if a.prompt_tokens is not None and a.completion_tokens is not None
    ]
    prompt = sum(a.prompt_tokens for a, _ in known)
    completion = sum(a.completion_tokens for a, _ in known)
    spent = sum((a.charged_usd for a, _ in known), Decimal(0))
    reserved = sum(
        (a.charged_usd for a, m in pairs if a.prompt_tokens is None or a.completion_tokens is None),
        Decimal(0),
    )

    def total(field):
        return sum(m.get(field, 0) or 0 for _, m in pairs)

    error_counts = {}
    diagnostic_counts = {}
    for _, metrics in pairs:
        if code := metrics.get("error"):
            error_counts[code] = error_counts.get(code, 0) + 1
        for code, count in (metrics.get("provider_diagnostics") or {}).items():
            diagnostic_counts[code] = diagnostic_counts.get(code, 0) + count
    errors = ", ".join(f"{code}: {count}" for code, count in sorted(error_counts.items())) or "нет"
    diagnostics = (
        ", ".join(f"{code}: {count}" for code, count in sorted(diagnostic_counts.items())) or "нет"
    )

    screen_jobs = {
        a.job_id
        for a, m in pairs
        if m.get("stage") == "screen" and a.outcome in {"screened", "completed"}
    }
    deep_jobs = {
        a.job_id for a, m in pairs if m.get("stage") == "deep" and a.outcome == "completed"
    }
    signals = {a.job_id for a, m in pairs if m.get("screen_signals")}
    important_ids = {mid for _, m in pairs for mid in m.get("important_source_ids", [])}
    used_ids = {mid for _, m in pairs for mid in m.get("used_source_ids", [])}
    zone = ZoneInfo(group.timezone)
    ended = utc(now).astimezone(zone)
    began = utc(run.started_at).astimezone(zone)
    duration = max(0, (utc(now) - utc(run.started_at)).total_seconds())
    status = (
        "Завершён не полностью"
        if abandoned
        else "Завершён с ошибками"
        if failed
        else "Завершён с предупреждениями"
        if diagnostic_counts
        else "Успех"
    )
    text = (
        f"■ Итог прогона {str(run.id)[:8]}\n{status}\nНачало: {began:%d.%m.%Y %H:%M:%S} МСК\nЗавершение: {ended:%d.%m.%Y %H:%M:%S} МСК\nДлительность с ожиданием: {duration:.1f} с\nСообщений в пакете: {len(json.loads(run.targets))}; ошибок: {failed}; устаревших: {skipped}"
        + (
            f"; не обработано в этом окне: {abandoned} (останутся на следующий прогон)"
            if abandoned
            else ""
        )
        + f"\nЛёгкий анализ: {len(screen_jobs)} сообщений; слабых сигналов: {len(signals)}\nГлубоко разобрано фрагментов: {len(deep_jobs)}\nВажных предложений: {total('important_proposals')}; уникальных сообщений-источников: {len(important_ids)}\nПрименено предложений: {total('applied_fragments')}; использовано сообщений-источников: {len(used_ids)}\nКарточек создано: {total('created_cards')}, обновлено: {total('updated_cards')}, без изменений: {total('unchanged_cards')}; на проверку: {total('review_proposals')}\nОшибки одним итогом: {errors}\nИсправлено/отклонено в ответах модели: {diagnostics}\nЗапросов/попыток: {len(pairs)}\nТокены: вход {prompt}, выход {completion}, всего {prompt + completion}\nРасчётная стоимость: ${spent:.6f}\nНеуточнённый резерв: ${reserved:.6f}."
    )
    text += await review_section(db, {a.job_id for a, _ in pairs})
    await queue(db, settings, f"batch-finish:{run.id}", group, text[:3900])
