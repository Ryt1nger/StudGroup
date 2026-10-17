"""Read-only private export of one stored C+ shadow wave.

The export contains source-message text and must stay outside Git.  It never calls a
provider and wraps preview generation in the existing rolled-back savepoint.
"""

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from studgroup.analysis.card_editor import edit_card
from studgroup.analysis.contracts import AnalysisInput, ValidationResult
from studgroup.analysis.preview import build_preview
from studgroup.analysis.stored import effective_validation
from studgroup.main import Settings
from studgroup.models import Group, RawMessage, ShadowEvaluationRun


class ShadowWaveUnavailable(RuntimeError):
    """The requested stored wave cannot be exported safely."""


async def resolve_wave_group(db, wave_key: str):
    group_ids = set(
        await db.scalars(
            select(ShadowEvaluationRun.group_id).where(
                ShadowEvaluationRun.metrics_json.contains(f'"wave": "{wave_key}"')
            )
        )
    )
    if not group_ids:
        raise ShadowWaveUnavailable("shadow_wave_not_found")
    if len(group_ids) != 1:
        raise ShadowWaveUnavailable("shadow_wave_group_ambiguous")
    group = await db.get(Group, next(iter(group_ids)))
    if group is None:
        raise ShadowWaveUnavailable("shadow_wave_group_missing")
    return group


def wave_preview_cards(cards, target_ids):
    indexed = [
        {"id": f"shadow-preview-{index}", "preview_index": index, **card}
        for index, card in enumerate(cards, start=1)
    ]
    return [card for card in indexed if card["target_message_id"] in target_ids], len(indexed)


