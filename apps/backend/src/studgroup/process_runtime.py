"""Worker-side control, bounded safe-point polling, and independent operator alerts."""

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from time import monotonic

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from studgroup.models import BackgroundProcess
from studgroup.process_control import PROCESS_NAMES, ensure_process

LABELS = {
    "ai": "AI / DeepSeek",
    "sdo": "SDO / RANEPA",
    "telegram_outbox": "Telegram outbox",
    "scheduler": "Scheduler",
}
ALERT_PREFIX = "process-control:"
ERROR_CODES = {
    "configuration_missing",
    "engine_disabled",
    "approval_missing",
    "credentials_unavailable",
    "browser_unavailable",
    "telegram_unavailable",
    "iteration_failed",
    "heartbeat_missed",
    "captcha_guard_paused",
    "scheduler_paused",
}
STAGES = {"preflight", "iteration", "watchdog"}
HEARTBEAT_TIMEOUT = {
    "ai": timedelta(minutes=15),
    "sdo": timedelta(minutes=20),
    "telegram_outbox": timedelta(minutes=3),
    "scheduler": timedelta(minutes=15),
}
LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class Intent:
    enabled: bool
    generation: int


def utc(value):
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


async def read_intent(engine, name, default) -> Intent:
    if name not in PROCESS_NAMES:
        raise ValueError("unknown_process")
    async with AsyncSession(engine) as db:
        row = await db.get(BackgroundProcess, name)
        if row is None:
            return Intent(default, 0)
        return Intent(default if row.enabled is None else row.enabled, row.generation)


async def queue_alert(db, settings, row, text, now):
    from studgroup.bot_admin import enqueue_once

    owner = settings.bot_admin_user_id
    if type(owner) is int and owner > 0:
        await enqueue_once(
            db,
            "sendMessage",
            {"chat_id": owner, "text": text},
            f"{ALERT_PREFIX}{row.name}:{row.generation}:{row.status}:{now.isoformat()}",
        )


async def report_process(
    engine, settings, name, generation, status, *, code=None, stage=None, fresh_start=False
) -> bool:
    if name not in PROCESS_NAMES or status not in {"starting", "running", "stopped", "error"}:
        raise ValueError("invalid_process_status")
    if status == "error" and (code not in ERROR_CODES or stage not in STAGES):
        raise ValueError("unsafe_process_error")
    if status != "error" and (code is not None or stage is not None):
        raise ValueError("unexpected_process_error")
    async with AsyncSession(engine, expire_on_commit=False) as db:
        await ensure_process(db, name)
        row = await db.get(BackgroundProcess, name, with_for_update=True)
        if row.generation != generation:
            await db.rollback()
            return False
        now = datetime.now(UTC)
        changed = row.status != status or row.applied_generation != generation
        error_changed = status == "error" and (row.error_code != code or row.error_stage != stage)
        previously_running = row.status == "running"
        row.status = status
        if status != "starting":
            row.applied_generation = generation
        row.heartbeat_at = now
        if status == "running" and (changed or fresh_start):
            row.started_at = now
            await queue_alert(db, settings, row, f"✅ Процесс запущен: {LABELS[name]}", now)
        elif status == "stopped" and changed and (generation > 0 or previously_running):
            await queue_alert(db, settings, row, f"⏹ Процесс остановлен: {LABELS[name]}", now)
        elif status == "error" and (changed or error_changed):
            row.error_at = now
            row.error_code = code
            row.error_stage = stage
            await queue_alert(
                db,
                settings,
                row,
                f"❌ Процесс не запущен: {LABELS[name]}. Причина: {code}. Этап: {stage}",
                now,
            )
        await db.commit()
        return True


class ProcessGate:
    """A worker owns this gate; never interrupt a claimed job when desired state changes."""

    def __init__(self, engine, settings, name, default, probe=None, *, poll_interval=0):
        self.engine = engine
        self.settings = settings
        self.name = name
        self.default = default
        self.probe = probe
        self.ready_generation = None
        self.reported_generation = None
        self.reported_status = None
        self.last_report = 0.0
        self.poll_interval = poll_interval
        self.last_poll = 0.0
        self.intent = None
        self.probe_retry_at = 0.0
        self.probe_generation = None

    async def _report(self, generation, status, *, code=None, stage=None, fresh_start=False):
        if await report_process(
            self.engine,
            self.settings,
            self.name,
            generation,
            status,
            code=code,
            stage=stage,
            fresh_start=fresh_start,
        ):
            self.reported_generation = generation
            self.reported_status = status
            self.last_report = monotonic()

    async def begin(self):
        if self.intent is None or monotonic() - self.last_poll >= self.poll_interval:
            self.intent = await read_intent(self.engine, self.name, self.default)
            self.last_poll = monotonic()
        intent = self.intent
        if not intent.enabled:
            self.ready_generation = None
            if self._due(intent.generation, "stopped"):
                await self._report(intent.generation, "stopped")
            return None
        if self.ready_generation != intent.generation:
            if self.probe_generation == intent.generation and monotonic() < self.probe_retry_at:
                return None
            self.probe_generation = intent.generation
            await self._report(intent.generation, "starting")
            code = await self.probe() if self.probe is not None else None
            if code is not None:
                self.probe_retry_at = monotonic() + 30
                if self._due(intent.generation, "error"):
                    await self._report(intent.generation, "error", code=code, stage="preflight")
                return None
            self.probe_retry_at = 0.0
            self.ready_generation = intent.generation
        return intent.generation

    def _due(self, generation, status):
        return (
            self.reported_generation != generation
            or self.reported_status != status
            or monotonic() - self.last_report >= 30
        )

    async def succeeded(self, generation):
        if self._due(generation, "running"):
            await self._report(
                generation,
                "running",
                fresh_start=self.reported_generation != generation
                or self.reported_status != "running",
            )

    async def failed(self, generation, code="iteration_failed"):
        self.ready_generation = None
        await self._report(generation, "error", code=code, stage="iteration")

    async def sleep(self, generation, seconds, stop=None):
        deadline = monotonic() + seconds
        while monotonic() < deadline:
            delay = min(5, max(0, deadline - monotonic()))
            if stop is None:
                await asyncio.sleep(delay)
            else:
                try:
                    await asyncio.wait_for(stop.wait(), timeout=delay)
                    return
                except TimeoutError:
                    pass
            if generation is not None:
                current = await read_intent(self.engine, self.name, self.default)
                if current.generation != generation or not current.enabled:
                    return
                if self.reported_status == "running":
                    await self.succeeded(generation)


