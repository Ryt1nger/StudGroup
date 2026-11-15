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
        assert solver.last_angles == (90, 180)
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


@pytest.mark.parametrize(
    ("submitted", "angles", "values", "status", "expected"),
    [
        (True, (90, 180), [90.0, 180.0], "accepted", "accepted"),
        (True, (90, 90), [90.0, 90.0], "rejected", "provider_angle_mismatch"),
        (True, (90, 180), [90.0, 90.0], "rejected", "browser_drag_mismatch"),
        (True, (90, 180), [90.0, 180.0], "rejected", "local_verifier_rejected"),
        (False, None, [0.0, 0.0], "pending", "solver_incomplete"),
        (True, (-270, -180), [90.0, 180.0], "accepted", "accepted"),
    ],
)
def test_fixture_diagnosis_distinguishes_provider_and_drag(
    submitted, angles, values, status, expected
):
    assert (
        ranepa_captcha_fixture.fixture_diagnosis(
            submitted=submitted, angles=angles, values=values, status=status
        )
        == expected
    )


@pytest.mark.parametrize(
    ("wrong_side", "unsolvable_side", "always_wrong", "tasks", "resends", "valid"),
    [
        ("left", None, False, 3, 1, True),
        ("right", None, False, 3, 1, True),
        (None, "right", False, 3, 1, True),
        ("right", None, True, 5, 3, False),
        ("left", None, True, 4, 3, False),
    ],
)
def test_fixture_retry_corrects_only_failing_half_with_global_cap(
    monkeypatch, wrong_side, unsolvable_side, always_wrong, tasks, resends, valid
):
    monkeypatch.setattr(ranepa_rotate_v2, "_POLL_SECONDS", 0)
    halves = ranepa_captcha_fixture.make_halves()
    solver, seen = ranepa_captcha_fixture.mock_rucaptcha(
        halves,
        progressive_context=True,
        max_resends=3,
        wrong_first_side=wrong_side,
        always_wrong=always_wrong,
        unsolvable_first_side=unsolvable_side,
    )

    async def scenario():
        rechecks = 0

        async def still_valid():
            nonlocal rechecks
            rechecks += 1
            return True

        if valid:
            assert await solver.solve_pair(halves, before_second=still_valid) == (90, 180)
        else:
            with pytest.raises(ranepa_rotate_v2.RotateV2Error, match="invalid_answer"):
                await solver.solve_pair(halves, before_second=still_valid)
        assert rechecks >= 1

    asyncio.run(scenario())
    assert seen["created"] == tasks
    assert seen["answered"] == tasks
    assert solver.tasks_submitted == tasks
    assert solver.resends_used == resends
    if wrong_side == "right" or unsolvable_side == "right":
        assert seen["side_requests"][0] == 1
    assert sum(seen["side_requests"]) <= 5


def test_fixture_without_retry_does_not_submit_second_task_after_wrong_left(monkeypatch):
    monkeypatch.setattr(ranepa_rotate_v2, "_POLL_SECONDS", 0)
    halves = ranepa_captcha_fixture.make_halves()
    solver, seen = ranepa_captcha_fixture.mock_rucaptcha(
        halves, progressive_context=False, max_resends=0, wrong_first_side="left"
    )

    async def scenario():
        with pytest.raises(ranepa_rotate_v2.RotateV2Error, match="invalid_answer"):
            await solver.solve_pair(halves)

    asyncio.run(scenario())
    assert seen["created"] == 1
    assert solver.resends_used == 0


def test_fixture_retry_rejects_changes_before_extra_paid_task(monkeypatch):
    monkeypatch.setattr(ranepa_rotate_v2, "_POLL_SECONDS", 0)
    halves = ranepa_captcha_fixture.make_halves()
    solver, seen = ranepa_captcha_fixture.mock_rucaptcha(
        halves, progressive_context=True, max_resends=3, wrong_first_side="left"
    )

    async def scenario():
        async def challenge_changed():
            return False

        with pytest.raises(ranepa_rotate_v2.RotateV2Error, match="challenge_changed"):
            await solver.solve_pair(halves, before_second=challenge_changed)

    asyncio.run(scenario())
    assert seen["created"] == 1
    assert seen["answered"] == 1
    assert solver.resends_used == 1


@pytest.mark.parametrize("max_resends", [-1, 4, 1.5, True])
def test_fixture_rejects_invalid_retry_limits(max_resends):
    with pytest.raises(ValueError, match="fixture_max_resends_out_of_range"):
        ranepa_captcha_fixture.ObservedFixtureV2Pair("mock", max_resends=max_resends)


