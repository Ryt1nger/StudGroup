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


def test_cropped_challenge_sends_no_profile_and_drags_each_slider_once(monkeypatch):
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

        async def known_handles(_page, _boxes, *, only_index=None):
            all_centers = [(125.0, 375.0), (125.0, 431.0)]
            return all_centers if only_index is None else [all_centers[only_index]]

        monkeypatch.setattr(ranepa_captcha, "_read_verified_handle_centers", known_handles)
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
def test_invalid_positions_never_touch_one_shot_sliders(code, stage, caplog, monkeypatch):
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

        async def known_handles(_page, _boxes, *, only_index=None):
            all_centers = [(125.0, 375.0), (125.0, 431.0)]
            return all_centers if only_index is None else [all_centers[only_index]]

        monkeypatch.setattr(ranepa_captcha, "_read_verified_handle_centers", known_handles)
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
                return _fake_png()

        with caplog.at_level(logging.WARNING):
            assert not await ranepa_captcha.solve_slider_captcha(Page(), None)
        stages = [
            record.message for record in caplog.records if "RANEPA_CAPTCHA_STAGE" in record.message
        ]
        assert "RANEPA_CAPTCHA_STAGE controls_confirmed" in stages
        assert "RANEPA_CAPTCHA_STAGE capture_ready" in stages
        assert "RANEPA_CAPTCHA_STAGE diagnostic_capture_grid_verified" in stages
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


def test_track_annotation_rejects_wrong_screenshot_size_without_provider_contact(
    caplog, monkeypatch
):
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

        async def known_handles(_page, _boxes, *, only_index=None):
            all_centers = [(125.0, 375.0), (125.0, 431.0)]
            return all_centers if only_index is None else [all_centers[only_index]]

        monkeypatch.setattr(ranepa_captcha, "_read_verified_handle_centers", known_handles)
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
    assert ranepa_captcha._slider_targets(
        ((140, 315), (220, 371)), boxes, clip, [(118.0, 375.0), (132.0, 431.0)]
    ) == [
        (118.0, 220, 375.0),
        (132.0, 300, 431.0),
    ]


@pytest.mark.parametrize("capture_kind", ["device_pixels_2x", "wrong_png_size", "invalid_bytes"])
def test_free_diagnostic_flags_pixel_grid_mismatch_without_motion(
    capture_kind, monkeypatch, caplog
):
    """No paid call, annotation or mouse motion on unsupported screenshot geometry."""

    async def scenario():
        boxes = [
            {"x": 100, "y": 360, "width": 300, "height": 30},
            {"x": 100, "y": 416, "width": 300, "height": 30},
        ]

        async def slider_boxes(_page):
            return boxes

        monkeypatch.setattr(ranepa_captcha, "_slider_boxes", slider_boxes)

        class NoMotion:
            async def move(self, *_args, **_kwargs):
                raise AssertionError("diagnostic must never move")

            async def down(self):
                raise AssertionError("diagnostic must never press")

            async def up(self):
                raise AssertionError("diagnostic must never release")

        class Page:
            url = "https://my.ranepa.ru/lk/student/schedule"
            mouse = NoMotion()

            async def screenshot(self, **kwargs):
                assert kwargs["clip"] == {"x": 80, "y": 60, "width": 340, "height": 431}
                if capture_kind == "invalid_bytes":
                    return b"intentionally-not-a-png"
                size = (680, 862) if capture_kind == "device_pixels_2x" else (40, 40)
                output = BytesIO()
                Image.new("RGB", size, "white").save(output, "PNG")
                return output.getvalue()

        with caplog.at_level(logging.WARNING):
            assert not await ranepa_captcha.solve_slider_captcha(Page(), None)
        assert "RANEPA_CAPTCHA_STAGE diagnostic_capture_grid_mismatch" in caplog.text
        assert "RANEPA_CAPTCHA_STAGE diagnostic_complete_no_provider_call" in caplog.text
        assert "provider_request_started" not in caplog.text
        assert "drag_submitted" not in caplog.text

    asyncio.run(scenario())