async def export_wave(
    db,
    wave_key: str,
    *,
    now: datetime | None = None,
    expected_group_id=None,
    max_runs: int | None = None,
    max_stored_bytes: int | None = None,
):
    group = await resolve_wave_group(db, wave_key)
    if expected_group_id is not None and group.id != expected_group_id:
        raise ShadowWaveUnavailable("shadow_wave_group_mismatch")
    wave_filter = ShadowEvaluationRun.metrics_json.contains(f'"wave": "{wave_key}"')
    run_count, stored_bytes = (
        await db.execute(
            select(
                func.count(ShadowEvaluationRun.id),
                func.coalesce(
                    func.sum(
                        func.coalesce(func.length(ShadowEvaluationRun.input_json), 0)
                        + func.coalesce(func.length(ShadowEvaluationRun.context_result_json), 0)
                        + func.coalesce(func.length(ShadowEvaluationRun.validation_result_json), 0)
                        + func.coalesce(func.length(ShadowEvaluationRun.metrics_json), 0)
                        + func.coalesce(func.length(ShadowEvaluationRun.provider_payload_hashes), 0)
                    ),
                    0,
                ),
            ).where(wave_filter, ShadowEvaluationRun.group_id == group.id)
        )
    ).one()
    if max_runs is not None and run_count > max_runs:
        raise ShadowWaveUnavailable("shadow_wave_too_many_runs")
    if max_stored_bytes is not None and stored_bytes > max_stored_bytes:
        raise ShadowWaveUnavailable("shadow_wave_stored_data_too_large")
    rows = (
        await db.execute(
            select(ShadowEvaluationRun, RawMessage)
            .join(RawMessage, RawMessage.id == ShadowEvaluationRun.raw_message_id)
            .where(wave_filter, ShadowEvaluationRun.group_id == group.id)
            .order_by(RawMessage.message_date, RawMessage.telegram_message_id)
        )
    ).all()
    if not rows or len(rows) != run_count:
        raise ShadowWaveUnavailable("shadow_wave_sources_unavailable")
    group_ids = {run.group_id for run, _raw in rows}
    if group_ids != {group.id}:
        raise ShadowWaveUnavailable("shadow_wave_group_mismatch")
    if group is None or group.bot_added_at is None:
        raise ShadowWaveUnavailable("shadow_wave_group_boundary_unavailable")

    runs = []
    target_ids = set()
    for run, raw in rows:
        target_ids.add(raw.telegram_message_id)
        input_data = AnalysisInput.model_validate_json(run.input_json) if run.input_json else None
        stored = (
            ValidationResult.model_validate_json(run.validation_result_json)
            if run.validation_result_json
            else None
        )
        effective = (
            effective_validation(stored, input_data.messages, group.timezone)
            if stored is not None and input_data is not None
            else None
        )
        cards = []
        if effective is not None and input_data is not None:
            stored_by_ordinal = dict(zip(stored.accepted_ordinals, stored.accepted, strict=True))
            for ordinal, proposal in zip(
                effective.accepted_ordinals, effective.accepted, strict=True
            ):
                edited = edit_card(proposal, input_data.messages)
                original = stored_by_ordinal[ordinal]
                cards.append(
                    {
                        "ordinal": ordinal,
                        "stored_proposal": original.model_dump(mode="json"),
                        "effective_proposal": proposal.model_dump(mode="json"),
                        "edited_card": edited.model_dump(mode="json"),
                        "fallback": {
                            "used": edited.used_fallback,
                            "reasons": edited.attention_reasons,
                        },
                        "source_message_ids": sorted(
                            {item.telegram_message_id for item in proposal.evidence}
                        ),
                    }
                )
        runs.append(
            {
                "run_id": str(run.id),
                "target_message_id": raw.telegram_message_id,
                "state": run.state,
                "last_error": run.last_error,
                "engine_version": run.engine_version,
                "source_revision": run.source_revision,
                "analysis_generation": run.analysis_generation,
                "created_at": run.created_at,
                "completed_at": run.completed_at,
                "provider_payload_hashes": json.loads(run.provider_payload_hashes or "null"),
                "metrics": json.loads(run.metrics_json or "{}"),
                "input_json": input_data.model_dump(mode="json") if input_data else None,
                "context_result_json": json.loads(run.context_result_json or "null"),
                "validation_result_json": stored.model_dump(mode="json") if stored else None,
                "effective_validation": effective.model_dump(mode="json") if effective else None,
                "cards_before_cross_run_merge": cards,
            }
        )

    preview = await build_preview(
        db,
        group_id=group.id,
        since=group.bot_added_at,
        now=now or datetime.now(UTC),
        limit=200,
    )
    wave_cards, all_card_count = wave_preview_cards(preview["cards"], target_ids)
    return {
        "schema_version": "studgroup-shadow-wave-export-1",
        "wave": wave_key,
        "exported_at": datetime.now(UTC),
        "contains_private_chat_data": True,
        "raw_provider_responses_available": False,
        "field_provenance": {
            "stored_proposal": "validated_and_adapter_normalized_not_raw_provider_output",
            "effective_proposal": "stored_proposal_with_current_deterministic_read_rules",
            "edited_card": "effective_proposal_processed_by_card_editor_before_cross_run_merge",
            "preview.cards": "final_rolled_back_owner_preview_after_cross_run_merge",
        },
        "counts": {
            "targets": len(target_ids),
            "runs": len(runs),
            "stored_accepted_proposals": sum(
                len(run["validation_result_json"]["accepted"])
                for run in runs
                if run["validation_result_json"] is not None
            ),
            "effective_accepted_proposals": sum(
                len(run["effective_validation"]["accepted"])
                for run in runs
                if run["effective_validation"] is not None
            ),
            "preview_cards": len(wave_cards),
        },
        "runs": runs,
        "preview": {
            "cards": wave_cards,
            "all_card_count": all_card_count,
            "review_marks": preview["review_marks"],
            "application_outcomes": preview["outcomes"],
        },
    }


async def _main(args):
    engine = create_async_engine(Settings().database_url, pool_pre_ping=True)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            bundle = await export_wave(db, args.wave)
            await db.rollback()
    finally:
        await engine.dispose()
    destination = Path(args.output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(bundle, ensure_ascii=False, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {"output": str(destination), "counts": bundle["counts"]},
            ensure_ascii=False,
        )
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wave", default="wave-a-spread-1")
    parser.add_argument(
        "--output",
        default="tmp/private/studgroup_cplus_wave_a.json",
        help="private local destination (tmp/ is ignored by Git)",
    )
    asyncio.run(_main(parser.parse_args()))


if __name__ == "__main__":
    main()
