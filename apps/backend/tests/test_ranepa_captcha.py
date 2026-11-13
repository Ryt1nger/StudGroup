"""Safe, network-free tests for the optional RuCaptcha dual-slider adapter."""

import asyncio
import base64
import logging
from datetime import date, time
from io import BytesIO
from types import SimpleNamespace

import pytest
from PIL import Image
from pydantic import SecretStr

from studgroup import ranepa_browser, ranepa_captcha
from studgroup.main import Settings


def _fake_png():
    output = BytesIO()
    Image.new("RGB", (340, 431), color=(245, 245, 245)).save(output, "PNG")
    return output.getvalue()


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
    # The real RuCaptcha legacy API uses the singular "coordinate:" prefix.
    for code in (
        "coordinate:x=141,y=315;x=220,y=371",
        "coordinates:x=141,y=315;x=220,y=371",
    ):
        assert decode({"code": code}) == ((141, 315), (220, 371))
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
            url = "https://my.ranepa.ru/lk/student/schedule"

            def get_by_text(self, text, exact=False):
                assert text == "Используйте ползунки" and not exact
                return FakeTitle()

            def locator(self, selector):
                assert selector == 'input[type="range"]'
                return FakeSliders()

            async def screenshot(self, **kwargs):
                self.clip = kwargs["clip"]
                assert kwargs["type"] == "png"
                return _fake_png()

            async def wait_for_timeout(self, ms):
                assert ms == 800

        class FakeClient:
            async def coordinates(self, encoded, **kwargs):
                with Image.open(BytesIO(base64.b64decode(encoded))) as annotated:
                    assert annotated.size == (340, 431)
                    assert annotated.getpixel((50, 298)) == (0, 165, 85)
                    assert annotated.getpixel((50, 354)) == (0, 105, 235)
                    # The circle remains unchanged, and the original clip is not scaled.
                    assert annotated.getpixel((170, 150)) == (245, 245, 245)
                assert kwargs["min_clicks"] == kwargs["max_clicks"] == 2
                assert kwargs["hintText"] == ranepa_captcha.HINT
                assert len(kwargs["hintText"]) <= 140
                return {"code": "coordinates:x=140,y=315;x=220,y=371"}

        page = FakePage()
        assert await ranepa_captcha.solve_slider_captcha(page, FakeClient())
        assert page.clip == {"x": 80, "y": 60, "width": 340, "height": 431}
        assert [entry[0] for entry in events] == [
            "move",
            "down",
            "move",
            "up",
            "move",
            "down",
            "move",
            "up",
        ]
        assert events[2][:3] == ("move", 220, 375)
        assert events[6][:3] == ("move", 300, 431)

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("code", "stage"),
    [
        ("coordinates:x=140,y=315;x=210,y=315", "target_2_above_track"),
        ("coordinates:x=1,y=315;x=210,y=371", "target_1_x_outside_track"),
        ("coordinates:x=140,y=490;x=210,y=371", "target_1_below_track"),
    ],
)
def test_invalid_positions_never_touch_one_shot_sliders(code, stage, caplog):
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
            url = "https://my.ranepa.ru/lk/student/schedule"

            def get_by_text(self, *_args, **_kwargs):
                return FakeTitle()

            def locator(self, *_args):
                return FakeSliders()

            async def screenshot(self, **_kwargs):
                return _fake_png()

        class FakeClient:
            async def coordinates(self, *_args, **_kwargs):
                return {"code": code}

        with caplog.at_level(logging.WARNING):
            assert not await ranepa_captcha.solve_slider_captcha(FakePage(), FakeClient())
        assert f"RANEPA_CAPTCHA_STAGE {stage}" in caplog.text
        assert "RANEPA_CAPTCHA_STAGE coordinates_outside_controls" in caplog.text
        assert code not in caplog.text

    asyncio.run(scenario())


