from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from studgroup.analysis.batching import advance_cursor, plan_batches
from studgroup.analysis.context_linker import link_segments, persist_segment_plans
from studgroup.analysis.contracts import SourceMessage
from studgroup.models import ConversationSegment, Group, RawMessage, SegmentSourceLink

pytest_plugins = ["test_schedule_api"]

GROUP_ID = UUID("00000000-0000-0000-0000-000000000001")
BASE = datetime(2026, 10, 12, 9, tzinfo=UTC)


def source(mid, text, *, revision=1, minutes=0, reply=None, topic=None):
    return SourceMessage(
        telegram_message_id=mid,
        revision=revision,
        message_date=BASE + timedelta(minutes=minutes),
        text=text,
        reply_to_message_id=reply,
        topic_id=topic,
    )


def test_batch_plan_is_bounded_reproducible_and_revision_aware():
    rows = [source(2, "второе", minutes=1), source(1, "первое", revision=2)]
    first = plan_batches(rows, first_sequence=7, max_messages=1)
    second = plan_batches(list(reversed(rows)), first_sequence=7, max_messages=1)
    assert first == second
    assert [batch.sequence for batch in first] == [7, 8]
    assert first[0].source_versions == ((1, 2),)


def test_oversized_source_is_not_silently_skipped():
    with pytest.raises(ValueError, match="source_exceeds_batch_limit"):
        plan_batches([source(1, "x" * 1000)], max_bytes=100)


def test_cursor_never_advances_past_failed_deferred_or_missing_batch():
    assert advance_cursor(0, [(1, "completed"), (2, "failed"), (3, "completed")]) == 1
    assert advance_cursor(0, [(1, "completed"), (3, "completed")]) == 1
    assert advance_cursor(1, [(2, "empty"), (3, "completed")]) == 3


def test_reply_chain_forms_one_stable_segment_and_edit_changes_only_revision():
    first = link_segments(
        GROUP_ID,
        [source(10, "ДЗ по математике"), source(11, "Номера 1–3", reply=10, minutes=1)],
    )
    edited = link_segments(
        GROUP_ID,
        [
            source(10, "ДЗ по математике"),
            source(11, "Номера 1–4", revision=2, reply=10, minutes=1),
        ],
    )
    assert len(first) == len(edited) == 1
    assert first[0].stable_key == edited[0].stable_key
    assert edited[0].links[1].revision == 2


def test_late_reply_reopens_root_segment_instead_of_creating_unrelated_key():
    original = link_segments(GROUP_ID, [source(10, "ДЗ по математике")])
    reopened = link_segments(
        GROUP_ID,
        [source(10, "ДЗ по математике"), source(20, "Сделать 1–3", reply=10, minutes=90)],
    )
    assert original[0].stable_key == reopened[0].stable_key
    assert [link.reason for link in reopened[0].links] == ["standalone", "reply"]


def test_same_hashtag_and_time_alone_do_not_force_merge():
    plans = link_segments(
        GROUP_ID,
        [source(1, "#матан привет"), source(2, "#матан аудитория", minutes=1)],
    )
    assert len(plans) == 2


def test_topic_neighborhood_requires_semantic_overlap_not_only_topic_id():
    plans = link_segments(
        GROUP_ID,
        [
            source(1, "Решить задачи по пределам", topic=5),
            source(2, "Решить задачи продолжение", topic=5, minutes=2),
            source(3, "Преподаватель задерживается", topic=5, minutes=3),
        ],
    )
    assert len(plans) == 2
    linked = next(plan for plan in plans if len(plan.links) == 2)
    assert linked.links[1].reason == "topic_semantic"


def test_segment_persistence_is_idempotent_and_edit_creates_new_version(client):
    async def run():
        async with AsyncSession(client.app.state.engine, expire_on_commit=False) as db:
            group = await db.scalar(select(Group).where(Group.telegram_chat_id == -1001))
            raw = RawMessage(
                group_id=group.id,
                telegram_message_id=10,
                sender_id=1,
                text="ДЗ по математике",
                message_date=BASE,
                version_date=BASE,
                revision=1,
                delete_at=BASE + timedelta(days=30),
            )
            db.add(raw)
            await db.commit()
            plans = link_segments(group.id, [source(10, raw.text)])
            first = await persist_segment_plans(db, group.id, plans, BASE)
            await db.commit()
            replayed = await persist_segment_plans(db, group.id, plans, BASE)
            await db.commit()
            assert first[0].id == replayed[0].id

            raw.revision = 2
            raw.text = "ДЗ по математике: номера 1–3"
            raw.version_date = BASE + timedelta(minutes=1)
            await db.commit()
            edited = link_segments(group.id, [source(10, raw.text, revision=2)])
            second = await persist_segment_plans(db, group.id, edited, BASE + timedelta(minutes=1))
            await db.commit()
            assert second[0].version == 2
            assert (
                await db.scalar(select(func.count()).select_from(ConversationSegment)) == 2
            )
            assert await db.scalar(select(func.count()).select_from(SegmentSourceLink)) == 2

    import asyncio

    asyncio.run(run())
