"""Local Chromium smoke test, with closed-vocabulary failure diagnostics."""

import asyncio
import logging

from playwright.async_api import async_playwright

LOGGER = logging.getLogger(__name__)


def browser_failure_reason(error):
    """Exception text is examined only in memory, never logged."""
    message = str(error).lower()
    if "executable doesn't exist" in message or "executable not found" in message:
        return "binary_missing"
    if "missing dependencies" in message or "error while loading shared libraries" in message:
        return "libraries_missing"
    if "no space left on device" in message or "shared memory" in message:
        return "shared_memory_or_disk"
    if "out of memory" in message or "cannot allocate memory" in message:
        return "memory_exhausted"
    if isinstance(error, TimeoutError) or "timeout" in type(error).__name__.lower():
        return "timeout"
    if "target page, context or browser has been closed" in message or "sigkill" in message:
        return "browser_exited"
    return "unknown"


async def probe_browser():
    stage = "driver"
    try:
        async with asyncio.timeout(45), async_playwright() as playwright:
            stage = "launch"
            browser = await playwright.chromium.launch(headless=True, timeout=30000)
            try:
                stage = "page"
                page = await browser.new_page()
                stage = "navigation"
                await page.goto("about:blank", timeout=5000)
            finally:
                await browser.close()
    except Exception as error:  # noqa: BLE001 -- never log raw browser errors
        LOGGER.warning(
            "SDO_BROWSER_PREFLIGHT stage=%s reason=%s", stage, browser_failure_reason(error)
        )
        return "browser_unavailable"
    return None


if __name__ == "__main__":
    raise SystemExit(0 if asyncio.run(probe_browser()) is None else 1)
