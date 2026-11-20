"""Privacy-safe crawl diagnostics and browser exception classification."""

import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from studgroup import ranepa_browser
from studgroup.ranepa_crawl_trace import CrawlTrace
from studgroup.ranepa_failure_diagnostics import (
    diagnose_browser_state,
    diagnose_exception,
    diagnose_http_response,
)


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (TimeoutError("private URL with cookie"), "timeout"),
        (TimeoutError("Locator.click: waiting for locator('button')"), "page_changed"),
        (Exception("net::ERR_NAME_NOT_RESOLVED at https://secret"), "dns"),
        (Exception("net::ERR_CONNECTION_RESET"), "connection"),
        (Exception("net::ERR_CERT_AUTHORITY_INVALID"), "tls"),
        (Exception("HTTP 403 private"), "http_403"),
        (Exception("status 429 private"), "http_429"),
        (Exception("HTTP 503 private"), "http_5xx"),
        (Exception("strict mode violation locator('secret')"), "page_changed"),
        (Exception("unexpected private URL with cookie"), "unknown"),
    ],
)
def test_exception_message_is_only_used_for_safe_category(error, expected):
    assert diagnose_exception(error) == expected


def test_http_response_and_page_state_are_classified_without_urls():
    class Response:
        def __init__(self, status):
            self.status = status

    assert diagnose_http_response(Response(403)) == "http_403"
    assert diagnose_http_response(Response(429)) == "http_429"
    assert diagnose_http_response(Response(502)) == "http_5xx"
    assert diagnose_http_response(Response(200)) is None
    assert diagnose_http_response(None) is None
    assert diagnose_browser_state("cabinet_month_incomplete") == "month_incomplete"
    assert diagnose_browser_state("source_unavailable") == "unknown"


def test_browser_return_includes_diagnostic_without_exception_text(monkeypatch):
    async def broken_open(*_args):
        raise RuntimeError("net::ERR_NAME_NOT_RESOLVED URL https://private/cookie=abc")

    monkeypatch.setattr(ranepa_browser, "_open", broken_open)
    today = datetime.now(ZoneInfo("Europe/Moscow")).date()
    month = asyncio.run(
        ranepa_browser.read_month(
            object(), "secret_login", "secret_password", "A-001", today.year, today.month
        )
    )
    monday = today.fromordinal(today.toordinal() - today.weekday())
    week = asyncio.run(
        ranepa_browser.read_week(object(), "secret_login", "secret_password", "A-001", monday)
    )
    assert month.status == week.status == "source_unavailable"
    assert month.diagnostic == week.diagnostic == "dns"
    assert "cookie" not in repr(month).lower()
    assert "secret" not in repr(week).lower()


def test_crawl_trace_records_only_whitelisted_diagnostic_codes():
    trace = CrawlTrace()
    trace.mark_diagnostic("timeout")
    trace.mark_diagnostic("connection")
    assert trace.diagnostic == "connection"
    assert trace.events[-1]["step"] == "diagnostic_connection"
    with pytest.raises(ValueError, match="unsafe_crawl_diagnostic"):
        trace.mark_diagnostic("https://private/cookie=secret")
    assert len(trace.events) == 2
