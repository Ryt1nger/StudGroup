"""The stored deadline_basis vocabulary is closed and every value is readable by the student API."""

from types import SimpleNamespace

import pytest

from studgroup.analysis.deadline_policy import DEADLINE_BASES
from studgroup.student_cards import deadline_metadata


def test_vocabulary_is_exactly_the_spec_set():
    assert DEADLINE_BASES == {"explicit", "relative_resolved", "schedule_inferred", "unknown"}


@pytest.mark.parametrize("basis", sorted(DEADLINE_BASES))
def test_every_stored_basis_maps_to_a_student_status(basis):
    item = SimpleNamespace(deadline_basis=basis)
    status, source = deadline_metadata(SimpleNamespace(deadline_at=None), item)
    if basis == "unknown":
        assert (status, source) == ("unknown", "unknown")
    else:
        assert status in {"from_source", "inferred"} and source != "unknown"


def test_relative_resolved_is_shown_like_a_date_from_the_message():
    item = SimpleNamespace(deadline_basis="relative_resolved")
    assert deadline_metadata(SimpleNamespace(), item) == ("from_source", "explicit_message")


def test_rows_written_before_the_vocabulary_keep_their_meaning():
    item = SimpleNamespace(deadline_basis="ambiguous_range")
    assert deadline_metadata(SimpleNamespace(), item) == (
        "needs_clarification",
        "ambiguous_message",
    )
