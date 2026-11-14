import asyncio
import base64
import logging
from io import BytesIO
from pathlib import Path

import pytest
from PIL import Image
from twocaptcha import AsyncTwoCaptcha
from twocaptcha.exceptions.api import (
    ApiException as ClientApiException,
)
from twocaptcha.exceptions.api import (
    NetworkException as ClientNetworkException,
)
from twocaptcha.exceptions.solver import (
    ApiException as SolverApiException,
)
from twocaptcha.exceptions.solver import (
    NetworkException as SolverNetworkException,
)
from twocaptcha.exceptions.solver import (
    ValidationException,
)

from studgroup import ranepa_rotate


def _data_uri(color: str) -> str:
    image = Image.new("RGB", (200, 200), color)
    output = BytesIO()
    image.save(output, format="PNG")
    return "data:image/png;base64," + base64.b64encode(output.getvalue()).decode("ascii")


def test_rotate_hint_fits_provider_textinstructions_limit():
    assert len(ranepa_rotate._HINT) <= 140
    assert len(ranepa_rotate._HINT.encode("utf-8")) <= 140
    assert "left" in ranepa_rotate._HINT.lower()
    assert "right" in ranepa_rotate._HINT.lower()


def test_rotate_provider_answer_contract_and_direction():
    assert ranepa_rotate.decode_rotation_angles({"code": "40|-120"}) == (40, -120)
    assert ranepa_rotate.decode_rotation_angles({"code": "0|360"}) == (0, 360)
    for bad in (
        None,
        {},
        {"code": "40"},
        {"code": "40|200|120"},
        {"code": "x=40|200"},
        {"code": "361|10"},
        {"code": "40.5|200"},
        {"code": "40,200"},
        {"code": "OK|40|200"},
        {"code": "-361|120"},
    ):
        assert ranepa_rotate.decode_rotation_angles(bad) is None


def test_rotate_angles_map_to_measured_travel_without_guessing():
    boxes = [
        {"x": 100, "y": 360, "width": 300, "height": 30},
        {"x": 100, "y": 416, "width": 300, "height": 30},
    ]
    starts = [(130.0, 375.0), (130.0, 431.0)]
    targets = ranepa_rotate._angle_targets((90, -180), boxes, starts)
    assert targets == [(130.0, 190.0, 375.0), (130.0, 250.0, 431.0)]
    assert ranepa_rotate._angle_targets((0, 90), boxes, starts) is None
    assert ranepa_rotate._angle_targets((360, 90), boxes, starts) is None
    assert ranepa_rotate._angle_targets((5, 90), boxes, starts) is None
    assert ranepa_rotate._angle_targets((90, 90), boxes, [(107, 375), (130, 431)]) is None


def test_only_supported_small_data_images_are_convertible():
    assert ranepa_rotate._original_half_png("https://my.ranepa.ru/private") is None
    assert ranepa_rotate._original_half_png("data:image/svg+xml;base64,PHN2Zy8+") is None
    assert ranepa_rotate._original_half_png("data:image/png;base64,not_base64") is None
    with Image.open(BytesIO(ranepa_rotate._original_half_png(_data_uri("white")))) as im:
        assert im.format == "PNG"
        assert im.size == (200, 200)


@pytest.mark.parametrize("result", [None, ["a"], ["a", "b", "c"], ["bad", "bad"]])
def test_challenge_image_capture_fails_closed(result):
    async def scenario():
        class Page:
            async def evaluate(self, script):
                assert ".img-left-block" in script and ".img-right-block" in script
                return result

        assert await ranepa_rotate._read_original_halves(Page()) is None

    asyncio.run(scenario())


