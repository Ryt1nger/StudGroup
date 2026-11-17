"""Evidence-attributed product outcomes, with no private source text in metrics."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from studgroup.analysis.product_outcomes import (
    PRODUCT_OUTCOMES,
    attributed_changes,
    blocked_image_ids,
    classify_targets,
    summarize_targets,
)
from studgroup.models import (
    Base,
    CanonicalAcademicItem,
    CanonicalItemEvidence,
    Group,
    RawMessage,
)


def test_classification_is_conservative_and_exhaustive():
    outcomes = classify_targets(
        [10, 11, 12, 13, 14, 15],
        changed={10: "published", 11: "updated"},
        accepted={12},
        candidates={13},
        blocked={14},
    )
    assert outcomes == {
        "10": "published",
        "11": "updated",
        "12": "needs_review",
        "13": "needs_review",
        "14": "blocked_by_image",
        "15": "no_academic_signal",
    }
    assert set(outcomes.values()) <= PRODUCT_OUTCOMES
    summary = summarize_targets(outcomes)
    assert summary["targets"] == 6
    assert summary["useful"] == 2
    assert summary["empty"] == 1
    assert json.dumps(outcomes)  # No model text or credentials enter metrics.


def test_rejections_without_attribution_are_not_misreported_as_empty():
    assert classify_targets([10, 11], rejected_count=4) == {
        "10": "needs_review",
        "11": "needs_review",
    }
    assert classify_targets([10], rejected_count=1) == {"10": "rejected"}
    assert classify_targets([10, 11], failed=True) == {
        "10": "failed",
        "11": "failed",
    }


def test_published_mapping_uses_changed_revision_and_exact_group(tmp_path):
    async def scenario():
        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/outcomes.db")
        try:
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            async with AsyncSession(engine, expire_on_commit=False) as db:
                group = Group(
                    id=uuid4(),
                    telegram_chat_id=-23100,
                    name="Private",
                    timezone="Europe/Moscow",
                )
                other_group = Group(
                    id=uuid4(),
                    telegram_chat_id=-23101,
                    name="Other",
                    timezone="Europe/Moscow",
                )
                now = datetime.now(UTC)
                source = RawMessage(
                    id=uuid4(),
                    group_id=group.id,
                    telegram_message_id=21,
                    text="private academic content",
                    message_date=now,
                    version_date=now,
                    delete_at=now + timedelta(days=2),
                )
                foreign_source = RawMessage(
                    id=uuid4(),
                    group_id=other_group.id,
                    telegram_message_id=22,
                    text="foreign private content",
                    message_date=now,
                    version_date=now,
                    delete_at=now + timedelta(days=2),
                )
                item = CanonicalAcademicItem(
                    id=uuid4(),
                    group_id=group.id,
                    destination="homework",
                    semantic_kind="homework",
                    title="Coursework",
                    description="",
                    state="active",
                    created_at=now,
                    updated_at=now,
                )
                db.add_all([group, other_group, source, foreign_source, item])
                await db.flush()
                db.add_all(
                    [
                        CanonicalItemEvidence(
                            item_id=item.id,
                            item_revision=1,
                            raw_message_id=source.id,
                            source_revision=1,
                            fact_path="title",
                        ),
                        CanonicalItemEvidence(
                            item_id=item.id,
                            item_revision=2,
                            raw_message_id=source.id,
                            source_revision=1,
                            fact_path="title",
                        ),
                        CanonicalItemEvidence(
                            item_id=item.id,
                            item_revision=2,
                            raw_message_id=foreign_source.id,
                            source_revision=1,
                            fact_path="title",
                        ),
                    ]
                )
                await db.flush()
                changed = await attributed_changes(
                    db, group.id, [(str(item.id), 2, "updated")], [21, 22]
                )
                assert changed == {21: "updated"}
                assert (
                    await attributed_changes(db, group.id, [(str(item.id), 3, "updated")], [21])
                    == {}
                )
        finally:
            await engine.dispose()

    asyncio.run(scenario())


def test_only_unresolved_image_dependencies_are_blocked():
    entries = [
        SimpleNamespace(telegram_message_id=2, state="queued"),
        SimpleNamespace(telegram_message_id=3, state="ready"),
        SimpleNamespace(telegram_message_id=4, state="failed"),
        SimpleNamespace(telegram_message_id=5, state="stale"),
    ]
    assert blocked_image_ids(SimpleNamespace(image_evidence=entries), [2, 3, 4, 5]) == {2}
