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
from studgroup.ranepa_rotate_v2 import RuCaptchaV2Pair

_FIXTURE_URL = "https://my.ranepa.ru/__studgroup_local_only_rotate_fixture__"
_EXPECTED_ANGLES = (90, 180)
_SIZE = 200

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
    draw.rectangle((59, 88, 141, 113), fill=(255, 255, 255, 255), outline=(35, 80, 125, 255), width=2)
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


def mock_rucaptcha(halves: tuple[bytes, bytes], *, progressive_context: bool):
    """Actual RuCaptchaV2Pair client against a strictly local HTTPX fake API."""
    seen = {"created": 0, "answered": 0, "contexts": []}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host != "api.rucaptcha.com" or request.method != "POST":
            raise AssertionError("fixture_mock_unexpected_endpoint")
        data = json.loads(request.content)
        if request.url.path == "/createTask":
            index = seen["created"]
            if index >= 2:
                raise AssertionError("fixture_mock_task_budget_exceeded")
            task = data["task"]
            if task["type"] != "RotateTask":
                raise AssertionError("fixture_mock_wrong_task")
            if base64.b64decode(task["body"], validate=True) != halves[index]:
                raise AssertionError("fixture_mock_wrong_image")
            seen["contexts"].append(task["imgInstructions"])
            seen["created"] += 1
            return httpx.Response(200, json={"errorId": 0, "taskId": 1001 + index})
        if request.url.path == "/getTaskResult":
            index = data["taskId"] - 1001
            if not 0 <= index < seen["created"]:
                raise AssertionError("fixture_mock_wrong_task_id")
            seen["answered"] += 1
            return httpx.Response(
                200, json={"errorId": 0, "status": "ready",
                           "solution": {"rotate": _EXPECTED_ANGLES[index]}}
            )
        raise AssertionError("fixture_mock_unexpected_api")

    def factory(**kwargs):
        return httpx.AsyncClient(transport=httpx.MockTransport(handler), **kwargs)

    return RuCaptchaV2Pair(
        "local-mock-only",
        client_factory=factory,
        progressive_context=progressive_context,
    ), seen


async def run_fixture(*, provider: str, allow_paid: bool, progressive_context: bool,
                      screenshot: Path | None = None) -> bool:
    if provider not in {"mock", "rucaptcha"}:
        raise ValueError("unsupported_fixture_provider")
    if provider == "rucaptcha":
        if not allow_paid:
            raise ValueError("paid_fixture_requires_explicit_allow_paid")
        key = os.getenv("CAPTCHA_API_KEY", "").strip()
        if not key:
            raise ValueError("paid_fixture_requires_CAPTCHA_API_KEY")
        solver = RuCaptchaV2Pair(key, progressive_context=progressive_context)
        seen = None
    else:
        halves = make_halves()
        solver, seen = mock_rucaptcha(halves, progressive_context=progressive_context)
        # Prevent artificial 5+5-second waits for a synchronous local mock.
        ranepa_rotate_v2._POLL_SECONDS = 0

    halves = make_halves()
    page_html = fixture_html(halves)
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=True)
        try:
            context = await browser.new_context(locale="ru-RU", viewport={"width": 1024, "height": 768})
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
                seen["created"] != 2
                or seen["answered"] != 2
                or len(seen["contexts"]) != 2
                or (seen["contexts"][0] != seen["contexts"][1]) != progressive_context
            ):
                raise AssertionError("fixture_mock_two_task_contract_failed")
            print("RANEPA_FIXTURE_PROVIDER " + provider)
            print("RANEPA_FIXTURE_RESULT " + ("accepted" if accepted else "rejected"))
            return accepted
        finally:
            await browser.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Isolated synthetic RotateTask test; never RANEPA traffic")
    parser.add_argument("--provider", choices=("mock", "rucaptcha"), default="mock")
    parser.add_argument("--allow-paid", action="store_true")
    parser.add_argument("--progressive-context", action="store_true")
    parser.add_argument("--screenshot", type=Path)
    args = parser.parse_args()
    try:
        accepted = asyncio.run(run_fixture(
            provider=args.provider, allow_paid=args.allow_paid,
            progressive_context=args.progressive_context,
            screenshot=args.screenshot,
        ))
    except Exception:  # noqa: BLE001
        # Never log HTTP errors or exceptions in paid mode: they may expose
        # provider metadata or secrets. A fixed error code is sufficient.
        print("RANEPA_FIXTURE_RESULT internal_failure")
        return 1
    return 0 if accepted else 1


if __name__ == "__main__":
    raise SystemExit(main())
