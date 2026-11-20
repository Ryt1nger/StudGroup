"""Closed-vocabulary, privacy-safe diagnostics for the RANEPA browser crawl.

Inspect untrusted exception text only in memory. Never persist or send exception
messages: Playwright errors may contain private URLs, cookies or credentials.
Codes below are safe for the trace, owner outbox and aggregate reporting.
"""

import re

DIAGNOSTIC_REASONS = {
    "timeout": "Сайт не ответил вовремя",
    "dns": "Не удалось найти сервер СДО",
    "connection": "Соединение с СДО прервалось",
    "tls": "Ошибка защищённого соединения с СДО",
    "http_403": "Сайт отклонил доступ (403)",
    "http_429": "Сайт ограничил частоту запросов (429)",
    "http_5xx": "Сервер СДО ответил с ошибкой",
    "challenge_pending": "Сайт не завершил проверку браузера",
    "unexpected_redirect": "Сайт перенаправил на неожиданную страницу",
    "page_changed": "Не удалось прочитать элементы страницы СДО",
    "month_incomplete": "Месячное расписание не удалось прочитать",
    "week_unavailable": "Недельное расписание не загрузилось",
    "unknown": "Точную причину сбоя установить не удалось",
}

_HTTP_SERVER_ERROR = re.compile(r"(?<!\d)5\d\d(?!\d)")


def diagnose_exception(error: BaseException) -> str:
    """Classify an exception without exposing its potentially sensitive message."""
    name = type(error).__name__.lower()
    if isinstance(error, TimeoutError) or "timeouterror" in name:
        return "timeout"
    if isinstance(error, (ConnectionError, ConnectionResetError, BrokenPipeError)):
        return "connection"
    message = str(error)[:2048].lower()
    if any(s in message for s in ("err_name_not_resolved", "getaddrinfo", "eai_again", "dns_probe_finished")):
        return "dns"
    if any(s in message for s in ("err_cert_", "ssl_error", "tls handshake", "certificate verify")):
        return "tls"
    if any(s in message for s in ("err_connection_", "err_tunnel_connection_", "network is unreachable", "connection refused")):
        return "connection"
    if re.search(r"\b(?:http[ /]|status[=: ]+|response[=: ]+|error[=: ]+)403\b", message):
        return "http_403"
    if re.search(r"\b(?:http[ /]|status[=: ]+|response[=: ]+|error[=: ]+)429\b", message):
        return "http_429"
    if re.search(r"\b(?:http[ /]|status[=: ]+|response[=: ]+|error[=: ]+)5\d\d\b", message):
        return "http_5xx"
    if any(s in message for s in ("strict mode violation", "no element matches", "selector", "locator(", "not visible")):
        return "page_changed"
    return "unknown"


def diagnose_http_response(response) -> str | None:
    """Classify the status from the page's own HTTP response, when available."""
    status = getattr(response, "status", None)
    if status == 403:
        return "http_403"
    if status == 429:
        return "http_429"
    if type(status) is int and 500 <= status <= 599:
        return "http_5xx"
    return None


def diagnose_browser_state(status: str) -> str | None:
    """Map known browser states, never interpreting an unknown state as fact."""
    if status in DIAGNOSTIC_REASONS:
        return status
    if status in {"cabinet_month_incomplete", "cabinet_month_card_incomplete", "cabinet_month_mismatch"}:
        return "month_incomplete"
    if status in {"cabinet_week_incomplete", "cabinet_week_grid_missing", "cabinet_week_loading"}:
        return "week_unavailable"
    if status == "source_unavailable":
        return "unknown"
    return None