def test_geometry_mapping_handles_offset_and_different_slider_widths():
    """Offline mapping covers both independent track boxes and clip offsets."""
    boxes = [
        {"x": 100, "y": 350, "width": 300, "height": 26},
        {"x": 116, "y": 410, "width": 280, "height": 30},
    ]
    clip = ranepa_captcha._challenge_clip(boxes)
    assert clip == {"x": 80, "y": 50, "width": 340, "height": 435}

    output = BytesIO()
    Image.new("RGB", (340, 435), (245, 245, 245)).save(output, "PNG")
    png = output.getvalue()
    assert ranepa_captcha._capture_grid_matches_clip(png, clip)

    marked = ranepa_captcha._annotate_slider_tracks(png, boxes, clip)
    with Image.open(BytesIO(marked)) as image:
        assert image.size == (340, 435)
        assert image.getpixel((50, 298)) == (0, 165, 85)
        assert image.getpixel((50, 358)) == (0, 105, 235)

    # Coordinates are in cropped image pixels, then translated by clip x/y.
    centers = [(118.0, 363.0), (130.0, 425.0)]
    assert ranepa_captcha._slider_targets(((100, 313), (180, 375)), boxes, clip, centers) == [
        (118.0, 180, 363.0),
        (130.0, 260, 425.0),
    ]
    # Swapping the two rows must be rejected, not silently reordered.
    assert ranepa_captcha._slider_targets(((100, 375), (180, 313)), boxes, clip, centers) is None


@pytest.mark.parametrize(
    ("snapshots", "expected"),
    [
        (
            [
                {"type": "range", "min": "0", "max": "100", "value": "0", "disabled": False},
                {"type": "range", "min": "5", "max": "15", "value": "5", "disabled": False},
            ],
            "diagnostic_range_state_at_min",
        ),
        (
            [
                {"type": "range", "min": "0", "max": "100", "value": "50", "disabled": False},
                {"type": "range", "min": "0", "max": "100", "value": "0", "disabled": False},
            ],
            "diagnostic_range_state_not_at_min",
        ),
        (
            [
                {"type": "range", "min": "0", "max": "100", "value": "0", "disabled": True},
                {"type": "range", "min": "0", "max": "100", "value": "0", "disabled": False},
            ],
            "diagnostic_range_state_unavailable",
        ),
        (
            [
                {"type": "range", "min": "0", "max": "100", "value": "NaN", "disabled": False},
                {"type": "range", "min": "0", "max": "100", "value": "0", "disabled": False},
            ],
            "diagnostic_range_state_unavailable",
        ),
        (
            [
                {"type": "range", "min": "10", "max": "10", "value": "10", "disabled": False},
                {"type": "range", "min": "0", "max": "100", "value": "0", "disabled": False},
            ],
            "diagnostic_range_state_unavailable",
        ),
        (
            [
                {"type": "range", "min": "0", "max": "100", "value": "101", "disabled": False},
                {"type": "range", "min": "0", "max": "100", "value": "0", "disabled": False},
            ],
            "diagnostic_range_state_unavailable",
        ),
        (
            [{"type": "range", "min": "0", "max": "100", "value": "0", "disabled": False}],
            "diagnostic_range_state_unavailable",
        ),
        (
            [
                {"type": "text", "min": "0", "max": "100", "value": "0", "disabled": False},
                {"type": "range", "min": "0", "max": "100", "value": "0", "disabled": False},
            ],
            "diagnostic_range_state_unavailable",
        ),
    ],
)
def test_read_only_range_state_semantics(snapshots, expected):
    assert ranepa_captcha._range_state_stage(snapshots) == expected


def test_free_diagnostic_observes_range_values_without_mouse_or_provider(caplog):
    async def scenario():
        values = [
            {"type": "range", "min": "0", "max": "100", "value": "50", "disabled": False},
            {"type": "range", "min": "0", "max": "100", "value": "0", "disabled": False},
        ]
        evaluated = []

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

            async def evaluate(self, expression, **kwargs):
                assert expression.startswith("(el) =>")
                assert kwargs == {"timeout": 1500}
                evaluated.append(self.index)
                return values[self.index]

        class Sliders:
            async def count(self):
                return 2

            def nth(self, index):
                return Slider(index)

        class NoMouse:
            async def move(self, *_args, **_kwargs):
                raise AssertionError("must not move")

            async def down(self):
                raise AssertionError("must not press")

            async def up(self):
                raise AssertionError("must not release")

        class Page:
            url = "https://my.ranepa.ru/lk/student/schedule"
            mouse = NoMouse()

            def get_by_text(self, *_args, **_kwargs):
                return Title()

            def locator(self, selector):
                assert selector == 'input[type="range"]'
                return Sliders()

            async def screenshot(self, **kwargs):
                assert kwargs["type"] == "png"
                return _fake_png()

        with caplog.at_level(logging.WARNING):
            assert not await ranepa_captcha.solve_slider_captcha(Page(), None)
        assert evaluated == [0, 1, 0, 1]  # Range values and geometry are inspected separately.
        assert "RANEPA_CAPTCHA_STAGE diagnostic_capture_grid_verified" in caplog.text
        assert "RANEPA_CAPTCHA_STAGE diagnostic_range_state_not_at_min" in caplog.text
        assert "RANEPA_CAPTCHA_STAGE diagnostic_geometry_unavailable" in caplog.text
        assert "RANEPA_CAPTCHA_STAGE diagnostic_complete_no_provider_call" in caplog.text
        assert "provider_request_started" not in caplog.text
        assert "drag_submitted" not in caplog.text
        assert "50" not in caplog.text  # Never log raw range values.

    asyncio.run(scenario())


