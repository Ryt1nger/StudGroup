"""No-network tests for typed RuCaptcha multipart image uploads."""

import asyncio
import logging
from io import BytesIO

import httpx
import pytest
from PIL import Image
from twocaptcha import AsyncTwoCaptcha
from twocaptcha.exceptions.api import ApiException, NetworkException
from twocaptcha.exceptions.solver import ValidationException

from studgroup.ranepa_rotate import _HINT, _provider_failure_stage
from studgroup.ranepa_rotate_upload import TypedRotateApiClient


def _png(*, transparent=False):
    output = BytesIO()
    color = (0, 0, 0, 0) if transparent else (55, 100, 190, 255)
    Image.new("RGBA", (200, 200), color).save(output, "PNG")
    return output.getvalue()


def _transport(handler):
    mock = httpx.MockTransport(handler)

    def factory(**kwargs):
        return httpx.AsyncClient(transport=mock, **kwargs)

    return TypedRotateApiClient(post_url="rucaptcha.com", client_factory=factory)


def test_actual_sdk_uploads_two_pngs_with_real_filenames_and_mime_offline(
    tmp_path, monkeypatch, caplog
):
    """Capture final HTTPX multipart body, not only SDK arguments."""
    async def scenario():
        left = tmp_path / "left.png"
        right = tmp_path / "right.png"
        left_bytes = _png()
        right_bytes = _png(transparent=False)
        assert 100 <= len(left_bytes) <= 600 * 1024
        left.write_bytes(left_bytes)
        right.write_bytes(right_bytes)
        calls = []

        def receive(request):
            calls.append(request)
            assert str(request.url) == "https://rucaptcha.com/in.php"
            assert request.method == "POST"
            assert "multipart/form-data" in request.headers["content-type"]
            payload = request.read()
            assert payload.count(b'name="file_1"; filename="left.png"') == 1
            assert payload.count(b'name="file_2"; filename="right.png"') == 1
            assert payload.count(b"Content-Type: image/png") == 2
            assert b'filename="upload"' not in payload
            assert b"application/octet-stream" not in payload
            assert payload.count(b'name="method"') == 1
            assert b"rotatecaptcha" in payload
            assert b'name="angle"' in payload
            assert b'name="textinstructions"' in payload
            assert b'name="key"' in payload
            assert left_bytes in payload
            assert right_bytes in payload
            return httpx.Response(200, text="OK|123456")

        solver = AsyncTwoCaptcha(apiKey="0" * 32, server="rucaptcha.com")
        solver.api_client = _transport(receive)

        async def pretend_polled(task_id, timeout, interval):
            assert task_id == "123456"
            return "90|-180"

        monkeypatch.setattr(solver, "wait_result", pretend_polled)
        with caplog.at_level(logging.WARNING):
            reply = await solver.rotate(
                {"left": str(left), "right": str(right)},
                angle=5,
                hintText=_HINT,
            )
        assert reply == {"captchaId": "123456", "code": "90|-180"}
        assert len(calls) == 1
        assert "rotate_upload_png_parts_verified" in caplog.text
        assert "rotate_upload_http_response_received" in caplog.text
        assert "00000000000000000000000000000000" not in caplog.text

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "bad",
    ["missing", "tiny", "invalid", "transparent", "large", "wrong_extension"],
)
def test_invalid_png_is_rejected_before_http_and_before_billing(tmp_path, bad):
    async def scenario():
        left = tmp_path / ("left.jpg" if bad == "wrong_extension" else "left.png")
        right = tmp_path / "right.png"
        good = _png()
        right.write_bytes(good)
        if bad == "tiny":
            left.write_bytes(b"a" * 12)
        elif bad == "invalid":
            left.write_bytes(b"\\x89PNG\\r\\n\\x1a\\n" + b"a" * 130)
        elif bad == "transparent":
            left.write_bytes(_png(transparent=True))
        elif bad == "large":
            left.write_bytes(good + b"a" * (601 * 1024))
        elif bad == "wrong_extension":
            left.write_bytes(good)
        calls = []

        def receive(request):
            calls.append(request)
            return httpx.Response(200, text="OK|123456")

        client = _transport(receive)
        with pytest.raises(ValidationException):
            await client.in_(
                files={"file_1": str(left), "file_2": str(right)},
                method="rotatecaptcha",
                key="0" * 32,
                angle=5,
            )
        assert calls == []

    asyncio.run(scenario())


def test_wrong_method_or_provider_host_fails_before_network(tmp_path):
    async def scenario():
        image = tmp_path / "left.png"
        image.write_bytes(_png())
        calls = []

        def receive(request):
            calls.append(request)
            return httpx.Response(200, text="OK|123456")

        other = TypedRotateApiClient(
            post_url="example.com",
            client_factory=lambda **kwargs: httpx.AsyncClient(
                transport=httpx.MockTransport(receive),
                **kwargs,
            ),
        )
        for adapter, method in ((_transport(receive), "normal"), (other, "rotatecaptcha")):
            with pytest.raises(ValidationException):
                await adapter.in_(
                    files={"file_1": str(image), "file_2": str(image)},
                    method=method,
                    key="0" * 32,
                )
        assert calls == []

    asyncio.run(scenario())


def test_known_provider_error_from_actual_http_transport_is_classified(tmp_path, caplog):
    async def scenario():
        image = tmp_path / "left.png"
        image.write_bytes(_png())
        calls = []

        def receive(request):
            calls.append(request)
            return httpx.Response(200, text="ERROR_ZERO_CAPTCHA_FILESIZE")

        adapter = _transport(receive)
        with caplog.at_level(logging.WARNING):
            with pytest.raises(ApiException) as caught:
                await adapter.in_(
                    files={"file_1": str(image), "file_2": str(image)},
                    method="rotatecaptcha",
                    key="0" * 32,
                )
        assert _provider_failure_stage(caught.value) == "rotate_provider_api_empty_image"
        assert len(calls) == 1
        assert "rotate_upload_png_parts_verified" in caplog.text
        assert "00000000000000000000000000000000" not in caplog.text

    asyncio.run(scenario())


def test_unexpected_http_status_never_retries_or_discloses_payload(tmp_path):
    async def scenario():
        image = tmp_path / "left.png"
        image.write_bytes(_png())
        calls = []

        def receive(request):
            calls.append(request)
            return httpx.Response(503, text="secret=do-not-log")

        adapter = _transport(receive)
        with pytest.raises(NetworkException) as caught:
            await adapter.in_(
                files={"file_1": str(image), "file_2": str(image)},
                method="rotatecaptcha",
                key="0" * 32,
            )
        assert _provider_failure_stage(caught.value) == "rotate_provider_network"
        assert len(calls) == 1
        assert "do-not-log" not in str(caught.value)

    asyncio.run(scenario())
