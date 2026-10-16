"""№04B: the whole chain, not only the DTOs.

provider answer (Pass 2) -> adapter -> validation -> durable storage -> re-read -> owner preview.
The provider answer is synthetic but shaped like a real structured response (several proposals, a
``candidates`` list, a synonym kind, an unknown kind, an unverifiable source). It is not a result of
the real ``wave-a-spread-1`` and says nothing about DeepSeek's own accuracy.
"""

import asyncio
import json
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from test_analysis_shadow import (
    NOW,
    HybridProvider,
    enable_shadow,
    product_snapshot,
)
from test_processing import source

from studgroup.analysis.candidates import event_records
from studgroup.analysis.contracts import (
    ANALYSIS_ENGINE_VERSION,
    ANALYSIS_SCHEMA_VERSION,
    AnalysisInput,
    ValidationResult,
)
from studgroup.analysis.dispatch import dispatch_next
from studgroup.analysis.extraction_strategy import EXTRACTION_PROMPT_VERSION
from studgroup.analysis.preview import build_preview
from studgroup.analysis.provider import ProviderResponse, ProviderUsage
from studgroup.analysis.review_text import render_preview
from studgroup.analysis.stored import effective_validation
from studgroup.main import Settings
from studgroup.models import Group, ShadowEvaluationRun

pytest_plugins = ["test_schedule_api"]
TEXT = "Квиз по цивилизациям в среду. В четверг после пар встреча студентов с выпускниками."


def structured_answer(message_id, revision):
    def cite(*ids):
        return [{"telegram_message_id": i, "revision": revision, "fact_paths": ["description"]} for i in ids]

    return {
        "schema_version": ANALYSIS_SCHEMA_VERSION,
        "engine_version": ANALYSIS_ENGINE_VERSION,
        "source_revision": revision,
        "proposals": [
            {   # the model's own word for a quiz: a synonym of the published kind "test"
                "semantic_kind": "Квиз", "subject": None, "title": "Квиз по цивилизациям",
                "description": "Квиз по цивилизациям в среду.", "confidence": 90,
                "audience": {"scope": "group", "label": None},
                "deadline": {"at": None, "date_only": False, "basis": "unknown"},
                "evidence": cite(message_id),
            },
            {   # a value the contract does not know and nobody may guess
                "semantic_kind": "лекция", "title": "Лекция по физике",
                "description": "Лекция по физике.", "confidence": 70, "evidence": cite(message_id),
            },
        ],
        "candidates": [
            {
                "category": "event", "title": "Встреча с выпускниками",
                "description": "Встреча студентов с выпускниками после пар.",
                "when_text": "в четверг после пар", "reason": "Встреча, не учебное задание",
                "confidence": 85, "evidence": cite(message_id),
            },
            {   # cites a message that does not exist
                "category": "event", "title": "Экскурсия на завод", "when_text": "в пятницу",
                "reason": "Событие", "confidence": 60, "evidence": cite(424242),
            },
            {"category": "organizational", "title": "Принести копии паспортов",
             "description": "Принести копии паспортов.", "confidence": 75, "evidence": cite(message_id)},
        ],
        "explicit_empty": False,
    }


class StructuredProvider(HybridProvider):
    async def complete(self, request):
        if request.prompt_version != EXTRACTION_PROMPT_VERSION:
            return await super().complete(request)
        self.requests.append(request)
        message = request.user_payload["messages"][-1]
        payload = structured_answer(message["telegram_message_id"], message["revision"])
        return ProviderResponse(
            content=json.dumps(payload, ensure_ascii=False),
            usage=ProviderUsage(prompt_tokens=300, completion_tokens=200),
            model="fake",
            finish_reason="stop",
            payload_sha256="4" * 64,
        )


