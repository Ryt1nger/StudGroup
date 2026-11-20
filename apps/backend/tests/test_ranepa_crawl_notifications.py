"""Private deduplicated owner notices for the RANEPA crawl.

All tests use SQLite and a fake owner, without Telegram API, RuCaptcha,
student accounts, HTML, cookies or network requests.
"""

import asyncio
import json
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from studgroup.bot_admin import enqueue_once
from studgroup.models import (
    Base,
    BotOutbox,
    Group,
    RanepaCrawlJob,
    RanepaManualCaptcha,
    User,
)
from studgroup.ranepa_crawl_notifications import (
    CrawlRunStats,
    failed_account_line,
    notify_account_crawl_result,
    notify_batch_report,
    notify_captcha_detected,
    notify_successful_crawl,
    successful_account_line,
)


def test_captcha_seen_notification_is_immediate_private_and_deduplicated(tmp_path):
    async def scenario():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/notices.db")
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            job_id = uuid4()
            student_id = uuid4()
            assert await notify_captcha_detected(
                engine,
                owner_id=915,
                job_id=job_id,
                attempt=1,
                location="month",
                user_id=student_id,
                student_name="Иванов Иван",
                telegram_group_name="БИ 1.2",
                group_code="Э-ОБ-385-04-26-02",
            )
            assert not await notify_captcha_detected(
                engine, owner_id=915, job_id=job_id, attempt=1, location="week"
            )
            async with AsyncSession(engine) as db:
                rows = (await db.scalars(select(BotOutbox))).all()
                assert len(rows) == 1
                notice = json.loads(rows[0].payload)
                assert notice["chat_id"] == 915
                assert "обнаружена CAPTCHA" in notice["text"]
                assert "месячное расписание" in notice["text"]
                assert f"Иванов Иван · ID {str(student_id)[:8]}" in notice["text"]
                assert "Группа ТГ: БИ 1.2" in notice["text"]
                assert "Группа РАНХиГС: Э-ОБ-385-04-26-02" in notice["text"]
                assert "password" not in rows[0].payload
                assert "cookie" not in rows[0].payload.lower()
                assert "http" not in rows[0].payload
            assert await notify_captcha_detected(
                engine, owner_id=915, job_id=job_id, attempt=2, location="week"
            )
            assert not await notify_captcha_detected(
                engine, owner_id=None, job_id=uuid4(), attempt=1, location="sdo"
            )
            async with AsyncSession(engine) as db:
                assert len((await db.scalars(select(BotOutbox))).all()) == 2
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_successful_run_summary_uses_persisted_counts_and_run_dedup(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_RUN_ID", "98902222")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "1")

    async def scenario():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/summary.db")
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            stats = CrawlRunStats(
                successful_accounts=2,
                failed_accounts=1,
                saved_weeks=9,
                saved_lessons=47,
                saved_sdo_links=15,
                saved_sdo_courses=6,
            )
            assert await notify_successful_crawl(engine, owner_id=915, stats=stats, scope="batch")
            assert not await notify_successful_crawl(
                engine, owner_id=915, stats=stats, scope="batch"
            )
            async with AsyncSession(engine) as db:
                rows = (await db.scalars(select(BotOutbox))).all()
                assert len(rows) == 1
                notice = json.loads(rows[0].payload)
                assert notice["chat_id"] == 915
                assert "Успешно обработано учёток: 2" in notice["text"]
                assert "Сохранено недель расписания: 9" in notice["text"]
                assert "Подтверждено занятий: 47" in notice["text"]
                assert "Найдено ссылок на занятия в СДО: 15" in notice["text"]
                assert rows[0].dedup_key == "ranepa-crawl-success:batch:98902222:1"
            assert await notify_successful_crawl(engine, owner_id=915, stats=stats, scope="pilot")
            monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "2")
            assert await notify_successful_crawl(engine, owner_id=915, stats=stats, scope="batch")
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_per_account_results_report_actual_sdo_extraction_and_failures(tmp_path):
    async def scenario():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/accounts.db")
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            job = uuid4()
            account = uuid4()
            params = {
                "owner_id": 915,
                "job_id": job,
                "attempt": 1,
                "user_id": account,
                "group_code": "БИ1.2",
                "telegram_group_name": "ТГ · БИ 1.2",
                "student_name": "Тестовый студент",
            }
            assert await notify_account_crawl_result(
                engine,
                **params,
                failure_code=None,
                stats=CrawlRunStats(saved_weeks=3, saved_lessons=12, saved_sdo_links=2),
            )
            assert not await notify_account_crawl_result(
                engine,
                **params,
                failure_code="captcha",
                stats=CrawlRunStats(),
            )
            assert await notify_account_crawl_result(
                engine,
                **{**params, "attempt": 2},
                failure_code="captcha",
                stats=CrawlRunStats(saved_weeks=1, saved_lessons=4),
            )
            assert await notify_account_crawl_result(
                engine,
                **{**params, "attempt": 3},
                failure_code=None,
                stats=CrawlRunStats(saved_weeks=3, saved_lessons=9),
            )
            async with AsyncSession(engine) as db:
                rows = (await db.scalars(select(BotOutbox).order_by(BotOutbox.dedup_key))).all()
                assert len(rows) == 3
                notices = {
                    int(row.dedup_key.rsplit(":", 1)[1]): json.loads(row.payload) for row in rows
                }
                assert all(notice["chat_id"] == 915 for notice in notices.values())
                assert "✅ StudGroup · данные СДО извлечены" in notices[1]["text"]
                assert "Группа ТГ: ТГ · БИ 1.2" in notices[1]["text"]
                assert "Группа РАНХиГС: БИ1.2" in notices[1]["text"]
                assert "Ссылок СДО: 2" in notices[1]["text"]
                assert "❌ StudGroup · ошибка обхода СДО" in notices[2]["text"]
                assert "CAPTCHA" in notices[2]["text"]
                assert "captcha" not in notices[2]["text"]
                assert "Автоповтор — не раньше чем через 6 ч" in notices[2]["text"]
                assert "Результат: недель 1, занятий 4" in notices[2]["text"]
                assert "Студент: Тестовый студент" in notices[2]["text"]
                assert "Группа РАНХиГС: БИ1.2" in notices[2]["text"]
                assert len(notices[2]["text"].splitlines()) == 8
                assert "⚠️ StudGroup · данные СДО не извлечены" in notices[3]["text"]
                assert "Ссылок СДО: 0" in notices[3]["text"]
                assert all("\\n" not in notice["text"] for notice in notices.values())
                assert all("cookie" not in row.payload.lower() for row in rows)
                assert all("http" not in row.payload.lower() for row in rows)
        finally:
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("failure_code", "expected"),
    [
        ("login_failed", "Не удалось войти"),
        ("captcha", "Не удалось пройти проверку CAPTCHA"),
        ("source_unavailable", "точная причина неизвестна"),
        ("identity_unverified", "Не удалось подтвердить учебную группу"),
        ("parse_failed", "не удалось проверить"),
        ("internal", "Внутренний сбой StudGroup"),
    ],
)
def test_every_account_failure_has_russian_explanation(tmp_path, failure_code, expected):
    async def scenario():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/russian-alert.db")
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            assert await notify_account_crawl_result(
                engine,
                owner_id=915,
                job_id=uuid4(),
                attempt=1,
                user_id=uuid4(),
                group_code="А-001",
                student_name="Тест",
                failure_code=failure_code,
                stats=CrawlRunStats(saved_weeks=1),
            )
            async with AsyncSession(engine) as db:
                row = await db.scalar(select(BotOutbox))
                message = json.loads(row.payload)["text"]
                assert expected in message
                assert failure_code not in message
                assert "Причина:" in message
                assert "Результат: недель 1" in message
                assert len(message.splitlines()) == 8
                assert "\\n" not in message
        finally:
            await engine.dispose()

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("detail", "expected"),
    [
        ("week_unreadable", "Недельное расписание не удалось прочитать"),
        ("week_month_mismatch", "Недельное расписание не совпало"),
        ("month_incomplete", "Месячное расписание загрузилось не полностью"),
    ],
)
def test_owner_gets_specific_safe_week_failure_reason(tmp_path, detail, expected):
    async def scenario():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/weekly-reason.db")
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            assert await notify_account_crawl_result(
                engine,
                owner_id=915,
                job_id=uuid4(),
                attempt=1,
                user_id=uuid4(),
                group_code="A-001",
                student_name="Тест",
                failure_code="parse_failed",
                failure_detail=detail,
                stats=CrawlRunStats(saved_weeks=1),
            )
            async with AsyncSession(engine) as db:
                row = await db.scalar(select(BotOutbox))
                message = json.loads(row.payload)["text"]
                assert expected in message
                assert "Сохранено недель" not in message
                assert detail not in message
                assert "password" not in row.payload.lower()
                assert "cookie" not in row.payload.lower()
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_per_account_results_reject_unsafe_values(tmp_path):
    async def scenario():
        account = uuid4()
        args = {
            "owner_id": 915,
            "job_id": uuid4(),
            "attempt": 1,
            "user_id": account,
            "group_code": "Test",
            "student_name": "Student",
            "stats": CrawlRunStats(),
        }
        with pytest.raises(ValueError, match="invalid_crawl_failure_code"):
            await notify_account_crawl_result(None, **args, failure_code="secret")
        with pytest.raises(ValueError, match="invalid_crawl_attempt"):
            await notify_account_crawl_result(
                None, **{**args, "attempt": 0}, failure_code="captcha"
            )
        with pytest.raises(ValueError, match="invalid_crawl_failure_detail"):
            await notify_account_crawl_result(
                None,
                **args,
                failure_code="parse_failed",
                failure_detail="https://private.example/cookie=secret",
            )
        with pytest.raises(ValueError, match="invalid_crawl_diagnostic_code"):
            await notify_account_crawl_result(
                None,
                **args,
                failure_code="source_unavailable",
                diagnostic_code="https://private.example/token=secret",
            )
        assert not await notify_account_crawl_result(
            None, **{**args, "owner_id": None}, failure_code="captcha"
        )

    asyncio.run(scenario())


