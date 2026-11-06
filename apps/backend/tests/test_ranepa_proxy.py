"""Regional egress settings do not expose or accept arbitrary user URLs."""

import pytest

from studgroup.ranepa_proxy import (
    ProxyConfigurationError,
    current_proxy_url,
    playwright_proxy_settings,
)


def test_direct_mode_by_default(monkeypatch):
    monkeypatch.delenv("RANEPA_PROXY_URL", raising=False)
    assert current_proxy_url() is None


def test_region_proxy_is_configured_server_side_only(monkeypatch):
    url = "http://service-user:private-password@proxy.example.org:3128"
    monkeypatch.setenv("RANEPA_PROXY_URL", url)
    assert current_proxy_url() == url
    assert playwright_proxy_settings(url) == {
        "server": "http://proxy.example.org:3128",
        "username": "service-user",
        "password": "private-password",
    }


@pytest.mark.parametrize(
    "url",
    [
        "https://proxy.example.org",
        "file:///etc/passwd",
        "http://proxy.example.org:abc",
        "http://proxy.example.org:8080/path",
        "http://proxy.example.org:8080?token=public",
    ],
)
def test_bad_proxy_url_rejected(monkeypatch, url):
    monkeypatch.setenv("RANEPA_PROXY_URL", url)
    with pytest.raises(ProxyConfigurationError):
        current_proxy_url()