def run_chain(client):
    enable_shadow(client)
    source(client, text=TEXT)
    provider = StructuredProvider()
    settings = Settings(ai_enabled=False, ai_shadow_enabled=True)
    before = product_snapshot(client)
    assert asyncio.run(dispatch_next(client.app.state.engine, settings, provider, NOW)) == "shadow_queued"
    assert asyncio.run(dispatch_next(client.app.state.engine, settings, provider, NOW)) == "shadow_completed"
    assert product_snapshot(client) == before  # shadow writes nothing to the product
    return provider


def stored(client):
    async def read():
        async with AsyncSession(client.app.state.engine) as db:
            row = await db.scalar(select(ShadowEvaluationRun))
            return row.validation_result_json, row.input_json, row.metrics_json

    return asyncio.run(read())


def test_pass2_answer_survives_adapter_storage_and_reread(client):
    provider = run_chain(client)
    assert len(provider.requests) == 2  # context + extraction: candidates cost no extra call
    raw_json, _, metrics = stored(client)
    assert '"candidates"' in raw_json
    result = ValidationResult.model_validate_json(raw_json)
    # the synonym became a published check; the unknown kind is held, not guessed
    (card,) = result.accepted
    assert (card.semantic_kind, card.destination) == ("test", "deadline")
    assert result.adapter_actions["semantic_kind_synonym_normalized"] == 1
    assert [r.salvaged for r in result.rejected] == [True]
    by_title = {c.title: c for c in result.candidates}
    assert set(by_title) == {
        "Лекция по физике", "Встреча с выпускниками", "Экскурсия на завод", "Принести копии паспортов"
    }  # fmt: skip
    assert by_title["Лекция по физике"].origin == "rejected_proposal"
    assert by_title["Встреча с выпускниками"].source_status == "verified"
    assert by_title["Экскурсия на завод"].source_status == "defaulted_to_target"
    assert json.loads(metrics)["accepted_items"] == 1
    # a second serialisation round trip changes nothing
    again = ValidationResult.model_validate_json(result.model_dump_json())
    assert again == result


def test_reading_a_stored_result_keeps_candidates_and_never_promotes_them(client):
    run_chain(client)
    raw_json, input_json, _ = stored(client)
    result = ValidationResult.model_validate_json(raw_json)
    data = AnalysisInput.model_validate_json(input_json)
    effective = effective_validation(result, data.messages, "Europe/Moscow")
    assert effective.candidates == result.candidates and len(effective.accepted) == 1
    records = event_records(effective)
    assert [r.title for r in records] == ["Встреча с выпускниками", "Экскурсия на завод"]
    verified, unverified = records
    assert verified.promotion_blockers == [] and verified.when_text == "в четверг после пар"
    assert unverified.promotion_blockers == ["unverified_source"]
    assert all(r.published is False for r in records)


def test_owner_preview_shows_held_candidates_with_their_source_status(client):
    run_chain(client)

    async def preview():
        async with AsyncSession(client.app.state.engine) as db:
            group = await db.scalar(select(Group).where(Group.telegram_chat_id == -1001))
            now = datetime.now(UTC) + timedelta(hours=1)
            report = await build_preview(
                db, group_id=group.id, since=now - timedelta(days=2), now=now
            )
            await db.rollback()
            return report

    report = asyncio.run(preview())
    held = {h["title"]: h for h in report["held_candidates"]}
    assert held["Встреча с выпускниками"]["category"] == "event"
    assert held["Встреча с выпускниками"]["source_status"] == "verified"
    assert held["Экскурсия на завод"]["source_status"] == "defaulted_to_target"
    assert [c["kind"] for c in report["cards"]] == ["test"]  # only the quiz is a card
    text = "\n".join(render_preview(report, -1001, 0, 5))
    assert "Не карточки" in text and "источник не подтверждён моделью" in text

    async def product_rows():
        async with AsyncSession(client.app.state.engine) as db:
            from studgroup.models import AcademicDeadline, Homework

            return [
                await db.scalar(select(func.count()).select_from(model))
                for model in (Homework, AcademicDeadline)
            ]

    assert asyncio.run(product_rows()) == [0, 0]  # preview leaves no trace
