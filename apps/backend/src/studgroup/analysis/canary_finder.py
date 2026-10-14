"""Read-only search for a useful canary package among stored, not yet analysed messages.

No AI call, no write. The result contains only ids, dates and counts (never message text), so
it is safe to print to the service log. A good canary is a task-like message that has provable
supplements (direct replies, files, links), has no shadow run for its current revision and fits
the per-package cost cap.
"""

import re
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select

from studgroup.academic_context import has_date_cue
from studgroup.models import (
    Group,
    GroupAnalysisSettings,
    Membership,
    MessageAttachment,
    RawMessage,
    ShadowEvaluationRun,
    User,
)

TASK_CUES = re.compile(
    r"презентац|доклад|реферат|конспект|эссе|задач|упражнен|номер|стр\.|параграф|проект|"
    r"сделать|подготовить|написать|решить|выучить|прочитать|сдать|принести",
    re.IGNORECASE,
)
URL = re.compile(r"https?://", re.IGNORECASE)
# The only real measurement: the 11-message package of the first canary cost $0.00207 and the
# planner uses $0.0025; the hard cap per package stays $0.04 regardless of this estimate.
TYPICAL_PACKAGE_USD = 0.0025


def _utc(value):
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


async def find_candidates(db, now=None, *, limit=5, window_days=7):
    now = _utc(now or datetime.now(UTC))
    group = await db.scalar(
        select(Group)
        .join(GroupAnalysisSettings, GroupAnalysisSettings.group_id == Group.id)
        .where(
            GroupAnalysisSettings.analysis_mode == "shadow",
            Group.status == "active",
            Group.pilot_authorized.is_(True),
            Group.bot_added_at.is_not(None),
        )
        .limit(1)
    )
    if group is None:
        return {"status": "group_unavailable", "candidates": []}
    lower = max(_utc(group.bot_added_at), now - timedelta(days=window_days))
    rows = (
        await db.scalars(
            select(RawMessage)
            .where(
                RawMessage.group_id == group.id,
                RawMessage.message_date >= lower,
                RawMessage.delete_at > now,
                RawMessage.text != "",
                ~select(ShadowEvaluationRun.id)
                .where(
                    ShadowEvaluationRun.raw_message_id == RawMessage.id,
                    ShadowEvaluationRun.source_revision == RawMessage.revision,
                    ShadowEvaluationRun.analysis_generation == RawMessage.analysis_generation,
                )
                .exists(),
            )
            .order_by(RawMessage.message_date)
        )
    ).all()
    if not rows:
        return {"status": "ok", "examined": 0, "candidates": []}
    attachments = {}
    for raw_id, count in (
        await db.execute(
            select(MessageAttachment.raw_message_id, func.count())
            .where(MessageAttachment.group_id == group.id, MessageAttachment.active.is_(True))
            .group_by(MessageAttachment.raw_message_id)
        )
    ).all():
        attachments[raw_id] = count
    roles = {
        telegram_id: role
        for telegram_id, role in (
            await db.execute(
                select(User.telegram_user_id, Membership.role)
                .join(Membership, Membership.user_id == User.id)
                .where(Membership.group_id == group.id, Membership.status == "active")
            )
        ).all()
    }
    replies_to = {}
    for message in (
        await db.scalars(
            select(RawMessage).where(
                RawMessage.group_id == group.id,
                RawMessage.message_date >= lower,
                RawMessage.reply_to_message_id.is_not(None),
            )
        )
    ).all():
        replies_to.setdefault(message.reply_to_message_id, []).append(message)
    scored = []
    for raw in rows:
        text = raw.text or ""
        if len(text) < 30 or not (TASK_CUES.search(text) or has_date_cue(text)):
            continue
        replies = replies_to.get(raw.telegram_message_id, [])
        reply_files = sum(attachments.get(item.id, 0) for item in replies)
        reply_text = sum(1 for item in replies if (item.text or "").strip())
        own_files = attachments.get(raw.id, 0)
        links = len(URL.findall(text)) + sum(len(URL.findall(item.text or "")) for item in replies)
        score = (
            3 * bool(replies)
            + 2 * bool(reply_files)
            + 2 * bool(reply_text)
            + 1 * bool(own_files)
            + 1 * bool(links)
        )
        if not score:
            continue
        size = len(text.encode()) + sum(len((item.text or "").encode()) for item in replies)
        scored.append(
            {
                "telegram_message_id": raw.telegram_message_id,
                "message_date": _utc(raw.message_date).isoformat(),
                "topic_id": raw.message_thread_id,
                "author_role": roles.get(raw.sender_id),
                "text_chars": len(text),
                "direct_replies": len(replies),
                "reply_text_messages": reply_text,
                "reply_files": reply_files,
                "own_files": own_files,
                "links": links,
                "score": score,
                "_size": size,
            }
        )
    scored.sort(key=lambda row: (-row["score"], row["message_date"]))
    top = scored[:limit]
    for row in top:
        row["supplement_text_bytes"] = row.pop("_size")
        row["estimated_cost_usd"] = TYPICAL_PACKAGE_USD
    return {"status": "ok", "examined": len(rows), "candidates": top}
