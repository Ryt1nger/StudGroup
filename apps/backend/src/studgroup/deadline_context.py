"""Build deadline policy input from published and verified university timetables."""

import json
from collections import defaultdict
from datetime import date, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from sqlalchemy import select

from studgroup.analysis.deadline_application import DeadlineContext
from studgroup.analysis.deadline_updates import group_timezone
from studgroup.deadlines import ScheduleDeadlineContext
from studgroup.models import (
    GroupNoLessonDay,
    MemberSubgroup,
    Membership,
    RanepaPersonalSnapshot,
    SchedulePattern,
    SubjectSplit,
)
from studgroup.ranepa_personal import _split_for_ranepa_subject
from studgroup.ranepa_schedule import (
    has_unverified_subgroup_hint,
    subgroup_choice_changed_since_check,
)

CHUNK_DAYS = 28


def days_between(start, end):
    return {start + timedelta(days=offset) for offset in range((end - start).days + 1)}


async def verified_weeks(db, group, timezone):
    """Use only independent, complete, identity-verified reads of active members.

    Common subjects use the freshest source week. Split subjects use the freshest
    source with a confirmed, unchanged subgroup choice. Never copy URLs or other
    account data into the deadline context.
    """
    if not group.academic_group_code:
        return [], set(), {}
    sources = (
        await db.execute(
            select(RanepaPersonalSnapshot, Membership.id)
            .join(
                Membership,
                (Membership.group_id == RanepaPersonalSnapshot.group_id)
                & (Membership.user_id == RanepaPersonalSnapshot.user_id),
            )
            .where(
                RanepaPersonalSnapshot.group_id == group.id,
                RanepaPersonalSnapshot.identity_verified.is_(True),
                RanepaPersonalSnapshot.source_complete.is_(True),
                RanepaPersonalSnapshot.source_digest.is_not(None),
                Membership.status == "active",
            )
            .order_by(RanepaPersonalSnapshot.checked_at.desc(), RanepaPersonalSnapshot.user_id)
        )
    ).all()
    splits = (
        await db.scalars(select(SubjectSplit).where(SubjectSplit.group_id == group.id))
    ).all()
    choices = defaultdict(dict)
    for row in (
        await db.scalars(
            select(MemberSubgroup).where(
                MemberSubgroup.membership_id.in_({member_id for _, member_id in sources})
            )
        )
    ).all():
        choices[row.membership_id][row.split_id] = row
    zone = ZoneInfo(timezone)
    common_days = set()
    subgroup_days = defaultdict(set)
    common_seen = set()
    subgroup_seen = set()
    lessons = []
    for source, member_id in sources:
        if source.week_start.weekday() or source.week_end != source.week_start + timedelta(days=6):
            continue
        week = source.week_start
        coverage = days_between(week, source.week_end)
        try:
            rows = json.loads(source.rows_json)
            if not isinstance(rows, list):
                continue
            prepared = []
            for row in rows:
                if row.get("source_scope") in {"common", "subgroup"}:
                    continue
                day = date.fromisoformat(row["day"])
                if day not in coverage:
                    raise ValueError("invalid_source_day")
                start = datetime.fromisoformat(f"{day}T{row['starts']}").replace(tzinfo=zone)
                subject = row["subject"]
                split = _split_for_ranepa_subject(splits, subject)
                subgroup_id = None
                if split is not None:
                    choice = choices[member_id].get(split.id)
                    if choice is None or subgroup_choice_changed_since_check(
                        choice.updated_at, source.checked_at
                    ):
                        continue
                    subgroup_id = str(choice.subgroup_id)
                    if (week, subgroup_id) in subgroup_seen:
                        continue
                    subject = split.subject_name
                elif has_unverified_subgroup_hint(row.get("subgroup_hint")):
                    continue
                elif week in common_seen:
                    continue
                prepared.append(
                    {
                        "subject": subject,
                        "starts_at": start.isoformat(),
                        "status": "cancelled" if row.get("cancelled") else "scheduled",
                        "subgroup_id": subgroup_id,
                    }
                )
        except (AttributeError, KeyError, TypeError, ValueError):
            continue
        lessons.extend(prepared)
        if week not in common_seen:
            common_seen.add(week)
            common_days.update(coverage)
        for choice in choices[member_id].values():
            if not subgroup_choice_changed_since_check(choice.updated_at, source.checked_at):
                ident = str(choice.subgroup_id)
                subgroup_seen.add((week, ident))
                subgroup_days[ident].update(coverage)
    return lessons, common_days, dict(subgroup_days)


async def build_deadline_context(db, group, *, horizon=None):
    from studgroup.api import schedule_data

    zone = group_timezone(group.timezone)
    view = SimpleNamespace(
        id=group.id, timezone=zone.name, first_week_anchor=group.first_week_anchor
    )
    patterns = (
        await db.scalars(select(SchedulePattern).where(SchedulePattern.group_id == group.id))
    ).all()
    no_lesson_days = frozenset(
        (
            await db.scalars(
                select(GroupNoLessonDay.day).where(GroupNoLessonDay.group_id == group.id)
            )
        ).all()
    )
    official, official_days, subgroup_days = await verified_weeks(db, group, zone.name)
    lessons = []
    published_days = set()
    if patterns:
        start = min(row.valid_from for row in patterns)
        end = max(row.valid_until for row in patterns)
        if horizon is not None:
            end = min(end, horizon)
        day = start
        while day <= end:
            chunk_end = min(day + timedelta(days=CHUNK_DAYS - 1), end)
            data = await schedule_data(day, chunk_end, view, db)
            if data["week_state"] == "ready":
                lessons.extend(data["lessons"])
                for row in patterns:
                    published_days.update(
                        days_between(max(day, row.valid_from), min(chunk_end, row.valid_until))
                    )
            day = chunk_end + timedelta(days=1)
    # Prefer verified official occurrences on complete source days; explicit
    # published exceptions/cancellations remain authoritative for their matching lesson.
    # Outside the official window the published timetable remains usable.
    zone_info = ZoneInfo(zone.name)
    for row in official:
        day = datetime.fromisoformat(row["starts_at"]).astimezone(zone_info).date()
        if day not in published_days:
            lessons.append(row)
    covered = published_days | official_days
    if horizon is not None:
        covered = {day for day in covered if day <= horizon}
    if not covered:
        return DeadlineContext(zone.name, zone.confirmed, None, no_lesson_days)
    scoped = {
        ident: frozenset(published_days | days)
        for ident, days in subgroup_days.items()
    }
    schedule = ScheduleDeadlineContext(
        lessons,
        min(covered),
        max(covered),
        True,
        frozenset(covered),
        scoped,
        frozenset(published_days),
    )
    return DeadlineContext(zone.name, zone.confirmed, schedule, no_lesson_days)
