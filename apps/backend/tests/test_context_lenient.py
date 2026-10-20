"""Lenient context validation (package worker): out-of-window references are dropped, not fatal."""

import json

import pytest
from test_analysis_shadow import analysis_input, context_payload

from studgroup.analysis.context_strategy import validate_context


def outside(message_id=9999):
    return {
        "fact": "Ответ на сообщение вне окна",
        "evidence": [{"telegram_message_id": message_id, "revision": 1, "fact_paths": ["subject"]}],
    }


def payload_with_outside():
    base = context_payload()
    base["relevant_source_ids"] = [10, 9999]
    base["discussion_facts"] = [*base["discussion_facts"], outside()]
    base["conflicts"] = [outside()]
    return json.dumps(base)


def test_strict_validation_still_rejects_a_reference_outside_the_package():
    with pytest.raises(ValueError, match="context_source_outside_envelope"):
        validate_context(payload_with_outside(), analysis_input())


def test_lenient_validation_drops_only_what_points_outside_the_package():
    result = validate_context(payload_with_outside(), analysis_input(), lenient=True)
    assert result.relevant_source_ids == [10]
    assert [fact.evidence[0].telegram_message_id for fact in result.discussion_facts] == [10]
    assert result.conflicts == []


def test_lenient_validation_keeps_a_fact_whose_other_evidence_is_inside():
    base = context_payload()
    mixed = outside()
    mixed["evidence"].append({"telegram_message_id": 10, "revision": 1, "fact_paths": ["subject"]})
    base["discussion_facts"] = [mixed]
    result = validate_context(json.dumps(base), analysis_input(), lenient=True)
    assert [row.telegram_message_id for row in result.discussion_facts[0].evidence] == [10]


def test_lenient_validation_does_not_relax_a_wrong_revision_or_bad_json():
    stale = context_payload()
    stale["discussion_facts"][0]["evidence"][0]["revision"] = 2
    # a wrong revision of a package message is dropped like any non-matching reference ...
    assert (
        validate_context(json.dumps(stale), analysis_input(), lenient=True).discussion_facts == []
    )
    # ... but unparseable output and a wrong package revision stay fatal
    with pytest.raises(ValueError, match="context_invalid_json"):
        validate_context("не json", analysis_input(), lenient=True)
    wrong = context_payload(source_revision=2)
    with pytest.raises(ValueError, match="context_source_revision_mismatch"):
        validate_context(json.dumps(wrong), analysis_input(), lenient=True)
