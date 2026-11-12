"""Safe, network-free tests for the optional RuCaptcha dual-slider adapter."""

import asyncio
import base64
import logging

from pydantic import SecretStr

from studgroup import ranepa_captcha
from studgroup.main import Settings


def test_rucaptcha_is_opt_in_and_missing_key_is_only_a_warning(caplog, monkeypatch):
    monkeypatch.delenv("CAPTCHA_API_KEY", raising=False)
    inactive = Settings(_env_file=None)
    assert ranepa_captcha.configured_solver(inactive) is None
    with caplog.at_level(logging.WARNING):
        ranepa_captcha.warn_if_unconfigured(inactive)
    assert caplog.records == []

    active = Settings(_env_file=None, ranepa_captcha_enabled=True)
    assert ranepa_captcha.configured_solver(active) is None
    with caplog.at_level(logging.WARNING):
        ranepa_captcha.warn_if_unconfigured(active)
    assert "CAPTCHA_API_KEY is missing" in caplog.text


def test_configured_solver_uses_rucaptcha_not_2captcha(monkeypatch):
    captured = {}

    def fake_solver(**kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(ranepa_captcha, "AsyncTwoCaptcha", fake_solver)
    settings = Settings(
        _env_file=None,
        ranepa_captcha_enabled=True,
        captcha_api_key=SecretStr("super-secret-test-key"),
        ranepa_captcha_timeout_seconds=90,
    )
    assert ranepa_captcha.configured_solver(settings) is not None
    assert captured == {
        "apiKey": "super-secret-test-key",
        "server": "rucaptcha.com",
        "defaultTimeout": 90,
        "pollingInterval": 10,
    }


def test_coordinates_reject_untrusted_or_ambiguous_answers():
    decode = ranepa_captcha.decode_slider_coordinates
    assert decode({"code": "coordinates:x=141,y=315;x=220,y=371"}) == (
        (141, 315),
        (220, 371),
    )
    for bad in (
        "coordinates:x=50,y=10;x=100,y=20",
        {"code": "coordinates:x=30,y=20"},
        {"code": "coordinates:x=-1,y=20;x=80,y=50"},
        {"code": "coordinates:x=1,y=2;x=3,y=4;x=5,y=6"},
        {"code": {"x": 1, "y": 2}},
        {},
    ):
        assert decode(bad) is None


def test_cropped_challenge_sends_no_profile_and_drags_each_slider_once():
    async def scenario():
        class FakeTitle:
            async def is_visible(self):
                return True

        boxes = [
            {"x": 100, "y": 360, "width": 300, "height": 30},
            {"x": 100, "y": 416, "width": 300, "height": 30},
        ]
        events = []

        class FakeSlider:
            def __init__(self, index):
                self.index = index

            async def bounding_box(self):
                return boxes[self.index]

        class FakeSliders:
            async def count(self):
                return 2

            def nth(self, index):
                return FakeSlider(index)

        class FakeMouse:
            async def move(self, x, y, **kwargs):
                events.append(("move", x, y, kwargs))

            async def down(self):
                events.append(("down",))

            async def up(self):
                events.append(("up",))

        class FakePage:
            mouse = FakeMouse()

            def get_by_text(self, text, exact=False):
                assert text == "Используйте ползунки" and not exact
                return FakeTitle()

            def locator(self, selector):
                assert selector == 'input[type="range"]'
                return FakeSliders()

            async def screenshot(self, **kwargs):
                self.clip = kwargs["clip"]
                assert kwargs["type"] == "png"
                return b"CHALLENGE_ONLY_NOT_STUDENT_PROFILE"

            async def wait_for_timeout(self, ms):
                assert ms == 800

        class FakeClient:
            async def coordinates(self, encoded, **kwargs):
                assert base64.b64decode(encoded) == b"CHALLENGE_ONLY_NOT_STUDENT_PROFILE"
                assert kwargs["min_clicks"] == kwargs["max_clicks"] == 2
                return {"code": "coordinates:x=140,y=315;x=220,y=371"}

        page = FakePage()
        assert await ranepa_captcha.solve_slider_captcha(page, FakeClient())
        assert page.clip == {"x": 80, "y": 60, "width": 340, "height": 431}
        assert [entry[0] for entry in events] == [
            "move", "down", "move", "up", "move", "down", "move", "up"
        ]
        assert events[2][:3] == ("move", 220, 375)
        assert events[6][:3] == ("move", 300, 431)

    asyncio.run(scenario())


def test_invalid_positions_never_touch_one_shot_sliders():
    async def scenario():
        class FakeTitle:
            async def is_visible(self):
                return True

        class FakeSlider:
            def __init__(self, index):
                self.index = index

            async def bounding_box(self):
                return {"x": 100, "y": 360 + self.index * 56, "width": 300, "height": 30}

        class FakeSliders:
            async def count(self):
                return 2

            def nth(self, index):
                return FakeSlider(index)

        class FakeMouse:
            async def move(self, *args, **kwargs):
                raise AssertionError("must not drag")

            async def down(self):
                raise AssertionError("must not press")

            async def up(self):
                raise AssertionError("must not release")

        class FakePage:
            mouse = FakeMouse()

            def get_by_text(self, *_args, **_kwargs):
                return FakeTitle()

            def locator(self, *_args):
                return FakeSliders()

            async def screenshot(self, **_kwargs):
                return b"fake image"

        class FakeClient:
            async def coordinates(self, *_args, **_kwargs):
                # Second coordinate is in the upper slider row.
                return {"code": "coordinates:x=140,y=315;x=210,y=315"}

        assert not await ranepa_captcha.solve_slider_captcha(FakePage(), FakeClient())

    asyncio.run(scenario())