def test_offline_range_diagnostic_is_unavailable_without_real_range_inputs():
    async def scenario():
        class FakeInputs:
            async def count(self):
                return 0

        class Page:
            def locator(self, selector):
                assert selector == 'input[type="range"]'
                return FakeInputs()

        assert (
            await ranepa_captcha._inspect_range_state_offline(Page())
            == "diagnostic_range_state_unavailable"
        )

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("answer", "expected"),
    [
        ({"code": "coordinate:x=0,y=0;x=100,y=200"}, ((0, 0), (100, 200))),
        ({"code": "coordinate:x=10,y=20"}, None),
        ({"code": "coordinate:x=10,y=20;x=30,y=40;x=50,y=60"}, None),
        ({"code": "coordinate:x=10.5,y=20;x=30,y=40"}, None),
        ({"code": "coordinate:x=-10,y=20;x=30,y=40"}, None),
        ({"code": "coordinate:x=10,y=20;x=30,y=40\\n"}, None),
        ({"code": [10, 20, 30, 40]}, None),
    ],
)
def test_two_point_coordinate_response_contract_is_strict(answer, expected):
    assert ranepa_captcha.decode_slider_coordinates(answer) == expected


def test_motion_requires_verified_handle_centers_and_rejects_zero_distance(caplog):
    """Never fall back to box.x + 25 or perform a zero-distance press."""
    boxes = [
        {"x": 100, "y": 360, "width": 300, "height": 30},
        {"x": 116, "y": 416, "width": 280, "height": 30},
    ]
    clip = {"x": 80, "y": 60, "width": 340, "height": 431}
    points = ((140, 315), (220, 371))
    assert ranepa_captcha._slider_targets(points, boxes, clip, []) is None
    starts = [(118.0, 375.0), (133.0, 431.0)]
    targets = ranepa_captcha._slider_targets(points, boxes, clip, starts)
    assert targets == [(118.0, 220, 375.0), (133.0, 300, 431.0)]
    assert targets[0][0] != boxes[0]["x"] + 25
    assert targets[1][0] != boxes[1]["x"] + 25
    with caplog.at_level(logging.WARNING):
        assert (
            ranepa_captcha._slider_targets(points, boxes, clip, [(220.0, 375.0), (133.0, 431.0)])
            is None
        )
    assert "target_1_zero_distance" in caplog.text


def test_dom_geometry_classification_is_fail_closed():
    boxes = [
        {"x": 100, "y": 360, "width": 300, "height": 30},
        {"x": 100, "y": 416, "width": 300, "height": 30},
    ]

    def sample(box):
        return {
            "rect": dict(box),
            "direction": "ltr",
            "writingMode": "horizontal-tb",
            "transform": "none",
            "pointerEvents": "auto",
            "appearance": "auto",
        }

    classify = ranepa_captcha._range_geometry_stage
    clean = [sample(box) for box in boxes]
    assert classify(clean, boxes) == "diagnostic_geometry_rect_consistent"
    assert classify(clean[:1], boxes) == "diagnostic_geometry_unavailable"

    mismatch = [sample(box) for box in boxes]
    mismatch[1]["rect"]["x"] += 12
    assert classify(mismatch, boxes) == "diagnostic_geometry_rect_mismatch"

    transformed = [sample(box) for box in boxes]
    transformed[0]["transform"] = "matrix(1, 0, 0, 1, 12, 0)"
    assert classify(transformed, boxes) == "diagnostic_geometry_nonstandard"

    rtl = [sample(box) for box in boxes]
    rtl[0]["direction"] = "rtl"
    assert classify(rtl, boxes) == "diagnostic_geometry_nonstandard"

    vertical = [sample(box) for box in boxes]
    vertical[1]["writingMode"] = "vertical-rl"
    assert classify(vertical, boxes) == "diagnostic_geometry_nonstandard"

    blocked = [sample(box) for box in boxes]
    blocked[1]["pointerEvents"] = "none"
    assert classify(blocked, boxes) == "diagnostic_geometry_nonstandard"

    custom = [sample(box) for box in boxes]
    custom[1]["appearance"] = "none"
    assert classify(custom, boxes) == "diagnostic_geometry_custom_range"

    missing = [sample(box) for box in boxes]
    del missing[1]["appearance"]
    assert classify(missing, boxes) == "diagnostic_geometry_unavailable"

    invalid = [sample(box) for box in boxes]
    invalid[0]["rect"]["x"] = float("nan")
    assert classify(invalid, boxes) == "diagnostic_geometry_unavailable"

    bad_value = [sample(box) for box in boxes]
    bad_value[0]["rect"]["width"] = "wrong"
    assert classify(bad_value, boxes) == "diagnostic_geometry_unavailable"


