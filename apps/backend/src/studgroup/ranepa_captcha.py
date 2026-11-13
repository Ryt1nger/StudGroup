"""Optional RuCaptcha coordinate solver for RANEPA's two-slider picture challenge.

Only the clipped challenge is transmitted; never send the page, cookies or credentials.
This is a best-effort pilot adapter, not a guarantee of passing any given challenge.
"""

import asyncio
import base64
import logging
import re
from dataclasses import dataclass
from io import BytesIO
from urllib.parse import urlsplit

from PIL import Image, ImageDraw
from twocaptcha import AsyncTwoCaptcha

LOGGER = logging.getLogger(__name__)


def log_captcha_stage(stage: str) -> None:
    """Emit only a fixed diagnostic stage, never the challenge or provider payload."""
    LOGGER.warning("RANEPA_CAPTCHA_STAGE %s", stage)


# The legacy 2captcha-python SDK returns the provider's "coordinate:x=..." string.
# Accept the historic plural spelling as well, but always require exactly two points.
_COORDINATES = re.compile(r"coordinates?:x=(\d+),y=(\d+);x=(\d+),y=(\d+)")
# Coordinates workers click on a screenshot. They do not return drag commands.
# Keep the prompt short enough for the provider's instruction limit.
HINT = (
    "Совместите половинки круга. Кликните 2 точки внутри ВЫДЕЛЕННЫХ рамками полос: "
    "сначала верхней, потом нижней. Не нажимайте на круг."
)


@dataclass
class CaptchaBudget:
    """Upper bound for paid solver tasks in one serial worker process/run."""

    remaining: int

    def claim(self) -> bool:
        if self.remaining <= 0:
            return False
        self.remaining -= 1
        return True


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
        log_captcha_stage("instruction_not_visible")
        return None
    # StormWall has been observed using duplicate id="myRange" controls.
    # Prefer genuine range inputs; accept two named controls if the live widget
    # exposes them differently, but never guess when the count is ambiguous.
    inputs = page.locator('input[type="range"]')
    if await inputs.count() != 2:
        inputs = page.locator('[id="myRange"]')
        if await inputs.count() != 2:
            log_captcha_stage("control_count_mismatch")
            return None
    boxes = [await inputs.nth(index).bounding_box() for index in range(2)]
    if any(box is None or box["width"] < 150 or box["height"] > 85 for box in boxes):
        log_captcha_stage("control_bounds_invalid")
        return None
    if boxes[0]["y"] >= boxes[1]["y"] or boxes[1]["y"] - boxes[0]["y"] > 140:
        log_captcha_stage("control_order_invalid")
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


def _annotate_slider_tracks(image: bytes, boxes: list[dict], clip: dict) -> bytes:
    """Outline only the two slider tracks in the provider image, without resizing.

    Coordinates from RuCaptcha must refer to exactly the same pixel positions as
    Playwright's screenshot clip. This only changes the copied PNG in memory;
    the browser DOM and one-shot slider state are never touched.
    """
    with Image.open(BytesIO(image)) as original:
        if original.format != "PNG":
            raise ValueError("unsupported_capture_format")
        if (
            abs(original.width - clip["width"]) > 2
            or abs(original.height - clip["height"]) > 2
        ):
            raise ValueError("capture_clip_size_mismatch")
        marked = original.convert("RGB")
    draw = ImageDraw.Draw(marked)
    for index, box in enumerate(boxes):
        x0 = round(box["x"] - clip["x"])
        x1 = round(x0 + box["width"])
        y0 = round(box["y"] - clip["y"])
        y1 = round(y0 + box["height"])
        if x0 < 3 or x1 >= marked.width - 3 or y0 < 3 or y1 >= marked.height - 3:
            raise ValueError("track_outside_capture")
        color = (0, 165, 85) if index == 0 else (0, 105, 235)
        # Outline the track from the outside. Do not cover the current handle.
        draw.rounded_rectangle(
            (x0 - 2, y0 - 2, x1 + 2, y1 + 2),
            radius=6,
            outline=color,
            width=3,
        )
        label_x = x0 - 12
        center_y = round((y0 + y1) / 2)
        if label_x >= 8:
            draw.ellipse(
                (label_x - 7, center_y - 7, label_x + 7, center_y + 7),
                fill=color,
            )
            draw.text((label_x - 3, center_y - 5), str(index + 1), fill=(255, 255, 255))
    output = BytesIO()
    marked.save(output, format="PNG", optimize=True)
    return output.getvalue()


