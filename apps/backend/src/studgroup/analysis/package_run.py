"""Contiguous-window package analysis: one Pass 1 and one Pass 2 for many target messages.

Offline and side-effect free: no database, no migration, no product write, no flag. The single
Pass 2 request already reads every supplied message as a whole, so a package is a contiguous
window of the chat (targets plus a little read-only context on both edges) instead of one
overlapping ~12 KB neighbourhood per message. Every target is paid for once.

Attribution is by evidence: a target is ``card`` when an accepted proposal cites it,
``candidate`` when an unpublished candidate with confirmed sources cites it, otherwise ``empty``
(analysed, nothing found). A candidate whose sources were not confirmed is attached to every
target by code (``defaulted_to_target``); it never attributes anything and is only counted.
A target of a failed package is ``not_analysed``, never ``empty``.
"""

import hashlib
import json
from collections import Counter
from dataclasses import dataclass, field
from decimal import Decimal
from time import monotonic

from studgroup.analysis.context_strategy import CONTEXT_PROMPT_VERSION, prepare_context_request
from studgroup.analysis.contracts import AnalysisInput
from studgroup.analysis.extraction_strategy import (
    EXTRACTION_PROMPT_VERSION,
    prepare_extraction_request,
)
from studgroup.analysis.provider import ProviderResponse
from studgroup.analysis.shadow import ShadowPassFailure, run_shadow, usage_cost_usd

# Pass 2 returns at most 4000 tokens; a window with many targets can hit that ceiling.
DEFAULT_MAX_TARGETS = 24
DEFAULT_OVERLAP = 8
MAX_MESSAGES = 80  # AnalysisInput.messages limit
DEFAULT_MAX_BYTES = 48000
# Deliberately pessimistic for Cyrillic JSON: more tokens than the real tokenizer produces.
CHARS_PER_TOKEN = Decimal("2.0")
_MISS_USD_PER_TOKEN = Decimal("0.30") / Decimal(1000000)
_OUT_USD_PER_TOKEN = Decimal("1.20") / Decimal(1000000)
_PASS1_MAX_OUTPUT = 1800


@dataclass(frozen=True)
class Package:
    sequence: int
    target_ids: tuple[int, ...]
    analysis_input: AnalysisInput
    key: str
    message_bytes: int


@dataclass
class PackageOutcome:
    sequence: int
    target_ids: tuple[int, ...]
    state: str  # completed | failed | not_run_budget
    cost_usd: Decimal = Decimal(0)
    prompt_tokens: int = 0
    completion_tokens: int = 0
    prompt_cache_hit_tokens: int = 0
    latency_ms: int = 0
    attempts: int = 0
    error: str | None = None
    per_target: dict = field(default_factory=dict)
    unattributed_candidates: int = 0
    validation: object = None
    context: object = None


def _size(message):
    return len(json.dumps(message.model_dump(mode="json"), ensure_ascii=False).encode())


def _window(stream, index, chunk, overlap):
    low = max(0, chunk[0] - overlap)
    high = min(len(stream) - 1, chunk[-1] + overlap)
    selected = {stream[i].telegram_message_id: stream[i] for i in range(low, high + 1)}
    for position in chunk:
        parent = stream[position].reply_to_message_id
        if parent in index and parent not in selected:
            selected[parent] = stream[index[parent]]
    return sorted(selected.values(), key=lambda row: (row.message_date, row.telegram_message_id))


def package_key(target_ids, revisions, source_revision):
    payload = [
        sorted((i, revisions[i]) for i in target_ids),
        source_revision,
        CONTEXT_PROMPT_VERSION,
        EXTRACTION_PROMPT_VERSION,
    ]
    return hashlib.sha256(json.dumps(payload).encode()).hexdigest()


