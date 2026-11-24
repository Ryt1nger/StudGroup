"""Reuse fresh, independently agreed common-class links inside one urgent run."""

import json
from collections import defaultdict

from sqlalchemy import select

from studgroup.models import (
    Membership,
    RanepaPersonalSnapshot,
    SubjectSplit,
    UrgentSDORun,
    UrgentSDOTarget,
)
from studgroup.ranepa_personal import _safe_link, _split_for_ranepa_subject, enrich_personal_links
from studgroup.ranepa_schedule import normalized


def lesson_key(row):
    return tuple(
        row.get(field) for field in ("day", "starts", "ends", "subject", "teacher", "room")
    )


def occurrence_identity(row):
    return (
        *lesson_key(row),
        normalized(row.get("lesson_type")),
        normalized(row.get("subgroup_hint")),
    )


def common_remote(row, splits):
    if row.get("room") != "СДО" or not row.get("teacher"):
        return False
    if any(_split_for_ranepa_subject([split], row["subject"]) for split in splits):
        return False
    # An explicitly named subgroup never becomes group-wide merely because
    # two students happened to select it. Official stream codes remain usable
    # only on an exactly matching occurrence, including the complete hint.
    hint = normalized(row.get("subgroup_hint"))
    return "подгрупп" not in hint and "ин.яз" not in hint


def agreed_links(target_rows, source_rows, splits, run_id):
    """At least two distinct successful sources, exact identity, no disagreement."""
    votes = defaultdict(dict)
    for user_id, rows in source_rows:
        for row in rows:
            url = row.get("online_url")
            if (
                common_remote(row, splits)
                and row.get("source_scope") not in {"common", "subgroup"}
                and row.get("direct_link_run") == str(run_id)
                and row.get("link_state") == "matched"
                and url
                and _safe_link(url)
            ):
                votes[occurrence_identity(row)].setdefault(user_id, set()).add(url)
    result = {}
    counts = defaultdict(int)
    for row in target_rows:
        counts[lesson_key(row)] += 1
    for row in target_rows:
        key = lesson_key(row)
        if counts[key] != 1 or not common_remote(row, splits):
            continue
        evidence = votes.get(occurrence_identity(row), {})
        urls = {url for values in evidence.values() for url in values}
        if len(evidence) >= 2 and len(urls) == 1:
            result[key] = next(iter(urls))
    return result


async def active_series_for_job(db, job_id, attempt):
    return await db.scalar(
        select(UrgentSDOTarget.run_id)
        .join(UrgentSDORun, UrgentSDORun.id == UrgentSDOTarget.run_id)
        .where(
            UrgentSDORun.state == "running",
            UrgentSDOTarget.job_id == job_id,
            UrgentSDOTarget.state == "pending",
            UrgentSDOTarget.baseline_attempts < attempt,
        )
    )


async def reuse_common_links(db, group_id, user_id, week_start, run_id):
    """Use only successful peers of this run; inherited/cached links cannot vote."""
    own = await db.get(RanepaPersonalSnapshot, (group_id, user_id, week_start))
    if own is None or not own.source_complete:
        return {}
    sources = (
        await db.scalars(
            select(RanepaPersonalSnapshot)
            .join(
                UrgentSDOTarget,
                (UrgentSDOTarget.user_id == RanepaPersonalSnapshot.user_id)
                & (UrgentSDOTarget.group_id == RanepaPersonalSnapshot.group_id),
            )
            .join(
                Membership,
                (Membership.user_id == RanepaPersonalSnapshot.user_id)
                & (Membership.group_id == RanepaPersonalSnapshot.group_id),
            )
            .where(
                UrgentSDOTarget.run_id == run_id,
                UrgentSDOTarget.state == "succeeded",
                Membership.status == "active",
                RanepaPersonalSnapshot.group_id == group_id,
                RanepaPersonalSnapshot.week_start == week_start,
                RanepaPersonalSnapshot.user_id != user_id,
                RanepaPersonalSnapshot.identity_verified.is_(True),
                RanepaPersonalSnapshot.source_complete.is_(True),
            )
        )
    ).all()
    if len(sources) < 2:
        return {}
    splits = (await db.scalars(select(SubjectSplit).where(SubjectSplit.group_id == group_id))).all()
    links = agreed_links(
        json.loads(own.rows_json),
        [(source.user_id, json.loads(source.rows_json)) for source in sources],
        splits,
        run_id,
    )
    if links:
        await enrich_personal_links(
            db,
            group_id,
            user_id,
            week_start,
            links=links,
            peer_reused=True,
        )
    return links