def _slider_targets(
    points: tuple[tuple[int, int], tuple[int, int]],
    boxes: list[dict],
    clip: dict,
) -> list[tuple[float, float, float]] | None:
    """Validate both positions and their row order before the first mouse action."""
    targets = []
    for index, ((px, py), box) in enumerate(zip(points, boxes, strict=True), start=1):
        x = clip["x"] + px
        y = clip["y"] + py
        cy = box["y"] + box["height"] / 2
        # Reject rather than treating any coordinate on the puzzle image as
        # a slider position. Only fixed reason codes are logged: never values.
        if x < box["x"] + 15 or x > box["x"] + box["width"] - 15:
            log_captcha_stage(f"target_{index}_x_outside_track")
            return None
        if y < cy - 22:
            log_captcha_stage(f"target_{index}_above_track")
            return None
        if y > cy + 22:
            log_captcha_stage(f"target_{index}_below_track")
            return None
        start_x = box["x"] + 25
        targets.append((start_x, x, cy))
    return targets


async def solve_slider_captcha(page, client, *, timeout_seconds: int = 110) -> bool:
    """Attempt exactly one drag per slider after a single coordinate task.

    A None client is a strictly offline diagnostic: inspect controls and
    capture only the cropped challenge, but never contact a paid provider or
    move either slider. False means no motions were submitted. A True result
    only means motions were submitted; caller must recheck page identity.
    """
    phase = "inspect"
    try:
        # The confirmed StormWall challenge is in front of the LK host, not Moodle.
        # Never ship a screenshot from any other origin to a paid third party.
        if urlsplit(page.url).hostname != "my.ranepa.ru":
            log_captcha_stage("unsupported_origin")
            return False
        boxes = await _slider_boxes(page)
        if boxes is None:
            log_captcha_stage("controls_unavailable")
            return False
        log_captcha_stage("controls_confirmed")
        clip = _challenge_clip(boxes)
        if clip["width"] > 1000 or clip["height"] > 1000:
            log_captcha_stage("capture_dimensions_invalid")
            return False
        image = await page.screenshot(type="png", clip=clip, animations="disabled")
        if len(image) > 600 * 1024:
            log_captcha_stage("capture_size_exceeded")
            return False
        log_captcha_stage("capture_ready")
        if client is None:
            log_captcha_stage("diagnostic_complete_no_provider_call")
            return False
        try:
            marked_image = _annotate_slider_tracks(image, boxes, clip)
        except (OSError, ValueError):
            log_captcha_stage("capture_annotation_failed")
            return False
        if len(marked_image) > 600 * 1024:
            log_captcha_stage("annotated_capture_size_exceeded")
            return False
        log_captcha_stage("tracks_marked_for_provider")
        encoded = base64.b64encode(marked_image).decode("ascii")
        phase = "provider"
        log_captcha_stage("provider_request_started")
        async with asyncio.timeout(timeout_seconds):
            result = await client.coordinates(encoded, hintText=HINT, min_clicks=2, max_clicks=2)
        log_captcha_stage("provider_answer_received")
        points = decode_slider_coordinates(result)
        if points is None:
            log_captcha_stage("provider_answer_invalid")
            return False
        targets = _slider_targets(points, boxes, clip)
        if targets is None:
            log_captcha_stage("coordinates_outside_controls")
            return False
        log_captcha_stage("coordinates_validated")
        phase = "drag"
        for start_x, end_x, y in targets:
            await page.mouse.move(start_x, y)
            await page.mouse.down()
            try:
                await page.mouse.move(end_x, y, steps=10)
            finally:
                await page.mouse.up()
        await page.wait_for_timeout(800)
        log_captcha_stage("drag_submitted")
        return True
    except TimeoutError:
        # Do not report provider errors verbatim: SDK exceptions may embed keys.
        log_captcha_stage("provider_timeout")
        return False
    except Exception:  # noqa: BLE001
        # Only the bounded stage label is logged, never provider exception text.
        log_captcha_stage(f"{phase}_error")
        return False
