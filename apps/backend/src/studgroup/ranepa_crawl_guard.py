"""Durable CAPTCHA circuit breaker layered over, never replacing, the fair queue.

Only confirmed unresolved CAPTCHA results contribute to the consecutive
counter. The singleton row persists between GitHub runner shifts. An owner
may request one manual recovery check; the circuit opens again only after
an explicitly requested single-account pilot successfully reads a schedule.
No CAPTCHA pixels, login credentials, cookies or session URLs leave this module.
"""

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from studgroup.models import RanepaCrawlGuard

MAX_CONSECUTIVE_CAPTCHA = 3
_GUARD_ID = 1


async def load_guard(db, *, lock=False):
    """Create singleton idempotently and obtain its state, optionally locked."""
    now = datetime.now(UTC)
    dialect = db.bind.dialect.name
    if dialect == "postgresql":
        statement = pg_insert(RanepaCrawlGuard)
    elif dialect == "sqlite":
        statement = sqlite_insert(RanepaCrawlGuard)
    else:
        raise RuntimeError("unsupported_crawl_guard_database")
    await db.execute(
        statement.values(
            id=_GUARD_ID, consecutive_captcha=0, paused=False,
            recovery_requested=False, updated_at=now
        ).on_conflict_do_nothing(index_elements=[RanepaCrawlGuard.id])
    )
    query = select(RanepaCrawlGuard).where(RanepaCrawlGuard.id == _GUARD_ID)
    if lock:
        query = query.with_for_update()
    state = await db.scalar(query)
    if state is None:
        raise RuntimeError("captcha_guard_missing")
    return state


async def guard_is_paused(db) -> bool:
    return bool((await load_guard(db)).paused)


async def reserve_daily_paid_tasks(engine, *, count: int, daily_limit: int) -> bool:
    """Reserve a conservative paid-task ceiling before contacting RuCaptcha.

    The reservation remains consumed if an API request fails before billing:
    this intentionally favors preventing unbounded spend over reclaiming
    unconfirmed charges. All GitHub runs share the same locked DB singleton.
    """
    if (
        type(count) is not int
        or type(daily_limit) is not int
        or count not in (1, 2)
        or daily_limit <= 0
    ):
        return False
    async with AsyncSession(engine) as db:
        state = await load_guard(db, lock=True)
        today = datetime.now(UTC).date()
        if state.paid_day != today:
            state.paid_day = today
            state.paid_tasks_reserved = 0
        if state.paid_tasks_reserved + count > daily_limit:
            await db.commit()
            return False
        state.paid_tasks_reserved += count
        state.updated_at = datetime.now(UTC)
        await db.commit()
        return True


async def request_recovery(db) -> bool:
    """Owner request only: permit ONE manual pilot to prove access is restored.

    Never turn off the pause merely because someone clicked a Telegram button.
    """
    state = await load_guard(db, lock=True)
    if not state.paused:
        return False
    state.recovery_requested = True
    state.updated_at = datetime.now(UTC)
    await db.flush()
    return True


async def _owner_message(db, owner_id, message, dedup_key):
    """Queue a private Telegram message through the existing durable outbox."""
    if type(owner_id) is not int or owner_id <= 0:
        return False
    from studgroup.bot_admin import enqueue_once

    return await enqueue_once(
        db,
        "sendMessage",
        {
            "chat_id": owner_id,
            "text": message,
            "link_preview_options": {"is_disabled": True},
        },
        dedup_key,
    )


async def record_crawl_outcome(
    db,
    *,
    owner_id,
    job_id,
    group_id,
    user_id,
    attempt,
    failure_code,
    manual_pilot=False,
):
    """Atomically record verified job outcome and enqueue owner-only alerts.

    A manual pilot never changes the scheduler's consecutive failure count.
    When paused, one owner-authorized pilot success reopens the queue; a
    failed probe consumes that request and leaves the queue paused.
    """
    state = await load_guard(db, lock=True)
    now = datetime.now(UTC)
    was_paused = bool(state.paused)

    if manual_pilot:
        if was_paused and state.recovery_requested:
            state.recovery_requested = False
            if failure_code is None:
                state.paused = False
                state.consecutive_captcha = 0
                await _owner_message(
                    db, owner_id,
                    "StudGroup: доступ к расписанию подтверждён одиночным пилотом. "
                    "Обход СДО возобновлён; сохранённая очередь продолжится в следующем цикле.",
                    f"ranepa-captcha-resumed:{job_id}:{attempt}",
                )
            else:
                await _owner_message(
                    db, owner_id,
                    "StudGroup: проверочный пилот не подтвердил доступ к расписанию. "
                    "Обход остаётся приостановленным. Повторную проверку можно разрешить "
                    "командой /ranepa_resume, когда проблема будет устранена.",
                    f"ranepa-captcha-probe-failed:{job_id}:{attempt}",
                )
    elif not was_paused:
        if failure_code == "captcha":
            state.consecutive_captcha += 1
            state.last_job_id = job_id
            blocked = state.consecutive_captcha > MAX_CONSECUTIVE_CAPTCHA
            if blocked:
                state.paused = True
            await _owner_message(
                db, owner_id,
                "StudGroup: CAPTCHA помешала получить расписание.\n"
                f"Группа UUID: {group_id}\n"
                f"Пользователь UUID: {user_id}\n"
                f"Неудач подряд: {state.consecutive_captcha}.\n"
                + (
                    "Обход ПРИОСТАНОВЛЕН после четырёх последовательных сбоев. "
                    "Для восстановления потребуется разрешённый одиночный пилот "
                    "и команда /ranepa_resume в личном чате бота."
                    if blocked else
                    "Эта учётка пропущена; умная очередь продолжит остальные."
                )
                + "\nCAPTCHA в боте не интерактивна: её нельзя пройти по одному скриншоту.",
                f"ranepa-captcha:{job_id}:{attempt}",
            )
        else:
            # Only consecutive unresolved CAPTCHA failures open the breaker.
            state.consecutive_captcha = 0

    state.updated_at = now
    await db.flush()
    return state.paused, state.consecutive_captcha
