"""Re-run actual saved C+ replies through today's validator; no HTTP or fresh AI budget."""

import asyncio
import importlib.util
import json
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


def test_recorded_complex_provider_results_keep_card_count_dates_audiences_and_pages(
    tmp_path, monkeypatch
):
    root = Path(__file__).parents[1]
    fixtures = root / "tests/fixtures/ai_analysis"
    old = json.loads((fixtures / "image-provider-observations.json").read_text())
    recorded = json.loads((fixtures / "image-complex-observations.json").read_text())
    gold = json.loads((fixtures / "image-complex-manifest.json").read_text())
    calls = old["calls"] + recorded["calls"]
    journal = tmp_path / "offline.json"
    journal.write_text(
        json.dumps(
            {
                "limit_usd": "1",
                "calls": calls,
                "authorized_unknown_baseline": 278,
                "complex_runs": recorded["runs"],
            }
        )
    )
    monkeypatch.setenv("DEEPSEEK_API_KEY", "offline-only-never-sent")
    location = root / "tools/complex_vision_check.py"
    sys.path.insert(0, str(location.parent))
    try:
        spec = importlib.util.spec_from_file_location("complex_replay", location)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        asyncio.run(module.main(journal, offline=True, validation_revision="regression"))
    finally:
        sys.path.pop(0)
    result = json.loads(journal.read_text())
    assert len(result["calls"]) == len(calls)  # exact recorded responses or fail closed, never HTTP
    run = next(v for k, v in result["complex_revalidations"].items() if k.endswith("/regression"))
    for scenario in gold["scenarios"]:
        output = run["scenarios"][scenario["id"]]
        assert "error" not in output, (scenario["id"], output)
        cards = output["validation"]["accepted"]
        assert len(cards) == len(scenario["gold"]["cards"]), scenario["id"]
        for actual, expected in zip(cards, scenario["gold"]["cards"]):
            assert actual["semantic_kind"] == expected["kind"]
            assert actual["subject"] == expected["subject"]
            assert actual["audience"]["scope"] == scenario["gold"]["audience"]
            deadline = datetime.fromisoformat(actual["deadline"]["at"]).astimezone(
                ZoneInfo("Europe/Moscow")
            )
            if "T" in expected["deadline"]:
                assert deadline == datetime.fromisoformat(expected["deadline"])
            else:
                assert deadline.date().isoformat() == expected["deadline"]
            text = (actual["title"] + " " + actual["description"]).casefold().replace("ё", "е")
            assert all(
                term.casefold().replace("ё", "е") in text for term in expected["required_terms"]
            )
    assert run["scenarios"]["three-overlapping-crops"]["selected_by_card"] == [["c15c"]]
    assert run["scenarios"]["four-distinct-pages"]["selected_by_card"] == [
        ["c161", "c162", "c163", "c164"]
    ]
