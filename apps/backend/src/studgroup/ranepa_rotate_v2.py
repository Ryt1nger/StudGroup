"""Two single-image RuCaptcha v2 RotateTask jobs with shared visual context.

One task rotates the left image, the next rotates the right. The default uses
the same unsolved context for both. The optional progressive mode virtually
rotates the left source image by the first answer before constructing the
second instruction image, without altering either task body or the browser.
No page screenshot, DOM, login, cookies or credentials are sent.
The second potentially billable task is created only after the first succeeds.
No automatic retries, no recording images, no raw provider payload in logs.
"""

import asyncio
import base64
import logging
from io import BytesIO

import httpx
from PIL import Image, ImageDraw

LOGGER = logging.getLogger(__name__)


def log_captcha_stage(stage: str) -> None:
    """Log only fixed stage names, never payloads or credentials."""
    LOGGER.warning("RANEPA_CAPTCHA_STAGE %s", stage)


_API_BASE = "https://api.rucaptcha.com"
_MAX_BODY = 600 * 1024
_MAX_INSTRUCTIONS = 100 * 1024
_POLL_SECONDS = 5

_COMMENTS = (
    (
        "Rotate ONLY the LEFT half in the main image so the two halves make one upright "
        "picture. The instruction image shows the current UNSOLVED combined view, "
        "LEFT and RIGHT references. Return the clockwise angle for LEFT only."
    ),
    (
        "Rotate ONLY the RIGHT half in the main image so the two halves make one upright "
        "picture. The instruction image shows the same current UNSOLVED combined view, "
        "LEFT and RIGHT references. Return the clockwise angle for RIGHT only."
    ),
)
_PROGRESSIVE_RIGHT_COMMENT = (
    "Rotate ONLY the RIGHT half in the main image. The instruction image shows "
    "LEFT VIRTUALLY ALIGNED using the first answer; RIGHT is still unsolved. "
    "Match the right half to the left and return the clockwise angle for RIGHT only."
)
_ERRORS = {
    "ERROR_ZERO_BALANCE": "zero_balance",
    "ERROR_CAPTCHA_UNSOLVABLE": "unsolvable",
    "ERROR_IMAGE_TYPE_NOT_SUPPORTED": "image_rejected",
    "ERROR_TASK_NOT_SUPPORTED": "task_unsupported",
    "ERROR_NO_SLOT_AVAILABLE": "no_worker_slot",
    "ERROR_KEY_DOES_NOT_EXIST": "key_rejected",
    "ERROR_IP_BLOCKED": "ip_blocked",
}

_SAFE_STAGES = frozenset(
    {
        "image_invalid",
        "context_invalid",
        "context_too_large",
        "challenge_changed",
        "http_failed",
        "invalid_response",
        "invalid_task_id",
        "invalid_answer",
        "network_failed",
        "api_rejected",
        *(f"api_{label}" for label in _ERRORS.values()),
    }
)


class RotateV2Error(Exception):
    """A fixed safe diagnostic stage, never an untrusted API message."""

    def __init__(self, stage: str):
        self.stage = stage if stage in _SAFE_STAGES else "invalid_response"
        super().__init__(self.stage)


def _image(png: bytes) -> Image.Image:
    """Parse a single original half, enforcing documented RotateTask limits."""
    if not isinstance(png, bytes) or not 100 <= len(png) <= _MAX_BODY:
        raise RotateV2Error("image_invalid")
    if not png.startswith(b"\x89PNG\r\n\x1a\n"):
        raise RotateV2Error("image_invalid")
    try:
        with Image.open(BytesIO(png)) as source:
            if (
                source.format != "PNG"
                or not 100 <= source.width <= 600
                or not 100 <= source.height <= 600
                or source.width != source.height
            ):
                raise RotateV2Error("image_invalid")
            image = source.convert("RGBA")
            if image.getchannel("A").getextrema()[1] == 0:
                raise RotateV2Error("image_invalid")
            return image
    except (OSError, ValueError, Image.DecompressionBombError):
        raise RotateV2Error("image_invalid") from None