async def watchdog_once(engine, settings, now=None):
    now = now or datetime.now(UTC)
    async with AsyncSession(engine) as db:
        rows = (await db.scalars(select(BackgroundProcess).with_for_update(skip_locked=True))).all()
        for row in rows:
            timeout = HEARTBEAT_TIMEOUT.get(row.name)
            if timeout is None:
                continue
            # Give an outstanding start/restart request its own grace period.
            pending_start = row.enabled is True and row.generation != row.applied_generation
            if pending_start:
                reference = row.requested_at
            elif row.status in {"starting", "running"} or (
                row.status == "error" and row.enabled is not False
            ):
                reference = row.heartbeat_at
            else:
                continue
            if reference is None or now - utc(reference) <= timeout:
                continue
            if row.status == "error" and row.error_code == "heartbeat_missed":
                continue
            row.status = "error"
            row.error_at = now
            row.error_code = "heartbeat_missed"
            row.error_stage = "watchdog"
            await queue_alert(
                db,
                settings,
                row,
                f"⚠️ Пропущен heartbeat: {LABELS[row.name]}. Причина: heartbeat_missed",
                now,
            )
        await db.commit()


async def monitor_loop(engine, settings):
    """Only this independent lane can deliver process alerts when normal outbox is paused."""
    from studgroup.bot_admin import deliver

    while True:
        try:
            await watchdog_once(engine, settings)
            await deliver(engine, settings, control_only=True)
        except Exception as error:  # noqa: BLE001 -- keep monitor alive without logging secrets
            LOGGER.warning("process_monitor_failed: %s", type(error).__name__)
        await asyncio.sleep(5)


async def ai_health(settings):
    # The owner switch never grants new paid-operation or publication permissions.
    if not (
        settings.ai_enabled
        or settings.new_engine_enabled
        or settings.owner_funded_pilot
        or settings.ai_shadow_enabled
        or settings.image_analysis_enabled
    ):
        return "engine_disabled"
    if not settings.deepseek_api_key.get_secret_value():
        return "configuration_missing"
    return None


async def telegram_health(settings):
    from studgroup.bot_admin import telegram_request

    if not settings.telegram_bot_token:
        return "configuration_missing"
    try:
        result = await telegram_request(settings, "getMe", {})
        if result.get("ok") is True:
            return None
    except Exception as error:  # noqa: BLE001 -- never expose provider URL or token
        LOGGER.warning("telegram_health_failed: %s", type(error).__name__)
    return "telegram_unavailable"


async def scheduler_loop(engine, delivery_engine, settings, *, delivery_enabled):
    """One scheduler acknowledgement covers all periodic jobs; no job is cancelled by a toggle."""
    from studgroup.billing import tick as billing_tick
    from studgroup.billing_campaign import queue_billing_announcement
    from studgroup.billing_manual import cleanup_due
    from studgroup.headman_digest import tick as digest_tick
    from studgroup.owner_updates import tick as owner_tick
    from studgroup.ranepa_manual_captcha import cleanup_expired_captcha_artwork

    jobs = []
    if delivery_enabled:
        jobs.extend(
            [
                (5, lambda: owner_tick(delivery_engine, settings)),
                (30, lambda: digest_tick(delivery_engine)),
                (300, lambda: cleanup_expired_captcha_artwork(delivery_engine)),
            ]
        )
    if settings.billing_enabled:
        jobs.append((600, lambda: billing_tick(engine, settings)))
        if settings.billing_payment_mode == "manual_alfa":
            jobs.extend(
                [
                    (3, lambda: cleanup_due(engine, settings)),
                    (3, lambda: queue_billing_announcement(engine, settings)),
                ]
            )

    async def probe():
        return None if jobs else "configuration_missing"

    gate = ProcessGate(engine, settings, "scheduler", bool(jobs), probe)
    due = [0.0] * len(jobs)
    previous_generation = None
    while True:
        generation = None
        try:
            generation = await gate.begin()
            if generation is not None:
                if generation != previous_generation:
                    due = [0.0] * len(jobs)
                    previous_generation = generation
                failed = False
                for index, (interval, job) in enumerate(jobs):
                    if monotonic() < due[index]:
                        continue
                    # Re-check before every scheduled unit, not only before the batch.
                    current = await read_intent(engine, "scheduler", bool(jobs))
                    if not current.enabled or current.generation != generation:
                        break
                    try:
                        await job()
                    except Exception as error:  # noqa: BLE001 -- retain independent job cadence
                        failed = True
                        LOGGER.warning("scheduler_job_failed: %s", type(error).__name__)
                    due[index] = monotonic() + interval
                if failed:
                    await gate.failed(generation)
                else:
                    await gate.succeeded(generation)
        except Exception as error:  # noqa: BLE001 -- fail closed on control database outage
            LOGGER.warning("scheduler_control_failed: %s", type(error).__name__)
        await asyncio.sleep(1)
