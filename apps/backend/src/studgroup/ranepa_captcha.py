"""Optional RuCaptcha coordinate solver for RANEPA's two-slider picture challenge.

Only the clipped challenge is transmitted; never send the page, cookies or credentials.
This is a best-effort pilot adapter, not a guarantee of passing any given challenge.
"""

import asyncio
import base64
import logging
import math
import re
from dataclasses import dataclass
from io import BytesIO
from urllib.parse import urlsplit

from PIL import Image, ImageDraw
from twocaptcha import AsyncTwoCaptcha

from studgroup.ranepa_rotate_upload import TypedRotateApiClient
from studgroup.ranepa_rotate_v2 import RuCaptchaV2Pair

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
        return self.claim_many(1)

    def claim_many(self, count: int) -> bool:
        """Atomically reserve all tasks for one CAPTCHA before contacting API."""
        if count not in (1, 2) or self.remaining < count:
            return False
        self.remaining -= count
        return True


def configured_solver(settings):
    """Return None for the disabled or unconfigured feature; keep the API key private."""
    if not settings.ranepa_captcha_enabled:
        return None
    api_key = settings.captcha_api_key.get_secret_value().strip()
    if not api_key:
        return None
    if settings.ranepa_captcha_v2_pair_enabled:
        return RuCaptchaV2Pair(
            api_key,
            progressive_context=settings.ranepa_captcha_v2_progressive_context_enabled,
        )
    solver = AsyncTwoCaptcha(
        apiKey=api_key,
        server="rucaptcha.com",
        defaultTimeout=settings.ranepa_captcha_timeout_seconds,
        pollingInterval=10,
    )
    # Only the approved two-image rotate pilot uses this solver. Replace the
    # SDK's generic octet-stream filename="upload" parts with explicit PNG
    # file_1/file_2 metadata; SDK handles task creation and result polling.
    solver.api_client = TypedRotateApiClient(post_url="rucaptcha.com")
    return solver


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


def _capture_grid_matches_clip(image: bytes, clip: dict) -> bool:
    """Check the screenshot pixel grid against Playwright CSS clip dimensions.

    The free diagnostic uses this without marking the image or contacting the
    provider. A mismatch can indicate device-pixel scaling; never infer the
    correct coordinate transform from an unchecked screenshot.
    """
    try:
        with Image.open(BytesIO(image)) as screenshot:
            return (
                screenshot.format == "PNG"
                and abs(screenshot.width - clip["width"]) <= 2
                and abs(screenshot.height - clip["height"]) <= 2
            )
    except (OSError, ValueError, TypeError, KeyError):
        return False


def _annotate_slider_tracks(image: bytes, boxes: list[dict], clip: dict) -> bytes:
    """Outline only the two slider tracks in the provider image, without resizing.

    Coordinates from RuCaptcha must refer to exactly the same pixel positions as
    Playwright's screenshot clip. This only changes the copied PNG in memory;
    the browser DOM and one-shot slider state are never touched.
    """
    with Image.open(BytesIO(image)) as original:
        if original.format != "PNG":
            raise ValueError("unsupported_capture_format")
        if abs(original.width - clip["width"]) > 2 or abs(original.height - clip["height"]) > 2:
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


