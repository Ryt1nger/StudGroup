import pytest

from studgroup.two_pass_probe import Signals, chunks, contexts


def rows(count=30):
    return [{"message_id": i, "text": "fragment", "reply_to": None} for i in range(count)]


def test_weak_signal_keeps_neighborhood_and_reply_parent():
    messages = rows()
    messages[15]["reply_to"] = 1
    selected = {row["message_id"] for batch in contexts(messages, [15]) for row in batch}
    assert set(range(7, 24)) <= selected
    assert 1 in selected


def test_quiet_screen_has_no_deep_calls():
    assert list(contexts(rows(), [])) == []


def test_chunks_cover_input_without_omission():
    messages = rows(140)
    assert [row for batch in chunks(messages) for row in batch] == messages
    assert all(len(batch) <= 60 for batch in chunks(messages))


def test_signal_schema_does_not_accept_strings():
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Signals.model_validate({"message_ids": ["123"]})


def test_probe_runs_two_passes_and_preserves_source_dates(monkeypatch, tmp_path):
    import asyncio
    import json
    from datetime import UTC, datetime

    from studgroup import two_pass_probe
    from studgroup.ai import BatchExtraction, BatchResult, TokenUsage
    from studgroup.chat_export import ExportMessage
    from studgroup.main import Settings

    stamp = datetime(2026, 10, 8, 12, tzinfo=UTC)
    monkeypatch.setattr(two_pass_probe, "Settings", lambda: Settings(_env_file=None))
    monkeypatch.setattr(
        two_pass_probe,
        "read_html_export",
        lambda *a, **k: [ExportMessage(1, stamp, "#матан 12–14", None, False)],
    )
    archive = tmp_path / "export.zip"
    archive.write_bytes(b"fixture")
    usage = TokenUsage(prompt_tokens=20, completion_tokens=10)

    class Provider:
        model = "deepseek-flash"

        def __init__(self):
            self.calls = []

        async def _complete(self, payload):
            assert "json" in payload["messages"][0]["content"].lower()
            self.calls.append("screen")
            return {
                "choices": [{"message": {"content": json.dumps({"message_ids": [1]})}}],
                "usage": usage.model_dump(),
            }

        async def extract_batch(self, messages, timezone):
            assert messages[0]["message_date"] == stamp.isoformat()
            self.calls.append("deep")
            return BatchResult(batch=BatchExtraction(assignments=[]), usage=usage, model=self.model)

    provider = Provider()
    report = asyncio.run(
        two_pass_probe.run(archive, tmp_path / "report.json", stamp, 0.08, provider)
    )
    assert report["complete"]
    assert provider.calls == ["screen", "deep"]
    assert report["signals"] == [1]
    assert report["budget_usd"] == 0.08
    assert report["actual_peak_usd"] < 0.08


@pytest.mark.parametrize("releasable", [True, False])
def test_failed_deep_call_preserves_screen_charge_and_settles_only_its_reserve(
    monkeypatch, tmp_path, releasable
):
    import asyncio
    import json
    from datetime import UTC, datetime

    from studgroup import two_pass_probe
    from studgroup.ai import ProviderFailure, TokenUsage
    from studgroup.chat_export import ExportMessage
    from studgroup.main import Settings

    stamp = datetime(2026, 10, 8, 12, tzinfo=UTC)
    monkeypatch.setattr(two_pass_probe, "Settings", lambda: Settings(_env_file=None))
    monkeypatch.setattr(
        two_pass_probe,
        "read_html_export",
        lambda *a, **k: [ExportMessage(1, stamp, "ДЗ", None, False)],
    )
    archive = tmp_path / "archive.zip"
    archive.write_bytes(b"fixture")

    class Provider:
        model = "deepseek-flash"

        async def _complete(self, payload):
            return {
                "choices": [{"message": {"content": json.dumps({"message_ids": [1]})}}],
                "usage": TokenUsage(prompt_tokens=20, completion_tokens=10).model_dump(),
            }

        async def extract_batch(self, *args):
            raise ProviderFailure("provider_error", False, reservation_releasable=releasable)

    report = asyncio.run(
        two_pass_probe.run(archive, tmp_path / "report.json", stamp, 0.08, Provider())
    )
    assert not report["complete"]
    assert report["actual_peak_usd"] > 0
    if releasable:
        assert report["reserved_usd"] == pytest.approx(report["actual_peak_usd"])
        assert len(report["released_reservations"]) == 1
    else:
        assert report["reserved_usd"] > report["actual_peak_usd"]
        assert "released_reservations" not in report