def test_read_only_dom_geometry_cannot_trigger_motion_or_provider(caplog):
    async def scenario():
        boxes = [
            {"x": 100, "y": 360, "width": 300, "height": 30},
            {"x": 100, "y": 416, "width": 300, "height": 30},
        ]
        seen = []

        class Label:
            async def is_visible(self):
                return True

        class Slider:
            def __init__(self, index):
                self.index = index

            async def bounding_box(self):
                return boxes[self.index]

            async def evaluate(self, expression, **kwargs):
                assert kwargs == {"timeout": 1500}
                seen.append((self.index, "getBoundingClientRect" in expression))
                if "getBoundingClientRect" in expression:
                    return {
                        "rect": dict(boxes[self.index]),
                        "direction": "ltr",
                        "writingMode": "horizontal-tb",
                        "transform": "none",
                        "pointerEvents": "auto",
                        "appearance": "none",
                    }
                return {
                    "type": "range",
                    "min": "0",
                    "max": "100",
                    "value": "0",
                    "disabled": False,
                }

        class Sliders:
            async def count(self):
                return 2

            def nth(self, index):
                return Slider(index)

        class NoMotion:
            async def move(self, *_args, **_kwargs):
                raise AssertionError("read-only diagnostic cannot move")

            async def down(self):
                raise AssertionError("read-only diagnostic cannot press")

            async def up(self):
                raise AssertionError("read-only diagnostic cannot release")

        class Page:
            url = "https://my.ranepa.ru/lk/student/schedule"
            mouse = NoMotion()

            def get_by_text(self, *_args, **_kwargs):
                return Label()

            def locator(self, selector):
                assert selector == 'input[type="range"]'
                return Sliders()

            async def screenshot(self, **kwargs):
                assert kwargs["type"] == "png"
                return _fake_png()

        with caplog.at_level(logging.WARNING):
            assert not await ranepa_captcha.solve_slider_captcha(Page(), None)

        assert seen == [(0, False), (1, False), (0, True), (1, True)]
        assert "RANEPA_CAPTCHA_STAGE diagnostic_range_state_at_min" in caplog.text
        assert "RANEPA_CAPTCHA_STAGE diagnostic_geometry_custom_range" in caplog.text
        assert "RANEPA_CAPTCHA_STAGE diagnostic_complete_no_provider_call" in caplog.text
        assert "provider_request_started" not in caplog.text
        assert "drag_submitted" not in caplog.text
        assert "matrix(" not in caplog.text

    asyncio.run(scenario())


def test_range_geometry_returns_unavailable_when_dom_changes():
    async def scenario():
        class Sliders:
            async def count(self):
                return 0

        class Page:
            def locator(self, selector):
                assert selector == 'input[type="range"]'
                return Sliders()

        assert (
            await ranepa_captcha._inspect_range_geometry_offline(Page(), [])
            == "diagnostic_geometry_unavailable"
        )

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("before", "after", "stage"),
    [
        (
            ((0.0, 100.0, 0.0), (0.0, 100.0, 0.0)),
            ((0.0, 100.0, 50.0), (0.0, 100.0, 20.0)),
            "drag_values_both_changed",
        ),
        (
            ((0.0, 100.0, 0.0), (0.0, 100.0, 0.0)),
            ((0.0, 100.0, 50.0), (0.0, 100.0, 0.0)),
            "drag_values_one_changed",
        ),
        (
            ((0.0, 100.0, 0.0), (0.0, 100.0, 0.0)),
            ((0.0, 100.0, 0.0), (0.0, 100.0, 0.0)),
            "drag_values_unchanged",
        ),
        (None, ((0.0, 100.0, 50.0), (0.0, 100.0, 0.0)), "drag_values_unavailable"),
        (((0.0, 100.0, 0.0),), ((0.0, 100.0, 60.0),), "drag_values_unavailable"),
        (
            ((0.0, 100.0, 0.0), (0.0, 100.0, 0.0)),
            ((0.0, 200.0, 50.0), (0.0, 100.0, 0.0)),
            "drag_values_unavailable",
        ),
    ],
)
def test_post_drag_value_stages_are_safe_and_precise(before, after, stage):
    assert ranepa_captcha._post_drag_value_stage(before, after) == stage


