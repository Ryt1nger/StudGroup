"""Regression guards for scheduled Render cutover workflows."""

from pathlib import Path

WORKFLOWS = Path(__file__).resolve().parents[3] / ".github" / "workflows"


def workflow(name: str) -> str:
    return (WORKFLOWS / name).read_text(encoding="utf-8")


def test_keepalive_routes_to_migrated_api_and_is_opt_in():
    data = workflow("keepalive.yml")
    assert "https://studgroup-api-v2.onrender.com" in data
    assert "studgroup-tyjh.onrender.com" not in data
    assert "STUDGROUP_KEEPALIVE_ENABLED" in data
    assert "github.event_name == 'workflow_dispatch'" in data
    assert "pilot-backfill-wake" not in data
    assert "  push:" not in data


def test_crawl_checks_schema_before_browser_and_student_access():
    data = workflow("ranepa-crawl.yml")
    assert "vars.RANEPA_SCHEDULE_ENABLED == 'true'" in data
    assert "https://studgroup-api-v2.onrender.com" in data
    assert "studgroup-tyjh.onrender.com" not in data
    assert "uv run alembic current" in data
    assert data.index("Require Alembic 0065") < data.index("Install browser")
    assert data.index("Require Alembic 0065") < data.index(
        "Process one bounded queue shift"
    )


def test_watchdog_only_checks_enabled_schedule():
    data = workflow("ranepa-watchdog.yml")
    assert "vars.RANEPA_SCHEDULE_ENABLED == 'true'" in data
    assert "python -m studgroup.ranepa_watchdog" in data
