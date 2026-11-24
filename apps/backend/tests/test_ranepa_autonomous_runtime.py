from pathlib import Path
from time import time

from studgroup.main import Settings
from studgroup.ranepa_worker import autonomous_captcha_budget
from studgroup.ranepa_worker_health import heartbeat, healthy


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
    import os

    os.utime(Path(path), (old, old))
    assert not healthy()