@pytest.mark.parametrize("solved", [False, True])
def test_sdo_transition_attempts_a_captcha_only_with_explicit_callback(solved):
    async def scenario():
        events = []

        class FakeText:
            def __init__(self, page):
                self.page = page

            async def is_visible(self):
                return self.page.url.endswith("/captcha")

        class FakePage:
            url = ""

            async def goto(self, *_args, **_kwargs):
                self.url = "https://lms.ranepa.ru/captcha"

            def get_by_text(self, text, exact=False):
                assert text == "Используйте ползунки"
                return FakeText(self)

            async def close(self):
                events.append("closed")

        class FakeContext:
            async def new_page(self):
                return FakePage()

        async def solver(page):
            events.append("solve")
            if solved:
                page.url = "https://lms.ranepa.ru/login/index.php"
            return solved

        lesson = SimpleNamespace(
            room="СДО",
            day=date(2026, 11, 12),
            starts=time(10, 0),
            ends=time(11, 30),
            subject="Math",
            teacher="Teacher",
        )
        result = SimpleNamespace(
            snapshot=SimpleNamespace(lessons=(lesson,)),
            sdo_exits=("https://my.ranepa.ru/sdo",),
        )
        links, states, courses = await ranepa_browser.resolve_sdo_exits(
            FakeContext(), result, solve_captcha=solver
        )
        expected = "lms_login_required" if solved else "captcha_required"
        assert list(states.values()) == [expected]
        assert links == {} and courses == {}
        assert events == ["solve", "closed"]

    asyncio.run(scenario())


def test_slider_coordinates_never_leak_other_host_images():
    async def scenario():
        class ExternalPage:
            url = "https://lms.ranepa.ru/login/index.php"

            def get_by_text(self, *args, **kwargs):
                raise AssertionError("must not inspect an external page")

            async def screenshot(self, **kwargs):
                raise AssertionError("must not screenshot the user's profile")

        class Provider:
            async def coordinates(self, *args, **kwargs):
                raise AssertionError("must not send any external image")

        assert not await ranepa_captcha.solve_slider_captcha(ExternalPage(), Provider())

    asyncio.run(scenario())


def test_slider_selector_accepts_legacy_duplicate_myrange_ids():
    async def scenario():
        class Title:
            async def is_visible(self):
                return True

        class Slider:
            def __init__(self, offset):
                self.offset = offset

            async def bounding_box(self):
                return {"x": 100, "y": 360 + self.offset, "width": 300, "height": 30}

        class List:
            def __init__(self, size):
                self.size = size

            async def count(self):
                return self.size

            def nth(self, index):
                return Slider(index * 56)

        class Page:
            def get_by_text(self, *args, **kwargs):
                return Title()

            def locator(self, selector):
                if selector == 'input[type="range"]':
                    return List(0)
                assert selector == '[id="myRange"]'
                return List(2)

        boxes = await ranepa_captcha._slider_boxes(Page())
        assert len(boxes) == 2
        assert boxes[1]["y"] > boxes[0]["y"]

    asyncio.run(scenario())


