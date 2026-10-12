"""Evidence-backed structured group memory persistence."""

import json

from sqlalchemy import select

from studgroup.analysis.knowledge_policy import next_status
from studgroup.models import (
    GroupKnowledgeEvidence,
    GroupKnowledgeFact,
    GroupKnowledgeHistory,
    RawMessage,
)

FACT_FIELDS = {
    "subject_alias": {"alias", "subject"},
    "teacher_alias": {"alias", "teacher"},
    "subject_teacher": {"subject", "teacher"},
}


def validate_fact_value(fact_type, value, confidence):
    allowed = FACT_FIELDS.get(fact_type)
    if allowed is None:
        raise ValueError("unsupported_knowledge_fact_type")
    if not 0 <= confidence <= 100:
        raise ValueError("invalid_knowledge_confidence")
    if (
        not isinstance(value, dict)
        or not value
        or set(value) - allowed
        or any(not isinstance(item, str) or not item.strip() or len(item) > 255 for item in value.values())
    ):
        raise ValueError("invalid_structured_knowledge_value")


async def propose_fact(
    db,
    *,
    group_id,
    fact_type,
    normalized_key,
    value,
    confidence,
    evidence,
    now,
):
    validate_fact_value(fact_type, value, confidence)
    if not evidence:
        raise ValueError("knowledge_fact_requires_evidence")
    source_ids = [message_id for message_id, _revision in evidence]
    rows = (
        await db.scalars(
            select(RawMessage).where(
                RawMessage.group_id == group_id,
                RawMessage.telegram_message_id.in_(source_ids),
            )
        )
    ).all()
    by_id = {row.telegram_message_id: row for row in rows}
    if set(by_id) != set(source_ids) or any(
        by_id[message_id].revision != revision for message_id, revision in evidence
    ):
        raise ValueError("knowledge_evidence_outside_group_or_stale")
    previous = await db.scalar(
        select(GroupKnowledgeFact)
        .where(
            GroupKnowledgeFact.group_id == group_id,
            GroupKnowledgeFact.fact_type == fact_type,
            GroupKnowledgeFact.normalized_key == normalized_key,
        )
        .order_by(GroupKnowledgeFact.revision.desc())
        .limit(1)
        .with_for_update()
    )
    value_json = json.dumps(value, ensure_ascii=False, sort_keys=True)
    if previous and previous.value_json == value_json and previous.status in {"proposed", "verified"}:
        return previous
    fact = GroupKnowledgeFact(
        group_id=group_id,
        fact_type=fact_type,
        normalized_key=normalized_key,
        value_json=value_json,
        status="proposed",
        confidence=confidence,
        revision=previous.revision + 1 if previous else 1,
        created_at=now,
        updated_at=now,
    )
    db.add(fact)
    await db.flush()
    for message_id, revision in evidence:
        db.add(
            GroupKnowledgeEvidence(
                fact_id=fact.id,
                raw_message_id=by_id[message_id].id,
                source_revision=revision,
                evidence_role="supports",
            )
        )
    db.add(
        GroupKnowledgeHistory(
            fact_id=fact.id,
            fact_revision=1,
            status="proposed",
            value_json=value_json,
            reason_code="model_proposal",
            created_at=now,
        )
    )
    return fact


async def transition_fact(db, fact, requested, *, verification_source, reason_code, now):
    fact.status = next_status(fact.status, requested, verification_source)
    fact.verification_source = verification_source
    fact.updated_at = now
    history_revision = await db.scalar(
        select(GroupKnowledgeHistory.fact_revision)
        .where(GroupKnowledgeHistory.fact_id == fact.id)
        .order_by(GroupKnowledgeHistory.fact_revision.desc())
        .limit(1)
    )
    db.add(
        GroupKnowledgeHistory(
            fact_id=fact.id,
            fact_revision=(history_revision or 0) + 1,
            status=fact.status,
            value_json=fact.value_json,
            reason_code=reason_code,
            created_at=now,
        )
    )
    return fact
