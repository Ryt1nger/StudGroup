"""Aggregated admin-only product reliability, no identities, text or secrets."""

import json
from collections import Counter
from datetime import UTC, timedelta

from sqlalchemy import and_, select

from studgroup.models import (
    Membership,
    RanepaCrawlJob,
    RanepaCrawlObservation,
    RanepaPersonalSnapshot,
    ShadowEvaluationRun,
)

SLA = timedelta(hours=6)


def _aware(value):
    if value is not None and value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def summarize_product_reliability(jobs, snapshots, observations, runs, now):
    """Return only counts and UTC instants from authenticated admin source rows."""
    queue = Counter(row.state for row in jobs)
    failure_reasons = Counter(
        row.retry_reason or row.failure_code
        for row in jobs
        if row.retry_reason or row.failure_code
    )
    links = Counter()
    timetable_only = enriched = fresh = stale = verified = materialized = 0
    latest_source = None
    for snapshot in snapshots:
        if snapshot.identity_verified:
            verified += 1
            if _aware(snapshot.checked_at) >= now - SLA:
                fresh += 1
            else:
                stale += 1
            checked = _aware(snapshot.checked_at)
            if latest_source is None or checked > latest_source:
                latest_source = checked
        rows = json.loads(snapshot.rows_json)
        if any(row.get("source_scope") in {"common", "subgroup"} for row in rows):
            materialized += 1
        sdo_rows = [row for row in rows if row.get("room") == "СДО"]
        for row in sdo_rows:
            links[row.get("link_state") or "not_checked"] += 1
        if rows and any(row.get("online_url") or row.get("course_url") for row in rows):
            enriched += 1
        elif rows:
            timetable_only += 1

    captcha_seen = sum(bool(row.captcha_seen) for row in observations)
    captcha_recovered = sum(
        bool(row.captcha_seen) and row.result_code == "succeeded"
        for row in observations
    )

    outcomes = Counter()
    zero_proposal = rejected_only = legacy_unknown = failed_packages = 0
    for run in runs:
        if run.state == "failed":
            failed_packages += 1
        metrics = json.loads(run.metrics_json or "{}")
        product = metrics.get("product_outcome_counts")
        if isinstance(product, dict):
            for kind, count in product.items():
                if kind in {
                    "published", "updated", "no_academic_signal", "rejected",
                    "blocked_by_image", "needs_review", "failed",
                } and isinstance(count, int) and count >= 0:
                    outcomes[kind] += count
        else:
            legacy_unknown += 1
        if run.state == "completed" and metrics.get("raw_proposal_count") == 0:
            zero_proposal += 1
        if (
            run.state == "completed"
            and metrics.get("raw_proposal_count", 0) > 0
            and metrics.get("accepted_items") == 0
        ):
            rejected_only += 1

    return {
        "sdo": {
            "queue": dict(sorted(queue.items())),
            "retry_reasons": dict(sorted(failure_reasons.items())),
            "covered_without_scan": sum(
                row.state == "covered" and row.attempts == 0 for row in jobs
            ),
            "source_verified_weeks": verified,
            "source_fresh_under_6h": fresh,
            "source_stale_6h_plus": stale,
            "source_latest_at": latest_source,
            "materialized_student_weeks": materialized,
            "timetable_only_weeks": timetable_only,
            "enriched_weeks": enriched,
            "link_states": dict(sorted(links.items())),
            "captcha_seen": captcha_seen,
            "captcha_verified_recovered": captcha_recovered,
        },
        "ai": {
            "product_outcomes": dict(sorted(outcomes.items())),
            "zero_proposal_packages": zero_proposal,
            "rejection_only_packages": rejected_only,
            "legacy_without_product_outcomes": legacy_unknown,
            "failed_packages": failed_packages,
        },
    }


async def load_product_reliability(db, group_id, since, now):
    """Read a bounded recent window; group scope is applied to every relation."""
    jobs_query = select(RanepaCrawlJob).where(RanepaCrawlJob.created_at >= since)
    snapshots_query = (
        select(RanepaPersonalSnapshot)
        .join(
            Membership,
            and_(
                Membership.group_id == RanepaPersonalSnapshot.group_id,
                Membership.user_id == RanepaPersonalSnapshot.user_id,
                Membership.status == "active",
            ),
        )
        .where(RanepaPersonalSnapshot.week_start >= (now.date() - timedelta(days=14)))
    )
    observations_query = (
        select(RanepaCrawlObservation)
        .join(RanepaCrawlJob, RanepaCrawlObservation.job_id == RanepaCrawlJob.id)
        .where(RanepaCrawlObservation.observed_at >= since)
    )
    runs_query = select(ShadowEvaluationRun).where(
        ShadowEvaluationRun.created_at >= since,
        ShadowEvaluationRun.state.in_(("completed", "failed")),
    )
    if group_id:
        jobs_query = jobs_query.where(RanepaCrawlJob.group_id == group_id)
        snapshots_query = snapshots_query.where(RanepaPersonalSnapshot.group_id == group_id)
        observations_query = observations_query.where(RanepaCrawlJob.group_id == group_id)
        runs_query = runs_query.where(ShadowEvaluationRun.group_id == group_id)
    jobs = (await db.scalars(jobs_query.limit(5000))).all()
    snapshots = (await db.scalars(snapshots_query.limit(5000))).all()
    observations = (await db.scalars(observations_query.limit(5000))).all()
    runs = (await db.scalars(runs_query.limit(5000))).all()
    return summarize_product_reliability(jobs, snapshots, observations, runs, now)
