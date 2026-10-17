"""Contiguous-window package analysis: planning, attribution, budget and failure handling."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID

import pytest

from studgroup.ai import ProviderFailure
from studgroup.analysis.context_strategy import CONTEXT_PROMPT_VERSION
from studgroup.analysis.contracts import (
    ANALYSIS_ENGINE_VERSION,
    ANALYSIS_SCHEMA_VERSION,
    SourceMessage,
)
from studgroup.analysis.extraction_strategy import prepare_extraction_request
from studgroup.analysis.package_run import (
    BudgetGuard,
    attribute_targets,
    estimate_package_usd,
    plan_packages,
    run_packages,
    summarize,
)
from studgroup.analysis.provider import ProviderResponse, ProviderUsage

GROUP = UUID("00000000-0000-4000-8000-0000000000aa")
START = datetime(2026, 10, 12, 6, 0, tzinfo=UTC)
CARD_TEXT = "Физика: подготовить конспект параграфа до 15.10"


def stream(count=50, *, reply=None, text_for=None):
    rows = []
    for i in range(1, count + 1):
        text = (text_for or {}).get(i) or (CARD_TEXT if i == 10 else f"болтовня {i}")
        rows.append(
            SourceMessage(
                telegram_message_id=i,
                revision=1,
                message_date=START + timedelta(minutes=i),
                text=text,
                reply_to_message_id=(reply or {}).get(i),
            )
        )
    return rows


def plan(rows=None, **limits):
    return plan_packages(rows or stream(), group_id=GROUP, timezone="Europe/Moscow", **limits)


def proposal(target):
    return {
        "semantic_kind": "homework",
        "subject": "Физика",
        "title": "Конспект параграфа",
        "description": "Подготовить конспект параграфа.",
        "confidence": 90,
        "deadline": {"at": "2026-10-15T23:59:00+03:00", "date_only": False, "basis": "explicit"},
        "evidence": [{"telegram_message_id": target, "revision": 1, "fact_paths": ["description"]}],
    }


class Provider:
    """Two answers per package: a valid Pass 1 and a Pass 2 citing target 10 when present."""

    def __init__(self, usage=(500, 400)):
        self.requests = []
        self.usage = usage

    async def complete(self, request):
        self.requests.append(request)
        ids = [row["telegram_message_id"] for row in request.user_payload["messages"]]
        if request.prompt_version == CONTEXT_PROMPT_VERSION:
            content = json.dumps(
                {
                    "schema_version": ANALYSIS_SCHEMA_VERSION,
                    "engine_version": ANALYSIS_ENGINE_VERSION,
                    "source_revision": 1,
                    "relevant_source_ids": ids[:1],
                    "discussion_facts": [],
                    "conflicts": [],
                    "memory_proposals": [],
                }
            )
        else:
            proposals = [proposal(10)] if 10 in ids else []
            content = json.dumps(
                {
                    "schema_version": ANALYSIS_SCHEMA_VERSION,
                    "engine_version": ANALYSIS_ENGINE_VERSION,
                    "source_revision": 1,
                    "proposals": proposals,
                    "explicit_empty": not proposals,
                }
            )
        return ProviderResponse(
            content=content,
            usage=ProviderUsage(prompt_tokens=self.usage[0], completion_tokens=self.usage[1]),
            model="fake",
            finish_reason="stop",
            payload_sha256=str(len(self.requests)) * 64,
        )


def test_plan_covers_every_target_once_with_read_only_edges():
    packages = plan(max_targets=20, overlap=3)
    assert [len(p.target_ids) for p in packages] == [20, 20, 10]
    flat = [target for p in packages for target in p.target_ids]
    assert flat == list(range(1, 51))
    second = packages[1].analysis_input
    ids = [row.telegram_message_id for row in second.messages]
    assert ids == list(range(18, 44)) and second.target_message_ids == list(range(21, 41))
    assert len(second.messages) <= 80


def test_catalog_reaches_every_package_and_changes_the_key():
    from types import SimpleNamespace as NS

    from studgroup.analysis.subject_catalog import build_catalog

    rows = [
        NS(subject="Математический анализ", teacher=None),
        NS(subject="История России", teacher=None),
    ]
    catalog = build_catalog(GROUP, rows)
    with_catalog, without = plan(max_targets=20, subject_catalog=catalog), plan(max_targets=20)
    assert all(p.analysis_input.subject_catalog == catalog for p in with_catalog)
    assert [p.key for p in with_catalog] != [p.key for p in without]
    payload = prepare_extraction_request(with_catalog[0].analysis_input, {}).user_payload
    names = [row["name"] for row in payload["subject_catalog"]["subjects"]]
    assert sorted(names) == ["История России", "Математический анализ"]
    assert (
        "subject_catalog"
        not in prepare_extraction_request(without[0].analysis_input, {}).user_payload
    )


def test_plan_is_deterministic_and_keys_are_per_package():
    first, again = plan(max_targets=20), plan(max_targets=20)
    assert [p.key for p in first] == [p.key for p in again]
    assert len({p.key for p in first}) == len(first)
    assert plan(max_targets=10)[0].key != first[0].key


def test_plan_shrinks_a_package_to_the_message_and_byte_limits():
    rows = stream(200)
    packages = plan(rows, max_targets=80, overlap=10, max_messages=30)
    assert all(len(p.analysis_input.messages) <= 30 for p in packages)
    assert [t for p in packages for t in p.target_ids] == list(range(1, 201))
    with pytest.raises(ValueError, match="package_single_target_exceeds_limits"):
        plan(rows, max_targets=5, overlap=10, max_bytes=300)


def test_plan_targets_subset_keeps_other_messages_as_context_only():
    packages = plan(target_ids={5, 30}, max_targets=24, overlap=2)
    assert [p.target_ids for p in packages] == [(5, 30)]
    assert {5, 30} <= {row.telegram_message_id for row in packages[0].analysis_input.messages}


def test_plan_adds_the_reply_parent_outside_the_window():
    packages = plan(stream(reply={40: 2}), target_ids={40}, max_targets=1, overlap=2)
    ids = [row.telegram_message_id for row in packages[0].analysis_input.messages]
    assert 2 in ids and 40 in ids and ids == sorted(ids)


def test_run_pays_two_calls_per_package_not_per_target():
    packages = plan(max_targets=25, overlap=3)
    provider = Provider()
    outcomes, guard = asyncio.run(run_packages(provider, packages, cap_usd="1"))
    assert len(provider.requests) == 2 * len(packages) == 4
    assert guard.calls == 4
    first = outcomes[0]
    assert first.per_target[10] == "card"
    assert {v for k, v in first.per_target.items() if k != 10} == {"empty"}
    assert {v for v in outcomes[1].per_target.values()} == {"empty"}
    report = summarize(outcomes, guard)
    assert report["targets"] == {"card": 1, "empty": 49}
    assert report["accepted_proposals"] == 1 and report["completed"] == 2
    assert Decimal(report["cost_usd"]) == guard.spent_usd > 0


def test_budget_cap_is_never_exceeded_and_unrun_packages_are_marked():
    packages = plan(max_targets=10, overlap=2)
    estimate = estimate_package_usd(packages[0])
    # Each call really costs a fifth of the package estimate: the cap fits two packages only.
    tokens = int(estimate * Decimal("0.2") * 1000000 / Decimal("0.30"))
    outcomes, guard = asyncio.run(
        run_packages(Provider(usage=(tokens, 0)), packages, cap_usd=str(estimate * Decimal("1.5")))
    )
    assert guard.spent_usd <= guard.cap_usd
    states = [o.state for o in outcomes]
    assert states == ["completed", "completed"] + ["not_run_budget"] * (len(packages) - 2)
    assert summarize(outcomes, guard)["targets"]["not_run_budget"] == sum(
        len(p.target_ids) for p in packages[2:]
    )
    none, empty_guard = asyncio.run(run_packages(Provider(), packages, cap_usd="0.0000001"))
    assert empty_guard.calls == 0 and {o.state for o in none} == {"not_run_budget"}


def test_guard_refuses_a_call_that_no_longer_fits():
    packages = plan(max_targets=10)
    guard = BudgetGuard(Provider(), "0.000001")
    request = SimpleNamespace(
        system_prompt="x" * 1000, user_payload={"a": "b"}, max_output_tokens=1800
    )
    with pytest.raises(ProviderFailure) as error:
        asyncio.run(guard.complete(request))
    assert error.value.code == "budget_cap" and guard.calls == 0 and packages


def test_failed_package_is_not_analysed_and_never_empty():
    class Failing(Provider):
        async def complete(self, request):
            raise ProviderFailure("provider_timeout", True, usage=None)

    packages = plan(max_targets=25)[:1]
    outcomes, guard = asyncio.run(run_packages(Failing(), packages, cap_usd="1", max_attempts=2))
    [outcome] = outcomes
    assert outcome.state == "failed" and outcome.attempts == 2
    assert set(outcome.per_target.values()) == {"not_analysed"}
    assert guard.spent_usd > 0 and guard.spent_usd <= guard.cap_usd


def test_unreachable_provider_charges_nothing_but_unknown_failure_charges_worst_case():
    class Unreachable(Provider):
        async def complete(self, request):
            raise ProviderFailure("provider_unreachable", True, reservation_releasable=True)

    class Unknown(Provider):
        async def complete(self, request):
            raise ProviderFailure("provider_timeout", True)

    packages = plan(max_targets=25)[:1]
    _, free = asyncio.run(run_packages(Unreachable(), packages, cap_usd="1", max_attempts=1))
    _, paid = asyncio.run(run_packages(Unknown(), packages, cap_usd="1", max_attempts=1))
    assert free.spent_usd == 0 and free.calls == 1
    assert paid.spent_usd > 0


def test_defaulted_candidate_attributes_no_target():
    ref = SimpleNamespace(telegram_message_id=3)
    validation = SimpleNamespace(
        accepted=[],
        candidates=[
            SimpleNamespace(source_status="defaulted_to_target", evidence=[ref]),
            SimpleNamespace(
                source_status="verified", evidence=[SimpleNamespace(telegram_message_id=4)]
            ),
        ],
    )
    package = SimpleNamespace(target_ids=(3, 4, 5))
    per_target, unattributed = attribute_targets(package, validation)
    assert per_target == {3: "empty", 4: "candidate", 5: "empty"} and unattributed == 1


def test_outage_pauses_between_retries_and_leaves_the_rest_resumable():
    class Unreachable(Provider):
        async def complete(self, request):
            raise ProviderFailure("provider_unreachable", True, reservation_releasable=True)

    pauses = []

    async def fake_sleep(seconds):
        pauses.append(seconds)

    packages = plan(max_targets=5)
    assert len(packages) > 4
    outcomes, guard = asyncio.run(
        run_packages(
            Unreachable(), packages, cap_usd="1", max_attempts=2, outage_limit=3, sleep=fake_sleep
        )
    )
    states = [row.state for row in outcomes]
    assert states[:3] == ["failed"] * 3
    assert set(states[3:]) == {"not_run_outage"}
    assert pauses == [5, 5, 5]  # one pause before each of the three retries
    assert guard.calls == 6 and guard.spent_usd == 0
    assert summarize(outcomes, guard)["not_run_outage"] == len(packages) - 3


def test_failed_package_keeps_raw_answers_for_private_diagnosis():
    class BadContext(Provider):
        async def complete(self, request):
            return ProviderResponse(
                content="not json at all",
                usage=ProviderUsage(prompt_tokens=10, completion_tokens=3),
                model="fake",
                finish_reason="stop",
                payload_sha256="0" * 64,
            )

    [package] = plan(max_targets=25)[:1]
    [outcome], _ = asyncio.run(run_packages(BadContext(), [package], cap_usd="1", max_attempts=1))
    assert outcome.state == "failed"
    assert outcome.raw_results == ("not json at all",)