async def _read_verified_handle_centers(
    page, boxes: list[dict], *, only_index: int | None = None
) -> list[tuple[float, float]] | None:
    """Read the starting centers of CSS-styled *native* range thumbs.

    The observed StormWall widget has two native inputs, not separate
    handle elements. Chromium's getComputedStyle(input, pseudo) does NOT
    expose the thumb width reliably; read explicit CSSOM width/height
    declarations instead. If the relevant thumb CSS is inaccessible or
    ambiguous, return None before the provider is called or a mouse press.
    """
    try:
        inputs = page.locator('input[type="range"]')
        if len(boxes) != 2 or await inputs.count() != 2:
            return None
        if only_index is not None and only_index not in (0, 1):
            return None
        centers = []
        for index in (0, 1) if only_index is None else (only_index,):
            box = boxes[index]
            sample = await inputs.nth(index).evaluate(
                """(el) => {
                    if (!el.isConnected || el.type !== 'range'
                        || el.disabled || el.classList.contains('disable-sl')) return null;
                    const rect = el.getBoundingClientRect();
                    const style = getComputedStyle(el);
                    if (style.direction !== 'ltr'
                        || style.writingMode !== 'horizontal-tb'
                        || style.transform !== 'none'
                        || style.pointerEvents === 'none'
                        || style.appearance !== 'none'
                        || style.visibility !== 'visible') return null;
                    const minimum = Number(el.min);
                    const maximum = Number(el.max);
                    const value = el.valueAsNumber;
                    if (![minimum, maximum, value].every(Number.isFinite)
                        || maximum <= minimum || value !== minimum) return null;

                    // Only explicit numeric pixel dimensions from matching,
                    // accessible ::-webkit-slider-thumb CSS rules are valid.
                    // getComputedStyle(el, '::-webkit-slider-thumb') can return
                    // the *input's* width, so never use that as thumb geometry.
                    const matches = [];
                    const scan = (rules) => {
                        for (const rule of rules) {
                            if (rule.cssRules) scan(rule.cssRules);
                            if (!rule.selectorText || !rule.style) continue;
                            for (const selector of rule.selectorText.split(',')) {
                                const m = selector.trim().match(/^(.*)::-webkit-slider-thumb$/);
                                if (!m) continue;
                                try {
                                    if (el.matches(m[1].trim())) {
                                        if (rule.style.width && rule.style.height) {
                                            matches.push([rule.style.width, rule.style.height]);
                                        }
                                    }
                                } catch (_) {
                                    // Unsupported or compound pseudo selector.
                                }
                            }
                        }
                    };
                    for (const sheet of el.ownerDocument.styleSheets) {
                        try { scan(sheet.cssRules); } catch (_) {
                            // Cross-origin or otherwise inaccessible CSS.
                            return null;
                        }
                    }
                    const unique = [...new Set(matches.map(pair => pair.join('|')))];
                    if (unique.length !== 1) return null;
                    const parsePx = (s) => /^\\d+(?:\\.\\d+)?px$/.test(s)
                        ? Number(s.slice(0, -2)) : NaN;
                    const [thumbWidth, thumbHeight] = unique[0].split('|').map(parsePx);
                    if (!Number.isFinite(thumbWidth) || !Number.isFinite(thumbHeight)
                        || thumbWidth < 16 || thumbWidth > 85
                        || thumbHeight < 16 || thumbHeight > 70
                        || thumbWidth >= rect.width / 2
                        || Math.abs(rect.height - 30) > 5) return null;
                    const x = rect.left + thumbWidth / 2;
                    const y = rect.top + rect.height / 2;
                    const hit = el.ownerDocument.elementFromPoint(x, y);
                    if (hit !== el) return null;
                    return {
                        x, y,
                        box: {x: rect.x, y: rect.y,
                              width: rect.width, height: rect.height}
                    };
                }""",
                timeout=1500,
            )
            if not isinstance(sample, dict) or not isinstance(sample.get("box"), dict):
                return None
            if any(
                not math.isfinite(float(sample["box"][key]))
                or abs(float(sample["box"][key]) - float(box[key])) > 2
                for key in ("x", "y", "width", "height")
            ):
                return None
            x, y = float(sample["x"]), float(sample["y"])
            if (
                not (math.isfinite(x) and math.isfinite(y))
                or not box["x"] <= x <= box["x"] + box["width"]
                or abs(y - (box["y"] + box["height"] / 2)) > 2
            ):
                return None
            centers.append((x, y))
        return centers
    except Exception:  # noqa: BLE001
        # The CAPTCHA may navigate while CSS is read. Never guess its thumb.
        return None


def _slider_targets(
    points: tuple[tuple[int, int], tuple[int, int]],
    boxes: list[dict],
    clip: dict,
    start_centers: list[tuple[float, float]],
) -> list[tuple[float, float, float]] | None:
    """Validate both positions and their row order before the first mouse action."""
    if len(start_centers) != 2:
        return None
    targets = []
    for index, ((px, py), box, start) in enumerate(
        zip(points, boxes, start_centers, strict=True), start=1
    ):
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
        start_x, start_y = start
        if (
            not (math.isfinite(start_x) and math.isfinite(start_y))
            or not box["x"] <= start_x <= box["x"] + box["width"]
            or abs(start_y - cy) > 20
        ):
            log_captcha_stage(f"start_{index}_unverified")
            return None
        # A native thumb's center travels between half a thumb width from
        # either track edge. Reject unreachable targets before the first press.
        half_thumb = start_x - box["x"]
        if x < start_x - 2 or x > box["x"] + box["width"] - half_thumb + 2:
            log_captcha_stage(f"target_{index}_outside_thumb_travel")
            return None
        if abs(x - start_x) < 8:
            log_captcha_stage(f"target_{index}_zero_distance")
            return None
        targets.append((start_x, x, start_y))
    return targets


