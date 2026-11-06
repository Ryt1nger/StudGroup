"""Persist complete account-local weeks immediately, without cross-user promotion."""

import json
from datetime import UTC, date, datetime, timedelta
from hashlib import sha256
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

from sqlalchemy import select

from studgroup.models import (
    Group,
    GroupOfficialSubject,
    Membership,
    MemberSubgroup,
    RanepaPersonalSnapshot,
    SubjectSplit,
)
from studgroup.ranepa_schedule import WeekSnapshot, normalized
from studgroup.ranepa_storage import encode_rows
from studgroup.subject_identity import subject_uuid


def _safe_link(url: str) -> bool:
    parsed = urlsplit(url)
    ids = parse_qs(parsed.query).get("id", [])
    return (
        parsed.scheme == "https"
        and parsed.hostname == "lms.ranepa.ru"
        and parsed.path == "/mod/url/view.php"
        and len(ids) == 1
        and ids[0].isdigit()
        and not parsed.username
        and not parsed.password
        and not parsed.fragment
    )


async def remember_personal(
    db,
    group_id,
    user_id,
    snapshot: WeekSnapshot,
    *,
    links=None,
    group_identity_verified=False,
    checked_at=None,
):
    group = await db.get(Group, group_id)
    member = await db.scalar(
        select(Membership).where(
            Membership.group_id == group_id,
            Membership.user_id == user_id,
            Membership.status == "active",
        )
    )
    if group is None or member is None or not group.academic_group_code:
        raise ValueError("inactive_or_unconfigured_group_member")
    if normalized(snapshot.group_code) != normalized(group.academic_group_code):
        raise ValueError("mismatched_group_code")
    if snapshot.week_start.weekday() != 0 or snapshot.week_end != snapshot.week_start + timedelta(
        days=6
    ):
        raise ValueError("invalid_week_coverage")
    if not snapshot.indexed() and not group_identity_verified:
        raise ValueError("empty_source_requires_review")
    rows = json.loads(encode_rows(snapshot))
    links = links or {}
    for row in rows:
        key = (row["day"], row["starts"], row["ends"], row["subject"], row["teacher"], row["room"])
        url = links.get(key)
        if url is not None and not _safe_link(url):
            raise ValueError("unsafe_lms_link")
        row["online_url"] = url
    encoded = json.dumps(rows, ensure_ascii=False, separators=(",", ":"))
    existing = await db.get(
        RanepaPersonalSnapshot, (group_id, user_id, snapshot.week_start), with_for_update=True
    )
    digest = sha256(encoded.encode()).hexdigest()
    status = (
        "new" if existing is None else "unchanged" if existing.data_digest == digest else "changed"
    )
    if existing is None:
        existing = RanepaPersonalSnapshot(
            group_id=group_id, user_id=user_id, week_start=snapshot.week_start
        )
        db.add(existing)
    existing.week_end = snapshot.week_end
    existing.data_digest = digest
    existing.rows_json = encoded
    existing.checked_at = checked_at or datetime.now(UTC)
    existing.identity_verified = bool(existing.identity_verified or group_identity_verified)
    if group_identity_verified:
        names = {}
        for lesson in snapshot.indexed().values():
            name = " ".join(lesson.subject.split())
            if not name or len(name) > 255:
                raise ValueError("invalid_official_subject")
            names.setdefault(name.casefold(), name)
        for key, name in names.items():
            subject = await db.get(GroupOfficialSubject, (group_id, key))
            if subject is None:
                db.add(
                    GroupOfficialSubject(
                        group_id=group_id,
                        name_key=key,
                        subject_id=subject_uuid(group_id, name),
                        name=name,
                        checked_at=existing.checked_at,
                    )
                )
            else:
                subject.checked_at = existing.checked_at
    await db.flush()
    return status


async def personal_period(db, group_id, user_id, start: date, end: date):
    if end < start or (end - start).days > 31:
        raise ValueError("invalid_period")
    snapshots = (
        await db.scalars(
            select(RanepaPersonalSnapshot)
            .where(
                RanepaPersonalSnapshot.group_id == group_id,
                RanepaPersonalSnapshot.user_id == user_id,
                RanepaPersonalSnapshot.week_start <= end,
                RanepaPersonalSnapshot.week_end >= start,
            )
            .order_by(RanepaPersonalSnapshot.week_start)
        )
    ).all()
    lessons = []
    covered = set()
    for row in snapshots:
        cursor = max(start, row.week_start)
        while cursor <= min(end, row.week_end):
            covered.add(cursor)
            cursor += timedelta(days=1)
        for lesson in json.loads(row.rows_json):
            if start <= date.fromisoformat(lesson["day"]) <= end:
                lessons.append(
                    {
                        "day": lesson["day"],
                        "starts": lesson["starts"],
                        "ends": lesson["ends"],
                        "subject": lesson["subject"],
                        "teacher": lesson["teacher"],
                        "room": lesson["room"],
                        "lesson_type": lesson["lesson_type"],
                        "stream_label": lesson["subgroup_hint"],
                        "online_url": lesson.get("online_url"),
                    }
                )
    lessons.sort(key=lambda row: (row["day"], row["starts"], row["subject"]))
    dates = (end - start).days + 1
    return {
        "start": start,
        "end": end,
        "complete": len(covered) == dates,
        "last_success_at": max((row.checked_at for row in snapshots), default=None),
        "lessons": lessons,
        "fallback_used": False,
    }


