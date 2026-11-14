"""Guarded, single-task multi-image RotateCaptcha pilot for RANEPA LK.

The existing Coordinates method asks humans to click image points; those
points are not drag distances. This module sends ONLY the two challenge
image assets to RuCaptcha's multi-image rotatecaptcha API (one paid task)
and interprets exactly two returned angles. No account page capture,
cookies or credentials are sent.

A returned angle is not proof of server-side CAPTCHA acceptance.
"""

import asyncio
import base64
import binascii
import math
import re
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlsplit

from PIL import Image
from twocaptcha.exceptions.solver import ApiException, NetworkException, ValidationException

from studgroup.ranepa_captcha import (
    _one_shot_motion_stage,
    _one_shot_release_stage,
    _read_one_shot_range_state,
    _read_verified_handle_centers,
    _slider_boxes,
    log_captcha_stage,
)

# The v1 RuCaptcha API returns OK|angle1|angle2 (the SDK strips OK|).
# Angle signs are meaningful: negative means counterclockwise.
_ROTATE_PAIR = re.compile(r"(-?\d{1,3})\|(-?\d{1,3})")
_IMAGE_DATA = re.compile(r"data:image/(?:webp|png|jpeg);base64,([A-Za-z0-9+/=]+)")
# The provider limits textinstructions to 140 characters. Use short ASCII
# text as a defensive choice against byte-length checks in older API routes.
_HINT = "Rotate both half-images upright. Two angles: left half first, right half second."


# Only map fixed, documented RuCaptcha status codes. Never log arbitrary
# exception text: an API/HTTP exception may include headers or request data.
_SAFE_API_ERRORS = {
    "ERROR_BAD_PARAMETERS": "bad_parameters",
    "ERROR_NO_SUCH_METHOD": "unsupported_method",
    "ERROR_ROTATECAPTCHA": "missing_rotate_file",
    "ERROR_UPLOAD_ROTATECAPTCHA": "upload_rotate_failed",
    "ERROR_UPLOAD": "upload_failed",
    "ERROR_WRONG_TEXTINSTRUCTIONS": "bad_instructions",
    "ERROR_ZERO_CAPTCHA_FILESIZE": "empty_image",
    "ERROR_TOO_BIG_CAPTCHA_FILESIZE": "image_too_large",
    "ERROR_WRONG_FILE_EXTENSION": "bad_image_extension",
    "ERROR_WRONG_ROTATECAPTCHA_EXTENSION": "bad_rotate_extension",
    "ERROR_IMAGE_TYPE_NOT_SUPPORTED": "unsupported_image",
    "ERROR_ZERO_BALANCE": "zero_balance",
    "ERROR_NO_SLOT_AVAILABLE": "no_worker_slot",
    "ERROR_KEY_DOES_NOT_EXIST": "key_not_found",
    "ERROR_WRONG_USER_KEY": "key_format",
    "ERROR_IP_NOT_ALLOWED": "ip_not_allowed",
    "ERROR_CAPTCHA_UNSOLVABLE": "unsolvable",
}


def _provider_failure_stage(exc: Exception) -> str:
    """Return a safe fixed stage for a provider failure; never expose its text."""
    if isinstance(exc, ApiException):
        # Check whole-word tokens only. A provider message is not trusted.
        message = str(exc)
        for code, label in _SAFE_API_ERRORS.items():
            if re.search(rf"(?<![A-Z0-9_]){code}(?![A-Z0-9_])", message):
                return f"rotate_provider_api_{label}"
        return "rotate_provider_api_other"
    if isinstance(exc, ValidationException):
        return "rotate_provider_sdk_validation"
    if isinstance(exc, NetworkException):
        return "rotate_provider_network"
    if isinstance(exc, TypeError):
        return "rotate_provider_sdk_type_error"
    if isinstance(exc, (OSError, ValueError)):
        return "rotate_provider_local_io"
    return "rotate_provider_unknown_error"


