"""Isolated two-thumb CAPTCHA test page, never a request to the university.

The production solver validates the my.ranepa.ru origin. In this *test-only*
harness every browser request is intercepted and fulfilled locally; the URL
is a synthetic fixture path on that origin, not a live network destination.
Only an explicitly paid invocation may reach api.rucaptcha.com, and only
the two generated challenge PNGs are submitted.
"""

import argparse
import asyncio
import base64
import json
import os
from io import BytesIO
from pathlib import Path

import httpx
from PIL import Image, ImageDraw
from playwright.async_api import async_playwright

from studgroup import ranepa_rotate_v2
from studgroup.ranepa_rotate import solve_rotating_halves
from studgroup.ranepa_rotate_v2 import RotateV2Error, RuCaptchaV2Pair

_FIXTURE_URL = "https://my.ranepa.ru/__studgroup_local_only_rotate_fixture__"
_EXPECTED_ANGLES = (90, 180)
_SIZE = 200
_MAX_RESENDS = 3

_HTML = """<!doctype html>
<html lang="ru">
<head>
<meta charset="utf-8">
<title>StudGroup: isolated rotate fixture (NOT StormWall)</title>
<style>
  body { margin:0; background:#f0f2f5; font:16px Arial,sans-serif; color:#18212f; }
  .card { box-sizing:border-box; background:white; width:380px; margin:42px auto;
          padding:22px 30px 26px; border-radius:14px; box-shadow:0 6px 28px #0002; }
  h1 { font-size:17px; text-align:center; margin:0 0 8px; }
  .hint { text-align:center; font-size:13px; margin:0 0 20px; }
  .picture { position:relative; margin:0 auto 27px; height:200px; width:200px; }
  .img-left-block, .img-right-block { position:absolute; height:200px; width:100px;
                                     top:0; overflow:hidden; }
  .img-left-block { left:0; }
  .img-right-block { left:100px; }
  .picture img { width:200px; height:200px; max-width:none; position:absolute;
                 top:0; transform-origin:100px 100px; }
  .img-left { left:0; }
  .img-right { left:-100px; }
  .controls { width:300px; margin:0 auto; }
  input[type="range"] { appearance:none; -webkit-appearance:none; display:block;
                        width:300px; height:30px; margin:0 0 26px 0; border:0;
                        border-radius:18px; background:#d7dfe9; cursor:pointer; }
  input[type="range"]::-webkit-slider-thumb {
      -webkit-appearance:none; appearance:none; width:60px; height:30px;
      border-radius:16px; background:#3377c6; border:2px solid white;
      box-shadow:0 1px 5px #0004; cursor:grab;
  }
  input[type="range"].disable-sl { cursor:not-allowed; opacity:.55; }
  #status { text-align:center; font-weight:bold; font-size:14px; padding-top:2px; }
  .notice { text-align:center; font-size:11px; color:#64748b; margin-top:14px; }
</style>
</head>
<body>
<div class="card">
  <h1>Тестовая CAPTCHA StudGroup</h1>
  <p class="hint">Используйте ползунки, чтобы совместить половинки изображения</p>
  <div class="picture">
    <div class="img-left-block"><img class="img-left" src="LEFT_SRC" alt="left"></div>
    <div class="img-right-block"><img class="img-right" src="RIGHT_SRC" alt="right"></div>
  </div>
  <div class="controls">
    <input class="slider-top" id="myRange" type="range" min="0" max="360" step="0.5" value="0" aria-label="Левая половина">
    <input class="slider-bottom" id="myRange" type="range" min="0" max="360" step="0.5" value="0" aria-label="Правая половина">
  </div>
  <div id="status">Ожидание решения</div>
  <div class="notice">Локальная имитация. Нет соединения с РАНХиГС или StormWall.</div>
</div>
<script>
(() => {
  const expected = [EXPECTED_LEFT, EXPECTED_RIGHT];
  const inputs = Array.from(document.querySelectorAll('input[type="range"]'));
  const images = [document.querySelector('.img-left'), document.querySelector('.img-right')];
  const state = {status:"pending", values:[0,0], completed:[false,false]};
  window.fixtureState = state;
  const near = (a,b) => Math.abs((a - b + 540) % 360 - 180) <= 8;
  inputs.forEach((input,index) => {
    input.addEventListener('input', () => {
      state.values[index] = input.valueAsNumber;
      images[index].style.transform = 'rotate(' + input.valueAsNumber + 'deg)';
    });
    input.addEventListener('change', () => {
      if (state.completed[index]) return;
      state.values[index] = input.valueAsNumber;
      state.completed[index] = true;
      input.classList.add('disable-sl');
      input.disabled = true;
      if (state.completed.every(Boolean)) {
        const accepted = state.values.every((v,i) => near(v,expected[i]));
        state.status = accepted ? 'accepted' : 'rejected';
        document.querySelector('#status').textContent =
          accepted ? 'Проверка принята (локальная)' : 'Проверка отклонена (локальная)';
      }
    });
  });
})();
</script>
</body>
</html>
"""