async def personal_with_group_fallback(db, group_id, user_id, start: date, end: date):
    """Fill missing weeks only from peers with confirmed compatible subgroups."""
    result = await personal_period(db, group_id, user_id, start, end)
    if result["complete"]:
        return result
    members = (
        await db.scalars(
            select(Membership).where(
                Membership.group_id == group_id,
                Membership.status == "active",
            )
        )
    ).all()
    target = next((row for row in members if row.user_id == user_id), None)
    if target is None:
        return result
    member_by_user = {row.user_id: row for row in members}
    choices = (
        await db.scalars(
            select(MemberSubgroup).where(
                MemberSubgroup.membership_id.in_([row.id for row in members])
            )
        )
    ).all()
    by_member = {}
    for row in choices:
        by_member.setdefault(row.membership_id, {})[row.split_id] = row.subgroup_id
    target_choices = by_member.get(target.id, {})
    splits = (await db.scalars(select(SubjectSplit).where(SubjectSplit.group_id == group_id))).all()
    split_by_name = {row.subject_name.casefold(): row for row in splits}
    own_weeks = set(
        (
            await db.scalars(
                select(RanepaPersonalSnapshot.week_start).where(
                    RanepaPersonalSnapshot.group_id == group_id,
                    RanepaPersonalSnapshot.user_id == user_id,
                    RanepaPersonalSnapshot.week_start <= end,
                    RanepaPersonalSnapshot.week_end >= start,
                )
            )
        ).all()
    )
    peers = (
        await db.scalars(
            select(RanepaPersonalSnapshot).where(
                RanepaPersonalSnapshot.group_id == group_id,
                RanepaPersonalSnapshot.user_id != user_id,
                RanepaPersonalSnapshot.identity_verified.is_(True),
                RanepaPersonalSnapshot.week_start <= end,
                RanepaPersonalSnapshot.week_end >= start,
            )
        )
    ).all()
    candidates = {}
    for source in peers:
        if source.week_start in own_weeks or source.user_id not in member_by_user:
            continue
        peer_choices = by_member.get(member_by_user[source.user_id].id, {})
        signature = frozenset(peer_choices.items())
        for lesson in json.loads(source.rows_json):
            if not start <= date.fromisoformat(lesson["day"]) <= end:
                continue
            split = split_by_name.get(lesson["subject"].casefold())
            if split is not None and (
                split.id not in target_choices
                or peer_choices.get(split.id) != target_choices[split.id]
            ):
                continue
            key = tuple(
                lesson.get(field)
                for field in (
                    "day",
                    "starts",
                    "ends",
                    "subject",
                    "teacher",
                    "room",
                    "lesson_type",
                    "subgroup_hint",
                )
            )
            candidates.setdefault(key, []).append(
                (source.user_id, signature, split is not None, source.checked_at)
            )
    for key, contributors in candidates.items():
        subgroup_match = contributors[0][2]
        distinct_signatures = {signature for _, signature, _, _ in contributors if signature}
        if not subgroup_match and len(distinct_signatures) < 2:
            continue
        day, starts, ends, subject, teacher, room, kind, stream = key
        result["lessons"].append(
            {
                "day": day,
                "starts": starts,
                "ends": ends,
                "subject": subject,
                "teacher": teacher,
                "room": room,
                "lesson_type": kind,
                "stream_label": stream,
                "online_url": None,
            }
        )
        checked = max(item[3] for item in contributors)
        result["last_success_at"] = max(filter(None, (result["last_success_at"], checked)))
        result["fallback_used"] = True
    result["lessons"].sort(key=lambda row: (row["day"], row["starts"], row["subject"]))
    return result


def schedule_projection(personal, group):
    """Existing app contract backed only by account-scoped or guarded peer rows."""
    zone = ZoneInfo(group.timezone)
    lessons = []
    for row in personal["lessons"]:
        key = "\x1f".join(
            str(row.get(field) or "")
            for field in ("day", "starts", "ends", "subject", "teacher", "room", "stream_label")
        )
        identity = sha256(key.encode()).hexdigest()[:24]
        day = row["day"]
        lessons.append(
            {
                "id": f"ranepa:{day}:{identity}",
                "subject": row["subject"],
                "teacher": row["teacher"] or None,
                "starts_at": datetime.fromisoformat(f"{day}T{row['starts']}")
                .replace(tzinfo=zone)
                .isoformat(),
                "ends_at": datetime.fromisoformat(f"{day}T{row['ends']}")
                .replace(tzinfo=zone)
                .isoformat(),
                "location": row["room"] or None,
                "online_url": row["online_url"],
                "status": "scheduled",
            }
        )
    return {
        "generated_at": datetime.now(UTC),
        "group_timezone": group.timezone,
        "start": personal["start"],
        "end": personal["end"],
        "week_state": "ready" if personal["complete"] else "needs_clarification",
        "first_week_anchor": group.first_week_anchor,
        "selected_week": None,
        "lessons": lessons,
    }
