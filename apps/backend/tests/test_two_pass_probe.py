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