@pytest.mark.parametrize("on_drag", ["both", "one", "none"])
def test_paid_adapter_distinguishes_dom_value_changes_without_extra_provider_calls(
    on_drag, caplog, monkeypatch
):
    async def scenario():
        boxes = [
            {"x": 100, "y": 360, "width": 300, "height": 30},
            {"x": 100, "y": 416, "width": 300, "height": 30},
        ]
        values = ["0", "0"]
        events = []
        calls = []

        class Slider:
            def __init__(self, index):
                self.index = index

            async def bounding_box(self):
                return boxes[self.index]

            async def evaluate(self, expression, **kwargs):
                assert kwargs == {"timeout": 1500}
                assert "el.value" in expression
                return {
                    "type": "range",
                    "min": "0",
                    "max": "100",
                    "value": values[self.index],
                    "disabled": False,
                }

        class Sliders:
            async def count(self):
                return 2

            def nth(self, index):
                return Slider(index)

        class Visible:
            async def is_visible(self):
                return True

        class Mouse:
            index = 0

            async def move(self, *_args, **_kwargs):
                events.append("move")

            async def down(self):
                events.append("down")

            async def up(self):
                events.append("up")
                if on_drag == "both" or (on_drag == "one" and self.index == 0):
                    values[self.index] = "50"
                self.index += 1

        class Page:
            url = "https://my.ranepa.ru/lk/student/schedule"
            mouse = Mouse()

            def get_by_text(self, *_args, **_kwargs):
                return Visible()

            def locator(self, selector):
                assert selector == 'input[type="range"]'
                return Sliders()

            async def screenshot(self, **kwargs):
                assert kwargs["type"] == "png"
                return _fake_png()

            async def wait_for_timeout(self, milliseconds):
                assert milliseconds == 800

        class Provider:
            async def coordinates(self, *_args, **_kwargs):
                calls.append("provider")
                return {"code": "coordinates:x=140,y=315;x=220,y=371"}

        async def known_handles(_page, _boxes, *, only_index=None):
            all_centers = [(125.0, 375.0), (125.0, 431.0)]
            return all_centers if only_index is None else [all_centers[only_index]]

        monkeypatch.setattr(ranepa_captcha, "_read_verified_handle_centers", known_handles)
        with caplog.at_level(logging.WARNING):
            assert await ranepa_captcha.solve_slider_captcha(Page(), Provider())
        assert len(calls) == 1
        assert events.count("down") == events.count("up") == 2
        expected = {
            "both": "drag_values_both_changed",
            "one": "drag_values_one_changed",
            "none": "drag_values_unchanged",
        }[on_drag]
        assert f"RANEPA_CAPTCHA_STAGE {expected}" in caplog.text
        assert "RANEPA_CAPTCHA_STAGE drag_submitted" in caplog.text
        assert "50.0" not in caplog.text
        assert "coordinates:x=" not in caplog.text

    asyncio.run(scenario())


def test_live_range_read_fails_closed_for_invalid_or_missing_controls():
    async def scenario():
        class Missing:
            async def count(self):
                return 1

        class Page:
            def locator(self, selector):
                assert selector == 'input[type="range"]'
                return Missing()

        assert await ranepa_captcha._read_live_range_values(Page()) is None

        class BrokenSlider:
            async def evaluate(self, *_args, **_kwargs):
                return {
                    "type": "range",
                    "min": "0",
                    "max": "100",
                    "value": "NaN",
                    "disabled": False,
                }

        class Invalid:
            async def count(self):
                return 2

            def nth(self, index):
                return BrokenSlider()

        class InvalidPage:
            def locator(self, selector):
                assert selector == 'input[type="range"]'
                return Invalid()

        assert await ranepa_captcha._read_live_range_values(InvalidPage()) is None

    asyncio.run(scenario())