def _png(image: Image.Image) -> bytes:
    buffer = BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def make_halves() -> tuple[bytes, bytes]:
    """Create two rotated full-circle images with a clear upright reference."""
    image = Image.new("RGBA", (_SIZE, _SIZE), (255, 255, 255, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse((4, 4, 195, 195), fill=(235, 246, 252, 255), outline=(32, 91, 141, 255), width=5)
    draw.line((100, 22, 100, 179), fill=(40, 105, 145, 255), width=5)
    draw.line((20, 100, 180, 100), fill=(40, 105, 145, 255), width=5)
    draw.polygon([(100, 34), (70, 86), (130, 86)], fill=(225, 64, 69, 255))
    draw.polygon([(100, 163), (82, 124), (118, 124)], fill=(45, 145, 188, 255))
    draw.rectangle(
        (59, 88, 141, 113), fill=(255, 255, 255, 255), outline=(35, 80, 125, 255), width=2
    )
    draw.text((73, 95), "NORTH", fill=(11, 40, 75, 255))
    draw.text((83, 14), "UP", fill=(10, 43, 91, 255))
    # Pillow's positive angle is counterclockwise. CSS rotates clockwise,
    # so these are the known clockwise corrections for the fixture.
    return tuple(
        _png(image.rotate(angle, resample=Image.Resampling.BICUBIC, expand=False))
        for angle in _EXPECTED_ANGLES
    )


def fixture_html(halves: tuple[bytes, bytes]) -> str:
    left = "data:image/png;base64," + base64.b64encode(halves[0]).decode("ascii")
    right = "data:image/png;base64," + base64.b64encode(halves[1]).decode("ascii")
    return (
        _HTML.replace("LEFT_SRC", left)
        .replace("RIGHT_SRC", right)
        .replace("EXPECTED_LEFT", str(_EXPECTED_ANGLES[0]))
        .replace("EXPECTED_RIGHT", str(_EXPECTED_ANGLES[1]))
    )


class ObservedFixtureV2Pair(RuCaptchaV2Pair):
    """Fixture-only response verification and bounded wrong-angle resubmission.

    Unlike a real StormWall CAPTCHA, the synthetic fixture has known angles.
    Only this test subclass can reject an incorrect provider angle before
    any browser slider is touched. The production solver is unchanged.
    """

    def __init__(
        self,
        api_key: str,
        *,
        client_factory=None,
        progressive_context: bool = False,
        max_resends: int = 0,
    ):
        if type(max_resends) is not int or not 0 <= max_resends <= _MAX_RESENDS:
            raise ValueError("fixture_max_resends_out_of_range")
        super().__init__(
            api_key, client_factory=client_factory, progressive_context=progressive_context
        )
        self.max_resends = max_resends
        self.resends_used = 0
        self.tasks_submitted = 0
        self.last_angles: tuple[int, int] | None = None

    async def solve_pair(
        self, halves: tuple[bytes, bytes], *, timeout_seconds: int = 110, before_second=None
    ) -> tuple[int, int]:
        self.last_angles = None
        self.resends_used = 0
        self.tasks_submitted = 0

        # Even with zero retries, reject a known-wrong synthetic answer
        # before consuming one-shot thumbs. This does not apply in production.

        if not isinstance(halves, tuple) or len(halves) != 2:
            raise RotateV2Error("image_invalid")
        left = ranepa_rotate_v2._image(halves[0])
        right = ranepa_rotate_v2._image(halves[1])
        context = ranepa_rotate_v2._context_png(left, right)
        ranepa_rotate_v2.log_captcha_stage("rotate_v2_shared_context_verified")
        angles = []

        try:
            async with asyncio.timeout(timeout_seconds):
                async with self._client_factory(timeout=20, follow_redirects=False) as http:
                    for side in (0, 1):
                        if side == 1:
                            if before_second is not None and not await before_second():
                                raise RotateV2Error("challenge_changed")
                            context = (
                                ranepa_rotate_v2._context_png(left, right, left_rotation=angles[0])
                                if self.progressive_context
                                else context
                            )
                            if self.progressive_context:
                                ranepa_rotate_v2.log_captcha_stage(
                                    "rotate_v2_progressive_context_verified"
                                )
                        while True:
                            # Every extra paid request must see the same images
                            # and native-thumb geometry before it is sent.
                            if (
                                self.tasks_submitted > 0
                                and before_second is not None
                                and not await before_second()
                            ):
                                raise RotateV2Error("challenge_changed")
                            if self.tasks_submitted >= 2 + self.max_resends:
                                raise RotateV2Error("invalid_answer")
                            self.tasks_submitted += 1
                            try:
                                angle = await self._solve_one(
                                    http,
                                    halves[side],
                                    context,
                                    side,
                                    progressive_right=(side == 1 and self.progressive_context),
                                )
                            except RotateV2Error as exc:
                                # A definitive unsolvable/invalid final provider
                                # response can be resubmitted. Transport failures,
                                # ambiguous charge state and changed challenges
                                # remain fail-closed without any new paid task.
                                if exc.stage not in {"api_unsolvable", "invalid_answer"}:
                                    raise
                                if self.resends_used >= self.max_resends:
                                    raise
                                self.resends_used += 1
                                ranepa_rotate_v2.log_captcha_stage(
                                    "fixture_worker_error_resubmission"
                                )
                                continue

                            if _aligned(angle, _EXPECTED_ANGLES[side]):
                                angles.append(angle)
                                break
                            print(
                                "RANEPA_FIXTURE_WRONG_ANGLE "
                                f"side={('left', 'right')[side]} "
                                f"returned={angle} expected={_EXPECTED_ANGLES[side]}"
                            )
                            if self.resends_used >= self.max_resends:
                                ranepa_rotate_v2.log_captcha_stage(
                                    "fixture_wrong_angle_retry_limit_reached"
                                )
                                raise RotateV2Error("invalid_answer")
                            self.resends_used += 1
                            ranepa_rotate_v2.log_captcha_stage("fixture_wrong_angle_resubmission")
        except httpx.RequestError:
            raise RotateV2Error("network_failed") from None

        result = angles[0], angles[1]
        self.last_angles = result
        return result


def _aligned(value: float, reference: float) -> bool:
    return abs((value - reference + 180) % 360 - 180) <= 8


def fixture_diagnosis(
    *, submitted: bool, angles: tuple[int, int] | None, values: list[float], status: str
) -> str:
    """Distinguish provider-answer error from incorrect browser movement."""

    if not submitted or angles is None:
        return "solver_incomplete"
    if any(
        not _aligned(actual, expected)
        for actual, expected in zip(angles, _EXPECTED_ANGLES, strict=True)
    ):
        return "provider_angle_mismatch"
    if len(values) != 2 or any(
        not _aligned(value, angle) for value, angle in zip(values, angles, strict=True)
    ):
        return "browser_drag_mismatch"
    if status != "accepted":
        return "local_verifier_rejected"
    return "accepted"


def mock_rucaptcha(
    halves: tuple[bytes, bytes],
    *,
    progressive_context: bool,
    max_resends: int = 0,
    wrong_first_side: str | None = None,
    always_wrong: bool = False,
    unsolvable_first_side: str | None = None,
):
    """Use the real v2 client with a deterministic, in-memory fake provider."""
    if wrong_first_side not in {None, "left", "right"}:
        raise ValueError("fixture_mock_wrong_side")
    if unsolvable_first_side not in {None, "left", "right"}:
        raise ValueError("fixture_mock_unsolvable_side")
    seen = {
        "created": 0,
        "answered": 0,
        "contexts": [],
        "task_sides": {},
        "side_requests": [0, 0],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host != "api.rucaptcha.com" or request.method != "POST":
            raise AssertionError("fixture_mock_unexpected_endpoint")
        data = json.loads(request.content)
        if request.url.path == "/createTask":
            index = seen["created"]
            if index >= 2 + max_resends:
                raise AssertionError("fixture_mock_task_budget_exceeded")
            task = data["task"]
            if task["type"] != "RotateTask":
                raise AssertionError("fixture_mock_wrong_task")
            body = base64.b64decode(task["body"], validate=True)
            if body not in halves:
                raise AssertionError("fixture_mock_wrong_image")
            side = halves.index(body)
            seen["contexts"].append(task["imgInstructions"])
            seen["side_requests"][side] += 1
            seen["task_sides"][1001 + index] = (
                side,
                seen["side_requests"][side],
            )
            seen["created"] += 1
            return httpx.Response(200, json={"errorId": 0, "taskId": 1001 + index})
        if request.url.path == "/getTaskResult":
            task_id = data["taskId"]
            if task_id not in seen["task_sides"]:
                raise AssertionError("fixture_mock_wrong_task_id")
            side, side_attempt = seen["task_sides"][task_id]
            seen["answered"] += 1
            if side_attempt == 1 and unsolvable_first_side == ("left", "right")[side]:
                return httpx.Response(
                    200, json={"errorId": 1, "errorCode": "ERROR_CAPTCHA_UNSOLVABLE"}
                )
            wrong = (
                always_wrong
                and (wrong_first_side is None or wrong_first_side == ("left", "right")[side])
            ) or (side_attempt == 1 and wrong_first_side == ("left", "right")[side])
            angle = (_EXPECTED_ANGLES[side] + 90) % 360 if wrong else _EXPECTED_ANGLES[side]
            return httpx.Response(
                200,
                json={
                    "errorId": 0,
                    "status": "ready",
                    "solution": {"rotate": angle},
                },
            )
        raise AssertionError("fixture_mock_unexpected_api")

    def factory(**kwargs):
        return httpx.AsyncClient(transport=httpx.MockTransport(handler), **kwargs)

    return ObservedFixtureV2Pair(
        "local-mock-only",
        client_factory=factory,
        progressive_context=progressive_context,
        max_resends=max_resends,
    ), seen


async def run_fixture(
    *,
    provider: str,
    allow_paid: bool,
    progressive_context: bool,
    screenshot: Path | None = None,
    max_resends: int = 0,
    allow_extra_paid: bool = False,
    mock_wrong_first_side: str | None = None,
    mock_always_wrong: bool = False,
    mock_unsolvable_first_side: str | None = None,
) -> bool:
    if provider not in {"mock", "rucaptcha"}:
        raise ValueError("unsupported_fixture_provider")
    if type(max_resends) is not int or not 0 <= max_resends <= _MAX_RESENDS:
        raise ValueError("fixture_max_resends_out_of_range")
    if provider == "rucaptcha":
        if not allow_paid:
            raise ValueError("paid_fixture_requires_explicit_allow_paid")
        if max_resends > 0 and not allow_extra_paid:
            raise ValueError("paid_fixture_retries_require_explicit_opt_in")
        if (
            mock_wrong_first_side is not None
            or mock_always_wrong
            or mock_unsolvable_first_side is not None
        ):
            raise ValueError("cannot_inject_mock_answers_into_paid_mode")
        key = os.getenv("CAPTCHA_API_KEY", "").strip()
        if not key:
            raise ValueError("paid_fixture_requires_CAPTCHA_API_KEY")
        solver = ObservedFixtureV2Pair(
            key, progressive_context=progressive_context, max_resends=max_resends
        )
        seen = None
    else:
        halves = make_halves()
        solver, seen = mock_rucaptcha(
            halves,
            progressive_context=progressive_context,
            max_resends=max_resends,
            wrong_first_side=mock_wrong_first_side,
            always_wrong=mock_always_wrong,
            unsolvable_first_side=mock_unsolvable_first_side,
        )
        # Prevent artificial 5+5-second waits for a synchronous local mock.
        ranepa_rotate_v2._POLL_SECONDS = 0

    halves = make_halves()
    page_html = fixture_html(halves)
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            context = await browser.new_context(
                locale="ru-RU", viewport={"width": 1024, "height": 768}
            )
            page = await context.new_page()

            async def serve_locally(route):
                # Intercept EVERY browser request. Nothing contacts the university,
                # including the top-level synthetic origin URL or any favicon.
                if route.request.url == _FIXTURE_URL:
                    await route.fulfill(status=200, content_type="text/html", body=page_html)
                else:
                    await route.abort()

            await page.route("**/*", serve_locally)
            await page.goto(_FIXTURE_URL, wait_until="domcontentloaded")
            submitted = await solve_rotating_halves(page, solver, timeout_seconds=110)
            state = await page.evaluate("window.fixtureState")
            if screenshot is not None:
                screenshot.parent.mkdir(parents=True, exist_ok=True)
                await page.screenshot(path=str(screenshot))
            accepted = submitted and state["status"] == "accepted"
            if seen is not None and (
                seen["created"] != solver.tasks_submitted
                or seen["created"] > 2 + max_resends
                or seen["answered"] != seen["created"]
                or len(seen["contexts"]) != seen["created"]
                or (
                    max_resends == 0
                    and (seen["contexts"][0] != seen["contexts"][1]) != progressive_context
                )
            ):
                raise AssertionError("fixture_mock_two_task_contract_failed")
            diagnosis = fixture_diagnosis(
                submitted=submitted,
                angles=solver.last_angles,
                values=state["values"],
                status=state["status"],
            )
            # These angles and values describe synthetic art ONLY. They are
            # safe for fixture logs and never originate from student data.
            if solver.last_angles is not None:
                print(
                    "RANEPA_FIXTURE_PROVIDER_ANGLES "
                    f"left={solver.last_angles[0]} right={solver.last_angles[1]}"
                )
            print(
                f"RANEPA_FIXTURE_SLIDER_VALUES left={state['values'][0]} right={state['values'][1]}"
            )
            print(
                "RANEPA_FIXTURE_EXPECTED_ANGLES "
                f"left={_EXPECTED_ANGLES[0]} right={_EXPECTED_ANGLES[1]}"
            )
            print(f"RANEPA_FIXTURE_RESENDS_USED {solver.resends_used}")
            print(f"RANEPA_FIXTURE_TASKS_SUBMITTED {solver.tasks_submitted}")
            print("RANEPA_FIXTURE_DIAGNOSIS " + diagnosis)
            print("RANEPA_FIXTURE_PROVIDER " + provider)
            print("RANEPA_FIXTURE_RESULT " + ("accepted" if accepted else "rejected"))
            return accepted
        finally:
            await browser.close()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Isolated synthetic RotateTask test; never RANEPA traffic"
    )
    parser.add_argument("--provider", choices=("mock", "rucaptcha"), default="mock")
    parser.add_argument("--allow-paid", action="store_true")
    parser.add_argument("--progressive-context", action="store_true")
    parser.add_argument("--max-resends", type=int, default=0, choices=range(4))
    parser.add_argument("--allow-extra-paid", action="store_true")
    parser.add_argument("--mock-wrong-first-side", choices=("left", "right"))
    parser.add_argument("--mock-always-wrong", action="store_true")
    parser.add_argument("--mock-unsolvable-first-side", choices=("left", "right"))
    parser.add_argument("--screenshot", type=Path)
    args = parser.parse_args()
    try:
        accepted = asyncio.run(
            run_fixture(
                provider=args.provider,
                allow_paid=args.allow_paid,
                progressive_context=args.progressive_context,
                screenshot=args.screenshot,
                max_resends=args.max_resends,
                allow_extra_paid=args.allow_extra_paid,
                mock_wrong_first_side=args.mock_wrong_first_side,
                mock_always_wrong=args.mock_always_wrong,
                mock_unsolvable_first_side=args.mock_unsolvable_first_side,
            )
        )
    except Exception:  # noqa: BLE001
        # Never log HTTP errors or exceptions in paid mode: they may expose
        # provider metadata or secrets. A fixed error code is sufficient.
        print("RANEPA_FIXTURE_RESULT internal_failure")
        return 1
    return 0 if accepted else 1


if __name__ == "__main__":
    raise SystemExit(main())