def test_rotation_pilot_one_provider_task_two_one_shot_drags_and_no_files_left(monkeypatch, caplog):
    async def scenario():
        boxes = [
            {"x": 100, "y": 360, "width": 300, "height": 30},
            {"x": 100, "y": 416, "width": 300, "height": 30},
        ]
        files_seen = []
        centers_seen = []

        class Mouse:
            def __init__(self):
                self.down_count = 0
                self.up_count = 0
                self.pressed = False
                self.movements = []

            async def move(self, x, y, **kwargs):
                self.movements.append((x, y, kwargs))
                if self.pressed:
                    assert kwargs.get("steps") == 10

            async def down(self):
                assert not self.pressed
                self.pressed = True
                self.down_count += 1

            async def up(self):
                assert self.pressed
                self.pressed = False
                self.up_count += 1

        class Page:
            url = "https://my.ranepa.ru/lk/student/schedule"
            mouse = Mouse()

            async def evaluate(self, expression):
                assert ".img-left-block" in expression
                return [_data_uri("red"), _data_uri("blue")]

        page = Page()

        async def slider_boxes(_page):
            return boxes

        async def thumb_centers(_page, _boxes, *, only_index=None):
            centers_seen.append(only_index)
            full = [(130.0, 375.0), (130.0, 431.0)]
            return full if only_index is None else [full[only_index]]

        async def read_state(_page, index):
            is_drag = page.mouse.pressed
            is_released = page.mouse.up_count > index
            return {
                "value": float(90 * (index + 1)) if is_drag else 0.0,
                "locked": is_released,
                "rotations": ("rotate(0deg)", "rotate(0deg)"),
            }

        monkeypatch.setattr(ranepa_rotate, "_slider_boxes", slider_boxes)
        monkeypatch.setattr(ranepa_rotate, "_read_verified_handle_centers", thumb_centers)
        monkeypatch.setattr(ranepa_rotate, "_read_one_shot_range_state", read_state)

        class Provider:
            calls = 0

            async def rotate(self, files, **kwargs):
                self.calls += 1
                assert list(files) == ["left", "right"]
                assert kwargs["angle"] == 5
                assert "half" in kwargs["hintText"].lower()
                assert len(kwargs["hintText"].encode("utf-8")) <= 140
                for path in files.values():
                    file = Path(path)
                    assert file.exists()
                    files_seen.append(file)
                    with Image.open(file) as image:
                        assert image.format == "PNG"
                        assert image.size == (200, 200)
                return {"code": "90|-180"}

        solver = Provider()
        with caplog.at_level(logging.WARNING):
            assert await ranepa_rotate.solve_rotating_halves(page, solver)
        assert solver.calls == 1
        assert page.mouse.down_count == page.mouse.up_count == 2
        assert centers_seen == [None, None, 0, 0, 1, 1]
        assert not any(path.exists() for path in files_seen)
        assert "rotate_angles_mapped_without_click" in caplog.text
        assert "drag_1_live_value_changed" in caplog.text
        assert "drag_2_live_value_changed" in caplog.text
        assert "drag_1_release_locked" in caplog.text
        assert "drag_2_release_locked" in caplog.text
        assert "rotate_drags_submitted_verification_pending" in caplog.text
        assert "90|-180" not in caplog.text
        assert "data:image" not in caplog.text

    asyncio.run(scenario())


@pytest.mark.parametrize("answer", [{"code": "40"}, {"code": "0|180"}, {"code": "361|45"}])
def test_invalid_rotate_task_result_does_not_click(monkeypatch, answer, caplog):
    async def scenario():
        boxes = [
            {"x": 100, "y": 360, "width": 300, "height": 30},
            {"x": 100, "y": 416, "width": 300, "height": 30},
        ]

        async def slider_boxes(_page):
            return boxes

        async def thumb_centers(_page, _boxes, *, only_index=None):
            assert only_index is None
            return [(130.0, 375.0), (130.0, 431.0)]

        monkeypatch.setattr(ranepa_rotate, "_slider_boxes", slider_boxes)
        monkeypatch.setattr(ranepa_rotate, "_read_verified_handle_centers", thumb_centers)

        class NoClick:
            async def move(self, *args, **kwargs):
                raise AssertionError("must not move on invalid rotation")

            async def down(self):
                raise AssertionError("must not click on invalid rotation")

            async def up(self):
                raise AssertionError("must not release on invalid rotation")

        class Page:
            url = "https://my.ranepa.ru/lk/student/schedule"
            mouse = NoClick()

            async def evaluate(self, script):
                return [_data_uri("red"), _data_uri("blue")]

        class Provider:
            calls = 0

            async def rotate(self, files, **kwargs):
                self.calls += 1
                return answer

        provider = Provider()
        with caplog.at_level(logging.WARNING):
            assert not await ranepa_rotate.solve_rotating_halves(Page(), provider)
        assert provider.calls == 1
        assert (
            "rotate_target_invalid_no_click" in caplog.text
            or "rotate_provider_answer_invalid" in caplog.text
        )

    asyncio.run(scenario())


def test_missing_half_or_unverified_css_does_not_spend_provider_or_click(monkeypatch):
    async def scenario():
        boxes = [
            {"x": 100, "y": 360, "width": 300, "height": 30},
            {"x": 100, "y": 416, "width": 300, "height": 30},
        ]

        async def slider_boxes(_page):
            return boxes

        async def no_css(_page, _boxes, *, only_index=None):
            return None

        async def available_css(_page, _boxes, *, only_index=None):
            return [(130, 375), (130, 431)]

        monkeypatch.setattr(ranepa_rotate, "_slider_boxes", slider_boxes)

        class Provider:
            async def rotate(self, files, **kwargs):
                raise AssertionError("no provider without verified input and challenge")

        class Page:
            url = "https://my.ranepa.ru/lk/student/schedule"

            async def evaluate(self, script):
                return None

        monkeypatch.setattr(ranepa_rotate, "_read_verified_handle_centers", no_css)
        assert not await ranepa_rotate.solve_rotating_halves(Page(), Provider())
        monkeypatch.setattr(ranepa_rotate, "_read_verified_handle_centers", available_css)
        assert not await ranepa_rotate.solve_rotating_halves(Page(), Provider())

    asyncio.run(scenario())


