"""Optional RuCaptcha coordinate solver for RANEPA's two-slider picture challenge.

Only the clipped challenge is transmitted; never send the page, cookies or credentials.
This is a best-effort pilot adapter, not a guarantee of passing any given challenge.
"""

import asyncio
import base64
import logging
import re

from twocaptcha import AsyncTwoCaptcha

LOGGER = logging.getLogger(__name__)

# RuCaptcha API v1 returns "coordinates:x=123,y=456;x=234,y=567".
_COORDINATES = re.compile(r"coordinates:x=(\d+),y=(\d+);x=(\d+),y=(\d+)")
HINT = (
    "Совместите половинки круга. Отметьте 2 точки: нужную позицию "
    "ВЕРХНЕГО ползунка, затем НИЖНЕГО ползунка. По одной точке на дорожке."
)


def configured_solver(settings):
    """Return None for the disabled or unconfigured feature; keep the API key private."""
    if not settings.ranepa_captcha_enabled:
        return None
    api_key = settings.captcha_api_key.get_secret_value().strip()
    if not api_key:
        return None
    return AsyncTwoCaptcha(
        apiKey=api_key,
        server="rucaptcha.com",
        defaultTimeout=settings.ranepa_captcha_timeout_seconds,
        pollingInterval=10,
    )


def warn_if_unconfigured(settings):
    """Emit a non-sensitive warning once at worker startup, never once per account."""
    if settings.ranepa_captcha_enabled and not settings.captcha_api_key.get_secret_value().strip():
        LOGGER.warning("RANEPA auto CAPTCHA solving disabled: CAPTCHA_API_KEY is missing")


def decode_slider_coordinates(answer) -> tuple[tuple[int, int], tuple[int, int]] | None:
    """Reject ambiguous provider answers before touching any one-shot slider."""
    if not isinstance(answer, dict):
        return None
    code = answer.get("code")
    if not isinstance(code, str):
        return None
    match = _COORDINATES.fullmatch(code)
    if match is None:
        return None
    x1, y1, x2, y2 = map(int, match.groups())
    return ((x1, y1), (x2, y2))


async def _slider_boxes(page) -> list[dict] | None:
    """Fail closed unless exactly two visible, wide horizontal range inputs exist."""
    if not await page.get_by_text("Используйте ползунки", exact=False).is_visible():
        return None
    inputs = page.locator('input[type="range"]')
    if await inputs.count() != 2:
        return None
    boxes = [await inputs.nth(index).bounding_box() for index in range(2)]
    if any(
        box is None or box["width"] < 150 or box["height"] > 85
        for box in boxes
    ):
        return None
    if boxes[0]["y"] >= boxes[1]["y"] or boxes[1]["y"] - boxes[0]["y"] > 140:
        return None
    return boxes


def _challenge_clip(boxes: list[dict]) -> dict:
    """Capture only the challenge circle and controls, not the account behind the modal."""
    left = max(0, min(box["x"] for box in boxes) - 20)
    right = max(box["x"] + box["width"] for box in boxes) + 20
    top = max(0, boxes[0]["y"] - 300)
    bottom = max(box["y"] + box["height"] for box in boxes) + 45
    return {
        "x": left,
        "y": top,
        "width": right - left,
        "height": bottom - top,
    }


def _slider_targets(
    points: tuple[tuple[int, int], tuple[int, int]],
    boxes: list[dict],
    clip: dict,
) -> list[tuple[float, float, float]] | None:
    """Validate both positions and their row order before the first mouse action."""
    targets = []
    for (px, py), box in zip(points, boxes, strict=True):
        x = clip["x"] + px
        y = clip["y"] + py
        cy = box["y"] + box["height"] / 2
        # A click outside the matching slider row probably means an unusable answer.
        if (
            x < box["x"] + 15
            or x > box["x"] + box["width"] - 15
            or abs(y - cy) > 22
        ):
            return None
        start_x = box["x"] + 25
        targets.append((start_x, x, cy))
    return targets


async def solve_slider_captcha(page, client, *, timeout_seconds: int = 110) -> bool:
    """Attempt exactly one drag per slider after a single coordinate task.

    False means the challenge is unknown, the provider failed or returned an
    ambiguous solution. A True result only means the motions were submitted;
    caller MUST recheck page state and verified student identity afterwards.
    """
    try:
        boxes = await _slider_boxes(page)
        if boxes is None:
            return False
        clip = _challenge_clip(boxes)
        image = await page.screenshot(type="png", clip=clip, animations="disabled")
        encoded = base64.b64encode(image).decode("ascii")
        async with asyncio.timeout(timeout_seconds):
            result = await client.coordinates(
                encoded, hintText=HINT, min_clicks=2, max_clicks=2
            )
        points = decode_slider_coordinates(result)
        if points is None:
            return False
        targets = _slider_targets(points, boxes, clip)
        if targets is None:
            return False
        for start_x, end_x, y in targets:
            await page.mouse.move(start_x, y)
            await page.mouse.down()
            try:
                await page.mouse.move(end_x, y, steps=10)
            finally:
                await page.mouse.up()
        await page.wait_for_timeout(800)
        return True
    except Exception:  # noqa: BLE001
        # Provider, Playwright and page exceptions can contain sensitive URLs.
        LOGGER.warning("RANEPA CAPTCHA attempt unavailable or unsuccessful")
        return False