def test_offline_diagnostic_inspects_challenge_without_clicks_or_provider(caplog):
    async def scenario():
        class VisibleLabel:
            async def is_visible(self):
                return True

        class Slider:
            def __init__(self, index):
                self.index = index

            async def bounding_box(self):
                return {
                    "x": 100,
                    "y": 360 + self.index * 56,
                    "width": 300,
                    "height": 30,
                }

        class Sliders:
            async def count(self):
                return 2

            def nth(self, index):
                return Slider(index)

        class NoMotion:
            async def move(self, *_args, **_kwargs):
                raise AssertionError("diagnostic must not move the slider")

            async def down(self):
                raise AssertionError("diagnostic must not press the slider")

            async def up(self):
                raise AssertionError("diagnostic must not release the slider")

        class Page:
            url = "https://my.ranepa.ru/lk/student/schedule"
            mouse = NoMotion()

            def get_by_text(self, *_args, **_kwargs):
                return VisibleLabel()

            def locator(self, *_args):
                return Sliders()

            async def screenshot(self, **kwargs):
                assert kwargs["type"] == "png"
                assert kwargs["clip"] == {
                    "x": 80,
                    "y": 60,
                    "width": 340,
                    "height": 431,
                }
                return b"test-only-local-image"

        with caplog.at_level(logging.WARNING):
            assert not await ranepa_captcha.solve_slider_captcha(Page(), None)
        stages = [
            record.message for record in caplog.records if "RANEPA_CAPTCHA_STAGE" in record.message
        ]
        assert "RANEPA_CAPTCHA_STAGE controls_confirmed" in stages
        assert "RANEPA_CAPTCHA_STAGE capture_ready" in stages
        assert "RANEPA_CAPTCHA_STAGE diagnostic_complete_no_provider_call" in stages
        assert "provider_request_started" not in caplog.text
        assert "test-only-local-image" not in caplog.text

    asyncio.run(scenario())


def test_missing_slider_controls_report_precise_stage_without_sending_image(caplog):
    async def scenario():
        class VisibleLabel:
            async def is_visible(self):
                return True

        class Empty:
            async def count(self):
                return 0

        class Page:
            url = "https://my.ranepa.ru/lk/student/schedule"

            def get_by_text(self, *_args, **_kwargs):
                return VisibleLabel()

            def locator(self, *_args):
                return Empty()

            async def screenshot(self, **_kwargs):
                raise AssertionError("no challenge screenshot without valid controls")

        with caplog.at_level(logging.WARNING):
            assert not await ranepa_captcha.solve_slider_captcha(Page(), None)
        assert "RANEPA_CAPTCHA_STAGE control_count_mismatch" in caplog.text
        assert "RANEPA_CAPTCHA_STAGE controls_unavailable" in caplog.text

    asyncio.run(scenario())



def test_track_annotation_rejects_wrong_screenshot_size_without_provider_contact(caplog):
    async def scenario():
        class Title:
            async def is_visible(self):
                return True

        class Slider:
            def __init__(self, index):
                self.index = index

            async def bounding_box(self):
                return {
                    "x": 100,
                    "y": 360 + self.index * 56,
                    "width": 300,
                    "height": 30,
                }

        class Sliders:
            async def count(self):
                return 2

            def nth(self, index):
                return Slider(index)

        class Page:
            url = "https://my.ranepa.ru/lk/student/schedule"

            def get_by_text(self, *_args, **_kwargs):
                return Title()

            def locator(self, *_args):
                return Sliders()

            async def screenshot(self, **_kwargs):
                output = BytesIO()
                Image.new("RGB", (40, 40), "white").save(output, "PNG")
                return output.getvalue()

        class Provider:
            async def coordinates(self, *_args, **_kwargs):
                raise AssertionError("must not send unmatched capture")

        with caplog.at_level(logging.WARNING):
            assert not await ranepa_captcha.solve_slider_captcha(Page(), Provider())
        assert "RANEPA_CAPTCHA_STAGE capture_annotation_failed" in caplog.text
        assert "RANEPA_CAPTCHA_STAGE provider_request_started" not in caplog.text

    asyncio.run(scenario())


def test_annotated_image_preserves_pixel_coordinate_system():
    clip = {"x": 80, "y": 60, "width": 340, "height": 431}
    boxes = [
        {"x": 100, "y": 360, "width": 300, "height": 30},
        {"x": 100, "y": 416, "width": 300, "height": 30},
    ]
    original = _fake_png()
    annotated = ranepa_captcha._annotate_slider_tracks(original, boxes, clip)
    assert original != annotated
    with Image.open(BytesIO(annotated)) as marked:
        assert marked.size == (340, 431)
    assert ranepa_captcha._slider_targets(((140, 315), (220, 371)), boxes, clip) == [
        (125, 220, 375),
        (125, 300, 431),
    ]
