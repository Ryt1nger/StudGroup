"""One conservative Celery worker + SQL inbox sweeper; no private text in broker tasks."""

import asyncio
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import httpx
from celery import Celery
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from studgroup.main import Settings
from studgroup.models import OwnerIncident, OwnerIncidentEpisode
from studgroup.processing import adopt_incident_episode, process_next

settings = Settings()

INCIDENT_LABELS = {
    "provider_unreachable": "Не удалось связаться с API DeepSeek.",
    "provider_error": "API DeepSeek вернул ошибку.",
    "rate_limited": "DeepSeek ограничил частоту запросов.",
    "invalid_api_key": "API-ключ DeepSeek отсутствует или отклонён.",
    "provider_not_configured": "Ключ DeepSeek не настроен. API не вызывался.",
    "invalid_provider_url": "Неверный адрес API в настройках. Ключ не отправлялся.",
    "invalid_message_timestamp": "Неверная исходная дата сообщения. Запрос не отправлялся.",
    "context_too_large": "Превышен лимит контекста. Запрос DeepSeek не отправлялся.",
    "insufficient_balance": "DeepSeek сообщает о недостаточном балансе.",
    "invalid_provider_output": "Ответ DeepSeek не прошёл проверку.",
    "incomplete_output": "DeepSeek вернул незавершённый ответ.",
    "ai_budget_limit": "Обработка приостановлена: достигнут лимит бюджета ИИ.",
    "ai_daily_group_budget_limit": "Следующий запрос превысил бы дневной лимит расходов ИИ для группы; необработанная очередь сохранена до следующего дня.",
    "ai_total_budget_limit": "Следующий запрос превысил бы общий лимит расходов ИИ; необработанная очередь сохранена до изменения лимита.",
    "ai_hourly_safety_limit": "Сработал часовой предохранитель расходов ИИ; очередь продолжится автоматически.",
    "ai_circuit_open": "Серия ошибок открыла временный предохранитель DeepSeek; будет автоматическая проба после охлаждения.",
    "processing_exhausted": "Один этап исчерпал безопасное число повторов и изолирован от остальной очереди.",
    "pipeline_error": "В обработчике ИИ произошла внутренняя ошибка.",
    "invalid_source_reference": "Ответ ИИ отклонён: он ссылается на сообщение вне переданного контекста. Это не падение API.",
    "invalid_date_only": "Ответ ИИ отклонён: дата без времени содержит ненулевое время. Это не падение API.",
}


def incident_text(row, event="opened", sent_at=None):
    def stamp(value):
        if value is None:
            return "неизвестно (не сохранено)"
        value = value.replace(tzinfo=UTC) if value.tzinfo is None else value
        return value.astimezone(ZoneInfo("Europe/Moscow")).strftime("%d.%m.%Y %H:%M:%S МСК")

    now = sent_at or datetime.now(UTC)
    budget_codes = {
        "ai_budget_limit",
        "ai_daily_group_budget_limit",
        "ai_total_budget_limit",
        "ai_hourly_safety_limit",
    }
    title = (
        "восстановлена обработка"
        if event == "recovered"
        else "лимит"
        if row.code in budget_codes
        else "ошибка проверки ответа ИИ"
        if row.code
        in {
            "invalid_source_reference",
            "invalid_date_only",
            "invalid_provider_output",
            "invalid_target_reference",
        }
        else "ошибка"
    )
    opened_label = "Время старой записи" if row.legacy else "Возникла"
    header = (
        f"StudGroup: {title} №{row.id:06d}\nКод: {row.code}\n{opened_label}: {stamp(row.opened_at)}"
    )
    if event == "recovered":
        header += f"\nВосстановление: {stamp(row.recovered_at)}"
    else:
        header += "\n" + INCIDENT_LABELS.get(row.code, "Ошибка разбора/обработчика ИИ.")
        if row.first_detail:
            label = "Параметры лимита" if row.code in budget_codes else "Технический тип"
            header += f"\n{label}: {row.first_detail}"
    last_seen = (
        "до обновления не сохранялось"
        if row.legacy and row.occurrences == 1
        else stamp(row.last_seen_at)
    )
    count = f"не менее {row.occurrences}" if row.legacy else str(row.occurrences)
    header += f"\nПоследнее проявление: {last_seen}\nУчтено проявлений в этом случае: {count}\nОтправка уведомления: {stamp(now)}"
    age = (
        now - (row.opened_at.replace(tzinfo=UTC) if row.opened_at.tzinfo is None else row.opened_at)
    ).total_seconds()
    if event == "opened" and age >= 60:
        header += f"\nНакопленное уведомление: задержка от возникновения {int(age)} с."
    if row.legacy:
        header += "\nИсторическая запись: время из старого журнала, не гарантирует точный момент сбоя; прежнее число повторов не сохранялось."
    if event == "opened" and row.recovered_at:
        header += f"\nК моменту отправки уже устранена: {stamp(row.recovered_at)}"
    return header


