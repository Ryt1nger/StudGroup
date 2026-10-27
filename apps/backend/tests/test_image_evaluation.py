import json
from pathlib import Path

import pytest
from PIL import ImageFont

from studgroup.analysis.image_evaluation import compare, evaluate, render

GOLD = json.loads((Path(__file__).parent / "fixtures/ai_analysis/image-scenarios.json").read_text())


def run(kind="synthetic_stub", fingerprint="0" * 64):
    return {
        "input_sha256": fingerprint,
        "evidence_kind": kind,
        "cases": [
            {
                "case_id": "in_class",
                "image_states": {"in_class:0": "ready"},
                "semantic_types": ["in_class_task"],
                "homework_count": 0,
                "cost_usd": "0.000342",
                "latency_ms": [10, 20],
            }
        ],
    }


def test_missing_measurements_stay_unmeasured():
    report = evaluate(GOLD)
    assert report["case_count"] == 11 and report["total_images"] == 16
    assert report["fact_precision"] is None and report["reported_cost_usd"] is None
    assert report["real_model_quality"].startswith("UNMEASURED")


def test_stub_statistics_never_claim_real_model_quality():
    report = evaluate(GOLD, run())
    assert report["image_completed_coverage"] == 1 / 16
    assert report["classification_accuracy"] == 1
    assert report["median_latency_ms"] == 15
    assert report["real_model_quality"].startswith("UNMEASURED")
    assert report["fact_precision"] is None


def test_comparison_requires_same_input_and_rejects_wrong_image_identity():
    assert compare(GOLD, run(), run())["delta"]["classification_accuracy"] == 0
    with pytest.raises(ValueError, match="identical_input"):
        compare(GOLD, run(), run(fingerprint="1" * 64))
    bad = run()
    bad["cases"][0]["image_states"] = {"poster:0": "ready"}
    with pytest.raises(ValueError, match="image_mismatch"):
        evaluate(GOLD, bad)


def test_renderer_creates_hashed_pixels_without_any_provider(tmp_path, monkeypatch):
    # The production command takes an installed Unicode font; portable unit uses Pillow's
    # built-in font so CI never depends on an OS font download.
    default = ImageFont.load_default()
    monkeypatch.setattr(ImageFont, "truetype", lambda *args, **kwargs: default)
    manifest = render(GOLD, tmp_path, "unused")
    assert len(manifest["images"]) == 16
    assert all(
        (tmp_path / row["path"]).read_bytes().startswith(b"\x89PNG") for row in manifest["images"]
    )
    assert len(manifest["input_sha256"]) == 64


pytest_plugins = ["test_schedule_api"]


def test_diagnostics_are_scoped_private_and_disabled_by_default(client, monkeypatch):
    from test_image_jobs import cfg, download_ok, reading, tick
    from test_package_worker import make_new
    from test_photo_reading import VisionProvider, photo_message
    from test_staff_api import AUTH, make

    download_ok(monkeypatch)
    photo_message(client)
    make_new(client)
    config = cfg(client)
    provider = VisionProvider(reading())
    assert tick(client, provider, config) == "ready"
    assert client.get("/v1/staff/image-findings/images", headers=AUTH).status_code == 403
    make(client, "headman")
    assert client.get("/v1/staff/image-findings/images", headers=AUTH).status_code == 404
    client.app.state.settings.image_diagnostics_enabled = True
    client.app.state.settings.image_analysis_groups = config.image_analysis_groups
    result = client.get("/v1/staff/image-findings/images", headers=AUTH)
    assert result.status_code == 200
    data = result.json()
    assert data["calls"] == 1 and data["quality"] == "UNMEASURED"
    assert "image_data_url" not in result.text and "result_json" not in result.text
