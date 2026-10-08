"""Per-attempt reports: not fabricated group-wide or two-pass batch statistics."""

import json
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from sqlalchemy import select

from studgroup.models import AIAttempt, Group
from studgroup.owner_updates import queue

OUTCOMES = {
    "completed": "Успех",
    "retry": "Неуспех — повтор запланирован",
    "failed": "Неуспех — требуется проверка",
    "superseded": "Результат устарел — не применён",
    "interrupted": "Попытка прервана — продолжение с этой точки",
}


def utc(stamp):
    return stamp.replace(tzinfo=UTC) if stamp.tzinfo is None else stamp.astimezone(UTC)


def enabled(settings, attempt):
    return (
        settings.owner_update_notifications_enabled
        and settings.owner_telegram_user_id
        and settings.owner_telegram_user_id > 0
        and settings.owner_update_notifications_since is not None
        and utc(attempt.created_at) >= utc(settings.owner_update_notifications_since)
    )


async def start(db, settings, group, job, attempt, raw):
    attempt.metrics = json.dumps(
        {
            "target_messages": 1,
            "context_messages": None,
            "attempt_number": job.attempts,
            "telegram_message_id": raw.telegram_message_id,
        }
    )
    if not enabled(settings, attempt):
        return
    at = utc(attempt.created_at).astimezone(ZoneInfo(group.timezone))
    await queue(
        db,
        settings,
        f"run-start:{attempt.id}",
        group,
        f"▶ Начало прогона {str(job.id)[:8]} · попытка {job.attempts}\n{at:%d.%m %H:%M:%S} · {group.timezone}\nОдно новое/изменённое сообщение #{raw.telegram_message_id} с контекстом. Не повторный проход по всему чату.",
    )


async def finish(db, settings, group, job, attempt, outcome, now, metrics=None, error=None):
    previous = attempt.outcome
    values = json.loads(attempt.metrics or "{}")
    values.update(metrics or {})
    values["error"] = error
    values["duration_seconds"] = max(0, (utc(now) - utc(attempt.created_at)).total_seconds())
    attempt.finished_at = now
    attempt.outcome = outcome
    attempt.metrics = json.dumps(values)
    if not enabled(settings, attempt):
        return
    known = attempt.prompt_tokens is not None and attempt.completion_tokens is not None
    billing = (
        f"Токены: вход {attempt.prompt_tokens}, выход {attempt.completion_tokens}, всего {attempt.prompt_tokens + attempt.completion_tokens}\nРасчётная стоимость: ${attempt.charged_usd:.6f}"
        if known
        else "Обработка у провайдера не началась / подтверждён отказ. Расход $0."
        if attempt.charged_usd == 0
        else f"Токены и расход: неизвестны. Удержан резерв ${attempt.charged_usd:.6f} — это не подтверждённая трата."
    )
    ended = utc(now).astimezone(ZoneInfo(group.timezone))
    context_count = values.get("context_messages")
    context_count = context_count if context_count is not None else "неизвестно"
    text = f"■ Итог прогона {str(job.id)[:8]} · попытка {values.get('attempt_number', '?')}\n{OUTCOMES.get(outcome, outcome)}\nЗавершение: {ended:%d.%m %H:%M:%S} · {group.timezone}\nДлительность: {values['duration_seconds']:.1f} с\nСообщения: целевых {values.get('target_messages', 1)}, контекстных {context_count}; подготовлено для запроса {values.get('submitted_messages', 'неизвестно')}\nПодтверждённо разобрано контекстных окон: {values.get('analyzed_fragments', 0)}\nВажных предложений: {values.get('important_proposals', 0)}; фрагментов-источников: {values.get('important_fragments', 0)}\nПрименено предложений: {values.get('applied_fragments', 0)}; использовано сообщений-источников: {values.get('used_source_messages', 0)}\nКарточек создано: {values.get('created_cards', 0)}, обновлено: {values.get('updated_cards', 0)}, без изменений: {values.get('unchanged_cards', 0)}\nНа проверку: {values.get('review_proposals', 0)}\n{billing}"
    if error:
        text += f"\nКод ошибки: {error}."
    attempts = (await db.scalars(select(AIAttempt).where(AIAttempt.job_id == job.id))).all()
    known_attempts = [
        a for a in attempts if a.prompt_tokens is not None and a.completion_tokens is not None
    ]
    known_tokens = sum(a.prompt_tokens + a.completion_tokens for a in known_attempts)
    known_cost = sum((a.charged_usd for a in known_attempts), Decimal(0))
    pending_reserve = sum(
        (a.charged_usd for a in attempts if a.prompt_tokens is None or a.completion_tokens is None),
        Decimal(0),
    )
    text += f"\nВсего по этому разбору: {len(attempts)} попыток, известных токенов {known_tokens}, расчётный расход ${known_cost:.6f}, неуточнённый резерв ${pending_reserve:.6f}."
    text += f"\nС начала разбора (с ожиданием): {max(0, (utc(now) - utc(job.created_at)).total_seconds()):.1f} с."
    # A late result for a reclaimed attempt can reconcile previously unknown spend.
    suffix = "reconciled" if previous == "interrupted" and known else "finish"
    if suffix == "reconciled":
        text = "Уточнение после позднего ответа провайдера\n" + text
    await queue(db, settings, f"run-{suffix}:{attempt.id}", group, text)


async def filtered(db, settings, sources_by_group, now, duration):
    """Report actual newly discarded sources, not every idle poll or waiting retry."""
    if not sources_by_group or not enabled(settings, SimpleNamespace(created_at=now)):
        return
    for group_id, sources in sources_by_group.items():
        group = await db.get(Group, group_id)
        identity = str(uuid.uuid5(group_id, ":".join(sorted(sources))))
        at = utc(now).astimezone(ZoneInfo(group.timezone))
        await queue(
            db,
            settings,
            f"filter-start:{identity}",
            group,
            f"▶ Первичный локальный фильтр · начало\n{at:%d.%m %H:%M:%S} · {group.timezone}\nНовых сообщений в этом фрагменте: {len(sources)}. Без вызова DeepSeek.",
        )
        ended = datetime.now(UTC).astimezone(ZoneInfo(group.timezone))
        message_refs = " ".join("#" + source.rsplit(":", 1)[-1] for source in sources)
        await queue(
            db,
            settings,
            f"filter-finish:{identity}",
            group,
            f"■ Первичный локальный фильтр · успех\nЗавершение: {ended:%d.%m %H:%M:%S} · {group.timezone}\nОтфильтровано новых сообщений: {len(sources)}\nСообщения: {message_refs}\nАкадемических сигналов по локальным правилам: 0\nЗапросов DeepSeek: 0; токенов: 0; расход: $0\nИспользовано для приложения: 0\nДлительность проверки: {duration:.1f} с. Это не результат глубокого анализа ИИ.",
        )
