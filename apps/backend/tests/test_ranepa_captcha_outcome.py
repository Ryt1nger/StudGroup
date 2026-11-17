"""Offline proof that crawl results explicitly report CAPTCHA observations.

No real website, credentials, paid provider or mouse actions are used.
"""

import asyncio
import logging
from datetime import date, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

from studgroup import ranepa_worker
from studgroup.ranepa_schedule import WeekSnapshot


@pytest.mark.parametrize("challenge_present", [False, True])
@pytest.mark.parametrize("fanout_fails", [False, True])
def test_successful_crawl_reports_whether_captcha_appeared_and_was_recovered(
    monkeypatch, caplog, challenge_present, fanout_fails
):
    async def scenario():
        calls = {
            "month": 0,
            "week": 0,
            "solve": 0,
            "wait": 0,
            "saved": 0,
            "materialized": 0,
            "rolled_back": 0,
        }

        class Browser:
            async def new_context(self, **kwargs):
                assert kwargs["locale"] == "ru-RU"
                return self

            async def new_page(self):
                return SimpleNamespace(url="https://my.ranepa.ru/lk/student/schedule")

            async def close(self):
                return None

        class Playwright:
            async def __aenter__(self):
                self.chromium = self
                return self

            async def __aexit__(self, *args):
                return False

            async def launch(self, *, headless):
                assert headless
                return Browser()

        async def month(*args):
            calls["month"] += 1
            if challenge_present and calls["month"] == 1:
                return SimpleNamespace(status="captcha_required")
            return SimpleNamespace(status="preview_only", cards=())

        async def week(_page, _login, _password, code, first_day, *, allow_empty):
            calls["week"] += 1
            assert allow_empty is True
            return SimpleNamespace(
                status="preview_only",
                snapshot=WeekSnapshot(code, first_day, first_day + timedelta(days=6), ()),
            )

        async def solve(_page, _client, **kwargs):
            calls["solve"] += 1
            assert kwargs["timeout_seconds"] == 110
            return True

        async def wait(_page):
            calls["wait"] += 1
            return "challenge_gone_after_wait"

        async def sdo(*args, **kwargs):
            return {}, {}, {}

        async def remember(*args, **kwargs):
            calls["saved"] += 1
            assert kwargs["group_identity_verified"] is True
            assert kwargs["source_complete"] is True

        async def materialize(*args, **kwargs):
            calls["materialized"] += 1
            if fanout_fails:
                raise ValueError("offline_fanout_failure")

        class Session:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def commit(self):
                return None

            async def rollback(self):
                calls["rolled_back"] += 1

        monkeypatch.setattr(ranepa_worker, "async_playwright", Playwright)
        monkeypatch.setattr(ranepa_worker, "read_month", month)
        monkeypatch.setattr(ranepa_worker, "read_week", week)
        monkeypatch.setattr(ranepa_worker, "solve_rotating_halves", solve)
        monkeypatch.setattr(ranepa_worker, "wait_for_captcha_resolution", wait)
        monkeypatch.setattr(ranepa_worker, "resolve_sdo_exits", sdo)
        monkeypatch.setattr(ranepa_worker, "remember_personal", remember)
        monkeypatch.setattr(ranepa_worker, "materialize_verified_facts", materialize)
        monkeypatch.setattr(ranepa_worker, "AsyncSession", lambda *_: Session())
        monkeypatch.delenv("RANEPA_PROXY_URL", raising=False)

        with caplog.at_level(logging.WARNING):
            result = await ranepa_worker._collect(
                None,
                uuid4(),
                uuid4(),
                "A-001",
                "test-user",
                "test-password",
                date(2026, 11, 15),
                captcha_client=object(),
                captcha_budget=ranepa_worker.CaptchaBudget(2),
            )

        if fanout_fails:
            assert result == "internal"
            assert calls["week"] == 1
            assert calls["saved"] == 1
            assert calls["materialized"] == 1
            assert calls["rolled_back"] == 1
            assert "peer_materialization_failed" in caplog.text
            return
        assert result is None
        assert calls == {
            "month": 2 if challenge_present else 1,
            "week": 6,
            "solve": 1 if challenge_present else 0,
            "wait": 1 if challenge_present else 0,
            "saved": 6,
            "materialized": 6,
            "rolled_back": 0,
        }
        if challenge_present:
            assert "RANEPA_CAPTCHA_STAGE challenge_detected_month" in caplog.text
            assert "RANEPA_CAPTCHA_STAGE schedule_recheck_after_captcha_month" in caplog.text
            assert "RANEPA_CAPTCHA_STAGE month_verified_after_challenge" in caplog.text
            assert "RANEPA_CAPTCHA_STAGE captcha_seen_schedule_recovered" in caplog.text
            assert "captcha_not_seen_schedule_verified" not in caplog.text
        else:
            assert "RANEPA_CAPTCHA_STAGE captcha_not_seen_schedule_verified" in caplog.text
            assert "paid_attempt_started" not in caplog.text
            assert "captcha_seen_schedule_recovered" not in caplog.text

    asyncio.run(scenario())