def test_failed_only_run_does_not_claim_success(tmp_path):
    async def scenario():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/no-success.db")
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            assert not await notify_successful_crawl(
                engine,
                owner_id=915,
                scope="batch",
                stats=CrawlRunStats(failed_accounts=4, saved_weeks=2),
            )
            async with AsyncSession(engine) as db:
                assert (await db.scalars(select(BotOutbox))).all() == []
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_notification_rejects_untrusted_location():
    async def scenario():
        with pytest.raises(ValueError, match="invalid_captcha_notice_location"):
            await notify_captcha_detected(
                None, owner_id=915, job_id=uuid4(), attempt=1, location="http://secret"
            )

    asyncio.run(scenario())


def test_stats_aggregation_keeps_partial_data_separate_from_success():
    summary = CrawlRunStats()
    summary.add(CrawlRunStats(successful_accounts=1, saved_weeks=3, saved_lessons=12))
    summary.add(CrawlRunStats(failed_accounts=1, saved_weeks=1, saved_lessons=4))
    assert summary.successful_accounts == 1
    assert summary.failed_accounts == 1
    assert summary.saved_weeks == 4
    assert summary.saved_lessons == 16


def test_owner_full_rescan_report_in_russian_even_if_every_account_fails(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_RUN_ID", "202611151234")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "1")

    async def scenario():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/full-report.db")
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            stats = CrawlRunStats(
                failed_accounts=2,
                saved_weeks=1,
                failure_reasons={"parse_failed": 1, "captcha": 1},
            )
            assert await notify_batch_report(
                engine,
                owner_id=915,
                stats=stats,
                full_rescan=True,
                planned=3,
                remaining=1,
                stop_reason="paused",
            )
            assert not await notify_batch_report(
                engine,
                owner_id=915,
                stats=stats,
                full_rescan=True,
                planned=3,
                remaining=1,
                stop_reason="paused",
            )
            async with AsyncSession(engine) as db:
                rows = (await db.scalars(select(BotOutbox))).all()
                assert len(rows) == 1
                notice = json.loads(rows[0].payload)
                assert notice["chat_id"] == 915
                assert "повторный обход СДО завершён частично" in notice["text"]
                assert "Запланировано учёток: 3" in notice["text"]
                assert "Неудачных попыток: 2" in notice["text"]
                assert "Не завершено из запланированных: 1" in notice["text"]
                assert "Данные источника не прошли проверку: 1" in notice["text"]
                assert "защитой CAPTCHA" in notice["text"]
                assert "parse_failed" not in notice["text"]
                assert "http" not in rows[0].payload.lower()
                assert "cookie" not in rows[0].payload.lower()
                assert rows[0].dedup_key == "ranepa-crawl-report:202611151234:1"
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_regular_reports_only_when_work_was_performed(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_RUN_ID", "202611151235")

    async def scenario():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/regular-report.db")
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            assert not await notify_batch_report(engine, owner_id=915, stats=CrawlRunStats())
            assert await notify_batch_report(
                engine,
                owner_id=915,
                stats=CrawlRunStats(
                    successful_accounts=1,
                    verified_sdo_accounts=1,
                    saved_weeks=6,
                    saved_lessons=71,
                    saved_sdo_links=1,
                    saved_sdo_courses=17,
                ),
            )
            async with AsyncSession(engine) as db:
                row = await db.scalar(select(BotOutbox))
                notice = json.loads(row.payload)
                assert "отчёт о плановом обходе" in notice["text"]
                assert "С подтверждёнными данными СДО: 1" in notice["text"]
                assert "Сохранено недель: 6; занятий: 71" in notice["text"]
                assert "Запланировано" not in notice["text"]
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_manual_captcha_button_replaces_duplicate_final_account_failure(tmp_path):
    async def scenario():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/manual-once.db")
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            group_id, user_id, job_id, challenge_id = (uuid4() for _ in range(4))
            now = datetime.now(UTC)
            async with AsyncSession(engine) as db:
                db.add_all(
                    [
                        Group(
                            id=group_id,
                            telegram_chat_id=-98721,
                            name="Группа Telegram",
                            academic_group_code="Э-ОБ-385-04-26-03",
                            timezone="Europe/Moscow",
                        ),
                        User(id=user_id, telegram_user_id=98721, display_name="Студент"),
                    ]
                )
                await db.flush()
                db.add(
                    RanepaCrawlJob(
                        id=job_id,
                        group_id=group_id,
                        user_id=user_id,
                        week_start=date(2026, 11, 16),
                        priority=0,
                        state="running",
                        attempts=1,
                        created_at=now,
                    )
                )
                await db.flush()
                db.add(
                    RanepaManualCaptcha(
                        id=challenge_id,
                        job_id=job_id,
                        attempt=1,
                        owner_telegram_id=915,
                        state="pending",
                        screenshot_base64=None,
                        created_at=now,
                        expires_at=now + timedelta(minutes=6),
                    )
                )
                assert await enqueue_once(
                    db,
                    "sendMessage",
                    {
                        "chat_id": 915,
                        "text": (
                            "❌ Прогон учётки: CAPTCHA не пройдена.\n"
                            "Группа ТГ: Группа Telegram\n"
                            "Группа РАНХиГС: Э-ОБ-385-04-26-03"
                        ),
                        "reply_markup": {
                            "inline_keyboard": [
                                [
                                    {
                                        "text": "🧩 Пройти CAPTCHA",
                                        "web_app": {
                                            "url": f"https://studgroup.example/ranepa-captcha/{challenge_id}"
                                        },
                                    }
                                ]
                            ]
                        },
                    },
                    f"ranepa-owner-captcha:{challenge_id}",
                )
                await db.commit()
            params = {
                "owner_id": 915,
                "job_id": job_id,
                "user_id": user_id,
                "group_code": "Э-ОБ-385-04-26-03",
                "telegram_group_name": "Группа Telegram",
                "student_name": "Студент",
                "stats": CrawlRunStats(),
            }
            assert not await notify_account_crawl_result(
                engine, **params, attempt=1, failure_code="captcha"
            )
            async with AsyncSession(engine) as db:
                rows = (await db.scalars(select(BotOutbox))).all()
                assert len(rows) == 1
                payload = json.loads(rows[0].payload)
                assert "Группа ТГ: Группа Telegram" in payload["text"]
                assert "Группа РАНХиГС: Э-ОБ-385-04-26-03" in payload["text"]
                assert payload["reply_markup"]["inline_keyboard"][0][0]["text"] == (
                    "🧩 Пройти CAPTCHA"
                )
            # A different parsing error after a human attempt remains reportable.
            assert await notify_account_crawl_result(
                engine, **params, attempt=1, failure_code="parse_failed"
            )
            # A new account attempt must not be silenced by an old manual link.
            assert await notify_account_crawl_result(
                engine, **params, attempt=2, failure_code="captcha"
            )
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_owner_receives_short_specific_timeout_diagnosis_and_real_backoff(tmp_path):
    async def scenario():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/timeout.db")
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            assert await notify_account_crawl_result(
                engine,
                owner_id=915,
                job_id=uuid4(),
                attempt=3,
                user_id=uuid4(),
                group_code="Э-ОБ-385-04-26-02",
                telegram_group_name="БИ 1.2",
                student_name="Katerina",
                failure_code="source_unavailable",
                diagnostic_code="timeout",
                stats=CrawlRunStats(),
            )
            async with AsyncSession(engine) as db:
                row = await db.scalar(select(BotOutbox))
                message = json.loads(row.payload)["text"]
            assert len(message.splitlines()) == 8
            assert "Студент: Katerina" in message
            assert "Группа ТГ: БИ 1.2" in message
            assert "Группа РАНХиГС: Э-ОБ-385-04-26-02" in message
            assert "Причина: Сайт не ответил вовремя." in message
            assert "Автоповтор — не раньше чем через 2 ч." in message
            assert "24 часа" not in message
            assert "Результат: недель 0" in message
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_routine_batch_groups_transient_failures_and_diagnostic_causes(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_RUN_ID", "12345")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "1")

    async def scenario():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/batch-errors.db")
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            stats = CrawlRunStats(
                failed_accounts=2,
                failure_reasons={"source_unavailable": 2},
                diagnostic_reasons={"timeout": 2},
            )
            assert await notify_batch_report(engine, owner_id=915, stats=stats)
            assert not await notify_batch_report(engine, owner_id=915, stats=stats)
            async with AsyncSession(engine) as db:
                rows = (await db.scalars(select(BotOutbox))).all()
                assert len(rows) == 1
                message = json.loads(rows[0].payload)["text"]
            assert len(message.splitlines()) >= 4
            assert "Обработано: 2; успешно: 0; ошибок: 2" in message
            assert "Сайт не ответил вовремя — 2" in message
            assert "Ожидают автоматического повтора: 2" in message
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_account_notice_explains_missing_consent_with_full_owner_context(tmp_path):
    async def scenario():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/consent.db")
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            uid = uuid4()
            assert await notify_account_crawl_result(
                engine,
                owner_id=915,
                job_id=uuid4(),
                attempt=1,
                user_id=uid,
                group_code="Э-ОБ-385-04-26-02",
                telegram_group_name="БИ 1.2",
                student_name="Иванов Иван",
                failure_code="login_failed",
                failure_detail="consent_missing",
                stats=CrawlRunStats(),
            )
            async with AsyncSession(engine) as db:
                row = await db.scalar(select(BotOutbox))
                message = json.loads(row.payload)["text"]
            assert f"Студент: Иванов Иван · ID {str(uid)[:8]}" in message
            assert "Группа ТГ: БИ 1.2" in message
            assert "Группа РАНХиГС: Э-ОБ-385-04-26-02" in message
            assert "Этап: Проверка согласия" in message
            assert "Причина: Отсутствует действующее согласие" in message
            assert "Студенту нужно дать согласие" in message
            assert "Автоповтор отключён" in message
            assert "пароль неверный" not in message.lower()
            assert len(message.splitlines()) == 8
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_batch_summary_does_not_promise_retry_for_login_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_RUN_ID", "2026112088888")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "1")

    async def scenario():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/mixed.db")
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            uid = uuid4()
            detail = failed_account_line(
                student_name="Иванов Иван",
                telegram_group_name="БИ 1.2",
                group_code="Э-ОБ-385-04-26-02",
                user_id=uid,
                failure_code="login_failed",
                failure_detail="credentials_missing",
            )
            success_item = successful_account_line(
                student_name="Katerina",
                telegram_group_name="БИ 1.2",
                group_code="Э-ОБ-385-04-26-02",
                user_id=uuid4(),
                stats=CrawlRunStats(
                    saved_weeks=6,
                    saved_lessons=70,
                    discovered_sdo_candidates=12,
                    saved_sdo_links=3,
                ),
            )
            stats = CrawlRunStats(
                successful_accounts=2,
                failed_accounts=1,
                failure_reasons={"login_failed": 1},
                failed_accounts_detail=[detail],
                successful_accounts_detail=[success_item],
            )
            assert await notify_batch_report(engine, owner_id=915, stats=stats)
            async with AsyncSession(engine) as db:
                row = await db.scalar(select(BotOutbox))
                message = json.loads(row.payload)["text"]
            assert "Обработано: 3; успешно: 2; ошибок: 1" in message
            assert "Успешно:" in message
            assert "Katerina" in message
            assert "кандидатов 12, подтверждённых ссылок 3" in message
            assert f"Иванов Иван (ID {str(uid)[:8]})" in message
            assert "БИ 1.2 / Э-ОБ-385-04-26-02" in message
            assert "Учётные данные СДО не подключены" in message
            assert "Требует проверки входа/согласия: 1; автоповтор отключён." in message
            assert "Ожидают автоматического повтора" not in message
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_failed_account_line_rejects_untrusted_cause():
    with pytest.raises(ValueError, match="invalid_crawl_failure_detail"):
        failed_account_line(
            student_name="Иван",
            telegram_group_name="БИ 1.2",
            group_code="Э-ОБ-385-04-26-02",
            user_id=uuid4(),
            failure_code="login_failed",
            failure_detail="https://evil.example/?token=secret",
        )