def _range_state_stage(snapshots: list[dict]) -> str:
    """Classify *live DOM range values*, never infer thumb pixel locations.

    The result intentionally has no numeric payload. Even when both values
    are at their minimum, CSS styling may place thumbs somewhere other than
    the adapter's currently hard-coded start coordinate.
    """
    unavailable = "diagnostic_range_state_unavailable"
    if len(snapshots) != 2:
        return unavailable
    at_min = []
    for row in snapshots:
        if (
            not isinstance(row, dict)
            or row.get("type") != "range"
            or row.get("disabled") is not False
        ):
            return unavailable
        try:
            minimum = float(row["min"])
            maximum = float(row["max"])
            current = float(row["value"])
        except (KeyError, TypeError, ValueError):
            return unavailable
        if not all(math.isfinite(number) for number in (minimum, maximum, current)):
            return unavailable
        if maximum <= minimum or not minimum <= current <= maximum:
            return unavailable
        at_min.append(math.isclose(current, minimum, rel_tol=0, abs_tol=1e-9))
    return "diagnostic_range_state_at_min" if all(at_min) else "diagnostic_range_state_not_at_min"


async def _inspect_range_state_offline(page) -> str:
    """Read only two range-input properties, with no input events or mouse use."""
    try:
        inputs = page.locator('input[type="range"]')
        if await inputs.count() != 2:
            return "diagnostic_range_state_unavailable"
        snapshots = [
            await inputs.nth(index).evaluate(
                "(el) => ({type: el.type, min: el.min, max: el.max, "
                "value: el.value, disabled: el.disabled})",
                timeout=1500,
            )
            for index in range(2)
        ]
        return _range_state_stage(snapshots)
    except Exception:  # noqa: BLE001
        # Challenge may disappear during diagnostics; never echo DOM/exception.
        return "diagnostic_range_state_unavailable"


def _range_geometry_stage(samples: list[dict], boxes: list[dict]) -> str:
    """Classify read-only DOM geometry; never claim to locate a slider thumb.

    Browser-reported input rectangles and CSS indicate whether the legacy
    bounding boxes are consistent, but cannot expose a reliable pixel
    position for a native or custom range thumb.
    """
    unavailable = "diagnostic_geometry_unavailable"
    if len(samples) != 2 or len(boxes) != 2:
        return unavailable
    custom = False
    for sample, box in zip(samples, boxes, strict=True):
        if not isinstance(sample, dict) or not isinstance(sample.get("rect"), dict):
            return unavailable
        try:
            if any(
                abs(float(sample["rect"][key]) - float(box[key])) > 2
                for key in ("x", "y", "width", "height")
            ):
                return "diagnostic_geometry_rect_mismatch"
            if not all(
                math.isfinite(float(sample["rect"][key])) and math.isfinite(float(box[key]))
                for key in ("x", "y", "width", "height")
            ):
                return unavailable
        except (KeyError, TypeError, ValueError, OverflowError):
            return unavailable
        if not all(
            isinstance(sample.get(key), str)
            for key in ("direction", "writingMode", "transform", "pointerEvents", "appearance")
        ):
            return unavailable
        if (
            sample["direction"] != "ltr"
            or sample["writingMode"] != "horizontal-tb"
            or sample["transform"] != "none"
            or sample["pointerEvents"] == "none"
        ):
            return "diagnostic_geometry_nonstandard"
        custom = custom or sample["appearance"] == "none"
    return "diagnostic_geometry_custom_range" if custom else "diagnostic_geometry_rect_consistent"


