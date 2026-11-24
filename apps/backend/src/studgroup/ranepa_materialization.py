"""Transaction-scoped, proven delivery of verified university timetable facts.

A complete account-local source week replaces only rows owned by that source
and week. Personal lessons and independently verified peers always win.
One receipt per recipient/source/week is committed atomically with the rows.
"""

import json
from datetime import UTC, datetime, timedelta
from hashlib import sha256

from sqlalchemy import select

from studgroup.models import (
    Membership,
    MemberSubgroup,
    RanepaMaterializationReceipt,
    RanepaPersonalSnapshot,
    SubjectSplit,
)
from studgroup.ranepa_personal import _safe_course, _safe_link, _split_for_ranepa_subject
from studgroup.ranepa_schedule import (
    has_unverified_subgroup_hint,
    normalized,
    subgroup_choice_changed_since_check,
)


def _occurrence(row):
    return (
        row["day"],
        row["starts"],
        row["ends"],
        normalized(row["subject"]),
        normalized(row.get("subgroup_hint")),
    )


def _shared_row(
    row,
    *,
    scope,
    source_user_id,
    week_start,
    source_snapshot_id,
    source_checked_at,
    materialized_at,
):
    """Copy verified facts, never provider credentials or unverified meeting URLs."""
    result = dict(row)
    # Inventories belong to the source account. Shared rows expose only the
    # chosen verified lesson link, never all private course meeting candidates.
    result.pop("link_candidates", None)
    result.pop("direct_link_run", None)
    if result.get("link_state") != "matched" or not (
        result.get("online_url") and _safe_link(result["online_url"])
    ):
        result["online_url"] = None
    if result.get("course_url") and not _safe_course(result["course_url"]):
        result["course_url"] = None
    result.update(
        source_scope=scope,
        source_user_id=str(source_user_id),
        source_week_start=week_start.isoformat(),
        source_snapshot_id=source_snapshot_id,
        source_checked_at=source_checked_at,
        materialized_at=materialized_at,
    )
    return result


def _preserve_verified_link(incoming, previous):
    """Keep a confirmed link only if the meeting identity has not changed."""
    if normalized(previous.get("teacher")) != normalized(incoming.get("teacher")) or normalized(
        previous.get("room")
    ) != normalized(incoming.get("room")):
        return
    if (
        previous.get("link_state") == "matched"
        and incoming.get("link_state") in {None, "not_checked"}
        and previous.get("online_url")
        and _safe_link(previous["online_url"])
        and not incoming.get("online_url")
    ):
        incoming["online_url"] = previous["online_url"]
        incoming["link_state"] = "matched"
        incoming["links_checked_at"] = previous.get("links_checked_at")
    if (
        previous.get("course_url")
        and _safe_course(previous["course_url"])
        and not incoming.get("course_url")
        and incoming.get("room") == "СДО"
    ):
        incoming["course_url"] = previous["course_url"]


