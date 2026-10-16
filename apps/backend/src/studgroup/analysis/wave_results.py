"""Read-only, text-free summary of a finished shadow wave: counts, tokens, money, ids.

Everything is derived from stored shadow runs and the same owner preview that ``/shadowcards``
shows (rolled-back savepoint), so the numbers describe exactly the cards the owner will inspect.
Nothing is written, no model is called and no message text is returned.
"""

from collections import Counter
from datetime import UTC, datetime

from sqlalchemy import select

from studgroup.analysis.contracts import AnalysisInput, ValidationResult
from studgroup.analysis.preview import build_preview
from studgroup.analysis.stored import effective_validation
from studgroup.analysis.subject_catalog import catalog_summary
from studgroup.models import Group, GroupAnalysisSettings, RawMessage, ShadowEvaluationRun

MAX_TARGET_IDS = 60


def _utc(value):
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


async def wave_results(db, now=None, *, wave_key="wave-a-spread-1"):
    now = _utc(now or datetime.now(UTC))
    group = await db.scalar(
        select(Group)
        .join(GroupAnalysisSettings, GroupAnalysisSettings.group_id == Group.id)
        .where(
            GroupAnalysisSettings.analysis_mode == "shadow",
            Group.status == "active",
            Group.pilot_authorized.is_(True),
            Group.bot_added_at.is_not(None),
        )
        .order_by(Group.id)
        .limit(1)
    )
    if group is None:
        return {"status": "group_unavailable"}
    rows = (
        await db.execute(
            select(ShadowEvaluationRun, RawMessage)
            .join(RawMessage, RawMessage.id == ShadowEvaluationRun.raw_message_id)
            .where(
                ShadowEvaluationRun.group_id == group.id,
                ShadowEvaluationRun.metrics_json.contains(f'"wave": "{wave_key}"'),
            )
            .order_by(RawMessage.message_date, RawMessage.telegram_message_id)
        )
    ).all()
    states, errors = Counter(), Counter()
    tokens = Counter()
    kinds, actions, rejected = Counter(), Counter(), Counter()
    held_categories, held_codes, held_sources = Counter(), Counter(), Counter()
    catalogs = Counter()
    per_run, empty_runs, accepted_total, raw_total = [], 0, 0, 0
    wave_ids = set()
    for run, raw in rows:
        states[run.state] += 1
        wave_ids.add(raw.telegram_message_id)
        tokens["prompt"] += run.prompt_tokens or 0
        tokens["completion"] += run.completion_tokens or 0
        tokens["cache_hit"] += run.prompt_cache_hit_tokens or 0
        tokens["charged_micro_usd"] += int((run.charged_usd or 0) * 1_000_000)
        tokens["attempts"] += run.attempts or 0
        if run.last_error:
            errors[run.last_error] += 1
        if run.state != "completed" or not run.validation_result_json:
            continue
        validation = ValidationResult.model_validate_json(run.validation_result_json)
        data = AnalysisInput.model_validate_json(run.input_json)
        validation = effective_validation(validation, data.messages, group.timezone)
        catalogs[catalog_summary(data.subject_catalog).get("fingerprint", "none")] += 1
        raw_total += validation.raw_proposal_count
        accepted_total += len(validation.accepted)
        if not validation.accepted and not validation.rejected and not validation.candidates:
            empty_runs += 1
        for candidate in validation.candidates:
            held_categories[candidate.category] += 1
            held_codes[candidate.hold_code] += 1
            held_sources[candidate.source_status] += 1
        for proposal in validation.accepted:
            kinds[proposal.semantic_kind] += 1
            actions[proposal.action] += 1
        for item in validation.rejected:
            rejected[item.reason_code] += 1
        per_run.append(
            {
                "target": raw.telegram_message_id,
                "raw": validation.raw_proposal_count,
                "accepted": len(validation.accepted),
                "rejected": len(validation.rejected),
            }
        )
    lower = group.bot_added_at
    preview = await build_preview(db, group_id=group.id, since=lower, now=now, limit=200)
    wave_cards = [card for card in preview["cards"] if card["target_message_id"] in wave_ids]
    cited = Counter()
    for card in wave_cards:
        for source in card["sources"]:
            cited[source["telegram_message_id"]] += 1
    shared = sorted(mid for mid, count in cited.items() if count > 1)
    statuses = Counter(card["status"] for card in wave_cards)
    return {
        "status": "ok",
        "wave": wave_key,
        "runs_by_state": dict(states),
        "errors": dict(errors),
        "tokens": {
            "prompt": tokens["prompt"],
            "completion": tokens["completion"],
            "prompt_cache_hit": tokens["cache_hit"],
            "attempts": tokens["attempts"],
            "charged_usd": f"{tokens['charged_micro_usd'] / 1_000_000:.6f}",
        },
        "proposals": {
            "raw": raw_total,
            "accepted": accepted_total,
            "rejected": sum(rejected.values()),
            "rejection_reasons": dict(rejected),
            "by_kind": dict(kinds),
            "by_action": dict(actions),
            "completed_runs_with_nothing_found": empty_runs,
            "completed_runs_with_cards": sum(1 for item in per_run if item["accepted"]),
        },
        # runs of one wave should share a catalog; several fingerprints mean the schedule changed
        "subject_catalogs": {"runs_by_fingerprint": dict(catalogs)},
        "held_candidates": {
            "total": sum(held_categories.values()),
            "by_category": dict(held_categories),
            "by_hold_code": dict(held_codes),
            "by_source_status": dict(held_sources),
        },
        "cards": {
            "total": len(wave_cards),
            "by_status": dict(statuses),
            "by_kind": dict(Counter(card["kind"] for card in wave_cards)),
            "with_subject": sum(1 for card in wave_cards if card["subject"]),
            "with_deadline": sum(1 for card in wave_cards if card["deadline_at"]),
            "deadline_basis": dict(Counter(card["deadline_basis"] for card in wave_cards)),
            "needs_attention": sum(1 for card in wave_cards if card["needs_attention"]),
            "multi_source": sum(1 for card in wave_cards if len(card["sources"]) > 1),
            "with_confirmed_materials": sum(1 for card in wave_cards if card["materials"]),
            "with_unconfirmed_candidates": sum(1 for card in wave_cards if card["candidates"]),
            "audience": dict(Counter(card["audience"]["scope"] for card in wave_cards)),
            "source_messages_cited_by_several_cards": shared[:MAX_TARGET_IDS],
        },
        "review_marks": dict(Counter(mark["reason"] for mark in preview["review_marks"])),
        "application_outcomes": preview["outcomes"],
        "runs": per_run[:MAX_TARGET_IDS],
    }
