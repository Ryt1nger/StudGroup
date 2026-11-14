"""Offline regression tests for opt-in progressive v2 CAPTCHA context.

The first half is only rotated in memory. No live browser, task, or mouse is used.
"""

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


def _asset(side: str) -> bytes:
    image = Image.new("RGBA", (200, 200), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    if side == "left":
        # An asymmetric dot reveals clockwise versus counterclockwise rotation.
        draw.rectangle((25, 115, 35, 125), fill=(245, 10, 10, 255))
    else:
        draw.rectangle((125, 80, 135, 100), fill=(10, 40, 240, 255))
    output = BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def _is_red(pixel: tuple[int, int, int]) -> bool:
    return pixel[0] > 150 and pixel[1] < 120 and pixel[2] < 120


def _context_pixel(image: Image.Image, x: int, y: int) -> tuple[int, int, int]:
    # The composite circle lives at (7, 30) and is 135px wide.
    return image.getpixel((7 + round(x * 135 / 200), 30 + round(y * 135 / 200)))


def test_progressive_image_turns_left_clockwise_before_clipping_but_keeps_references():
    left_bytes, right_bytes = _asset("left"), _asset("right")
    left = ranepa_rotate_v2._image(left_bytes)
    right = ranepa_rotate_v2._image(right_bytes)
    original = ranepa_rotate_v2._context_png(left, right)
    progressive = ranepa_rotate_v2._context_png(left, right, left_rotation=90)
    assert original != progressive
    assert len(progressive) <= 100 * 1024

    with Image.open(BytesIO(original)) as png:
        before = png.convert("RGB")
    with Image.open(BytesIO(progressive)) as png:
        after = png.convert("RGB")

    # An original point at (30, 120) becomes (80, 30) under CSS rotate(+90).
    assert _is_red(_context_pixel(before, 30, 120))
    assert not _is_red(_context_pixel(after, 30, 120))
    assert not _is_red(_context_pixel(before, 80, 30))
    assert _is_red(_context_pixel(after, 80, 30))

    # No changes to the unrotated references or the right half in the composite.
    assert before.crop((160, 30, 448, 165)).tobytes() == after.crop(
        (160, 30, 448, 165)
    ).tobytes()
    assert before.crop((75, 30, 142, 165)).tobytes() == after.crop(
        (75, 30, 142, 165)
    ).tobytes()
    assert left.tobytes() == ranepa_rotate_v2._image(left_bytes).tobytes()
    assert right.tobytes() == ranepa_rotate_v2._image(right_bytes).tobytes()


@pytest.mark.parametrize("bad_angle", [True, 0, 360, -360, 361, -361, 0.5, "90"])
def test_progressive_context_rejects_invalid_or_unusable_first_angles(bad_angle):
    left = ranepa_rotate_v2._image(_asset("left"))
    right = ranepa_rotate_v2._image(_asset("right"))
    with pytest.raises(RotateV2Error) as caught:
        ranepa_rotate_v2._context_png(left, right, left_rotation=bad_angle)
    assert caught.value.stage == "context_invalid"


def _client(handler, *, progressive_context=True) -> RuCaptchaV2Pair:
    transport = httpx.MockTransport(handler)

    def factory(**kwargs):
        return httpx.AsyncClient(transport=transport, **kwargs)

    return RuCaptchaV2Pair(
        "secret-never-log", client_factory=factory, progressive_context=progressive_context
    )


def test_progressive_second_request_has_updated_context_and_original_right_body(
    monkeypatch, caplog
):
    async def scenario():
        monkeypatch.setattr(ranepa_rotate_v2, "_POLL_SECONDS", 0)
        left, right = _asset("left"), _asset("right")
        events = []
        contexts = []
        comments = []

        def handler(request):
            assert request.method == "POST"
            assert request.url.host == "api.rucaptcha.com"
            payload = json.loads(request.read())
            if request.url.path == "/createTask":
                n = len(contexts) + 1
                assert n in (1, 2)
                events.append(f"create-{n}")
                task = payload["task"]
                assert task["type"] == "RotateTask"
                assert task["angle"] == 5
                assert base64.b64decode(task["body"], validate=True) == (
                    left if n == 1 else right
                )
                contexts.append(base64.b64decode(task["imgInstructions"], validate=True))
                comments.append(task["comment"])
                return httpx.Response(200, json={"errorId": 0, "taskId": 900 + n})
            assert request.url.path == "/getTaskResult"
            task_id = payload["taskId"]
            events.append(f"poll-{task_id}")
            return httpx.Response(
                200,
                json={
                    "errorId": 0,
                    "status": "ready",
                    "solution": {"rotate": 90 if task_id == 901 else -120},
                },
            )

        async def before_second():
            events.append("check-before-second")
            assert events == ["create-1", "poll-901", "check-before-second"]
            return True

        with caplog.at_level(logging.WARNING):
            result = await _client(handler).solve_pair(
                (left, right), before_second=before_second
            )
        assert result == (90, -120)
        assert events == [
            "create-1",
            "poll-901",
            "check-before-second",
            "create-2",
            "poll-902",
        ]
        assert contexts[0] != contexts[1]
        original_left = ranepa_rotate_v2._image(left)
        original_right = ranepa_rotate_v2._image(right)
        assert contexts[0] == ranepa_rotate_v2._context_png(original_left, original_right)
        assert contexts[1] == ranepa_rotate_v2._context_png(
            original_left, original_right, left_rotation=90
        )
        assert "LEFT" in comments[0]
        assert "LEFT VIRTUALLY ALIGNED" in comments[1]
        assert "RIGHT" in comments[1]
        assert "rotate_v2_progressive_context_verified" in caplog.text
        assert "secret-never-log" not in caplog.text

    asyncio.run(scenario())


def test_changed_challenge_between_tasks_never_creates_second_paid_task(monkeypatch):
    async def scenario():
        monkeypatch.setattr(ranepa_rotate_v2, "_POLL_SECONDS", 0)
        paths = []

        def handler(request):
            paths.append(request.url.path)
            if request.url.path == "/createTask":
                return httpx.Response(200, json={"errorId": 0, "taskId": 987})
            return httpx.Response(
                200, json={"errorId": 0, "status": "ready", "solution": {"rotate": 90}}
            )

        async def changed():
            return False

        with pytest.raises(RotateV2Error) as caught:
            await _client(handler).solve_pair(
                (_asset("left"), _asset("right")), before_second=changed
            )
        assert caught.value.stage == "challenge_changed"
        assert paths == ["/createTask", "/getTaskResult"]

    asyncio.run(scenario())


@pytest.mark.parametrize("bad_answer", [0, 360, -360, "90", True, 361])
def test_invalid_first_answer_never_uses_second_billable_request(monkeypatch, bad_answer):
    async def scenario():
        monkeypatch.setattr(ranepa_rotate_v2, "_POLL_SECONDS", 0)
        paths = []

        def handler(request):
            paths.append(request.url.path)
            if request.url.path == "/createTask":
                return httpx.Response(200, json={"errorId": 0, "taskId": 222})
            return httpx.Response(
                200,
                json={"errorId": 0, "status": "ready", "solution": {"rotate": bad_answer}},
            )

        with pytest.raises(RotateV2Error) as caught:
            await _client(handler).solve_pair((_asset("left"), _asset("right")))
        assert caught.value.stage == "invalid_answer"
        assert paths == ["/createTask", "/getTaskResult"]

    asyncio.run(scenario())


def test_legacy_shared_context_mode_is_unchanged(monkeypatch):
    async def scenario():
        monkeypatch.setattr(ranepa_rotate_v2, "_POLL_SECONDS", 0)
        contexts = []

        def handler(request):
            payload = json.loads(request.read())
            if request.url.path == "/createTask":
                contexts.append(base64.b64decode(payload["task"]["imgInstructions"]))
                return httpx.Response(200, json={"errorId": 0, "taskId": len(contexts)})
            return httpx.Response(
                200, json={"errorId": 0, "status": "ready", "solution": {"rotate": 90}}
            )

        result = await _client(handler, progressive_context=False).solve_pair(
            (_asset("left"), _asset("right"))
        )
        assert result == (90, 90)
        assert len(contexts) == 2
        assert contexts[0] == contexts[1]

    asyncio.run(scenario())