def _context_png(
    left: Image.Image, right: Image.Image, *, left_rotation: int | None = None
) -> bytes:
    """Build a context image from original assets, optionally aligning LEFT.

    CSS rotate(+degrees) is clockwise; Pillow rotate(-degrees) matches it.
    Rotate the *uncropped* original image about its centre, then apply the
    same left-half clipping as the page. Keep original references unchanged.
    This is an estimate from the first answer, NOT a verified solved image.
    """
    if left.size != right.size:
        raise RotateV2Error("context_invalid")
    if left_rotation is not None and (
        type(left_rotation) is not int
        or not -360 <= left_rotation <= 360
        or left_rotation % 360 == 0
    ):
        raise RotateV2Error("context_invalid")
    size = left.width
    rotated_left = (
        left.rotate(-left_rotation, resample=Image.Resampling.BICUBIC, expand=False)
        if left_rotation is not None
        else left
    )
    circle = Image.new("RGBA", (size, size), (255, 255, 255, 0))
    circle.paste(rotated_left.crop((0, 0, size // 2, size)), (0, 0))
    circle.paste(right.crop((size // 2, 0, size, size)), (size // 2, 0))

    canvas = Image.new("RGB", (456, 195), "white")
    draw = ImageDraw.Draw(canvas)
    slots = (
        ("LEFT ALIGNED (EST.)" if left_rotation is not None else "CURRENT UNSOLVED", circle),
        ("LEFT ORIGINAL", left),
        ("RIGHT ORIGINAL", right),
    )
    for x, (label, artwork) in zip((7, 160, 313), slots, strict=True):
        draw.text((x, 7), label, fill="black")
        thumbnail = artwork.resize((135, 135), Image.Resampling.LANCZOS)
        bg = Image.new("RGBA", (135, 135), (238, 241, 245, 255))
        bg.alpha_composite(thumbnail)
        canvas.paste(bg.convert("RGB"), (x, 30))
    caption = (
        "LEFT estimated from first answer; RIGHT unsolved."
        if left_rotation is not None
        else "Reference only: both halves need separate rotation."
    )
    draw.text((7, 173), caption, fill="black")

    # Paletted PNG provides legible instructions while respecting the
    # provider's stricter 100 KiB imgInstructions limit.
    for colors in (128, 64, 32):
        output = BytesIO()
        canvas.convert("P", palette=Image.Palette.ADAPTIVE, colors=colors).save(
            output, format="PNG", optimize=True
        )
        result = output.getvalue()
        if 100 <= len(result) <= _MAX_INSTRUCTIONS:
            return result
    raise RotateV2Error("context_too_large")


def _safe_json(response: httpx.Response) -> dict:
    if response.status_code != 200:
        raise RotateV2Error("http_failed")
    try:
        value = response.json()
    except ValueError:
        raise RotateV2Error("invalid_response") from None
    if not isinstance(value, dict) or type(value.get("errorId")) is not int:
        raise RotateV2Error("invalid_response")
    if value["errorId"] != 0:
        code = value.get("errorCode")
        reason = _ERRORS.get(code, "rejected") if isinstance(code, str) else "rejected"
        raise RotateV2Error(f"api_{reason}")
    return value


class RuCaptchaV2Pair:
    """Two sequential, bounded v2 jobs; never submit a second after failure."""

    max_tasks = 2

    def __init__(self, api_key: str, *, client_factory=None, progressive_context: bool = False):
        self._api_key = api_key
        self._client_factory = client_factory or httpx.AsyncClient
        self.progressive_context = progressive_context

    async def _solve_one(
        self,
        http: httpx.AsyncClient,
        body: bytes,
        context: bytes,
        side: int,
        *,
        progressive_right: bool = False,
    ) -> int:
        name = ("left", "right")[side]
        payload = {
            "clientKey": self._api_key,
            "task": {
                "type": "RotateTask",
                "body": base64.b64encode(body).decode("ascii"),
                "imgInstructions": base64.b64encode(context).decode("ascii"),
                "comment": (_PROGRESSIVE_RIGHT_COMMENT if progressive_right else _COMMENTS[side]),
                "angle": 5,
            },
            "languagePool": "en",
        }
        log_captcha_stage(f"rotate_v2_{name}_request_started")
        response = _safe_json(await http.post(f"{_API_BASE}/createTask", json=payload))
        task_id = response.get("taskId")
        if type(task_id) is not int or not 0 < task_id < 2**63:
            raise RotateV2Error("invalid_task_id")
        log_captcha_stage(f"rotate_v2_{name}_task_created")

        while True:
            # Provider documentation requires a 5-10 second polling interval.
            await asyncio.sleep(_POLL_SECONDS)
            answer = _safe_json(
                await http.post(
                    f"{_API_BASE}/getTaskResult",
                    json={"clientKey": self._api_key, "taskId": task_id},
                )
            )
            status = answer.get("status")
            if status == "processing":
                continue
            if status != "ready" or not isinstance(answer.get("solution"), dict):
                raise RotateV2Error("invalid_answer")
            angle = answer["solution"].get("rotate")
            if type(angle) is not int or not -360 <= angle <= 360 or angle % 360 == 0:
                raise RotateV2Error("invalid_answer")
            log_captcha_stage(f"rotate_v2_{name}_answer_received")
            return angle

    async def solve_pair(
        self,
        halves: tuple[bytes, bytes],
        *,
        timeout_seconds: int = 110,
        before_second=None,
    ) -> tuple[int, int]:
        if not isinstance(halves, tuple) or len(halves) != 2:
            raise RotateV2Error("image_invalid")
        left, right = (_image(halves[0]), _image(halves[1]))
        context = _context_png(left, right)
        log_captcha_stage("rotate_v2_shared_context_verified")
        try:
            async with asyncio.timeout(timeout_seconds):
                async with self._client_factory(timeout=20, follow_redirects=False) as http:
                    first = await self._solve_one(http, halves[0], context, 0)
                    # A CAPTCHA can expire while a human works. Re-check it
                    # before another billable request, without moving sliders.
                    if before_second is not None and not await before_second():
                        raise RotateV2Error("challenge_changed")
                    second_context = (
                        _context_png(left, right, left_rotation=first)
                        if self.progressive_context
                        else context
                    )
                    if self.progressive_context:
                        log_captcha_stage("rotate_v2_progressive_context_verified")
                    second = await self._solve_one(
                        http,
                        halves[1],
                        second_context,
                        1,
                        progressive_right=self.progressive_context,
                    )
                    return first, second
        except httpx.RequestError:
            raise RotateV2Error("network_failed") from None
