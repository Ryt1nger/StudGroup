"""Read-only GitHub scheduled-run watchdog; alerts via a single deduplicated issue.

This never starts account crawls, reads personal university accounts, or spends RuCaptcha
credits. Running another GitHub schedule is best-effort, not an external SLA guarantee.
"""

import json
import os
from datetime import UTC, datetime, timedelta
from urllib.request import Request, urlopen

ISSUE_TITLE = "StudGroup: RANEPA scheduled crawl has missed its launch window"
STALENESS = timedelta(hours=2)
WORKFLOW = "ranepa-crawl.yml"


def latest_scheduled_at(runs):
    """Only scheduled launches count; manual and ordinary main pushes cannot mask a missed cron."""
    stamps = []
    for run in runs:
        if run.get("event") != "schedule" or not run.get("created_at"):
            continue
        stamps.append(datetime.fromisoformat(run["created_at"]))
    return max(stamps, default=None)


def is_missing_schedule(runs, now):
    latest = latest_scheduled_at(runs)
    if latest is None:
        return True, None
    if latest.tzinfo is None:
        raise ValueError("schedule_timestamp_must_have_timezone")
    return now.astimezone(UTC) - latest.astimezone(UTC) > STALENESS, latest


def github_api(method, path, payload=None):
    """GitHub metadata and issue-only API; no student data, database or crawler secrets."""
    repository = os.environ["GITHUB_REPOSITORY"]
    token = os.environ["GITHUB_TOKEN"]
    if repository != "Ryt1nger/StudGroup":
        raise RuntimeError("watchdog_wrong_repository")
    body = None if payload is None else json.dumps(payload).encode()
    req = Request(
        "https://api.github.com/repos/" + repository + "/" + path,
        data=body,
        method=method,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": "Bearer " + token,
            "X-GitHub-Api-Version": "2022-11-28",
            **({"Content-Type": "application/json"} if body is not None else {}),
        },
    )
    with urlopen(req, timeout=20) as response:
        data = response.read()
        return json.loads(data) if data else {}


def check_once(now=None):
    """Report missing runs through at most one open issue; auto-close on recovery."""
    now = now or datetime.now(UTC)
    data = github_api(
        "GET",
        "actions/workflows/" + WORKFLOW + "/runs?event=schedule&branch=main&per_page=30",
    )
    runs = data.get("workflow_runs")
    if not isinstance(runs, list):
        raise TypeError("missing_workflow_runs")
    missing, last_launch = is_missing_schedule(runs, now)
    issues = github_api("GET", "issues?state=open&per_page=100")
    if not isinstance(issues, list):
        raise TypeError("missing_open_issues")
    existing = next(
        (issue for issue in issues if issue.get("title") == ISSUE_TITLE and "pull_request" not in issue),
        None,
    )
    if missing and existing is None:
        last = last_launch.isoformat() if last_launch else "none"
        github_api(
            "POST",
            "issues",
            {
                "title": ISSUE_TITLE,
                "body": (
                    "The scheduled RANEPA crawl workflow has no scheduled GitHub Actions "
                    "launch within two hours. Latest launch (UTC): "
                    + last
                    + ". Check Actions scheduling, runner failures, and explicit "
                    "owner authorization before any paid recovery. This watchdog "
                    "does not run or restart any crawler."
                ),
            },
        )
    if not missing and existing is not None:
        github_api("PATCH", "issues/" + str(existing["number"]), {"state": "closed"})
    return "missing" if missing else "healthy"


def main():
    status = check_once()
    print("RANEPA_SCHEDULE_WATCHDOG", status, flush=True)
    if status == "missing":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