def recovery_fixture(monkeypatch, tmp_path, count=65):
    from datetime import UTC, datetime

    from studgroup import two_pass_probe
    from studgroup.chat_export import ExportMessage
    from studgroup.main import Settings

    stamp = datetime(2026, 10, 8, 12, tzinfo=UTC)
    monkeypatch.setattr(two_pass_probe, "Settings", lambda: Settings(_env_file=None))
    monkeypatch.setattr(
        two_pass_probe,
        "read_html_export",
        lambda *a, **k: [ExportMessage(i, stamp, "ДЗ", None, False) for i in range(1, count + 1)],
    )
    archive = tmp_path / "archive.zip"
    archive.write_bytes(b"fixture")
    return archive, stamp


def test_automatic_recovery_does_not_repeat_screen_or_completed_deep_fragments(
    monkeypatch, tmp_path
):
    import asyncio
    import json
    from collections import Counter

    from studgroup.ai import BatchExtraction, BatchResult, ProviderFailure, TokenUsage
    from studgroup.two_pass_probe import run_until_complete

    archive, stamp = recovery_fixture(monkeypatch, tmp_path)
    usage = TokenUsage(prompt_tokens=20, completion_tokens=10)

    class Provider:
        model = "deepseek-flash"

        def __init__(self):
            self.screen = 0
            self.deep = Counter()

        async def _complete(self, payload):
            self.screen += 1
            batch = json.loads(payload["messages"][1]["content"])
            return {
                "choices": [
                    {"message": {"content": json.dumps({"message_ids": [batch[0]["message_id"]]})}}
                ],
                "usage": usage.model_dump(),
            }

        async def extract_batch(self, batch, timezone):
            key = batch[0]["message_id"]
            self.deep[key] += 1
            if key > 1 and self.deep[key] <= 2:
                raise ProviderFailure("provider_unreachable", True, reservation_releasable=True)
            return BatchResult(batch=BatchExtraction(assignments=[]), usage=usage, model=self.model)

    waits = []

    async def wait(delay):
        waits.append(delay)

    provider = Provider()
    report = asyncio.run(
        run_until_complete(archive, tmp_path / "report.json", stamp, 0.08, provider, wait)
    )
    assert report["complete"]
    assert provider.screen == 2
    assert provider.deep[1] == 1
    assert list(provider.deep.values()) == [1, 3]
    assert len(waits) == 2
    # Reopening an already completed report is free and idempotent.
    asyncio.run(run_until_complete(archive, tmp_path / "report.json", stamp, 0.08, provider, wait))
    assert provider.screen == 2
    assert list(provider.deep.values()) == [1, 3]


def test_crash_after_saved_response_reuses_it_without_paid_replay(monkeypatch, tmp_path):
    import asyncio
    import json

    from studgroup.ai import BatchExtraction, BatchResult, TokenUsage
    from studgroup.two_pass_probe import run

    archive, stamp = recovery_fixture(monkeypatch, tmp_path, count=1)
    usage = TokenUsage(prompt_tokens=20, completion_tokens=10)

    class Provider:
        model = "deepseek-flash"
        calls = 0

        async def _complete(self, payload):
            self.calls += 1
            return {
                "choices": [{"message": {"content": json.dumps({"message_ids": [1]})}}],
                "usage": usage.model_dump(),
            }

        async def extract_batch(self, *args):
            return BatchResult(batch=BatchExtraction(assignments=[]), usage=usage, model=self.model)

    original = Signals.model_validate_json

    def crash(*args):
        raise KeyboardInterrupt

    monkeypatch.setattr(Signals, "model_validate_json", crash)
    provider = Provider()
    with pytest.raises(KeyboardInterrupt):
        asyncio.run(run(archive, tmp_path / "report.json", stamp, 0.08, provider))
    monkeypatch.setattr(Signals, "model_validate_json", original)
    report = asyncio.run(run(archive, tmp_path / "report.json", stamp, 0.08, provider, resume=True))
    assert report["complete"]
    assert provider.calls == 1
    assert len([c for c in report["calls"] if c["stage"] == "screen"]) == 1