async def _inspect_range_geometry_offline(page, boxes: list[dict]) -> str:
    """Read input rectangles/CSS only; do not emit underlying values or styles."""
    try:
        inputs = page.locator('input[type="range"]')
        if await inputs.count() != 2:
            return "diagnostic_geometry_unavailable"
        samples = [
            await inputs.nth(index).evaluate(
                """(el) => {
                    const rect = el.getBoundingClientRect();
                    const style = getComputedStyle(el);
                    return {
                        rect: {x: rect.x, y: rect.y, width: rect.width, height: rect.height},
                        direction: style.direction,
                        writingMode: style.writingMode,
                        transform: style.transform,
                        pointerEvents: style.pointerEvents,
                        appearance: style.appearance
                    };
                }""",
                timeout=1500,
            )
            for index in range(2)
        ]
        return _range_geometry_stage(samples, boxes)
    except Exception:  # noqa: BLE001
        # A challenge may change while we inspect; log only a constant stage.
        return "diagnostic_geometry_unavailable"


async def _read_live_range_values(page) -> tuple[tuple[float, float, float], ...] | None:
    """Read only live HTML range values; never log or transmit these values.

    Values are a proxy for DOM change, not proof of a physical thumb motion
    or successful server-side verification. A custom handle may not update
    native range values at all.
    """
    try:
        inputs = page.locator('input[type="range"]')
        if await inputs.count() != 2:
            return None
        result = []
        for index in range(2):
            row = await inputs.nth(index).evaluate(
                "(el) => ({type: el.type, min: el.min, max: el.max, "
                "value: el.value, disabled: el.disabled})",
                timeout=1500,
            )
            if (
                not isinstance(row, dict)
                or row.get("type") != "range"
                or row.get("disabled") is not False
            ):
                return None
            minimum, maximum, value = (
                float(row["min"]),
                float(row["max"]),
                float(row["value"]),
            )
            if (
                not all(math.isfinite(v) for v in (minimum, maximum, value))
                or maximum <= minimum
                or not minimum <= value <= maximum
            ):
                return None
            result.append((minimum, maximum, value))
        return tuple(result)
    except Exception:  # noqa: BLE001
        # This may be a navigation or a disappearing challenge. Do not leak
        # Playwright exception strings, DOM, values, URLs, or session state.
        return None


async def _read_one_shot_range_state(page, index: int) -> dict | None:
    """Observe one range and its picture motion without any pointer events."""
    if index not in (0, 1):
        return None
    try:
        controls = page.locator('input[type="range"]')
        if await controls.count() != 2:
            return None
        data = await controls.nth(index).evaluate(
            """(el) => {
                const images = ['.img-left', '.img-right'].map(selector => {
                    const img = el.ownerDocument.querySelector(selector);
                    return img ? img.style.transform : null;
                });
                return {
                    type: el.type,
                    value: el.value,
                    disabled: el.disabled,
                    lockedClass: el.classList.contains('disable-sl'),
                    imageRotations: images
                };
            }""",
            timeout=1500,
        )
        if not isinstance(data, dict) or data.get("type") != "range":
            return None
        value = float(data["value"])
        if not math.isfinite(value):
            return None
        disabled = data.get("disabled")
        locked_class = data.get("lockedClass")
        if not isinstance(disabled, bool) or not isinstance(locked_class, bool):
            return None
        rotations = data.get("imageRotations")
        if not (
            isinstance(rotations, list)
            and len(rotations) == 2
            and all(v is None or isinstance(v, str) for v in rotations)
        ):
            rotations = [None, None]
        return {
            "value": value,
            "locked": disabled or locked_class,
            "rotations": tuple(rotations),
        }
    except Exception:  # noqa: BLE001
        return None


def _one_shot_motion_stage(index: int, before: dict | None, during: dict | None) -> str:
    """Report evidence of motion *before mouse-up*; never expose angles."""
    prefix = f"drag_{index + 1}_live"
    if before is None or during is None:
        return f"{prefix}_unavailable"
    if not math.isclose(before["value"], during["value"], abs_tol=1e-9):
        return f"{prefix}_value_changed"
    if before["rotations"] != during["rotations"]:
        return f"{prefix}_image_changed"
    return f"{prefix}_unchanged"


def _one_shot_release_stage(index: int, after: dict | None) -> str:
    """Distinguish a consumed disabled control from one still enabled."""
    prefix = f"drag_{index + 1}_release"
    if after is None:
        return f"{prefix}_unavailable"
    return f"{prefix}_locked" if after["locked"] else f"{prefix}_enabled"