def test_actual_sdk_builds_two_image_rotate_request_without_network(tmp_path, monkeypatch):
    """Exercise real pinned SDK serialization, without any HTTP or billing."""

    async def scenario():
        left = tmp_path / "left.png"
        right = tmp_path / "right.png"
        left.write_bytes(ranepa_rotate._original_half_png(_data_uri("red")))
        right.write_bytes(ranepa_rotate._original_half_png(_data_uri("blue")))
        solver = AsyncTwoCaptcha(apiKey="0" * 32, server="rucaptcha.com")
        calls = []

        async def receive_upload(*, files, **params):
            calls.append(("upload", dict(files), dict(params)))
            assert list(files) == ["file_1", "file_2"]
            assert [Path(p) for p in files.values()] == [left, right]
            assert params["method"] == "rotatecaptcha"
            assert params["angle"] == 5
            assert params["textinstructions"] == ranepa_rotate._HINT
            assert params["key"] == "0" * 32
            return "OK|123456"

        async def receive_answer(task_id, timeout, interval):
            calls.append(("answer", task_id, timeout, interval))
            assert task_id == "123456"
            return "40|-180"

        monkeypatch.setattr(solver.api_client, "in_", receive_upload)
        monkeypatch.setattr(solver, "wait_result", receive_answer)
        response = await solver.rotate(
            {"left": str(left), "right": str(right)},
            angle=5,
            hintText=ranepa_rotate._HINT,
        )
        assert response == {"captchaId": "123456", "code": "40|-180"}
        assert [call[0] for call in calls] == ["upload", "answer"]

    asyncio.run(scenario())


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (
            ClientApiException("cannot recognize response ERROR_BAD_PARAMETERS"),
            "rotate_provider_api_bad_parameters",
        ),
        (
            ClientApiException("ERROR_UPLOAD_ROTATECAPTCHA"),
            "rotate_provider_api_upload_rotate_failed",
        ),
        (ClientApiException("ERROR_ZERO_BALANCE"), "rotate_provider_api_zero_balance"),
        (ClientApiException("ERROR_WRONG_USER_KEY"), "rotate_provider_api_key_format"),
        (ClientApiException("secret=never-log-me"), "rotate_provider_api_other"),
        (SolverApiException("ERROR_BAD_PARAMETERS"), "rotate_provider_api_bad_parameters"),
        (SolverNetworkException("secret=never-log-me"), "rotate_provider_network"),
        (ValidationException("secret=never-log-me"), "rotate_provider_sdk_validation"),
        (ClientNetworkException("secret=never-log-me"), "rotate_provider_network"),
        (TypeError("secret=never-log-me"), "rotate_provider_sdk_type_error"),
        (OSError("secret=never-log-me"), "rotate_provider_local_io"),
        (RuntimeError("secret=never-log-me"), "rotate_provider_unknown_error"),
    ],
)
def test_provider_error_stage_is_fixed_and_never_reveals_payload(error, expected):
    stage = ranepa_rotate._provider_failure_stage(error)
    assert stage == expected
    assert "never-log-me" not in stage
    assert "secret=" not in stage
    assert ClientApiException is not SolverApiException
    assert ClientNetworkException is not SolverNetworkException


def test_provider_exception_logs_only_safe_fixed_stages_before_any_click(monkeypatch, caplog):
    async def scenario():
        async def boxes(_page):
            return [
                {"x": 100, "y": 360, "width": 300, "height": 30},
                {"x": 100, "y": 416, "width": 300, "height": 30},
            ]

        async def centers(_page, _boxes):
            return [(130.0, 375.0), (130.0, 431.0)]

        async def halves(_page):
            return (b"dummy-left", b"dummy-right")

        monkeypatch.setattr(ranepa_rotate, "_slider_boxes", boxes)
        monkeypatch.setattr(ranepa_rotate, "_read_verified_handle_centers", centers)
        monkeypatch.setattr(ranepa_rotate, "_read_original_halves", halves)

        class NeverTouchMouse:
            async def move(self, *args, **kwargs):
                raise AssertionError("no movement before provider answer")

            async def down(self):
                raise AssertionError("no click before provider answer")

            async def up(self):
                raise AssertionError("no release before provider answer")

        class Page:
            url = "https://my.ranepa.ru/lk/student/schedule"
            mouse = NeverTouchMouse()

        class Provider:
            async def rotate(self, *args, **kwargs):
                raise ClientApiException(
                    "cannot recognize response ERROR_BAD_PARAMETERS; secret=never-log-me"
                )

        with caplog.at_level(logging.WARNING):
            assert not await ranepa_rotate.solve_rotating_halves(Page(), Provider())
        assert "rotate_provider_api_bad_parameters" in caplog.text
        assert "rotate_provider_failed" in caplog.text
        assert "never-log-me" not in caplog.text
        assert "rotate_angles_mapped_without_click" not in caplog.text

    asyncio.run(scenario())
