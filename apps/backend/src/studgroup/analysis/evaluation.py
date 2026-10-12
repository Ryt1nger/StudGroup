"""Entity-level shadow comparison without invented rollout thresholds."""

import math
import statistics
from dataclasses import dataclass


@dataclass(frozen=True)
class EntityMatch:
    reference_index: int
    candidate_index: int
    evidence_overlap: float


def evidence_ids(item):
    return {
        evidence.telegram_message_id
        for evidence in item.evidence
    }


def overlap(left, right):
    union = left | right
    return len(left & right) / len(union) if union else 0.0


def match_entities(reference, candidate):
    scored = []
    for reference_index, expected in enumerate(reference):
        for candidate_index, actual in enumerate(candidate):
            if expected.destination != actual.destination:
                continue
            score = overlap(evidence_ids(expected), evidence_ids(actual))
            if score:
                scored.append((-score, reference_index, candidate_index))
    matches = []
    used_reference = set()
    used_candidate = set()
    for negative_score, reference_index, candidate_index in sorted(scored):
        if reference_index in used_reference or candidate_index in used_candidate:
            continue
        used_reference.add(reference_index)
        used_candidate.add(candidate_index)
        matches.append(
            EntityMatch(reference_index, candidate_index, -negative_score)
        )
    return matches


def safe_ratio(numerator, denominator):
    return numerator / denominator if denominator else None


def compare_entities(reference, candidate, *, reference_actions=None, candidate_actions=None):
    matches = match_entities(reference, candidate)
    date_matches = 0
    audience_matches = 0
    kind_matches = 0
    action_matches = 0
    for match in matches:
        expected = reference[match.reference_index]
        actual = candidate[match.candidate_index]
        date_matches += expected.deadline == actual.deadline
        audience_matches += expected.audience == actual.audience
        kind_matches += expected.semantic_kind == actual.semantic_kind
        if reference_actions is not None and candidate_actions is not None:
            action_matches += (
                reference_actions[match.reference_index]
                == candidate_actions[match.candidate_index]
            )
    reference_sources = [evidence_ids(item) for item in reference]
    candidate_sources = [evidence_ids(item) for item in candidate]
    overmerge = sum(
        sum(bool(sources & expected) for expected in reference_sources) > 1
        for sources in candidate_sources
    )
    oversplit = sum(
        sum(bool(sources & actual) for actual in candidate_sources) > 1
        for sources in reference_sources
    )
    return {
        "reference_items": len(reference),
        "candidate_items": len(candidate),
        "matched_items": len(matches),
        "entity_precision": safe_ratio(len(matches), len(candidate)),
        "entity_recall": safe_ratio(len(matches), len(reference)),
        "deadline_accuracy": safe_ratio(date_matches, len(matches)),
        "audience_accuracy": safe_ratio(audience_matches, len(matches)),
        "kind_accuracy": safe_ratio(kind_matches, len(matches)),
        "action_accuracy": (
            safe_ratio(action_matches, len(matches))
            if reference_actions is not None and candidate_actions is not None
            else None
        ),
        "overmerge_count": overmerge,
        "oversplit_count": oversplit,
    }


def aggregate_display_reviews(reviews):
    if not reviews:
        return {
            "reviewed_items": 0,
            "factual_consistency": None,
            "completeness_mean": None,
            "readability_mean": None,
        }
    return {
        "reviewed_items": len(reviews),
        "factual_consistency": safe_ratio(
            sum(review["factually_consistent"] for review in reviews), len(reviews)
        ),
        "completeness_mean": statistics.mean(
            review["completeness"] for review in reviews
        ),
        "readability_mean": statistics.mean(
            review["readability"] for review in reviews
        ),
    }


def percentile(values, quantile):
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, math.ceil(quantile * len(ordered)) - 1)
    return ordered[index]


def aggregate_operations(samples):
    latencies = [sample["latency_ms"] for sample in samples]
    return {
        "runs": len(samples),
        "prompt_tokens": sum(sample["prompt_tokens"] for sample in samples),
        "completion_tokens": sum(sample["completion_tokens"] for sample in samples),
        "charged_usd": sum(sample["charged_usd"] for sample in samples),
        "latency_p50_ms": statistics.median(latencies) if latencies else None,
        "latency_p95_ms": percentile(latencies, 0.95),
    }