def plan_packages(
    messages,
    *,
    group_id,
    timezone,
    target_ids=None,
    max_targets=DEFAULT_MAX_TARGETS,
    overlap=DEFAULT_OVERLAP,
    max_messages=MAX_MESSAGES,
    max_bytes=DEFAULT_MAX_BYTES,
    source_revision=1,
):
    """Cut the chronological stream into packages; deterministic and idempotent."""
    if not (1 <= max_targets <= 80 and overlap >= 0 and 1 <= max_messages <= MAX_MESSAGES):
        raise ValueError("package_limits_invalid")
    stream = sorted(messages, key=lambda row: (row.message_date, row.telegram_message_id))
    index = {row.telegram_message_id: i for i, row in enumerate(stream)}
    if len(index) != len(stream):
        raise ValueError("duplicate_source_message_id")
    wanted = None if target_ids is None else set(target_ids)
    targets = [
        i for i, row in enumerate(stream) if wanted is None or row.telegram_message_id in wanted
    ]
    if any(stream[i].revision != source_revision for i in targets):
        raise ValueError("package_mixed_target_revisions")
    revisions = {row.telegram_message_id: row.revision for row in stream}
    packages = []
    position = 0
    while position < len(targets):
        count = min(max_targets, len(targets) - position)
        while True:
            chunk = targets[position : position + count]
            window = _window(stream, index, chunk, overlap)
            used = sum(_size(row) for row in window)
            if len(window) <= max_messages and used <= max_bytes:
                break
            if count == 1:
                raise ValueError("package_single_target_exceeds_limits")
            count -= 1
        ids = tuple(stream[i].telegram_message_id for i in chunk)
        packages.append(
            Package(
                sequence=len(packages) + 1,
                target_ids=ids,
                analysis_input=AnalysisInput(
                    group_id=group_id,
                    group_timezone=timezone,
                    source_revision=source_revision,
                    target_message_ids=list(ids),
                    messages=window,
                ),
                key=package_key(ids, revisions, source_revision),
                message_bytes=used,
            )
        )
        position += count
    return packages


def _request_chars(request):
    return len(request.system_prompt) + len(json.dumps(request.user_payload, ensure_ascii=False))


def worst_case_call_usd(request):
    tokens = Decimal(_request_chars(request)) / CHARS_PER_TOKEN
    return tokens * _MISS_USD_PER_TOKEN + Decimal(request.max_output_tokens) * _OUT_USD_PER_TOKEN


def estimate_package_usd(package, memory=None):
    """Upper bound for both passes: no cache, full output ceilings, pessimistic tokenizer."""
    first = prepare_context_request(package.analysis_input, memory or [])
    second = prepare_extraction_request(package.analysis_input, {"relevant_source_ids": []})
    # Pass 2 also carries Pass 1's answer; allow for a full-size one.
    understood = Decimal(_PASS1_MAX_OUTPUT) * _MISS_USD_PER_TOKEN
    return worst_case_call_usd(first) + worst_case_call_usd(second) + understood


class BudgetGuard:
    """Counts reported usage and refuses a call whose worst case no longer fits the cap."""

    def __init__(self, provider, cap_usd):
        self._provider = provider
        self.cap_usd = Decimal(str(cap_usd))
        self.spent_usd = Decimal(0)
        self.calls = 0

    async def complete(self, request) -> ProviderResponse:
        from studgroup.ai import ProviderFailure

        if self.spent_usd + worst_case_call_usd(request) > self.cap_usd:
            raise ProviderFailure("budget_cap", False, reservation_releasable=True)
        self.calls += 1
        try:
            response = await self._provider.complete(request)
        except ProviderFailure as error:
            if error.usage is not None:
                self.spent_usd += usage_cost_usd(error.usage)
            else:
                # Unknown usage: keep the call's whole worst case as the conservative charge.
                self.spent_usd += worst_case_call_usd(request)
            raise
        self.spent_usd += usage_cost_usd(response.usage)
        return response


def attribute_targets(package, validation):
    """Per target: card / candidate / empty. Returns (per_target, unattributed_candidates)."""
    cited = {ref.telegram_message_id for item in validation.accepted for ref in item.evidence}
    confirmed = [row for row in validation.candidates if row.source_status != "defaulted_to_target"]
    candidate_cited = {ref.telegram_message_id for row in confirmed for ref in row.evidence}
    per_target = {
        target: "card" if target in cited else "candidate" if target in candidate_cited else "empty"
        for target in package.target_ids
    }
    return per_target, len(validation.candidates) - len(confirmed)