def test_fixture_paid_retries_require_separate_consent(monkeypatch):
    monkeypatch.setenv("CAPTCHA_API_KEY", "test-do-not-use")
    with pytest.raises(ValueError, match="retries_require_explicit_opt_in"):
        asyncio.run(
            ranepa_captcha_fixture.run_fixture(
                provider="rucaptcha",
                allow_paid=True,
                allow_extra_paid=False,
                max_resends=3,
                progressive_context=True,
            )
        )


def test_fixture_paid_mode_forbids_mock_response_injection(monkeypatch):
    monkeypatch.setenv("CAPTCHA_API_KEY", "test-do-not-use")
    with pytest.raises(ValueError, match="cannot_inject_mock_answers_into_paid_mode"):
        asyncio.run(
            ranepa_captcha_fixture.run_fixture(
                provider="rucaptcha",
                allow_paid=True,
                allow_extra_paid=True,
                max_resends=3,
                progressive_context=True,
                mock_wrong_first_side="right",
            )
        )


def test_fixture_timeout_defaults_to_six_minutes():
    import inspect

    signature = inspect.signature(ranepa_captcha_fixture.run_fixture)
    assert signature.parameters["timeout_seconds"].default == 360
    assert ranepa_captcha_fixture._MIN_RETRY_WINDOW_SECONDS == 70


def test_fixture_logs_explicit_worker_unsolvable_failure(monkeypatch, caplog):
    monkeypatch.setattr(ranepa_rotate_v2, "_POLL_SECONDS", 0)
    halves = ranepa_captcha_fixture.make_halves()
    solver, seen = ranepa_captcha_fixture.mock_rucaptcha(
        halves,
        progressive_context=True,
        max_resends=3,
        unsolvable_first_side="left",
    )

    async def scenario():
        assert await solver.solve_pair(halves, timeout_seconds=160) == (90, 180)

    asyncio.run(scenario())
    assert "RANEPA_CAPTCHA_STAGE fixture_worker_error_unsolvable" in caplog.text
    assert solver.last_provider_failure == "api_unsolvable"
    assert solver.last_error_reason is None
    assert solver.resends_used == 1
    assert seen["created"] == 3


def test_fixture_does_not_bill_retry_if_too_little_time_remains(monkeypatch, caplog):
    monkeypatch.setattr(ranepa_rotate_v2, "_POLL_SECONDS", 0)
    halves = ranepa_captcha_fixture.make_halves()
    solver, seen = ranepa_captcha_fixture.mock_rucaptcha(
        halves,
        progressive_context=True,
        max_resends=3,
        wrong_first_side="left",
    )
    # Once the first incorrect answer arrives, only 69 seconds are left
    # from the 160-second synthetic deadline (below the 70s guard).
    solver._clock = lambda: 91 if seen["answered"] else 0

    async def scenario():
        with pytest.raises(ranepa_rotate_v2.RotateV2Error, match="invalid_answer"):
            await solver.solve_pair(halves, timeout_seconds=160)

    asyncio.run(scenario())
    assert seen["created"] == 1
    assert seen["answered"] == 1
    assert solver.resends_used == 1
    assert solver.tasks_submitted == 1
    assert solver.last_error_reason == "retry_time_budget_insufficient"
    assert "fixture_retry_skipped_insufficient_time" in caplog.text


def test_fixture_timeout_has_explicit_nonsecret_diagnostic(monkeypatch, caplog):
    halves = ranepa_captcha_fixture.make_halves()
    solver, seen = ranepa_captcha_fixture.mock_rucaptcha(
        halves, progressive_context=True, max_resends=3
    )

    async def hanging_solver(*args, **kwargs):
        await asyncio.sleep(0.03)
        return 90

    monkeypatch.setattr(solver, "_solve_one", hanging_solver)

    async def scenario():
        with pytest.raises(TimeoutError):
            await solver.solve_pair(halves, timeout_seconds=0.001)

    asyncio.run(scenario())
    assert seen["created"] == 0
    assert solver.tasks_submitted == 1
    assert solver.last_error_reason == "overall_timeout"
    assert "fixture_overall_timeout" in caplog.text


@pytest.mark.parametrize("timeout_seconds", [0, 90, 361, 1.5, True])
def test_fixture_rejects_out_of_range_timeout_without_paid_api(monkeypatch, timeout_seconds):
    monkeypatch.setenv("CAPTCHA_API_KEY", "test-do-not-use")
    with pytest.raises(ValueError, match="fixture_timeout_out_of_range"):
        asyncio.run(
            ranepa_captcha_fixture.run_fixture(
                provider="rucaptcha",
                allow_paid=True,
                allow_extra_paid=True,
                max_resends=3,
                progressive_context=True,
                timeout_seconds=timeout_seconds,
            )
        )