def _post_drag_value_stage(
    before: tuple[tuple[float, float, float], ...] | None,
    after: tuple[tuple[float, float, float], ...] | None,
) -> str:
    """Classify two range values without asserting CAPTCHA success."""
    if before is None or after is None or len(before) != 2 or len(after) != 2:
        return "drag_values_unavailable"
    if any(
        (a_min, a_max) != (b_min, b_max)
        for (a_min, a_max, _), (b_min, b_max, _) in zip(before, after, strict=True)
    ):
        return "drag_values_unavailable"
    changed = sum(
        not math.isclose(previous[2], current[2], rel_tol=0, abs_tol=1e-9)
        for previous, current in zip(before, after, strict=True)
    )
    if changed == 2:
        return "drag_values_both_changed"
    if changed == 1:
        return "drag_values_one_changed"
    return "drag_values_unchanged"


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
            # Validate the pixel grid offline without annotating the PNG.
            if _capture_grid_matches_clip(image, clip):
                log_captcha_stage("diagnostic_capture_grid_verified")
            else:
                log_captcha_stage("diagnostic_capture_grid_mismatch")
            log_captcha_stage(await _inspect_range_state_offline(page))
            log_captcha_stage(await _inspect_range_geometry_offline(page, boxes))
            handle_centers = await _read_verified_handle_centers(page, boxes)
            log_captcha_stage(
                "diagnostic_native_thumb_css_verified"
                if handle_centers is not None
                else "diagnostic_native_thumb_css_unavailable"
            )
            log_captcha_stage("diagnostic_complete_no_provider_call")
            return False
        # A single click on a wrong track position can lock this challenge.
        # Never spend on a provider or press a button without verified native thumb CSS.
        start_centers = await _read_verified_handle_centers(page, boxes)
        if start_centers is None:
            log_captcha_stage("thumb_css_unverified_no_provider_no_motion")
            return False
        log_captcha_stage("thumb_css_centers_verified_read_only")
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
        # Provider latency can invalidate earlier geometry. Re-read without
        # any interaction and reject a changed handle before the first click.
        fresh_centers = await _read_verified_handle_centers(page, boxes)
        if fresh_centers is None or any(
            abs(a - c) > 2 or abs(b - d) > 2
            for (a, b), (c, d) in zip(start_centers, fresh_centers, strict=True)
        ):
            log_captcha_stage("thumb_changed_before_motion_no_click")
            return False
        targets = _slider_targets(points, boxes, clip, fresh_centers)
        if targets is None:
            log_captcha_stage("coordinates_outside_controls")
            return False
        log_captcha_stage("coordinates_validated")
        before_values = await _read_live_range_values(page)
        phase = "drag"
        for index, (start_x, end_x, y) in enumerate(targets):
            # Guard the second one-shot press as well; the first drag may cause
            # the challenge to reset or change before it is safe to continue.
            current = await _read_verified_handle_centers(page, boxes, only_index=index)
            if current is None or abs(current[0][0] - start_x) > 2 or abs(current[0][1] - y) > 2:
                log_captcha_stage("thumb_changed_before_press_no_click")
                return False
            await page.mouse.move(start_x, y)
            # Hovering can change a one-shot control. Recheck without pressing.
            hovered = await _read_verified_handle_centers(page, boxes, only_index=index)
            if hovered is None or abs(hovered[0][0] - start_x) > 2 or abs(hovered[0][1] - y) > 2:
                log_captcha_stage("thumb_changed_on_hover_no_click")
                return False
            before_drag = await _read_one_shot_range_state(page, index)
            await page.mouse.down()
            try:
                await page.mouse.move(end_x, y, steps=10)
                # Read while held: StormWall can reset the value on mouse-up.
                during_drag = await _read_one_shot_range_state(page, index)
                log_captcha_stage(_one_shot_motion_stage(index, before_drag, during_drag))
            finally:
                await page.mouse.up()
            released = await _read_one_shot_range_state(page, index)
            log_captcha_stage(_one_shot_release_stage(index, released))
        await page.wait_for_timeout(800)
        log_captcha_stage("drag_submitted")
        log_captcha_stage(
            _post_drag_value_stage(before_values, await _read_live_range_values(page))
        )
        return True
    except TimeoutError:
        # Do not report provider errors verbatim: SDK exceptions may embed keys.
        log_captcha_stage("provider_timeout")
        return False
    except Exception:  # noqa: BLE001
        # Only the bounded stage label is logged, never provider exception text.
        log_captcha_stage(f"{phase}_error")
        return False
