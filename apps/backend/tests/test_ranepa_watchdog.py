"""The watchdog only observes scheduled workflow metadata and deduplicates owner alerts."""

from datetime import UTC, datetime, timedelta

from studgroup import ranepa_watchdog as monitor

NOW = datetime(2026, 11, 16, 14, tzinfo=UTC)


def run(at, event="schedule"):
    return {"created_at": at.isoformat().replace("+00:00", "Z"), "event": event}


def test_schedule_health_ignores_push_and_manual_run():
    old = NOW - timedelta(hours=3)
    manual = NOW - timedelta(minutes=5)
    assert monitor.is_missing_schedule(
        [run(old), run(manual, "push"), run(manual, "workflow_dispatch")], NOW
    ) == (True, old)
    assert monitor.is_missing_schedule([run(NOW - timedelta(minutes=35))], NOW)[0] is False


def test_no_scheduled_runs_is_missing():
    assert monitor.is_missing_schedule([], NOW) == (True, None)


def test_deduplicated_issue_creation_and_recovery(monkeypatch):
    state = {"runs": [run(NOW - timedelta(hours=4))], "issues": []}
    writes = []

    def fake(method, path, payload=None):
        if path.startswith("actions/"):
            return {"workflow_runs": state["runs"]}
        if method == "GET" and path.startswith("issues?"):
            return list(state["issues"])
        writes.append((method, path, payload))
        if method == "POST":
            state["issues"] = [{"title": monitor.ISSUE_TITLE, "number": 17}]
        elif method == "PATCH":
            state["issues"] = []
        return {}

    monkeypatch.setattr(monitor, "github_api", fake)
    assert monitor.check_once(NOW) == "missing"
    assert len(writes) == 1
    assert writes[0][0] == "POST"
    assert monitor.check_once(NOW) == "missing"
    assert len(writes) == 1  # do not spam a new issue every hour
    state["runs"] = [run(NOW - timedelta(minutes=20))]
    assert monitor.check_once(NOW) == "healthy"
    assert writes[-1] == ("PATCH", "issues/17", {"state": "closed"})


def test_deduplication_finds_existing_issue_beyond_first_page(monkeypatch):
    """A busy repository must never open a duplicate watchdog issue."""

    requests = []

    def fake(method, path, payload=None):
        requests.append((method, path))
        if path.startswith("actions/"):
            return {"workflow_runs": [run(NOW - timedelta(hours=4))]}
        if path == "issues?state=open&per_page=100":
            return [{"number": index, "title": "Other"} for index in range(100)]
        if path == "issues?state=open&per_page=100&page=2":
            return [{"number": 999, "title": monitor.ISSUE_TITLE}]
        raise AssertionError("unexpected_write_or_page")

    monkeypatch.setattr(monitor, "github_api", fake)
    assert monitor.check_once(NOW) == "missing"
    assert requests[-1] == ("GET", "issues?state=open&per_page=100&page=2")
    assert all(method == "GET" for method, _ in requests)
