"""Server-only regional egress proxy. Never expose credentials in logs."""

import os
from urllib.parse import unquote, urlsplit


class ProxyConfigurationError(ValueError):
    """Invalid server-only proxy configuration."""


def current_proxy_url() -> str | None:
    value = os.environ.get("RANEPA_PROXY_URL", "").strip()
    if not value:
        return None
    parts = urlsplit(value)
    try:
        port = parts.port
    except ValueError:
        raise ProxyConfigurationError("invalid_proxy_port") from None
    if (
        parts.scheme not in {"http", "https"}
        or not parts.hostname
        or port is None
        or parts.path not in {"", "/"}
        or parts.query
        or parts.fragment
    ):
        raise ProxyConfigurationError("invalid_proxy_url")
    return value


def playwright_proxy_settings(proxy_url: str) -> dict:
    parts = urlsplit(proxy_url)
    host = parts.hostname or ""
    if ":" in host:
        host = f"[{host}]"
    settings = {"server": f"{parts.scheme}://{host}:{parts.port}"}
    if parts.username is not None:
        settings["username"] = unquote(parts.username)
    if parts.password is not None:
        settings["password"] = unquote(parts.password)
    return settings
