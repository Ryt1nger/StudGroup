"""Keep unrelated historical tests on the legacy, manually funded AI configuration.

The production owner-funded pilot defaults to on. Tests that exercise it must opt in
explicitly so an old worker test does not start newly eligible paid jobs by accident.
"""

import pytest


@pytest.fixture(autouse=True)
def disable_owner_funded_pilot_for_legacy_tests(monkeypatch):
    monkeypatch.setenv("OWNER_FUNDED_PILOT", "false")
