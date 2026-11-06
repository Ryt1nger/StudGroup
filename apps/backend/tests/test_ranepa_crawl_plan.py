"""Fairness and subgroup coverage of the first crawl rounds."""

from uuid import UUID

import pytest

from studgroup.ranepa_crawl_plan import CrawlCandidate, fair_crawl_order


def uid(number):
    return UUID(int=number)


def candidate(group, user, *subgroups):
    return CrawlCandidate(uid(group), uid(user), frozenset(uid(value) for value in subgroups))


def test_first_round_covers_groups_and_distinct_subgroups_before_repeats():
    rows = [
        candidate(1, 11, 101),
        candidate(1, 12, 101),
        candidate(1, 13, 102),
        candidate(2, 21, 201),
        candidate(2, 22, 202),
        candidate(3, 31),
    ]
    order = fair_crawl_order(list(reversed(rows)))
    assert [row.group_id for row in order[:3]] == [uid(1), uid(2), uid(3)]
    assert {row.subgroup_ids for row in order if row.group_id == uid(1)} == {
        frozenset({uid(101)}),
        frozenset({uid(102)}),
    }
    assert order.index(candidate(1, 13, 102)) < order.index(candidate(1, 12, 101))
    assert order == fair_crawl_order(rows)


def test_candidate_covering_two_subject_subgroups_is_prioritized():
    order = fair_crawl_order(
        [
            candidate(1, 2, 101),
            candidate(1, 3),
            candidate(1, 1, 101, 201),
        ]
    )
    assert [row.user_id for row in order] == [uid(1), uid(2), uid(3)]


def test_duplicate_account_in_same_group_is_rejected_but_cross_group_is_allowed():
    with pytest.raises(ValueError, match="duplicate_crawl_candidate"):
        fair_crawl_order([candidate(1, 1), candidate(1, 1, 101)])
    assert len(fair_crawl_order([candidate(1, 1), candidate(2, 1)])) == 2