def test_verified_native_css_thumb_centers_are_read_only_and_not_guessed():
    async def scenario():
        boxes = [
            {"x": 100, "y": 360, "width": 300, "height": 30},
            {"x": 116, "y": 416, "width": 280, "height": 30},
        ]
        calls = []

        class Slider:
            def __init__(self, index, *, missing=False):
                self.index = index
                self.missing = missing

            async def evaluate(self, expression, **kwargs):
                assert "elementFromPoint" in expression
                assert "::-webkit-slider-thumb" in expression
                assert "styleSheets" in expression
                assert "getBoundingClientRect" in expression
                assert kwargs == {"timeout": 1500}
                calls.append(self.index)
                if self.missing:
                    return None
                return {
                    "x": [118.0, 129.0][self.index],
                    "y": [375.0, 431.0][self.index],
                    "box": dict(boxes[self.index]),
                }

        class Sliders:
            def __init__(self, *, missing=False):
                self.missing = missing

            async def count(self):
                return 2

            def nth(self, index):
                return Slider(index, missing=self.missing and index == 1)

        class Page:
            def __init__(self, *, missing=False):
                self.missing = missing

            def locator(self, selector):
                assert selector == 'input[type="range"]'
                return Sliders(missing=self.missing)

        centers = await ranepa_captcha._read_verified_handle_centers(Page(), boxes)
        assert centers == [(118.0, 375.0), (129.0, 431.0)]
        assert calls == [0, 1]
        assert await ranepa_captcha._read_verified_handle_centers(Page(missing=True), boxes) is None
        # The first handle may lock after its one allowed movement. The
        # second handle must still be independently measurable.
        assert await ranepa_captcha._read_verified_handle_centers(
            Page(missing=True), boxes, only_index=0
        ) == [(118.0, 375.0)]
        assert (
            await ranepa_captcha._read_verified_handle_centers(
                Page(missing=True), boxes, only_index=1
            )
            is None
        )

    asyncio.run(scenario())


@pytest.mark.parametrize(
    "handle_sample",
    [
        None,
        {"x": float("nan"), "y": 375.0, "box": {"x": 100, "y": 360, "width": 300, "height": 30}},
        {"x": 30, "y": 375, "box": {"x": 100, "y": 360, "width": 300, "height": 30}},
        {"x": 125, "y": 300, "box": {"x": 100, "y": 360, "width": 300, "height": 30}},
        {"x": 125, "y": 375, "box": {"x": 100, "y": 360, "width": 280, "height": 30}},
    ],
)
def test_unverified_handle_never_contacts_rucaptcha_or_clicks(handle_sample, caplog):
    async def scenario():
        boxes = [
            {"x": 100, "y": 360, "width": 300, "height": 30},
            {"x": 100, "y": 416, "width": 300, "height": 30},
        ]

        class Title:
            async def is_visible(self):
                return True

        class Slider:
            def __init__(self, index):
                self.index = index

            async def bounding_box(self):
                return boxes[self.index]

            async def evaluate(self, expression, **kwargs):
                assert kwargs == {"timeout": 1500}
                if self.index == 0:
                    return handle_sample
                return {
                    "x": 125,
                    "y": 431,
                    "box": dict(boxes[1]),
                }

        class Sliders:
            async def count(self):
                return 2

            def nth(self, index):
                return Slider(index)

        class NoMotion:
            async def move(self, *_args, **_kwargs):
                raise AssertionError("no mouse motion on unverified handle")

            async def down(self):
                raise AssertionError("no click on unverified handle")

            async def up(self):
                raise AssertionError("no mouse up on unverified handle")

        class Page:
            mouse = NoMotion()
            url = "https://my.ranepa.ru/lk/student/schedule"

            def get_by_text(self, *_args, **_kwargs):
                return Title()

            def locator(self, selector):
                assert selector == 'input[type="range"]'
                return Sliders()

            async def screenshot(self, **kwargs):
                assert kwargs["type"] == "png"
                return _fake_png()

        class NoProvider:
            async def coordinates(self, *_args, **_kwargs):
                raise AssertionError("cannot spend money without verified handles")

        with caplog.at_level(logging.WARNING):
            assert not await ranepa_captcha.solve_slider_captcha(Page(), NoProvider())
        assert "thumb_css_unverified_no_provider_no_motion" in caplog.text
        assert "provider_request_started" not in caplog.text
        assert "drag_submitted" not in caplog.text

    asyncio.run(scenario())