async def materialize_verified_facts(db, group_id, source_user_id, week_start, *, checked_at=None):
    """Apply a whole source version to every compatible recipient in one transaction.

    The caller MUST commit only after a successful flush; rollback on any
    exception leaves previous recipients and all receipts unchanged.
    """
    source = await db.get(RanepaPersonalSnapshot, (group_id, source_user_id, week_start))
    if source is None or not source.identity_verified:
        raise ValueError("source_identity_unverified")
    if (
        not source.source_complete
        or not source.source_digest
        or source.week_end != week_start + timedelta(days=6)
    ):
        raise ValueError("source_week_not_complete")

    members = (
        await db.scalars(
            select(Membership).where(
                Membership.group_id == group_id,
                Membership.status == "active",
            )
        )
    ).all()
    by_user = {member.user_id: member for member in members}
    if source_user_id not in by_user:
        raise ValueError("source_membership_inactive")
    splits = (await db.scalars(select(SubjectSplit).where(SubjectSplit.group_id == group_id))).all()
    choices = (
        await db.scalars(
            select(MemberSubgroup).where(
                MemberSubgroup.membership_id.in_([member.id for member in members])
            )
        )
    ).all()
    by_member = {}
    choice_updated = {}
    for choice in choices:
        by_member.setdefault(choice.membership_id, {})[choice.split_id] = choice.subgroup_id
        choice_updated[(choice.membership_id, choice.split_id)] = choice.updated_at
    source_member_id = by_user[source_user_id].id
    source_choices = by_member.get(source_member_id, {})

    rows = json.loads(source.rows_json)
    if not isinstance(rows, list):
        raise TypeError("invalid_source_snapshot")
    now_dt = checked_at or datetime.now(UTC)
    now = now_dt.isoformat()
    source_at = source.checked_at.isoformat()
    prepared = []
    own_count = 0
    for row in rows:
        if not isinstance(row, dict):
            raise TypeError("invalid_source_lesson")
        # Never retransmit projected rows, even after a recipient's own read.
        if row.get("source_scope") in {"common", "subgroup"}:
            continue
        own_count += 1
        if not week_start <= datetime.fromisoformat(row["day"]).date() <= source.week_end:
            raise ValueError("source_lesson_outside_week")
        split = _split_for_ranepa_subject(splits, row["subject"])
        if split is None:
            if has_unverified_subgroup_hint(row.get("subgroup_hint")):
                continue
            prepared.append(
                (
                    None,
                    _shared_row(
                        row,
                        scope="common",
                        source_user_id=source_user_id,
                        week_start=week_start,
                        source_snapshot_id=source.source_digest,
                        source_checked_at=source_at,
                        materialized_at=now,
                    ),
                )
            )
        elif split.id in source_choices and not subgroup_choice_changed_since_check(
            choice_updated.get((source_member_id, split.id)), source.checked_at
        ):
            prepared.append(
                (
                    split.id,
                    {
                        **_shared_row(
                            row,
                            scope="subgroup",
                            source_user_id=source_user_id,
                            week_start=week_start,
                            source_snapshot_id=source.source_digest,
                            source_checked_at=source_at,
                            materialized_at=now,
                        ),
                        "source_split_id": str(split.id),
                        "source_subgroup_id": str(source_choices[split.id]),
                    },
                )
            )

    updated_users = 0
    inserted_lessons = 0
    source_id = str(source_user_id)
    for user_id in sorted(by_user, key=str):
        if user_id == source_user_id:
            continue
        member = by_user[user_id]
        target_choices = by_member.get(member.id, {})
        eligible = [
            row
            for split_id, row in prepared
            if split_id is None or target_choices.get(split_id) == source_choices.get(split_id)
        ]
        covered_subgroups = sorted(
            {
                (str(split_id), str(source_choices[split_id]))
                for split_id, _ in prepared
                if split_id is not None
                and target_choices.get(split_id) == source_choices.get(split_id)
            }
        )
        # Even if no rows remain, a full empty snapshot must retire this
        # source's old materialization. It must NOT create a coverage receipt.
        target = await db.get(
            RanepaPersonalSnapshot, (group_id, user_id, week_start), with_for_update=True
        )
        previous_rows = json.loads(target.rows_json) if target else []
        if not isinstance(previous_rows, list):
            raise TypeError("invalid_target_snapshot")
        retained = []
        old_by_occurrence = {}
        retired = False
        for old in previous_rows:
            if not isinstance(old, dict):
                raise TypeError("invalid_target_lesson")
            belongs = (
                old.get("source_scope") in {"common", "subgroup"}
                and old.get("source_user_id") == source_id
                and old.get("source_week_start") == week_start.isoformat()
            )
            if belongs:
                retired = True
                old_by_occurrence[_occurrence(old)] = old
            else:
                retained.append(old)

        existing_keys = {}
        for old in retained:
            existing_keys.setdefault(_occurrence(old), []).append(old)
        for incoming in eligible:
            found = existing_keys.get(_occurrence(incoming), [])
            if found:
                # Never overwrite a local or independent source; ambiguous
                # occurrences also remain untouched until manual review.
                continue
            projected = dict(incoming)
            previous = old_by_occurrence.get(_occurrence(projected))
            if previous:
                _preserve_verified_link(projected, previous)
            retained.append(projected)
            existing_keys.setdefault(_occurrence(projected), []).append(projected)
            if previous is None:
                inserted_lessons += 1

        receipt = await db.get(
            RanepaMaterializationReceipt,
            (group_id, user_id, week_start, source_user_id),
            with_for_update=True,
        )
        # If a complete source contains only unverified subgroup hints or is
        # empty, it cannot prove fresh coverage for a compatible account.
        has_evidence = own_count > 0 and bool(eligible)
        if receipt is not None and not has_evidence:
            await db.delete(receipt)
        if not (retired or eligible or receipt is not None or has_evidence):
            continue
        if target is None:
            target = RanepaPersonalSnapshot(
                group_id=group_id,
                user_id=user_id,
                week_start=week_start,
                week_end=source.week_end,
                identity_verified=False,
                source_complete=False,
                source_digest=None,
                checked_at=source.checked_at,
            )
            db.add(target)
        retained.sort(key=lambda row: (row["day"], row["starts"], normalized(row["subject"])))
        encoded = json.dumps(retained, ensure_ascii=False, separators=(",", ":"))
        target.rows_json = encoded
        target.data_digest = sha256(encoded.encode()).hexdigest()
        if has_evidence:
            if receipt is None:
                receipt = RanepaMaterializationReceipt(
                    group_id=group_id,
                    recipient_user_id=user_id,
                    week_start=week_start,
                    source_user_id=source_user_id,
                )
                db.add(receipt)
            receipt.source_digest = source.source_digest
            receipt.source_checked_at = source.checked_at
            receipt.applied_at = now_dt
            receipt.coverage_json = json.dumps(
                {
                    "common": any(split_id is None for split_id, _ in prepared),
                    "subgroups": covered_subgroups,
                },
                separators=(",", ":"),
            )
        updated_users += 1

    await db.flush()
    return {"users_materialized": updated_users, "lessons_added": inserted_lessons}
