"""Opt-in private Telegram report after manual timetable recognition.

Sends only to the configured admin user. A failed Telegram request never
changes a saved review, triggers new AI calls, or publishes a timetable.
"""

import json
from decimal import Decimal

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from studgroup.models import AnalysisBudgetCall, CabinetScheduleUpload, Group


async def notify_completed(engine, settings, ident):
    if (
        not settings.cabinet_review_admin_push_enabled
        or not settings.cabinet_review_admin_push_telegram_id
        or not settings.telegram_bot_token
    ):
        return False

    async with AsyncSession(engine) as db:
        row = await db.get(CabinetScheduleUpload, ident)
        if row is None or row.state != "pending":
            return False
        group = await db.get(Group, row.group_id)
        if group is None or not group.pilot_authorized:
            return False
        attempts = (
            await db.scalars(
                select(AnalysisBudgetCall).where(
                    AnalysisBudgetCall.group_id == row.group_id,
                    AnalysisBudgetCall.attempt_key.like(f"cabinet-schedule:{ident}:%"),
                )
            )
        ).all()
        charged = sum((item.charged_usd for item in attempts), Decimal("0"))
        reserved = sum((item.reserved_usd for item in attempts), Decimal("0"))
        unsettled = sum(item.state != "settled" for item in attempts)
        rows_count = len(json.loads(row.rows_json))
        description = (
            f"📚 Расписание распознано — {group.name}\n"
            f"Статус: черновик на проверке\n"
            f"Пар: {rows_count}\n"
            f"Неделя: {row.week_start.isoformat()}\n"
            f"Полнота: {'частично' if row.progress_stage == 'ready_partial' else 'готово'}\n"
            f"DeepSeek: ${charged:.6f} учтено"
            + (f", ${reserved:.6f} зарезервировано" if reserved else "")
            + (f"; неокончательных запросов: {unsettled}" if unsettled else "")
            + f"\nЗапросов: {len(attempts)}\nЗадача: {ident}\n"
            "Открой админку → Загрузка расписания → Подтвердить."
        )

    try:
        async with httpx.AsyncClient(timeout=5, trust_env=False) as client:
            response = await client.post(
                f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage",
                json={
                    "chat_id": settings.cabinet_review_admin_push_telegram_id,
                    "text": description,
                    "disable_web_page_preview": True,
                },
            )
            response.raise_for_status()
            ok = response.json().get("ok") is True
            if not ok:
                print("CABINET_REVIEW_PUSH_FAILED provider_rejected", flush=True)
            return ok
    except (httpx.HTTPError, ValueError):
        print("CABINET_REVIEW_PUSH_FAILED transport_or_response", flush=True)
        return False
