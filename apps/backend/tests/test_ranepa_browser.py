"""The month browser renews a session that expires during navigation."""

import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

from studgroup import ranepa_browser


def test_month_relogs_in_after_session_expires_mid_navigation(monkeypatch):
    calls = {"open": 0, "state": 0}

    class Clicker:
        async def click(self):
            return None

    class Page:
        def get_by_role(self, *args, **kwargs):
            return Clicker()

        def locator(self, *args, **kwargs):
            return self

        def nth(self, index):
            return Clicker()

        async def content(self):
            return "checked page"

        async def wait_for_timeout(self, milliseconds):
            return None

    async def open_(page, login, password):
        calls["open"] += 1
        return "rendered"

    async def verify(page, code):
        return True

    async def state(page):
        calls["state"] += 1
        return "login_required" if calls["state"] <= 2 else "rendered"

    monkeypatch.setattr(ranepa_browser, "_open", open_)
    monkeypatch.setattr(ranepa_browser, "_verify_group", verify)
    monkeypatch.setattr(ranepa_browser, "_state", state)
    monkeypatch.setattr(ranepa_browser, "extract_cabinet_month", lambda *args: ("card",))
    today = datetime.now(ZoneInfo("Europe/Moscow")).date()
    result = asyncio.run(read_month_for_current_month(Page(), today))
    assert result.status == "preview_only" and result.cards == ("card",)
    assert calls["open"] == 2


async def read_month_for_current_month(page, today):
    return await ranepa_browser.read_month(
        page, "login", "password", "Э-ОБ-385-04-26-02", today.year, today.month
    )