def test_one_shot_preflight_rejects_changed_handle_after_provider_without_press(
    monkeypatch, caplog
):
    async def scenario():
        boxes = [
            {"x": 100, "y": 360, "width": 300, "height": 30},
            {"x": 100, "y": 416, "width": 300, "height": 30},
        ]
        center_calls = 0
        provider_calls = 0

        async def handle_centers(_page, _boxes):
            nonlocal center_calls
            center_calls += 1
            if center_calls == 1:
                return [(125.0, 375.0), (125.0, 431.0)]
            return [(140.0, 375.0), (125.0, 431.0)]

        monkeypatch.setattr(ranepa_captcha, "_read_verified_handle_centers", handle_centers)

        class Title:
            async def is_visible(self):
                return True

        class Slider:
            def __init__(self, index):
                self.index = index

            async def bounding_box(self):
                return boxes[self.index]

        class Sliders:
            async def count(self):
                return 2

            def nth(self, index):
                return Slider(index)

        class NoMouse:
            async def move(self, *_args, **_kwargs):
                raise AssertionError("never move if provider returned to changed DOM")

            async def down(self):
                raise AssertionError("never press if provider returned to changed DOM")

            async def up(self):
                raise AssertionError("never release if provider returned to changed DOM")

        class Page:
            mouse = NoMouse()
            url = "https://my.ranepa.ru/lk/student/schedule"

            def get_by_text(self, *_args, **_kwargs):
                return Title()

            def locator(self, selector):
                assert selector == 'input[type="range"]'
                return Sliders()

            async def screenshot(self, **kwargs):
                assert kwargs["type"] == "png"
                return _fake_png()

        class Provider:
            async def coordinates(self, *_args, **_kwargs):
                nonlocal provider_calls
                provider_calls += 1
                return {"code": "coordinates:x=140,y=315;x=220,y=371"}

        with caplog.at_level(logging.WARNING):
            assert not await ranepa_captcha.solve_slider_captcha(Page(), Provider())
        assert provider_calls == 1
        assert center_calls == 2
        assert "thumb_changed_before_motion_no_click" in caplog.text
        assert "drag_submitted" not in caplog.text

    asyncio.run(scenario())


def test_hover_geometry_change_aborts_before_first_press(monkeypatch, caplog):
    async def scenario():
        boxes = [
            {"x": 100, "y": 360, "width": 300, "height": 30},
            {"x": 100, "y": 416, "width": 300, "height": 30},
        ]
        calls = {"centers": 0, "provider": 0, "hover": 0}

        async def centers(_page, _boxes, *, only_index=None):
            calls["centers"] += 1
            all_centers = (
                [(140.0, 375.0), (125.0, 431.0)]
                if calls["hover"]
                else [(125.0, 375.0), (125.0, 431.0)]
            )
            return all_centers if only_index is None else [all_centers[only_index]]

        monkeypatch.setattr(ranepa_captcha, "_read_verified_handle_centers", centers)

        class Title:
            async def is_visible(self):
                return True

        class Slider:
            def __init__(self, index):
                self.index = index

            async def bounding_box(self):
                return boxes[self.index]

        class Sliders:
            async def count(self):
                return 2

            def nth(self, index):
                return Slider(index)

        class Mouse:
            async def move(self, *_args, **_kwargs):
                calls["hover"] += 1

            async def down(self):
                raise AssertionError("must not press after a hover change")

            async def up(self):
                raise AssertionError("must not release after a hover change")

        class Page:
            url = "https://my.ranepa.ru/lk/student/schedule"
            mouse = Mouse()

            def get_by_text(self, *_args, **_kwargs):
                return Title()

            def locator(self, selector):
                assert selector == 'input[type="range"]'
                return Sliders()

            async def screenshot(self, **kwargs):
                assert kwargs["type"] == "png"
                return _fake_png()

        class Provider:
            async def coordinates(self, *_args, **_kwargs):
                calls["provider"] += 1
                return {"code": "coordinates:x=140,y=315;x=220,y=371"}

        with caplog.at_level(logging.WARNING):
            assert not await ranepa_captcha.solve_slider_captcha(Page(), Provider())
        assert calls["provider"] == 1
        assert calls["hover"] == 1
        assert calls["centers"] >= 4
        assert "thumb_changed_on_hover_no_click" in caplog.text
        assert "drag_submitted" not in caplog.text

    asyncio.run(scenario())


def test_first_handle_lock_does_not_block_second_verified_drag(monkeypatch):
    async def scenario():
        boxes = [
            {"x": 100, "y": 360, "width": 300, "height": 30},
            {"x": 100, "y": 416, "width": 300, "height": 30},
        ]
        state = {"locked_first": False, "presses": 0, "provider": 0}
        observed = []

        async def centers(_page, _boxes, *, only_index=None):
            observed.append(only_index)
            known = [(125.0, 375.0), (125.0, 431.0)]
            if only_index is None:
                assert not state["locked_first"]
                return known
            if only_index == 0 and state["locked_first"]:
                raise AssertionError("must not inspect the first locked handle")
            return [known[only_index]]

        monkeypatch.setattr(ranepa_captcha, "_read_verified_handle_centers", centers)

        class Title:
            async def is_visible(self):
                return True

        class Slider:
            def __init__(self, index):
                self.index = index

            async def bounding_box(self):
                return boxes[self.index]

        class Sliders:
            async def count(self):
                return 2

            def nth(self, index):
                return Slider(index)

        class Mouse:
            async def move(self, *_args, **_kwargs):
                pass

            async def down(self):
                state["presses"] += 1
                assert state["presses"] <= 2

            async def up(self):
                if state["presses"] == 1:
                    state["locked_first"] = True

        class Page:
            url = "https://my.ranepa.ru/lk/student/schedule"
            mouse = Mouse()

            def get_by_text(self, *_args, **_kwargs):
                return Title()

            def locator(self, selector):
                assert selector == 'input[type="range"]'
                return Sliders()

            async def screenshot(self, **kwargs):
                assert kwargs["type"] == "png"
                return _fake_png()

            async def wait_for_timeout(self, milliseconds):
                assert milliseconds == 800

        class Provider:
            async def coordinates(self, *_args, **_kwargs):
                state["provider"] += 1
                return {"code": "coordinates:x=140,y=315;x=220,y=371"}

        assert await ranepa_captcha.solve_slider_captcha(Page(), Provider())
        assert state == {"locked_first": True, "presses": 2, "provider": 1}
        assert observed == [None, None, 0, 0, 1, 1]

    asyncio.run(scenario())


