"""Transaction-scoped verified group fact materialization.

Only active group members receive confirmed common lessons. Split-subject
lessons require the same explicit source and recipient subgroup membership.
No account-local credential/session data is read or copied.
"""

import json
import re
from datetime import UTC, datetime
from hashlib import sha256

from sqlalchemy import select

from studgroup.models import (
    Membership,
    MemberSubgroup,
    RanepaPersonalSnapshot,
    SubjectSplit,
)
from studgroup.ranepa_personal import _safe_course, _safe_link
from studgroup.ranepa_schedule import normalized


def _occurrence(row):
    return (
        row["day"],
        row["starts"],
        row["ends"],
        normalized(row["subject"]),
        normalized(row.get("subgroup_hint")),
    )


def _shared_row(row, *, scope, source_checked_at, materialized_at):
    # URLs are propagated only when canonical, non-credentialed and verified.
    # Course navigation remains distinct from meeting navigation.
    result = dict(row)
    if result.get("link_state") != "matched" or not (
        result.get("online_url") and _safe_link(result["online_url"])
    ):
        result["online_url"] = None
    if result.get("course_url") and not _safe_course(result["course_url"]):
        result["course_url"] = None
    result["source_scope"] = scope
    result["source_checked_at"] = source_checked_at
    result["materialized_at"] = materialized_at
    return result


async def materialize_verified_facts(
    db, group_id, source_user_id, week_start, *, checked_at=None
):
    """Atomically upsert one representative's facts for compatible peers.

    Caller commits only after this fully validates and flushes; on error
    rollback leaves every previous student snapshot intact.
    """
    source = await db.get(
        RanepaPersonalSnapshot, (group_id, source_user_id, week_start)
    )
    if source is None or not source.identity_verified:
        raise ValueError("source_identity_unverified")
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
    splits = (
        await db.scalars(select(SubjectSplit).where(SubjectSplit.group_id == group_id))
    ).all()
    by_subject = {normalized(split.subject_name): split for split in splits}
    choices = (
        await db.scalars(
            select(MemberSubgroup).where(
                MemberSubgroup.membership_id.in_([m.id for m in members])
            )
        )
    ).all()
    by_member = {}
    for row in choices:
        by_member.setdefault(row.membership_id, {})[row.split_id] = row.subgroup_id
    source_choices = by_member.get(by_user[source_user_id].id, {})
    rows = json.loads(source.rows_json)
    if not isinstance(rows, list):
        raise ValueError("invalid_source_snapshot")
    prepared = []
    now = (checked_at or datetime.now(UTC)).isoformat()
    source_at = source.checked_at.isoformat()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("invalid_source_lesson")
        split = by_subject.get(normalized(row["subject"]))
        if split is None:
            # Unconfigured explicit subgroup signals are not evidence
            # that the lesson belongs to every student.
            hint = normalized(row.get("subgroup_hint"))
            if re.search(r"подгрупп|\\bсг[ая]\\b", hint):
                continue
            prepared.append((None, _shared_row(
                row, scope="common", source_checked_at=source_at, materialized_at=now
            )))
        elif split.id in source_choices:
            prepared.append((split.id, _shared_row(
                row, scope="subgroup", source_checked_at=source_at, materialized_at=now
            )))
    updated_users = 0
    inserted_lessons = 0
    for user_id in sorted(by_user, key=str):
        if user_id == source_user_id:
            continue
        member = by_user[user_id]
        target_choices = by_member.get(member.id, {})
        eligible = [
            row for split_id, row in prepared
            if split_id is None or target_choices.get(split_id) == source_choices[split_id]
        ]
        if not eligible:
            continue
        target = await db.get(
            RanepaPersonalSnapshot, (group_id, user_id, week_start), with_for_update=True
        )
        previous_rows = json.loads(target.rows_json) if target else []
        if not isinstance(previous_rows, list):
            raise ValueError("invalid_target_snapshot")
        existing_keys = {}
        for index, row in enumerate(previous_rows):
            existing_keys.setdefault(_occurrence(row), []).append(index)
        for incoming in eligible:
            found = existing_keys.get(_occurrence(incoming), [])
            if len(found) == 1:
                old = previous_rows[found[0]]
                if old.get("source_scope") in {"common", "subgroup"}:
                    previous_rows[found[0]] = {
                        **old,
                        **incoming,
                        # Never clear a personal or independently verified link.
                        "online_url": incoming.get("online_url") or old.get("online_url"),
                        "course_url": incoming.get("course_url") or old.get("course_url"),
                    }
                else:
                    # Own account fields and links win over the group projection.
                    old["source_checked_at"] = source_at
                    old["materialized_at"] = now
            elif not found:
                previous_rows.append(dict(incoming))
                existing_keys.setdefault(_occurrence(incoming), []).append(
                    len(previous_rows) - 1
                )
                inserted_lessons += 1
            # An ambiguous target occurrence is left untouched.
        previous_rows.sort(
            key=lambda row: (row["day"], row["starts"], normalized(row["subject"]))
        )
        encoded = json.dumps(previous_rows, ensure_ascii=False, separators=(",", ":"))
        if target is None:
            target = RanepaPersonalSnapshot(
                group_id=group_id,
                user_id=user_id,
                week_start=week_start,
                week_end=source.week_end,
                identity_verified=False,
                checked_at=source.checked_at,
            )
            db.add(target)
        target.rows_json = encoded
        target.data_digest = sha256(encoded.encode()).hexdigest()
        updated_users += 1
    await db.flush()
    return {"users_materialized": updated_users, "lessons_added": inserted_lessons}
