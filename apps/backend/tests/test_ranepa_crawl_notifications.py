"""Private deduplicated owner notices for the RANEPA crawl.

All tests use SQLite and a fake owner, without Telegram API, RuCaptcha,
student accounts, HTML, cookies or network requests.
"""

import asyncio
import json
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from studgroup.models import Base, BotOutbox
from studgroup.ranepa_crawl_notifications import (
    CrawlRunStats,
    notify_account_crawl_result,
    notify_captcha_detected,
    notify_successful_crawl,
)


def test_captcha_seen_notification_is_immediate_private_and_deduplicated(tmp_path):
    async def scenario():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/notices.db")
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            job_id = uuid4()
            assert await notify_captcha_detected(
                engine, owner_id=915, job_id=job_id, attempt=1, location="month"
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
                    int(row.dedup_key.rsplit(":", 1)[1]): json.loads(row.payload)
                    for row in rows
                }
                assert all(notice["chat_id"] == 915 for notice in notices.values())
                assert "✅ StudGroup · данные СДО извлечены" in notices[1]["text"]
                assert "Ссылок СДО: 2" in notices[1]["text"]
                assert "❌ StudGroup · ошибка" in notices[2]["text"]
                assert "captcha" in notices[2]["text"]
                assert "Сохранено недель: 1" in notices[2]["text"]
                assert "⚠️ StudGroup · данные СДО не извлечены" in notices[3]["text"]
                assert "Ссылок СДО: 0" in notices[3]["text"]
                assert all("\\n" not in notice["text"] for notice in notices.values())
                assert all("cookie" not in row.payload.lower() for row in rows)
                assert all("http" not in row.payload.lower() for row in rows)
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