def test_one_shot_telemetry_distinguishes_motion_from_lock_and_reset():
    before = {"value": 0.0, "locked": False, "rotations": ("rotate(0deg)", "rotate(0deg)")}
    during = {"value": 140.0, "locked": False, "rotations": ("rotate(0deg)", "rotate(140deg)")}
    reset_and_locked = {
        "value": 0.0,
        "locked": True,
        "rotations": ("rotate(0deg)", "rotate(140deg)"),
    }
    assert ranepa_captcha._one_shot_motion_stage(1, before, during) == "drag_2_live_value_changed"
    assert ranepa_captcha._one_shot_release_stage(1, reset_and_locked) == "drag_2_release_locked"
    assert ranepa_captcha._one_shot_release_stage(0, before) == "drag_1_release_enabled"
    image_only = {"value": 0.0, "locked": False, "rotations": ("rotate(0deg)", "rotate(202deg)")}
    assert (
        ranepa_captcha._one_shot_motion_stage(1, before, image_only) == "drag_2_live_image_changed"
    )
    assert ranepa_captcha._one_shot_motion_stage(1, before, before) == "drag_2_live_unchanged"
    assert ranepa_captcha._one_shot_motion_stage(1, None, during) == "drag_2_live_unavailable"
    assert ranepa_captcha._one_shot_release_stage(1, None) == "drag_2_release_unavailable"


@pytest.mark.parametrize(
    ("disabled", "locked_class", "expected_locked"),
    [(False, False, False), (True, False, True), (False, True, True)],
)
def test_one_shot_read_only_state_observes_disabled_or_locked_class_without_clicking(
    disabled, locked_class, expected_locked
):
    async def scenario():
        class Slider:
            async def evaluate(self, expression, **kwargs):
                assert kwargs == {"timeout": 1500}
                assert "classList.contains('disable-sl')" in expression
                assert ".img-right" in expression
                return {
                    "type": "range",
                    "value": "0",
                    "disabled": disabled,
                    "lockedClass": locked_class,
                    "imageRotations": ["rotate(0deg)", "rotate(202deg)"],
                }

        class Inputs:
            async def count(self):
                return 2

            def nth(self, index):
                assert index == 1
                return Slider()

        class Page:
            def locator(self, selector):
                assert selector == 'input[type="range"]'
                return Inputs()

        state = await ranepa_captcha._read_one_shot_range_state(Page(), 1)
        assert state == {
            "value": 0.0,
            "locked": expected_locked,
            "rotations": ("rotate(0deg)", "rotate(202deg)"),
        }

    asyncio.run(scenario())


def test_css_thumb_travel_boundaries_reject_unreachable_targets(caplog):
    boxes = [
        {"x": 100, "y": 360, "width": 300, "height": 30},
        {"x": 100, "y": 416, "width": 300, "height": 30},
    ]
    clip = {"x": 80, "y": 60, "width": 340, "height": 431}
    # Explicit CSS gave a 60px thumb: its rightmost center is x + 270,
    # not the final 15px track margin accepted by the old rectangle check.
    starts = [(130.0, 375.0), (130.0, 431.0)]
    with caplog.at_level(logging.WARNING):
        assert (
            ranepa_captcha._slider_targets(
                ((298, 315), (200, 371)), boxes, clip, starts
            )
            is None
        )
    assert "target_1_outside_thumb_travel" in caplog.text
    assert ranepa_captcha._slider_targets(
        ((200, 315), (200, 371)), boxes, clip, starts
    ) == [(130.0, 280, 375.0), (130.0, 280, 431.0)]