app = Celery("studgroup", broker=settings.redis_url)
app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    task_ignore_result=True,
    worker_prefetch_multiplier=1,
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    broker_connection_retry_on_startup=True,
    task_soft_time_limit=100,
    task_time_limit=120,
    timezone="UTC",
    beat_schedule={"durable-inbox": {"task": "studgroup.sweep", "schedule": 5.0}},
)


async def flush_incidents(engine, config, transport=None):
    # Never fall back to a group, headman, admin panel or broadcast recipient.
    if (
        not config.owner_telegram_user_id
        or config.owner_telegram_user_id <= 0
        or not config.telegram_bot_token
    ):
        return
    # Expiration after committing the first alert used to break subsequent alerts
    # with MissingGreenlet. Each delivery now reacquires its own locked episode.
    async with AsyncSession(engine, expire_on_commit=False) as db:
        legacy_rows = (
            await db.scalars(
                select(OwnerIncident)
                .where(
                    or_(
                        OwnerIncident.current_episode_id.is_(None),
                        OwnerIncident.notification_pending.is_(True),
                    )
                )
                .order_by(OwnerIncident.code)
                .with_for_update(skip_locked=True)
            )
        ).all()
        for row in legacy_rows:
            await adopt_incident_episode(db, row)
        await db.commit()
        numbers = (
            await db.scalars(
                select(OwnerIncidentEpisode.id)
                .where(
                    or_(
                        OwnerIncidentEpisode.opening_pending.is_(True),
                        OwnerIncidentEpisode.recovery_pending.is_(True),
                    )
                )
                .order_by(OwnerIncidentEpisode.id)
            )
        ).all()
        for number in numbers:
            pair = (
                await db.execute(
                    select(OwnerIncidentEpisode, OwnerIncident)
                    .join(OwnerIncident, OwnerIncident.code == OwnerIncidentEpisode.code)
                    .where(
                        OwnerIncidentEpisode.id == number,
                        or_(
                            OwnerIncidentEpisode.opening_pending.is_(True),
                            OwnerIncidentEpisode.recovery_pending.is_(True),
                        ),
                    )
                    .with_for_update(skip_locked=True)
                    .execution_options(populate_existing=True)
                )
            ).one_or_none()
            if pair is None:
                continue
            row, current = pair
            for event, field, notified in [
                ("opened", "opening_pending", "opening_notified_at"),
                ("recovered", "recovery_pending", "recovery_notified_at"),
            ]:
                if not getattr(row, field):
                    continue
                if event == "recovered" and row.opening_pending:
                    break
                try:
                    async with httpx.AsyncClient(
                        timeout=10, trust_env=False, follow_redirects=False, transport=transport
                    ) as client:
                        response = await client.post(
                            f"https://api.telegram.org/bot{config.telegram_bot_token}/sendMessage",
                            json={
                                "chat_id": config.owner_telegram_user_id,
                                "text": incident_text(row, event),
                            },
                        )
                        success = response.status_code == 200 and response.json().get("ok") is True
                except (httpx.HTTPError, ValueError):
                    success = False
                if not success:
                    break
                setattr(row, field, False)
                setattr(row, notified, datetime.now(UTC))
            if current.current_episode_id == row.id:
                current.notification_pending = row.opening_pending or row.recovery_pending
            await db.commit()


async def sweep_once(config=None, provider=None):
    config = config or settings
    engine = create_async_engine(config.database_url, pool_pre_ping=True)
    try:
        status = await process_next(engine, config, provider)
        await flush_incidents(engine, config)
        return {"status": status, "checked_at": datetime.now(UTC).isoformat()}
    finally:
        await engine.dispose()


@app.task(name="studgroup.sweep")
def sweep():
    return asyncio.run(sweep_once())
