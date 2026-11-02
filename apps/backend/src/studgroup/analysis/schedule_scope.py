"""Conservative, zero-AI enrichment of a timetable image review suggestion.

This file never writes to SchedulePattern/ScheduleException.  Editors still map
replaced lessons and confirm through the normal version/audit/permission guards.
"""

from datetime import date, timedelta

from studgroup.schedule import week_kind


def suggest_review_rows(
    reading, first_week_anchor: date | None, horizon: date | None
) -> list[dict]:
    """Set proposed validity/parity from pass-B scope and the group's trusted calendar.

    Unknown scope/date/anchor/horizon remains editable but unresolved.  Never
    derive a permanent pattern from a one-off notice, and never treat the
    photographed week's label as a one-week expiry by itself.
    """
    original = [row.model_dump(mode="json") for row in reading.schedule]
    plan = reading.schedule_interpretation
    if (
        plan is None
        or plan.week_start is None
        or plan.week_start.weekday() != 0
        or plan.duration == "uncertain"
        or plan.coverage == "unknown"
    ):
        return original
    monday = plan.week_start
    sunday = monday + timedelta(days=6)

    if plan.duration == "recurring":
        # The user/group defines first/second weeks, not ISO calendar parity.
        # Without a trusted end-of-term boundary, require the editor to supply it.
        if first_week_anchor is None or horizon is None or horizon < sunday:
            return original
        parity = week_kind(monday, first_week_anchor)
        if any(row.week not in ("all", parity) for row in reading.schedule):
            return original
    else:
        parity = "all"

    rows = []
    for source, row in zip(original, reading.schedule):
        # A date explicitly anchored to a different week is a conflicting source,
        # not permission to reinterpret or silently overwrite the fact.
        if row.valid_from and not monday <= row.valid_from <= sunday:
            return original
        if row.valid_until and row.valid_until < monday:
            return original
        if plan.duration == "one_week" and row.valid_until and row.valid_until > sunday:
            return original

        if plan.duration == "one_week":
            # Respect explicit single-day edits in a partial-change screenshot.
            single_day = (
                plan.coverage == "partial"
                and row.valid_from is not None
                and row.valid_from == row.valid_until
            )
            start, end = (row.valid_from, row.valid_until) if single_day else (monday, sunday)
        else:
            # A source may explicitly specify a longer confirmed validity range.
            # Dates printed only for the shown week do not end a recurring rule.
            start = row.valid_from or monday
            end = (
                row.valid_until
                if row.valid_until is not None and row.valid_until > sunday
                else horizon
            )
        rows.append(
            source
            | {
                "week": parity,
                "valid_from": start.isoformat(),
                "valid_until": end.isoformat(),
            }
        )
    return rows