async def run_packages(
    provider,
    packages,
    *,
    cap_usd,
    memory=None,
    repair_enabled=False,
    max_attempts=2,
    on_outcome=None,
):
    """Run packages in order; stop spending before a package that cannot fit the remaining cap."""
    guard = BudgetGuard(provider, cap_usd)
    outcomes = []
    for package in packages:
        outcome = PackageOutcome(package.sequence, package.target_ids, "not_run_budget")
        outcomes.append(outcome)
        if guard.spent_usd + estimate_package_usd(package, memory) > guard.cap_usd:
            if on_outcome:
                on_outcome(outcome)
            continue
        before = guard.spent_usd
        started = monotonic()
        for attempt in range(1, max_attempts + 1):
            outcome.attempts = attempt
            try:
                result = await run_shadow(
                    guard, package.analysis_input, memory or [], repair_enabled=repair_enabled
                )
            except ShadowPassFailure as error:
                outcome.state = "failed"
                outcome.error = error.code
                outcome.prompt_tokens += error.usage.prompt_tokens
                outcome.completion_tokens += error.usage.completion_tokens
                outcome.prompt_cache_hit_tokens += error.usage.prompt_cache_hit_tokens
                if not error.retryable:
                    break
                if guard.spent_usd + estimate_package_usd(package, memory) > guard.cap_usd:
                    break
                continue
            outcome.state = "completed"
            outcome.error = None
            outcome.prompt_tokens += result.prompt_tokens
            outcome.completion_tokens += result.completion_tokens
            outcome.prompt_cache_hit_tokens += result.prompt_cache_hit_tokens
            outcome.validation = result.validation
            outcome.context = result.context
            outcome.per_target, outcome.unattributed_candidates = attribute_targets(
                package, result.validation
            )
            break
        outcome.cost_usd = guard.spent_usd - before
        outcome.latency_ms = round((monotonic() - started) * 1000)
        if outcome.state != "completed":
            outcome.per_target = {target: "not_analysed" for target in package.target_ids}
        if on_outcome:
            on_outcome(outcome)
    return outcomes, guard


def summarize(outcomes, guard=None):
    """Counts and money only: no message text."""
    targets = Counter()
    kinds = Counter()
    rejected = Counter()
    candidates = Counter()
    raw = accepted = unattributed = 0
    for row in outcomes:
        targets.update(row.per_target.values())
        if row.state == "not_run_budget":
            targets["not_run_budget"] += len(row.target_ids)
        unattributed += row.unattributed_candidates
        if row.validation is not None:
            raw += row.validation.raw_proposal_count
            accepted += len(row.validation.accepted)
            kinds.update(item.semantic_kind for item in row.validation.accepted)
            rejected.update(item.reason_code for item in row.validation.rejected)
            candidates.update(item.category for item in row.validation.candidates)
    done = [row for row in outcomes if row.state in {"completed", "failed"}]
    return {
        "packages": len(outcomes),
        "completed": sum(row.state == "completed" for row in outcomes),
        "failed": sum(row.state == "failed" for row in outcomes),
        "not_run_budget": sum(row.state == "not_run_budget" for row in outcomes),
        "targets": dict(sorted(targets.items())),
        "raw_proposals": raw,
        "accepted_proposals": accepted,
        "accepted_by_kind": dict(sorted(kinds.items())),
        "rejected_by_reason": dict(sorted(rejected.items())),
        "candidates_by_category": dict(sorted(candidates.items())),
        "unattributed_candidates": unattributed,
        "errors": dict(sorted(Counter(row.error for row in done if row.error).items())),
        "prompt_tokens": sum(row.prompt_tokens for row in done),
        "completion_tokens": sum(row.completion_tokens for row in done),
        "prompt_cache_hit_tokens": sum(row.prompt_cache_hit_tokens for row in done),
        "cost_usd": str(sum((row.cost_usd for row in done), Decimal(0))),
        "provider_calls": None if guard is None else guard.calls,
    }
