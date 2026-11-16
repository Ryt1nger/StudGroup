"""Reliability admin stats contain no private student data or false CAPTCHA success."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from studgroup.analysis.reliability_summary import summarize_product_reliability


def obj(**kwargs):
    return SimpleNamespace(**kwargs)


def test_aggregates_product_and_sdo_outcomes_without_identifiers():
    now = datetime(2026, 11, 16, 12, tzinfo=UTC)
    jobs = [
        obj(state="covered", attempts=0, retry_reason="covered_by_peers", failure_code=None),
        obj(state="failed", attempts=1, retry_reason="captcha", failure_code="captcha"),
    ]
    snapshots = [
        obj(
            identity_verified=True, checked_at=now - timedelta(hours=2),
            rows_json='[{"room":"СДО","link_state":"ambiguous","source_scope":"common"}]',
        ),
        obj(
            identity_verified=False, checked_at=now - timedelta(hours=25),
            rows_json='[{"room":"СДО","link_state":"matched","online_url":"https://lms.ranepa.ru/mod/url/view.php?id=777"}]',
        ),
    ]
    observations = [
        obj(captcha_seen=True, result_code="captcha"),
        obj(captcha_seen=True, result_code="succeeded"),
    ]
    runs = [
        obj(state="completed", metrics_json='{"product_outcome_counts":{"no_academic_signal":2},"raw_proposal_count":0}'),
        obj(state="completed", metrics_json='{"raw_proposal_count":2,"accepted_items":0}'),
    ]
    result = summarize_product_reliability(jobs, snapshots, observations, runs, now)
    assert result["sdo"]["covered_without_scan"] == 1
    assert result["sdo"]["captcha_seen"] == 2
    assert result["sdo"]["captcha_verified_recovered"] == 1
    assert result["sdo"]["link_states"] == {"ambiguous": 1, "matched": 1}
    assert result["ai"]["product_outcomes"] == {"no_academic_signal": 2}
    assert result["ai"]["legacy_without_product_outcomes"] == 1
    assert result["ai"]["rejection_only_packages"] == 1
    assert result["ai"]["zero_proposal_packages"] == 1
    assert "online_url" not in str(result)
    assert "777" not in str(result)
