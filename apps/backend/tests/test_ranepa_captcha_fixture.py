"""No browser, university traffic, credentials or paid provider in these tests."""

import asyncio
import base64
from io import BytesIO

import pytest
from PIL import Image

from studgroup import ranepa_captcha_fixture, ranepa_rotate_v2
from studgroup.ranepa_rotate import _original_half_png


def test_fixture_images_are_valid_and_html_has_native_one_shot_controls():
    halves = ranepa_captcha_fixture.make_halves()
    assert len(halves) == 2 and halves[0] != halves[1]
    for png in halves:
        with Image.open(BytesIO(png)) as image:
            assert image.format == "PNG" and image.size == (200, 200)
        assert (
            _original_half_png("data:image/png;base64," + base64.b64encode(png).decode())
            is not None
        )
    html = ranepa_captcha_fixture.fixture_html(halves)
    assert 'input class="slider-top"' in html
    assert 'input class="slider-bottom"' in html
    assert 'type="range" min="0" max="360" step="0.5" value="0"' in html
    assert "::-webkit-slider-thumb" in html
    assert "width:60px; height:30px;" in html
    assert 'src="data:image/png;base64,' in html
    assert "EXPECTED_LEFT" not in html and "EXPECTED_RIGHT" not in html
    assert "expected = [90, 180]" in html
    assert "window.fixtureState" in html


@pytest.mark.parametrize("progressive", [False, True])
def test_fixture_exercises_actual_v2_client_without_api_cost(monkeypatch, progressive):
    monkeypatch.setattr(ranepa_rotate_v2, "_POLL_SECONDS", 0)
    halves = ranepa_captcha_fixture.make_halves()
    solver, state = ranepa_captcha_fixture.mock_rucaptcha(halves, progressive_context=progressive)

    async def scenario():
        calls = 0

        async def before_second():
            nonlocal calls
            calls += 1
            return True

        angles = await solver.solve_pair(halves, before_second=before_second)
        assert angles == (90, 180)
        assert calls == 1

    asyncio.run(scenario())
    assert state["created"] == 2
    assert state["answered"] == 2
    assert len(state["contexts"]) == 2
    first, second = state["contexts"]
    for context in state["contexts"]:
        with Image.open(BytesIO(base64.b64decode(context, validate=True))) as image:
            assert image.size == (456, 195)
    assert (first != second) is progressive


def test_fixture_explicitly_rejects_paid_mode_without_opt_in(monkeypatch):
    monkeypatch.setenv("CAPTCHA_API_KEY", "fake-key")
    with pytest.raises(ValueError, match="explicit_allow_paid"):
        asyncio.run(
            ranepa_captcha_fixture.run_fixture(
                provider="rucaptcha",
                allow_paid=False,
                progressive_context=True,
            )
        )


def test_fixture_mock_api_never_sees_private_data(monkeypatch):
    monkeypatch.setattr(ranepa_rotate_v2, "_POLL_SECONDS", 0)
    halves = ranepa_captcha_fixture.make_halves()
    solver, _ = ranepa_captcha_fixture.mock_rucaptcha(halves, progressive_context=True)

    async def scenario():
        assert await solver.solve_pair(halves) == (90, 180)

    asyncio.run(scenario())
    # Synthetic images only: a randomly generated real credential is not
    # needed and no external network transport is configured.
    assert not any(b"student" in png for png in halves)
