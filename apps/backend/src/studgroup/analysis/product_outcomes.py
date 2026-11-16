"""Privacy-safe, evidence-attributed product outcomes for each C+ package target.

A completed model call is not a published card. We derive the user-visible
result from canonical revision evidence after promotion in the SAME transaction.
Only stable reason codes/counts are stored; no chat text or user identifiers.
"""

from collections import Counter
from uuid import UUID

from sqlalchemy import select

from studgroup.models import CanonicalItemEvidence, RawMessage


PRODUCT_OUTCOMES = frozenset(
    {
        "published",
        "updated",
        "no_academic_signal",
        "rejected",
        "blocked_by_image",
        "needs_review",
        "failed",
    }
)


def classify_targets(
    targets,
    *,
    changed=None,
    accepted=None,
    candidates=None,
    blocked=None,
    rejected_count=0,
    failed=False,
):
    """Exact outcome of an attributed target, conservative when attribution is ambiguous.

    Rejections without source attribution cannot be assigned to one target:
    unresolved targets say needs_review, not no_academic_signal.
    """
    accepted = set(accepted or ())
    candidates = set(candidates or ())
    blocked = set(blocked or ())
    changed = changed or {}
    rows = {}
    for target in targets:
        if failed:
            rows[str(target)] = "failed"
        elif target in blocked:
            rows[str(target)] = "blocked_by_image"
        elif changed.get(target) == "published":
            rows[str(target)] = "published"
        elif changed.get(target) == "updated":
            rows[str(target)] = "updated"
        elif target in accepted or target in candidates:
            rows[str(target)] = "needs_review"
        elif rejected_count:
            rows[str(target)] = "rejected" if len(targets) == 1 else "needs_review"
        else:
            rows[str(target)] = "no_academic_signal"
    return rows


def summarize_targets(outcomes):
    counts = Counter(outcomes.values())
    return {
        "targets": len(outcomes),
        "by_outcome": {key: counts.get(key, 0) for key in sorted(PRODUCT_OUTCOMES)},
        "useful": counts["published"] + counts["updated"],
        "empty": counts["no_academic_signal"],
    }


async def attributed_changes(db, group_id, revisions, targets):
    """Evidence of *changed revisions*, never old evidence of untouched items."""
    changed = {}
    for item_id, revision, action in revisions:
        state = "published" if action == "created" else "updated" if action == "updated" else None
        if state is None:
            continue
        telegram_ids = (
            await db.scalars(
                select(RawMessage.telegram_message_id)
                .join(CanonicalItemEvidence, CanonicalItemEvidence.raw_message_id == RawMessage.id)
                .where(
                    CanonicalItemEvidence.item_id == UUID(str(item_id)),
                    CanonicalItemEvidence.item_revision == revision,
                    RawMessage.group_id == group_id,
                    RawMessage.telegram_message_id.in_(targets),
                )
            )
        ).all()
        for telegram_id in telegram_ids:
            if changed.get(telegram_id) != "published":
                changed[telegram_id] = state
    return changed


def evidence_ids(items):
    return {
        ref.telegram_message_id
        for item in items
        for ref in getattr(item, "evidence", ())
    }


def blocked_image_ids(analysis_input, targets):
    """Only explicit unresolved attachment dependencies count as image blocking."""
    target_set = set(targets)
    return {
        item.telegram_message_id
        for item in analysis_input.image_evidence
        if item.telegram_message_id in target_set
        and item.state not in {"ready", "failed", "stale"}
    }