def decode_rotation_angles(answer) -> tuple[int, int] | None:
    """Strictly accept exactly two signed angles; do not guess a missing one."""
    if not isinstance(answer, dict) or not isinstance(answer.get("code"), str):
        return None
    match = _ROTATE_PAIR.fullmatch(answer["code"])
    if match is None:
        return None
    angles = tuple(int(x) for x in match.groups())
    if any(abs(x) > 360 for x in angles):
        return None
    return angles


def _original_half_png(data_url: str) -> bytes | None:
    """Decode a *data URI* image only, with strict format, dimensions and size."""
    if not isinstance(data_url, str) or len(data_url) > 900_000:
        return None
    match = _IMAGE_DATA.fullmatch(data_url)
    if match is None:
        return None
    try:
        payload = base64.b64decode(match.group(1), validate=True)
        if len(payload) > 600 * 1024:
            return None
        with Image.open(BytesIO(payload)) as original:
            if original.format not in {"WEBP", "PNG", "JPEG"}:
                return None
            if not (100 <= original.width <= 600 and 100 <= original.height <= 600):
                return None
            if original.width != original.height:
                return None
            output = BytesIO()
            # Keep transparent half-image masks; do not rotate or flatten the
            # original images before asking a worker for their orientation.
            original.convert("RGBA").save(output, format="PNG", optimize=True)
        png = output.getvalue()
        return png if len(png) <= 600 * 1024 else None
    except (OSError, ValueError, TypeError, binascii.Error):
        return None


async def _read_original_halves(page) -> tuple[bytes, bytes] | None:
    """Read only the two challenge image data URIs from the LK DOM."""
    try:
        data = await page.evaluate(
            """() => {
                const top = document.querySelector('input.slider-top[type="range"]');
                const bottom = document.querySelector('input.slider-bottom[type="range"]');
                const left = document.querySelector('.img-left-block img.img-left');
                const right = document.querySelector('.img-right-block img.img-right');
                if (!top || !bottom || !left || !right) return null;
                if (top.disabled || bottom.disabled
                    || top.classList.contains('disable-sl')
                    || bottom.classList.contains('disable-sl')) return null;
                if (top.min !== '0' || bottom.min !== '0'
                    || top.max !== '360' || bottom.max !== '360'
                    || top.step !== '0.5' || bottom.step !== '0.5'
                    || top.valueAsNumber !== 0 || bottom.valueAsNumber !== 0) return null;
                if (!left.complete || !right.complete
                    || !left.naturalWidth || !right.naturalWidth) return null;
                return [left.getAttribute('src'), right.getAttribute('src')];
            }"""
        )
        if not isinstance(data, list) or len(data) != 2:
            return None
        halves = tuple(_original_half_png(x) for x in data)
        if any(x is None for x in halves):
            return None
        return halves
    except Exception:  # noqa: BLE001
        return None


def _angle_targets(
    angles: tuple[int, int],
    boxes: list[dict],
    centers: list[tuple[float, float]],
) -> list[tuple[float, float, float]] | None:
    """Turn two rotation angles into endpoints along measured thumb travel."""
    if len(boxes) != 2 or len(centers) != 2:
        return None
    targets = []
    for angle, box, (start_x, start_y) in zip(angles, boxes, centers, strict=True):
        width = float(box["width"])
        half_thumb = start_x - float(box["x"])
        travel = width - 2 * half_thumb
        # Only one drag is allowed. Zero/small distances are not attempted.
        if not (
            math.isfinite(travel)
            and 16 <= half_thumb * 2 <= 85
            and travel > 100
            and math.isfinite(start_y)
        ):
            return None
        clockwise = angle % 360
        end_x = start_x + travel * (clockwise / 360)
        if not (8 <= end_x - start_x <= travel + 0.5):
            return None
        targets.append((start_x, end_x, start_y))
    return targets


