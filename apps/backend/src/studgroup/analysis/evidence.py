"""Resolve proposal evidence to exact current source rows."""

from sqlalchemy import select

from studgroup.models import CanonicalItemEvidence, RawMessage


async def resolve_evidence(db, group_id, proposal):
    source_ids = {row.telegram_message_id for row in proposal.evidence}
    sources = (
        await db.scalars(
            select(RawMessage).where(
                RawMessage.group_id == group_id,
                RawMessage.telegram_message_id.in_(source_ids),
            )
        )
    ).all()
    by_id = {row.telegram_message_id: row for row in sources}
    if set(by_id) != source_ids or any(
        by_id[row.telegram_message_id].revision != row.revision for row in proposal.evidence
    ):
        raise ValueError("canonical_evidence_stale_or_missing")
    return by_id


def add_item_evidence(db, item, proposal, sources):
    for reference in proposal.evidence:
        for fact_path in reference.fact_paths:
            db.add(
                CanonicalItemEvidence(
                    item_id=item.id,
                    item_revision=item.revision,
                    raw_message_id=sources[reference.telegram_message_id].id,
                    source_revision=reference.revision,
                    fact_path=fact_path,
                )
            )
