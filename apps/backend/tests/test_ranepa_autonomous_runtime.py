import os
from time import time

from studgroup.main import Settings
from studgroup.ranepa_worker import autonomous_captcha_budget
from studgroup.ranepa_worker_health import healthy, heartbeat


def test_autonomous_captcha_budget_respects_both_caps():
    settings = Settings(ranepa_captcha_max_tasks_per_run=6, ranepa_captcha_daily_max_tasks=2)
    assert autonomous_captcha_budget(settings).remaining == 2
    settings.ranepa_captcha_daily_max_tasks = 0
    assert autonomous_captcha_budget(settings).remaining == 0


def test_container_heartbeat(tmp_path, monkeypatch):
    path = tmp_path / "heartbeat"
    monkeypatch.setenv("RANEPA_WORKER_HEARTBEAT_FILE", str(path))
    assert not healthy()
    heartbeat()
    assert healthy()
    old = time() - 1300
    os.utime(path, (old, old))
    assert not healthy()


def test_browser_failure_diagnostics_are_closed_vocabulary():
    from studgroup.ranepa_browser_health import browser_failure_reason

    assert (
        browser_failure_reason(RuntimeError("Executable doesn't exist at /private"))
        == "binary_missing"
    )
    assert browser_failure_reason(RuntimeError("No space left on device")) == "shared_memory_or_disk"
    assert browser_failure_reason(RuntimeError("Cannot allocate memory")) == "memory_exhausted"
    assert browser_failure_reason(RuntimeError("private URL and credentials")) == "unknown"
