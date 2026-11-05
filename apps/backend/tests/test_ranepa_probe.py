"""Read-only RANEPA probe: real-source identity must precede any calendar writes."""

import asyncio
from uuid import UUID

import httpx
import pytest

from studgroup.ranepa_probe import SourceAccessError, probe_public

UUID_GROUP = UUID("92637eb0-817b-11f1-9fac-00620bc81861")
CODE = "Э-ОБ-385-04-26-02"


async def request_with_body(text, *, status=200, content_type="text/html"):
    def response(request):
        assert request.url.host == "my.ranepa.ru"
        assert request.url.params.get("f") == str(UUID_GROUP)
        assert request.url.params.get("by") == "group"
        return httpx.Response(
            status, text=text, headers={"content-type": content_type}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as client:
        return await probe_public(UUID_GROUP, CODE, client=client)


def test_probe_accepts_matching_group_only():
    body = """
        <html><body><h2>Э-ОБ-385-04-26-02</h2>
        <section>11/5/2026 Четверг</section>
        <div>09 : 00 - 10 : 20</div>
        <div>Информатика</div></body></html>
    """
    result = asyncio.run(request_with_body(body))
    assert result.group_code == CODE
    assert result.pair_time_ranges == 1
    assert result.has_lesson_candidates
    assert len(result.page_sha256) == 64


def test_probe_rejects_foreign_group_not_silent_empty_schedule():
    with pytest.raises(SourceAccessError, match="group_identity_not_confirmed"):
        asyncio.run(request_with_body("<html>Чужая группа</html>"))


def test_probe_rejects_captcha_and_non_html():
    with pytest.raises(SourceAccessError, match="captcha_required"):
        asyncio.run(request_with_body("<html>Э-ОБ-385-04-26-02 Captcha</html>"))
    with pytest.raises(SourceAccessError, match="unexpected_response_type"):
        asyncio.run(request_with_body('{"lessons":[]}', content_type="application/json"))


def test_probe_rejects_http_error():
    with pytest.raises(SourceAccessError, match="authentication_required"):
        asyncio.run(request_with_body("Login", status=403))
    with pytest.raises(SourceAccessError, match="source_http_error"):
        asyncio.run(request_with_body("Server error", status=503))