async def solve_rotating_halves(page, client, *, timeout_seconds: int = 110) -> bool:
    """Single paid multi-image RotateCaptcha, then at most one drag per input.

    A True result says only that both mouse drags were submitted. The caller
    must still confirm the LK schedule and group identity.
    """
    phase = "inspect"
    try:
        if urlsplit(page.url).hostname != "my.ranepa.ru":
            log_captcha_stage("rotate_unsupported_origin")
            return False
        boxes = await _slider_boxes(page)
        if boxes is None:
            log_captcha_stage("rotate_controls_unavailable")
            return False
        centers = await _read_verified_handle_centers(page, boxes)
        if centers is None:
            log_captcha_stage("rotate_thumb_css_unverified_no_provider")
            return False
        halves = await _read_original_halves(page)
        if halves is None:
            log_captcha_stage("rotate_half_images_unavailable_no_provider")
            return False
        log_captcha_stage("rotate_two_halves_validated_read_only")
        # The Python SDK accepts a dict of two local image paths and sends
        # file_1/file_2 in ONE rotatecaptcha task. Neither file is preserved.
        phase = "provider"
        with TemporaryDirectory(prefix="ranepa-rotate-") as directory:
            first = Path(directory) / "left.png"
            second = Path(directory) / "right.png"
            first.write_bytes(halves[0])
            second.write_bytes(halves[1])
            log_captcha_stage("rotate_provider_request_started")
            async with asyncio.timeout(timeout_seconds):
                response = await client.rotate(
                    {"left": str(first), "right": str(second)},
                    angle=5,
                    hintText=_HINT,
                )
        log_captcha_stage("rotate_provider_answer_received")
        angles = decode_rotation_angles(response)
        if angles is None:
            log_captcha_stage("rotate_provider_answer_invalid")
            return False
        fresh = await _read_verified_handle_centers(page, boxes)
        if fresh is None or any(
            abs(x1 - x2) > 2 or abs(y1 - y2) > 2
            for (x1, y1), (x2, y2) in zip(centers, fresh, strict=True)
        ):
            log_captcha_stage("rotate_thumb_changed_before_motion_no_click")
            return False
        targets = _angle_targets(angles, boxes, fresh)
        if targets is None:
            log_captcha_stage("rotate_target_invalid_no_click")
            return False
        log_captcha_stage("rotate_angles_mapped_without_click")
        phase = "drag"
        for index, (start_x, end_x, y) in enumerate(targets):
            current = await _read_verified_handle_centers(page, boxes, only_index=index)
            if current is None or abs(current[0][0] - start_x) > 2 or abs(current[0][1] - y) > 2:
                log_captcha_stage("rotate_pending_thumb_unverified_no_click")
                return False
            await page.mouse.move(start_x, y)
            hovered = await _read_verified_handle_centers(page, boxes, only_index=index)
            if hovered is None or abs(hovered[0][0] - start_x) > 2 or abs(hovered[0][1] - y) > 2:
                log_captcha_stage("rotate_thumb_changed_on_hover_no_click")
                return False
            before = await _read_one_shot_range_state(page, index)
            await page.mouse.down()
            try:
                await page.mouse.move(end_x, y, steps=10)
                during = await _read_one_shot_range_state(page, index)
                motion_stage = _one_shot_motion_stage(index, before, during)
                log_captcha_stage(motion_stage)
            finally:
                await page.mouse.up()
            after = await _read_one_shot_range_state(page, index)
            log_captcha_stage(_one_shot_release_stage(index, after))
            # If the first one-shot control was consumed with no observed
            # movement, don't waste the second control by clicking it too.
            if index == 0 and motion_stage == "drag_1_live_unchanged":
                log_captcha_stage("rotate_first_drag_no_motion_abort")
                return False
        log_captcha_stage("rotate_drags_submitted_verification_pending")
        return True
    except TimeoutError:
        log_captcha_stage(
            "rotate_provider_timeout" if phase == "provider" else "rotate_inspection_timeout"
        )
    except Exception as exc:  # noqa: BLE001
        # Neither API exception messages nor provider payloads may enter logs.
        if phase == "provider":
            log_captcha_stage(_provider_failure_stage(exc))
        log_captcha_stage(f"rotate_{phase}_failed")
    return False
