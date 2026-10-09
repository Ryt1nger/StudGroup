"""One durable start/finish per group-slot; attempts keep their own telemetry."""

import json
import uuid
from datetime import UTC
from decimal import Decimal
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from sqlalchemy import exists, select

from studgroup.models import AIAttempt, AIJob, AIRun, Group, RawMessage
from studgroup.owner_updates import queue


def utc(value):
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def target_key(raw_id, revision):
    return f"{raw_id}:{revision}"


async def attach(db, settings, group, job, raw, attempt):
    from studgroup.ai_schedule import window
    from studgroup.run_reports import enabled

    timing = window(utc(attempt.created_at), settings.ai_schedule_timezone)
    if not timing:
        return
    slot = timing[0]
    key = target_key(raw.id, job.source_revision)
    active = (
        await db.scalars(
            select(AIRun)
            .where(AIRun.group_id == group.id, AIRun.finished_at.is_(None))
            .order_by(AIRun.started_at)
            .with_for_update()
        )
    ).all()
    run = next((r for r in active if key in json.loads(r.targets)), None)
    if run is None:
        run = await db.scalar(
            select(AIRun).where(AIRun.group_id == group.id, AIRun.slot == slot).with_for_update()
        )
    if run is None:
        # Snapshot ALL eligible targets, not the worker's first 50-item claim page.
        previous_keys = {k for r in active for k in json.loads(r.targets)}
        sources = (
            await db.execute(
                select(RawMessage.id, RawMessage.revision).where(
                    RawMessage.group_id == group.id,
                    RawMessage.imported.is_(False),
                    RawMessage.processing_state == "pending",
                    RawMessage.delete_at > attempt.created_at,
                    RawMessage.live_received_at.is_not(None),
                    RawMessage.live_received_at <= slot,
                    ~exists(
                        select(AIJob.id).where(
                            AIJob.raw_message_id == RawMessage.id,
                            AIJob.source_revision == RawMessage.revision,
                            AIJob.state.in_(["done", "failed", "superseded"]),
                        )
                    ),
                )
            )
        ).all()
        keys = sorted(
            {target_key(mid, revision) for mid, revision in sources} - previous_keys | {key}
        )
        run = AIRun(
            id=uuid.uuid5(group.id, "planned-run:" + slot.isoformat()),
            group_id=group.id,
            slot=slot,
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


async def finish_ready(db, settings, now):
    from studgroup.run_reports import enabled

    runs = (
        await db.scalars(select(AIRun).where(AIRun.finished_at.is_(None)).with_for_update())
    ).all()
    for run in runs:
        keys = set(json.loads(run.targets))
        raw_ids = {uuid.UUID(k.split(":")[0]) for k in keys}
        jobs = (
            await db.scalars(
                select(AIJob).where(
                    AIJob.group_id == run.group_id, AIJob.raw_message_id.in_(raw_ids)
                )
            )
        ).all()
        indexed = {target_key(j.raw_message_id, j.source_revision): j for j in jobs}
        raws = {
            r.id: r
            for r in (await db.scalars(select(RawMessage).where(RawMessage.id.in_(raw_ids)))).all()
        }
        pending, failed, skipped = 0, 0, 0
        for key in keys:
            job = indexed.get(key)
            if job:
                pending += job.state not in {"done", "failed", "superseded"}
                failed += job.state == "failed"
                skipped += job.state == "superseded"
            else:
                raw_id, revision = key.split(":")
                raw = raws.get(uuid.UUID(raw_id))
                obsolete = (
                    raw is None or raw.revision != int(revision) or utc(raw.delete_at) <= utc(now)
                )
                pending += not obsolete
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
        if pending or any(a.finished_at is None for a, _ in pairs):
            continue
        run.finished_at = now
        run.outcome = "failed" if failed else "completed"
        if not enabled(settings, SimpleNamespace(created_at=run.started_at)):
            continue
        await emit(db, settings, run, pairs, failed, skipped, now)


async def emit(db, settings, run, pairs, failed, skipped, now):
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
    text = f"■ Итог прогона {str(run.id)[:8]}\n{'Завершён с ошибками' if failed else 'Успех'}\nНачало: {began:%d.%m.%Y %H:%M:%S} МСК\nЗавершение: {ended:%d.%m.%Y %H:%M:%S} МСК\nДлительность с ожиданием: {duration:.1f} с\nСообщений в пакете: {len(json.loads(run.targets))}; ошибок: {failed}; устаревших: {skipped}\nЛёгкий анализ: {len(screen_jobs)} сообщений; слабых сигналов: {len(signals)}\nГлубоко разобрано фрагментов: {len(deep_jobs)}\nВажных предложений: {total('important_proposals')}; уникальных сообщений-источников: {len(important_ids)}\nПрименено предложений: {total('applied_fragments')}; использовано сообщений-источников: {len(used_ids)}\nКарточек создано: {total('created_cards')}, обновлено: {total('updated_cards')}; на проверку: {total('review_proposals')}\nЗапросов/попыток: {len(pairs)}\nТокены: вход {prompt}, выход {completion}, всего {prompt + completion}\nРасчётная стоимость: ${spent:.6f}\nНеуточнённый резерв: ${reserved:.6f}."
    await queue(db, settings, f"batch-finish:{run.id}", group, text)
