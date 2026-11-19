"""Persist complete account-local weeks immediately, without cross-user promotion."""

import json
import logging
from datetime import UTC, date, datetime, time, timedelta
from hashlib import sha256
from urllib.parse import parse_qsl, urlsplit
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
from studgroup.ranepa_link_states import LINK_STATES
from studgroup.ranepa_lms import LinkCandidate, meeting_url, select_lesson_link
from studgroup.ranepa_schedule import (
    WeekSnapshot,
    has_unverified_subgroup_hint,
    normalized,
    subgroup_choice_changed_since_check,
)
from studgroup.ranepa_storage import encode_rows
from studgroup.subject_identity import subject_uuid


def _safe_link(url: str) -> bool:
    try:
        return bool(url) and meeting_url(url) == url
    except ValueError:
        return False


def _safe_course(url: str) -> bool:
    parsed = urlsplit(url)
    parameters = parse_qsl(parsed.query, keep_blank_values=True)
    return (
        parsed.scheme == "https"
        and parsed.hostname == "lms.ranepa.ru"
        and parsed.path == "/course/view.php"
        and len(parameters) == 1
        and parameters[0][0] == "id"
        and parameters[0][1].isdigit()
        and not parsed.username
        and not parsed.password
        and not parsed.fragment
    )


LOGGER = logging.getLogger(__name__)


MAX_COURSE_CANDIDATES = 80


def _sdo_cache_key(row):
    return (
        normalized(row.get("subject")),
        normalized(row.get("teacher")),
        normalized(row.get("subgroup_hint")),
    )


def _sdo_subject_key(row):
    return (normalized(row.get("subject")), normalized(row.get("subgroup_hint")))


def _valid_candidate(record):
    if not isinstance(record, dict):
        return None
    url, label, course = (
        record.get("url"),
        record.get("label"),
        record.get("course_url"),
    )
    if (
        not isinstance(url, str)
        or not _safe_link(url)
        or not isinstance(label, str)
        or not (1 <= len(label) <= 500)
        or not isinstance(course, str)
        or not _safe_course(course)
    ):
        return None
    return {"url": url, "label": label, "course_url": course}


def _merge_course_candidates(previous, discovered, course, checked):
    """Retain validated history; a failed/empty refresh cannot erase old evidence."""
    merged = {}
    for item in previous or ():
        vetted = _valid_candidate(item)
        if vetted:
            merged[(vetted["course_url"], vetted["url"])] = {
                **vetted,
                "seen_at": item.get("seen_at") or checked,
            }
    if course and _safe_course(course):
        for link in discovered or ():
            if isinstance(link, LinkCandidate):
                url, label = link.url, link.label
            elif isinstance(link, dict):
                url, label = link.get("url"), link.get("label")
            else:
                continue
            if not isinstance(url, str) or not _safe_link(url):
                continue
            if not isinstance(label, str) or not (1 <= len(label) <= 500):
                continue
            merged[(course, url)] = {
                "url": url,
                "label": label,
                "course_url": course,
                "seen_at": checked,
            }
    return list(merged.values())[-MAX_COURSE_CANDIDATES:]


async def _verified_sdo_link_cache(db, group_id, user_id):
    """Account-local verified links and full per-course candidate history."""
    snapshots = (
        await db.scalars(
            select(RanepaPersonalSnapshot)
            .where(
                RanepaPersonalSnapshot.group_id == group_id,
                RanepaPersonalSnapshot.user_id == user_id,
            )
            .order_by(RanepaPersonalSnapshot.checked_at.desc())
        )
    ).all()
    by_identity = {}
    by_course = {}
    by_subject = {}
    for snapshot in snapshots:
        for row in json.loads(snapshot.rows_json):
            if row.get("room") != "СДО" or row.get("source_scope") in {"common", "subgroup"}:
                continue
            url = row.get("online_url")
            course = row.get("course_url")
            if url is not None and not _safe_link(url):
                url = None
            if course is not None and not _safe_course(course):
                course = None
            if course is not None:
                by_subject.setdefault(_sdo_subject_key(row), set()).add(course)
            if url is not None or course is not None:
                item = by_identity.setdefault(_sdo_cache_key(row), {})
                if url is not None and "online_url" not in item:
                    item["online_url"] = url
                    item["online_checked_at"] = (
                        row.get("links_checked_at") or snapshot.checked_at.isoformat()
                    )
                    item["link_course_url"] = course
                if course is not None and "course_url" not in item:
                    item["course_url"] = course
                    item["course_checked_at"] = (
                        row.get("links_checked_at") or snapshot.checked_at.isoformat()
                    )
            for record in row.get("link_candidates", ()):
                vetted = _valid_candidate(record)
                if vetted:
                    course_map = by_course.setdefault(vetted["course_url"], {})
                    course_map.setdefault(vetted["url"], vetted)
    return by_identity, by_course, by_subject


