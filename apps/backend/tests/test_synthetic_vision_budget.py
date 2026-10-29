"""No network: verify the paid-check guard before authorizing a real request."""

import importlib.util
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "synthetic_check", Path(__file__).parents[1] / "tools/synthetic_vision_check.py"
)
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)


def payload():
    return {
        "model": "deepseek-flash",
        "messages": [
            {
                "content": [
                    {"type": "text", "text": "Синтетика"},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,abc"}},
                ]
            }
        ],
        "max_tokens": 6000,
    }


def test_persistent_reservation_blocks_duplicate_after_restart(tmp_path):
    path = tmp_path / "ledger.json"
    ledger = check.Ledger(path)
    ledger.reserve("first", payload())
    assert path.stat().st_mode & 0o777 == 0o600
    with pytest.raises(ValueError, match="unresolved_paid_attempt_stop"):
        check.Ledger(path).reserve("again", payload())


def test_limit_is_shared_and_checked_before_request(tmp_path):
    ledger = check.Ledger(tmp_path / "ledger.json")
    ledger.data["calls"] = [
        {"state": "completed", "reservation_usd": ".999", "cost_upper_usd": ".999"}
    ]
    with pytest.raises(ValueError, match="total_budget_exceeded_before_request"):
        ledger.reserve("over budget", payload())
    assert len(ledger.data["calls"]) == 1


def test_unknown_charge_is_reserved_and_same_payload_cannot_be_rebought(tmp_path):
    import hashlib
    import json

    from studgroup.ai import ProviderFailure

    ledger = check.Ledger(tmp_path / "ledger.json")
    fingerprint = hashlib.sha256(json.dumps(payload(), sort_keys=True).encode()).hexdigest()
    ledger.data["calls"] = [
        {"state": "unknown", "reservation_usd": ".02", "request_sha256": fingerprint}
    ]
    with pytest.raises(ProviderFailure) as error:
        ledger.reserve("retry", payload())
    assert error.value.code == "synthetic_ambiguous_attempt_held"
    other = payload()
    other["messages"][0]["content"][0]["text"] = "Другое изображение"
    assert ledger.reserve("different input", other)["state"] == "reserved"


def test_tariff_and_conservative_image_output_reserve():
    assert check.cost(
        {"prompt_tokens": 1000, "prompt_cache_hit_tokens": 500, "completion_tokens": 100}
    ) == Decimal(".000273")
    assert check.reservation(payload()) > Decimal(".0072")


def test_runner_keeps_failed_images_in_cplus_input_without_network(tmp_path, monkeypatch):
    import asyncio

    from studgroup.ai import ProviderFailure
    from studgroup.analysis.shadow import ShadowPassFailure

    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"data": [{"id": "deepseek-flash"}]}

    class Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def get(self, *args, **kwargs):
            return Response()

    async def failed(*args, **kwargs):
        raise ProviderFailure(
            "provider_unreachable", True, reservation_releasable=True, detail="ConnectTimeout"
        )

    seen = []

    async def core(provider, data, *args, **kwargs):
        seen.extend(data.image_evidence)
        assert all(image.state == "failed" for image in data.image_evidence)
        assert all(
            any(
                m.telegram_message_id == image.telegram_message_id and m.attachments
                for m in data.messages
            )
            for image in data.image_evidence
        )
        raise ShadowPassFailure("test_only", False)

    monkeypatch.setattr(check, "dotenv_values", lambda *args: {"DEEPSEEK_API_KEY": "test-only"})
    monkeypatch.setattr(check.httpx, "AsyncClient", Client)
    monkeypatch.setattr(check, "read_pass", failed)
    monkeypatch.setattr(check, "run_shadow", core)
    path = tmp_path / "ledger.json"
    asyncio.run(check.main(path))
    assert len(seen) == 16
    assert len(check.Ledger(path).data["scenarios"]) == 11


def test_explicit_synthetic_retest_never_releases_unknown_charge(tmp_path):
    import hashlib
    import json

    fingerprint = hashlib.sha256(json.dumps(payload(), sort_keys=True).encode()).hexdigest()
    ledger = check.Ledger(tmp_path / "ledger.json", retest_ambiguous=True)
    ledger.data["calls"] = [
        {"state": "unknown", "reservation_usd": ".999", "request_sha256": fingerprint}
    ]
    with pytest.raises(ValueError, match="total_budget_exceeded_before_request"):
        ledger.reserve("bounded synthetic retest", payload())
    assert ledger.data["calls"][0]["reservation_usd"] == ".999"


def test_owner_lower_limit_preserves_all_previous_charges(tmp_path):
    import json

    path = tmp_path / "ledger.json"
    path.write_text(
        json.dumps(
            {
                "limit_usd": "2",
                "calls": [
                    {"state": "completed", "reservation_usd": ".2", "cost_upper_usd": ".181507560"}
                ],
                "images": {},
                "scenarios": {},
            }
        )
    )
    ledger = check.Ledger(path)
    assert ledger.data["limit_usd"] == "1"
    assert ledger.data["calls"][0]["cost_upper_usd"] == ".181507560"
    assert ledger.data["limit_history"][0]["previous_usd"] == "2"


def test_pilot_funding_freezes_remaining_money_in_original_journal(tmp_path):
    path = tmp_path / "ledger.json"
    ledger = check.Ledger(path)
    ledger.data["calls"] = [
        {"state": "completed", "reservation_usd": ".2", "cost_upper_usd": ".181507560"}
    ]
    now = datetime.now(UTC)
    scope = {"group_id": "approved-group", "since": now, "until": now + timedelta(hours=48)}
    allocation = ledger.allocate_pilot(**scope)
    assert allocation["amount_usd"] == "0.81849244"
    assert check.Ledger(path).allocate_pilot(**scope) == allocation
    assert ledger.used() == 1
    with pytest.raises(ValueError, match="total_budget_exceeded_before_request"):
        check.Ledger(path).reserve("benchmarks cannot spend pilot money", payload())
    with pytest.raises(ValueError, match="pilot_funding_cannot_reset"):
        ledger.allocate_pilot(**(scope | {"group_id": "another-group"}))


def test_new_unknown_stops_every_payload_after_authorized_old_baseline(tmp_path):
    ledger = check.Ledger(tmp_path / "ledger.json", retest_ambiguous=True)
    ledger.data["calls"] = [{"state": "unknown", "reservation_usd": ".02"}]
    ledger.data["authorized_unknown_baseline"] = 1
    call = ledger.reserve("new", payload())
    call["state"] = "unknown"
    other = payload() | {"max_tokens": 3000}
    with pytest.raises(ValueError, match="new_unknown_paid_attempt_stop"):
        ledger.reserve("even a different request", other)
