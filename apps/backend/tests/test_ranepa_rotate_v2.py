"""No-network tests of sequential RuCaptcha API v2 RotateTask with shared context."""

import asyncio
import base64
import json
import logging
from io import BytesIO

import httpx
import pytest
from PIL import Image, ImageDraw

from studgroup import ranepa_rotate_v2
from studgroup.ranepa_rotate_v2 import RotateV2Error, RuCaptchaV2Pair


def _half(side: str) -> bytes:
    image = Image.new("RGBA", (200, 200), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    if side == "left":
        draw.rectangle((0, 0, 99, 199), fill=(245, 10, 10, 255))
    else:
        draw.rectangle((100, 0, 199, 199), fill=(10, 40, 240, 255))
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def _client(respond):
    transport = httpx.MockTransport(respond)

    def factory(**kwargs):
        return httpx.AsyncClient(transport=transport, **kwargs)

    return RuCaptchaV2Pair("secret-test-api-key", client_factory=factory)


def test_two_sequential_v2_tasks_receive_same_unsolved_context_and_correct_halves(
    monkeypatch, caplog
):
    async def scenario():
        monkeypatch.setattr(ranepa_rotate_v2, "_POLL_SECONDS", 0)
        halves = (_half("left"), _half("right"))
        expected_context = ranepa_rotate_v2._context_png(
            ranepa_rotate_v2._image(halves[0]),
            ranepa_rotate_v2._image(halves[1]),
        )
        assert len(expected_context) <= 100 * 1024
        with Image.open(BytesIO(expected_context)) as reference:
            full = reference.convert("RGB")
            red = full.getpixel((42, 103))
            blue = full.getpixel((107, 103))
            assert red[0] > 150 and red[2] < 120
            assert blue[2] > 150 and blue[0] < 120

        history = []
        seen_context = []

        def respond(request):
            assert request.method == "POST"
            assert request.url.host == "api.rucaptcha.com"
            assert request.headers["content-type"] == "application/json"
            payload = json.loads(request.read())
            assert payload["clientKey"] == "secret-test-api-key"
            if request.url.path == "/createTask":
                task = payload["task"]
                number = 1 + sum(x == "create" for x in history)
                history.append("create")
                assert number in (1, 2)
                assert set(task) == {
                    "type",
                    "body",
                    "imgInstructions",
                    "comment",
                    "angle",
                }
                assert task["type"] == "RotateTask"
                assert task["angle"] == 5
                assert payload["languagePool"] == "en"
                assert base64.b64decode(task["body"], validate=True) == halves[number - 1]
                context = base64.b64decode(task["imgInstructions"], validate=True)
                assert context == expected_context
                seen_context.append(context)
                assert ("LEFT" if number == 1 else "RIGHT") in task["comment"]
                assert all(item not in task for item in ("cookie", "url", "password"))
                return httpx.Response(200, json={"errorId": 0, "taskId": 1000 + number})
            assert request.url.path == "/getTaskResult"
            task_id = payload["taskId"]
            history.append(f"poll-{task_id}")
            if task_id == 1001:
                return httpx.Response(
                    200,
                    json={"errorId": 0, "status": "ready", "solution": {"rotate": 90}},
                )
            assert task_id == 1002
            return httpx.Response(
                200,
                json={"errorId": 0, "status": "ready", "solution": {"rotate": 240}},
            )

        with caplog.at_level(logging.WARNING):
            angles = await _client(respond).solve_pair(halves)
        assert angles == (90, 240)
        assert history == ["create", "poll-1001", "create", "poll-1002"]
        assert len(seen_context) == 2
        assert "secret-test-api-key" not in caplog.text
        assert "rotate_v2_shared_context_verified" in caplog.text
        assert "rotate_v2_left_answer_received" in caplog.text
        assert "rotate_v2_right_answer_received" in caplog.text

    asyncio.run(scenario())


def test_v2_first_failure_never_creates_second_paid_task(monkeypatch, caplog):
    async def scenario():
        monkeypatch.setattr(ranepa_rotate_v2, "_POLL_SECONDS", 0)
        seen = []

        def respond(request):
            seen.append(request.url.path)
            return httpx.Response(200, json={"errorId": 10, "errorCode": "ERROR_ZERO_BALANCE"})

        with caplog.at_level(logging.WARNING), pytest.raises(RotateV2Error) as caught:
            await _client(respond).solve_pair((_half("left"), _half("right")))
        assert caught.value.stage == "api_zero_balance"
        assert seen == ["/createTask"]
        assert "secret-test-api-key" not in caplog.text
        assert "rotate_v2_right_request_started" not in caplog.text

    asyncio.run(scenario())


def test_v2_invalid_first_answer_blocks_second_paid_task(monkeypatch):
    async def scenario():
        monkeypatch.setattr(ranepa_rotate_v2, "_POLL_SECONDS", 0)
        seen = []

        def respond(request):
            seen.append(request.url.path)
            if request.url.path == "/createTask":
                return httpx.Response(200, json={"errorId": 0, "taskId": 123})
            return httpx.Response(
                200,
                json={"errorId": 0, "status": "ready", "solution": {"rotate": "wrong"}},
            )

        with pytest.raises(RotateV2Error) as caught:
            await _client(respond).solve_pair((_half("left"), _half("right")))
        assert caught.value.stage == "invalid_answer"
        assert seen == ["/createTask", "/getTaskResult"]

    asyncio.run(scenario())


def test_v2_bad_input_never_contacts_provider():
    async def scenario():
        calls = []

        def respond(request):
            calls.append(request.url.path)
            return httpx.Response(200, json={"errorId": 0, "taskId": 1})

        client = _client(respond)
        with pytest.raises(RotateV2Error) as caught:
            await client.solve_pair((b"not an image", _half("right")))
        assert caught.value.stage == "image_invalid"
        assert calls == []

    asyncio.run(scenario())


def test_v2_untrusted_api_text_is_reduced_to_fixed_safe_stage():
    with pytest.raises(RotateV2Error) as caught:
        ranepa_rotate_v2._safe_json(
            httpx.Response(
                200,
                json={
                    "errorId": 1,
                    "errorCode": "ERROR_SERVER_RESPONSE",
                    "errorDescription": "secret=never-print-me",
                },
            )
        )
    assert caught.value.stage == "api_rejected"
    assert "never-print-me" not in str(caught.value)
