"""Read-only private export of one stored C+ shadow wave.

The export contains source-message text and must stay outside Git.  It never calls a
provider and wraps preview generation in the existing rolled-back savepoint.
"""

import argparse
import asyncio
import json
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from studgroup.analysis.card_editor import edit_card
from studgroup.analysis.contracts import AnalysisInput, ValidationResult
from studgroup.analysis.preview import build_preview
from studgroup.analysis.stored import effective_validation
from studgroup.main import Settings
from studgroup.models import Group, RawMessage, ShadowEvaluationRun


async def export_wave(db, wave_key: str, *, now: datetime | None = None):
    rows = (
        await db.execute(
            select(ShadowEvaluationRun, RawMessage)
            .join(RawMessage, RawMessage.id == ShadowEvaluationRun.raw_message_id)
            .where(ShadowEvaluationRun.metrics_json.contains(f'"wave": "{wave_key}"'))
            .order_by(RawMessage.message_date, RawMessage.telegram_message_id)
        )
    ).all()
    if not rows:
        raise RuntimeError(f"shadow wave not found: {wave_key}")
    group_ids = {run.group_id for run, _raw in rows}
    if len(group_ids) != 1:
        raise RuntimeError("shadow wave spans more than one group")
    group = await db.get(Group, next(iter(group_ids)))
    if group is None or group.bot_added_at is None:
        raise RuntimeError("wave group or bot_added_at is unavailable")

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
                "input": input_data.model_dump(mode="json") if input_data else None,
                "context_result": json.loads(run.context_result_json or "null"),
                "validation_result": stored.model_dump(mode="json") if stored else None,
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
    wave_cards = [
        {"id": f"shadow-preview-{index}", **card}
        for index, card in enumerate(
            (card for card in preview["cards"] if card["target_message_id"] in target_ids),
            start=1,
        )
    ]
    return {
        "schema_version": "studgroup-shadow-wave-export-1",
        "wave": wave_key,
        "exported_at": datetime.now(UTC),
        "contains_private_chat_data": True,
        "raw_provider_responses_available": False,
        "counts": {
            "targets": len(target_ids),
            "runs": len(runs),
            "stored_accepted_proposals": sum(
                len(run["validation_result"]["accepted"])
                for run in runs
                if run["validation_result"] is not None
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
