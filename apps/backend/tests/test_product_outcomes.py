"""Every target has an explainable final outcome, without semantic overclaims."""

from types import SimpleNamespace

import pytest

from studgroup.analysis.product_outcomes import FINAL_OUTCOMES, counts, target_outcomes


def ref(mid):
    return SimpleNamespace(telegram_message_id=mid)


def data(*, accepted=(), candidates=(), rejected=()):
    return SimpleNamespace(
        accepted=[SimpleNamespace(evidence=[ref(n)]) for n in accepted],
        candidates=[
            SimpleNamespace(evidence=[ref(n)], source_status="confirmed")
            for n in candidates
        ],
        rejected=[SimpleNamespace(reason_code="proposal_schema_invalid") for _ in rejected],
    )


def test_classifies_all_seven_product_outcomes():
    cases = {
        "published": target_outcomes((1,), data(accepted=(1,)), promotion={"created": 1}),
        "updated": target_outcomes((2,), data(accepted=(2,)), promotion={"updated": 1}),
        "no_academic_signal": target_outcomes((3,), data()),
        "rejected": target_outcomes((4,), data(rejected=(1,))),
        "blocked_by_image": target_outcomes((5,), data(), blocked_image_ids={5}),
        "needs_review": target_outcomes((6,), data(candidates=(6,))),
        "failed": target_outcomes((7,), failed=True),
    }
    assert {next(iter(result.values())) for result in cases.values()} == FINAL_OUTCOMES
    assert all(len(result) == 1 for result in cases.values())


def test_no_false_success_when_promotion_is_ambiguous():
    results = target_outcomes(
        [1, 2, 3], data(accepted=(1,), candidates=(2,)),
        promotion={"created": 1, "updated": 1},
    )
    assert results == {1: "needs_review", 2: "needs_review", 3: "no_academic_signal"}
    assert counts(results) == {"needs_review": 2, "no_academic_signal": 1}


def test_rejection_cannot_hide_image_wait():
    results = target_outcomes([10, 11], data(rejected=(1,)), blocked_image_ids={10})
    assert results == {10: "blocked_by_image", 11: "rejected"}


def test_failed_targets_are_not_relabelled_empty():
    assert target_outcomes([1, 2], failed=True) == {1: "failed", 2: "failed"}


def test_missing_validation_never_silently_reports_no_academic_signal():
    with pytest.raises(ValueError, match="validation_required"):
        target_outcomes((1,), None)