def _reuse_verified_candidate(row, course, by_course, identity):
    """Never guess when stored candidates conflict or the course changed."""
    if not course or not _safe_course(course):
        return None
    links = tuple(
        LinkCandidate(item["url"], item["label"])
        for item in by_course.get(course, {}).values()
    )
    if links:
        choice = select_lesson_link(
            links,
            teacher=row.get("teacher") or "",
            group_label=row.get("subgroup_hint") or "",
            day=date.fromisoformat(row["day"]),
            starts=time.fromisoformat(row["starts"]),
        )
        if choice.state == "matched":
            return choice.url
        if choice.state == "ambiguous":
            return None
    old = identity.get(_sdo_cache_key(row), {})
    if (
        old.get("online_url")
        and _safe_link(old["online_url"])
        and old.get("link_course_url") in {None, course}
    ):
        return old["online_url"]
    return None


async def remember_personal(
    db,
    group_id,
    user_id,
    snapshot: WeekSnapshot,
    *,
    links=None,
    courses=None,
    link_states=None,
    group_identity_verified=False,
    source_complete=False,
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
    existing = await db.get(
        RanepaPersonalSnapshot, (group_id, user_id, snapshot.week_start), with_for_update=True
    )
    cached_links, course_candidates, subject_courses = await _verified_sdo_link_cache(db, group_id, user_id)
    previous = {}
    if existing is not None:
        for old in json.loads(existing.rows_json):
            key = (
                old["day"],
                old["starts"],
                old["ends"],
                old["subject"],
                old["teacher"],
                old["room"],
            )
            previous[key] = old
    rows = json.loads(encode_rows(snapshot))
    links = links or {}
    courses = courses or {}
    link_states = link_states or {}
    # Preserve already materialized peer facts absent from a new account read.
    # A missing source row is never evidence of cancellation.
    own_keys = {
        (row["day"], row["starts"], row["ends"], row["subject"], row["teacher"], row["room"])
        for row in rows
    }
    for key, old in previous.items():
        if key not in own_keys and old.get("source_scope") in {"common", "subgroup"}:
            rows.append(dict(old))
    for row in rows:
        key = (row["day"], row["starts"], row["ends"], row["subject"], row["teacher"], row["room"])
        old = previous.get(key, {})
        # A schedule-only refresh cannot erase previously verified link data.
        # Explicit link-state enrichment is allowed to replace that data.
        url = links.get(key, old.get("online_url") if key not in link_states else None)
        course = courses.get(key, old.get("course_url") if key not in link_states else None)
        if url is not None and not _safe_link(url):
            raise ValueError("unsafe_lms_link")
        if course is not None and not _safe_course(course):
            raise ValueError("unsafe_lms_course")
        if course is not None and row["room"] != "СДО":
            raise ValueError("course_without_sdo")
        state = link_states.get(key, old.get("link_state"))
        cached = cached_links.get(_sdo_cache_key(row), {}) if row["room"] == "СДО" else {}
        link_cached = False
        if course is None and cached.get("course_url"):
            course = cached["course_url"]
            row["course_cached_at"] = cached.get("course_checked_at")
            link_cached = True
        if course is None and row["room"] == "СДО":
            unique_courses = subject_courses.get(_sdo_subject_key(row), set())
            if len(unique_courses) == 1:
                # A single verified course for this subject + stream can be
                # reused even when the teacher changes between weeks.
                course = next(iter(unique_courses))
                row["course_cached_at"] = (
                    cached.get("course_checked_at") or datetime.now(UTC).isoformat()
                )
                link_cached = True
        if (
            url is None
            and row["room"] == "СДО"
            and state not in {"ambiguous", "timing_unverified", "identity_missing"}
        ):
            url = _reuse_verified_candidate(row, course, course_candidates, cached_links)
            if url:
                state = "matched"
                row["link_cached_at"] = cached.get("online_checked_at")
                link_cached = True
        if row["room"] == "СДО":
            # Durable candidate list stays on the verified account-local row.
            row["link_candidates"] = _merge_course_candidates(
                old.get("link_candidates", ()),
                course_candidates.get(course, {}).values() if course else (),
                course,
                (checked_at or datetime.now(UTC)).isoformat(),
            )
        if link_cached:
            row["link_source"] = "cached"
        elif url is not None or course is not None:
            row["link_source"] = "live"
        if state is not None and state not in LINK_STATES:
            raise ValueError("unsafe_lms_state")
        if url is not None and state not in {None, "matched"}:
            raise ValueError("contradictory_lms_state")
        if state == "matched" and url is None:
            raise ValueError("contradictory_lms_state")
        row["online_url"] = url
        row["course_url"] = course
        row["link_state"] = state or (
            "matched" if url else "not_checked" if row["room"] == "СДО" else None
        )
    encoded = json.dumps(rows, ensure_ascii=False, separators=(",", ":"))
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
    # Verification describes THIS read, not a previous successful read.
    # A partial/unconfirmed refresh must never retain old source authority.
    existing.identity_verified = bool(group_identity_verified)
    existing.source_complete = bool(group_identity_verified and source_complete)
    existing.source_digest = snapshot.digest() if existing.source_complete else None
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


async def enrich_personal_links(
    db,
    group_id,
    user_id,
    week_start: date,
    *,
    links=None,
    courses=None,
    link_states=None,
    candidates=None,
    checked_at=None,
):
    """Update link fields only after schedule commit, in a separate transaction.

    An exception in this step must be rolled back by the caller without
    undoing the validated schedule. Timetable checked_at is not advanced.
    """
    stored = await db.get(
        RanepaPersonalSnapshot, (group_id, user_id, week_start), with_for_update=True
    )
    if stored is None or not stored.identity_verified:
        raise ValueError("unverified_personal_snapshot")
    links = links or {}
    courses = courses or {}
    link_states = link_states or {}
    candidates = candidates or {}
    rows = json.loads(stored.rows_json)
    cached_links, course_candidates, subject_courses = await _verified_sdo_link_cache(db, group_id, user_id)
    checked = (checked_at or datetime.now(UTC)).isoformat()
    for row in rows:
        key = (row["day"], row["starts"], row["ends"], row["subject"], row["teacher"], row["room"])
        if (
            key not in links
            and key not in courses
            and key not in link_states
            and key not in candidates
        ):
            continue
        url = links.get(key)
        course = courses.get(key) or row.get("course_url")
        state = link_states.get(key, "matched" if url else "not_checked")
        cached = cached_links.get(_sdo_cache_key(row), {}) if row["room"] == "СДО" else {}
        link_cached = False
        if course is None and cached.get("course_url"):
            course = cached["course_url"]
            row["course_cached_at"] = cached.get("course_checked_at")
            link_cached = True
        if course is None and row["room"] == "СДО":
            unique_courses = subject_courses.get(_sdo_subject_key(row), set())
            if len(unique_courses) == 1:
                # A single verified course for this subject + stream can be
                # reused even when the teacher changes between weeks.
                course = next(iter(unique_courses))
                row["course_cached_at"] = (
                    cached.get("course_checked_at") or datetime.now(UTC).isoformat()
                )
                link_cached = True
        if row["room"] == "СДО":
            # Store all candidates even for ambiguous or currently unmatched
            # lessons. The record is private to the verified source account.
            row["link_candidates"] = _merge_course_candidates(
                (*row.get("link_candidates", ()), *(
                    course_candidates.get(course, {}).values() if course else ()
                )),
                candidates.get(key, ()),
                course,
                checked,
            )
        if (
            url is None
            and row["room"] == "СДО"
            and state not in {"ambiguous", "timing_unverified", "identity_missing"}
        ):
            historical = _reuse_verified_candidate(
                row, course, course_candidates, cached_links
            )
            if historical:
                url = historical
                link_cached = True
            else:
                # A previously unchosen meeting may become matchable after
                # the timetable supplies the exact teacher and stream.
                choices = tuple(
                    LinkCandidate(item["url"], item["label"])
                    for item in row.get("link_candidates", ())
                    if item["course_url"] == course
                )
                if choices:
                    selected = select_lesson_link(
                        choices,
                        teacher=row.get("teacher") or "",
                        group_label=row.get("subgroup_hint") or "",
                        day=date.fromisoformat(row["day"]),
                        starts=time.fromisoformat(row["starts"]),
                    )
                    url = selected.url
            if url:
                state = "matched"
                row["link_cached_at"] = cached.get("online_checked_at")
                link_cached = True
        if link_cached:
            row["link_source"] = "cached"
        elif url is not None or course is not None:
            row["link_source"] = "live"
        if state not in LINK_STATES:
            raise ValueError("unsafe_lms_state")
        if url is not None and not _safe_link(url):
            raise ValueError("unsafe_lms_link")
        if course is not None and not _safe_course(course):
            raise ValueError("unsafe_lms_course")
        if course is not None and row["room"] != "СДО":
            raise ValueError("course_without_sdo")
        if (url is not None and state != "matched") or (state == "matched" and url is None):
            raise ValueError("contradictory_lms_state")
        row["online_url"] = url
        row["course_url"] = course or row.get("course_url")
        row["link_state"] = state
        row["links_checked_at"] = checked
    encoded = json.dumps(rows, ensure_ascii=False, separators=(",", ":"))
    updated = stored.data_digest != sha256(encoded.encode()).hexdigest()
    stored.rows_json = encoded
    stored.data_digest = sha256(encoded.encode()).hexdigest()
    await db.flush()
    return "changed" if updated else "unchanged"


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
    verified_covered = set()
    week_freshness = []
    link_checks = []
    materialized = False
    for row in snapshots:
        cursor = max(start, row.week_start)
        while cursor <= min(end, row.week_end):
            covered.add(cursor)
            if row.identity_verified:
                verified_covered.add(cursor)
            cursor += timedelta(days=1)
        source_rows = json.loads(row.rows_json)
        # Legacy shared rows have no durable provenance; never render them as
        # independently confirmed timetable facts after the safe migration.
        source_rows = [
            lesson
            for lesson in source_rows
            if lesson.get("source_scope") not in {"common", "subgroup"}
            or (lesson.get("source_user_id") and lesson.get("source_snapshot_id"))
        ]
        sdo_rows = [lesson for lesson in source_rows if lesson.get("room") == "СДО"]
        direct_links = sum(lesson.get("link_state") == "matched" for lesson in sdo_rows)
        sdo_checked = sum(
            lesson.get("link_state") not in {None, "not_checked"} for lesson in sdo_rows
        )
        dates_checked = [
            datetime.fromisoformat(lesson["links_checked_at"])
            for lesson in sdo_rows
            if lesson.get("links_checked_at")
        ]
        link_checks.extend(dates_checked)
        materialized = materialized or any(
            lesson.get("source_scope") in {"common", "subgroup"} for lesson in source_rows
        )
        week_freshness.append(
            {
                "week_start": row.week_start,
                "week_end": row.week_end,
                "source_checked_at": row.checked_at if row.identity_verified else None,
                "materialized_at": max(
                    (
                        datetime.fromisoformat(lesson["materialized_at"])
                        for lesson in source_rows
                        if lesson.get("materialized_at")
                    ),
                    default=None,
                ),
                "sdo_total": len(sdo_rows),
                "sdo_checked": sdo_checked,
                "direct_links": direct_links,
                "course_links": sum(bool(lesson.get("course_url")) for lesson in sdo_rows),
                "links_checked_at": min(dates_checked)
                if len(dates_checked) == len(sdo_rows) and dates_checked
                else None,
            }
        )
        for lesson in source_rows:
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
                        "cancelled": bool(lesson.get("cancelled", False)),
                        "source_scope": lesson.get("source_scope"),
                        "source_split_id": lesson.get("source_split_id"),
                        "source_subgroup_id": lesson.get("source_subgroup_id"),
                        "online_url": lesson.get("online_url"),
                        "course_url": lesson.get("course_url"),
                        "link_state": lesson.get("link_state"),
                    }
                )
    # Filter materialized subgroup facts against CURRENT confirmed membership.
    # A subgroup change must not expose stale previously shared subgroup rows.
    member = await db.scalar(
        select(Membership).where(
            Membership.group_id == group_id,
            Membership.user_id == user_id,
            Membership.status == "active",
        )
    )
    if member is None:
        lessons = []
    else:
        chosen = (
            await db.scalars(
                select(MemberSubgroup).where(MemberSubgroup.membership_id == member.id)
            )
        ).all()
        current = {str(choice.split_id): str(choice.subgroup_id) for choice in chosen}
        lessons = [
            row
            for row in lessons
            if row.get("source_scope") != "subgroup"
            or (
                row.get("source_split_id")
                and current.get(row["source_split_id"]) == row.get("source_subgroup_id")
            )
        ]
    lessons.sort(key=lambda row: (row["day"], row["starts"], row["subject"]))
    dates = (end - start).days + 1
    return {
        "start": start,
        "end": end,
        "complete": len(covered) == dates,
        # Preserve the legacy cached-data timestamp for existing clients.
        # Source verification is conveyed separately by source_checked and
        # oldest_required_checked_at.
        "last_success_at": max((row.checked_at for row in snapshots), default=None),
        "oldest_required_checked_at": (
            min(row.checked_at for row in snapshots if row.identity_verified)
            if len(verified_covered) == dates
            else None
        ),
        "schedule_complete": len(covered) == dates,
        "links_complete": all(week["sdo_checked"] == week["sdo_total"] for week in week_freshness)
        and len(covered) == dates,
        "source_checked": len(verified_covered) == dates,
        "user_materialized": materialized,
        "oldest_links_checked_at": min(link_checks, default=None),
        "weeks": week_freshness,
        "lessons": lessons,
        "covered_dates": sorted(day.isoformat() for day in covered),
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
    choice_updated = {}
    for row in choices:
        by_member.setdefault(row.membership_id, {})[row.split_id] = row.subgroup_id
        choice_updated[(row.membership_id, row.split_id)] = row.updated_at
    target_choices = by_member.get(target.id, {})
    splits = (await db.scalars(select(SubjectSplit).where(SubjectSplit.group_id == group_id))).all()
    split_by_name = {normalized(row.subject_name): row for row in splits}
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
            # Re-shared rows are not independently verified by this account.
            # A subgroup change must not relabel another peer's lesson.
            if lesson.get("source_scope") in {"common", "subgroup"}:
                continue
            if not start <= date.fromisoformat(lesson["day"]) <= end:
                continue
            split = split_by_name.get(normalized(lesson["subject"]))
            if split is None and has_unverified_subgroup_hint(lesson.get("subgroup_hint")):
                continue
            if split is not None and (
                split.id not in target_choices
                or peer_choices.get(split.id) != target_choices[split.id]
                or subgroup_choice_changed_since_check(
                    choice_updated.get((member_by_user[source.user_id].id, split.id)),
                    source.checked_at,
                )
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
        # A single identity-verified peer can confirm an ordinary group lesson.
        # Only subjects explicitly split by the headman require an exact matching
        # subgroup choice. The number of splits/subgroups is fully data-driven.
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
                "course_url": None,
                "link_state": None,
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
                "course_url": row.get("course_url"),
                "status": "cancelled" if row.get("cancelled") else "scheduled",
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


def schedule_with_group_fallback(personal, published, group):
    """Merge individual occurrences without treating an absent row as cancellation.

    The published occurrence retains its stable public ID. Only an explicit
    verified source cancellation can mark it cancelled, and ambiguous matches
    preserve both original published entries instead of hiding either.
    """
    personal_view = schedule_projection(personal, group)
    zone = ZoneInfo(group.timezone)
    result = [dict(row) for row in published["lessons"]]
    conflicts = 0

    def occurrence(row):
        start_at = datetime.fromisoformat(row["starts_at"]).astimezone(zone)
        end_at = datetime.fromisoformat(row["ends_at"]).astimezone(zone)
        return (
            start_at.date(),
            start_at.time().replace(tzinfo=None),
            end_at.time().replace(tzinfo=None),
            normalized(row["subject"]),
        )

    for source in personal_view["lessons"]:
        key = occurrence(source)
        candidates = [
            index
            for index, published_row in enumerate(result)
            if occurrence(published_row) == key
            and (
                not normalized(source.get("teacher"))
                or not normalized(published_row.get("teacher"))
                or normalized(source.get("teacher")) == normalized(published_row.get("teacher"))
            )
        ]
        if len(candidates) != 1:
            if candidates:
                conflicts += 1
            result.append(source)
            continue
        index = candidates[0]
        old = result[index]
        if old.get("status") == "cancelled" and source["status"] != "cancelled":
            # A successful source read does not implicitly reverse editorial cancellation.
            continue
        merged = dict(old)
        for field in ("teacher", "location", "online_url", "course_url"):
            if source.get(field) is not None:
                merged[field] = source[field]
        if source["status"] == "cancelled":
            merged["status"] = "cancelled"
        result[index] = merged
    if conflicts:
        LOGGER.warning("ranepa_schedule_occurrence_conflict count=%d", conflicts)
    result.sort(key=lambda row: (row["starts_at"], row["id"]))
    personal_view["lessons"] = result
    personal_view["week_state"] = (
        "ready"
        if personal["complete"]
        else published["week_state"]
        if published["lessons"]
        else "needs_clarification"
    )
    personal_view["selected_week"] = published["selected_week"]
    return personal_view
