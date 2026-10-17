import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from studgroup.analysis.contracts import AnalysisInput, ValidationResult
from studgroup.export_shadow_wave import export_wave
from studgroup.models import Group, RawMessage, ShadowEvaluationRun

pytest_plugins = ["test_schedule_api"]


def test_export_shadow_wave_uses_only_stored_results_and_marks_fallback(client, monkeypatch):
    now = datetime(2026, 10, 12, 12, tzinfo=UTC)

    async def seed(db: AsyncSession):
        group = await db.scalar(select(Group).where(Group.telegram_chat_id == -1001))
        group_id = group.id
        group.bot_added_at = now - timedelta(days=1)
        raw = RawMessage(
            id=uuid4(),
            group_id=group_id,
            telegram_message_id=10,
            sender_id=1,
            text="По математике решить задачи 1–3",
            message_date=now,
            version_date=now,
            revision=1,
            analysis_generation=1,
            processing_state="completed",
            delete_at=now + timedelta(days=30),
        )
        data = AnalysisInput(
            group_id=group_id,
            group_timezone="Europe/Moscow",
            source_revision=1,
            target_message_ids=[10],
            messages=[
                {
                    "telegram_message_id": 10,
                    "revision": 1,
                    "message_date": now,
                    "text": raw.text,
                }
            ],
        )
        validation = ValidationResult(
            source_revision=1,
            raw_proposal_count=1,
            accepted_ordinals=[0],
            accepted=[
                {
                    "destination": "task",
                    "semantic_kind": "homework",
                    "subject": "Математика",
                    "title": "Задание",
                    "description": "Решить задачи 1–3",
                    "deadline": {},
                    "audience": {},
                    "confidence": 90,
                    "evidence": [
                        {
                            "telegram_message_id": 10,
                            "revision": 1,
                            "fact_paths": ["subject", "title", "description"],
                        }
                    ],
                }
            ],
            rejected=[],
            adapter_actions={},
            explicit_empty=False,
        )
        db.add_all(
            [
                raw,
                ShadowEvaluationRun(
                    id=uuid4(),
                    group_id=group_id,
                    raw_message_id=raw.id,
                    source_revision=1,
                    analysis_generation=1,
                    source_snapshot_sha256="a" * 64,
                    engine_version="cplus-shadow-1",
                    state="completed",
                    input_json=data.model_dump_json(),
                    context_result_json=json.dumps({"discussion_facts": []}),
                    validation_result_json=validation.model_dump_json(),
                    metrics_json=json.dumps({"wave": "wave-a-spread-1"}),
                    attempts=1,
                    prompt_tokens=0,
                    completion_tokens=0,
                    prompt_cache_hit_tokens=0,
                    reserved_usd=Decimal(0),
                    charged_usd=Decimal(0),
                    created_at=now,
                    completed_at=now,
                    delete_at=now + timedelta(days=30),
                ),
            ]
        )
        await db.commit()

    async def check():
        async with AsyncSession(client.app.state.engine) as db:
            await seed(db)
            result = await export_wave(db, "wave-a-spread-1", now=now)
            assert result["counts"] == {
                "targets": 1,
                "runs": 1,
                "stored_accepted_proposals": 1,
                "effective_accepted_proposals": 1,
                "preview_cards": 1,
            }
            card = result["runs"][0]["cards_before_cross_run_merge"][0]
            assert card["stored_proposal"]["title"] == "Задание"
            assert card["fallback"]["used"] is True
            assert "generic_title" in card["fallback"]["reasons"]
            assert result["preview"]["cards"][0]["id"] == "shadow-preview-1"

    import asyncio

    asyncio.run(check())
