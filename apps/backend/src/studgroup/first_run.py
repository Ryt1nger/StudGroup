"""Owner-requested reset so a group's first AI run starts clean.

Deletes only AI processing artefacts (runs, jobs, attempts, candidates). Published
cards, raw messages and the global spend counter are left untouched. Sources from
the bot installation onward are queued again; older history is never reopened.
"""

from sqlalchemy import delete, func, select, update

from studgroup.models import (
    AIAttempt,
    AICandidate,
    AIJob,
    AIRun,
    Group,
    GroupAIActivity,
    GroupAIProfile,
    RawMessage,
)


async def summary(db, group_id):
    async def count(model, *where):
        return int(await db.scalar(select(func.count()).select_from(model).where(*where)) or 0)

    group = await db.get(Group, group_id)
    since = group.bot_added_at if group else None
    return {
        "runs": await count(AIRun, AIRun.group_id == group_id),
        "jobs": await count(AIJob, AIJob.group_id == group_id),
        "attempts": await count(AIAttempt, AIAttempt.group_id == group_id),
        "candidates": await count(AICandidate, AICandidate.group_id == group_id),
        "since": since,
        "to_analyze": 0
        if since is None
        else await count(
            RawMessage, RawMessage.group_id == group_id, RawMessage.message_date >= since
        ),
    }


async def reset_first_run(db, group_id, now):
    group = await db.get(Group, group_id)
    if group is None or group.bot_added_at is None:
        return None
    before = await summary(db, group_id)
    job_ids = select(AIJob.id).where(AIJob.group_id == group_id)
    await db.execute(delete(AICandidate).where(AICandidate.job_id.in_(job_ids)))
    await db.execute(delete(AIAttempt).where(AIAttempt.job_id.in_(job_ids)))
    await db.execute(delete(AIJob).where(AIJob.group_id == group_id))
    await db.execute(delete(AIRun).where(AIRun.group_id == group_id))
    await db.execute(
        update(RawMessage)
        .where(RawMessage.group_id == group_id, RawMessage.message_date >= group.bot_added_at)
        .values(processing_state="pending")
    )
    activity = await db.get(GroupAIActivity, group_id)
    if activity is not None:
        activity.last_batch_slot = None
    profile = await db.get(GroupAIProfile, group_id)
    if profile is None:
        from studgroup.group_structure import ensure_profile

        profile = await ensure_profile(db, group_id, now)
    profile.state = "mapping"
    profile.backfill_requested = True
    profile.requested_at = now
    profile.mapped_at = None
    profile.completed_at = None
    await db.flush()
    return before
