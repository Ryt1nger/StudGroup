"""Frozen gold is verified without spending tokens or using candidate replies as labels."""

import importlib.util
from pathlib import Path


def test_complex_corpus_has_frozen_pixels_and_independent_complete_annotations():
    location = Path(__file__).parents[1] / "tools/complex_vision_check.py"
    import sys

    sys.path.insert(0, str(location.parent))
    try:
        spec = importlib.util.spec_from_file_location("complex_check", location)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        manifest = module.snapshot()
    finally:
        sys.path.pop(0)
    assert len(manifest["images"]) == 23
    assert len(manifest["scenarios"]) == 16
    ids = {image["id"] for image in manifest["images"]}
    for scenario in manifest["scenarios"]:
        assert set(scenario["images"]) <= ids
        assert set(scenario["gold"]) == {
            "facts",
            "categories",
            "actions",
            "audience",
            "dates",
            "numbers",
            "cards",
            "display",
            "notes",
        }
        assert set(scenario["gold"]["display"]) <= set(scenario["images"])
        assert scenario["gold"]["facts"]
    for image in manifest["images"][:5]:
        assert image["author"] and image["source_page"]
        assert image["license"].startswith("https://creativecommons.org/")
