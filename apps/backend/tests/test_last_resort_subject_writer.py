"""The live writer uses timetable evidence only after semantic subject resolution."""

import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from studgroup import api
from studgroup.analysis import writer
from studgroup.analysis.card_editor import EditedCard
from studgroup.analysis.resolver import Resolution


@pytest.mark.parametrize("destination", ["task", "deadline"])
def test_writer_fallback_runs_only_for_missing_subject_and_marks_review(monkeypatch, destination):
    group_id = uuid4()
    group = SimpleNamespace(id=group_id, timezone="Europe/Moscow")
    db = SimpleNamespace(get=AsyncMock(return_value=group))
    source = SimpleNamespace(
        telegram_message_id=101,
        message_date=datetime(2026, 10, 14, 8, 0, tzinfo=UTC),
        text="Разобрать упражнения",
    )
    proposal = SimpleNamespace(
        subject=None,
        evidence=[SimpleNamespace(telegram_message_id=101, fact_paths=["title"])],
        action="create",
        existing_item_id=None,
        destination=destination,
        deadline=SimpleNamespace(at=None),
    )
    validation = SimpleNamespace(
        accepted=[proposal], accepted_ordinals=[0], rejected=[], source_revision=1
    )
    monkeypatch.setattr(writer, "effective_validation", lambda result, _messages, _tz: result)
    monkeypatch.setattr(
        writer,
        "edit_card",
        lambda _proposal, _messages, **_kwargs: EditedCard(
            title="Разобрать упражнения",
            description="Упражнения к семинару.",
            visibility="normal",
        ),
    )
    captured = []

    async def fake_resolve(_db, **kwargs):
        captured.append(kwargs["edited_card"])
        return Resolution("review", None, "proposal_requires_review")

    async def fake_calendar(_start, _end, _group, _db):
        return {
            "week_state": "ready",
            "lessons": [
                {
                    "subject": "Математический анализ",
                    "status": "scheduled",
                    "starts_at": "2026-10-14T10:40:00+03:00",
                    "ends_at": "2026-10-14T12:00:00+03:00",
                }
            ],
        }

    monkeypatch.setattr(writer, "resolve_proposal", fake_resolve)
    monkeypatch.setattr(writer, "recent_manual_cards", AsyncMock(return_value=[]))
    monkeypatch.setattr(api, "schedule_data", fake_calendar)
    asyncio.run(
        writer.apply_validated_batch(
            db,
            group_id=group_id,
            validation=validation,
            messages=[source],
            now=datetime(2026, 10, 14, 9, 0, tzinfo=UTC),
            subject_context={"names": ["Математический анализ", "История России"]},
        )
    )
    assert len(captured) == 1
    assert captured[0].subject == "Математический анализ"
    assert captured[0].visibility == "headman_attention"
    assert captured[0].attention_reasons == ["subject_inferred_from_current_lesson"]


def test_writer_does_not_override_semantic_subject_with_current_lesson(monkeypatch):
    group_id = uuid4()
    group = SimpleNamespace(id=group_id, timezone="Europe/Moscow")
    db = SimpleNamespace(get=AsyncMock(return_value=group))
    source = SimpleNamespace(
        telegram_message_id=102,
        message_date=datetime(2026, 10, 14, 8, 0, tzinfo=UTC),
        text="По истории подготовить конспект",
    )
    proposal = SimpleNamespace(
        subject="История России",
        evidence=[SimpleNamespace(telegram_message_id=102, fact_paths=["title"])],
        action="create",
        existing_item_id=None,
        destination="deadline",
        deadline=SimpleNamespace(at=None),
    )
    validation = SimpleNamespace(
        accepted=[proposal], accepted_ordinals=[0], rejected=[], source_revision=1
    )
    monkeypatch.setattr(writer, "effective_validation", lambda result, _messages, _tz: result)
    monkeypatch.setattr(
        writer,
        "edit_card",
        lambda _proposal, _messages, **_kwargs: EditedCard(
            title="Подготовить конспект",
            description="По истории.",
            subject="История России",
            visibility="normal",
        ),
    )
    captured = []

    async def fake_resolve(_db, **kwargs):
        captured.append(kwargs["edited_card"])
        return Resolution("review", None, "proposal_requires_review")

    async def no_calendar(*_args):
        raise AssertionError("Calendar must not load for an already resolved subject")

    monkeypatch.setattr(writer, "resolve_proposal", fake_resolve)
    monkeypatch.setattr(api, "schedule_data", no_calendar)
    asyncio.run(
        writer.apply_validated_batch(
            db,
            group_id=group_id,
            validation=validation,
            messages=[source],
            now=datetime(2026, 10, 14, 9, 0, tzinfo=UTC),
            subject_context={"names": ["Математический анализ", "История России"]},
        )
    )
    assert captured[0].subject == "История России"
    assert captured[0].visibility == "normal"
    assert "subject_inferred_from_current_lesson" not in captured[0].attention_reasons
