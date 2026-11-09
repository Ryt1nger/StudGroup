"""A group's official code must match exactly and resolve to one UUID."""

from uuid import UUID

import pytest

from studgroup.ranepa_discovery import (
    DiscoveryError,
    exact_code_match,
    parse_uuid,
    resolve_group_uuid,
)

CODE = "Э-ОБ-385-04-26-02"
KNOWN = UUID("00000000-0000-4000-8000-000000000001")
OTHER = UUID("00000000-0000-4000-8000-000000000002")


def test_exact_code_with_suffix_and_dash_normalization():
    assert exact_code_match(CODE, CODE)
    assert exact_code_match(CODE + " (ЭМИТ) 38.03.05", CODE)
    assert exact_code_match(CODE + " / ЭМИТ", CODE)
    assert not exact_code_match(CODE + "0", CODE)
    assert not exact_code_match(CODE + "-1", CODE)
    assert not exact_code_match("ОБ-385-04-26-02", CODE)
    assert not exact_code_match("Э-ОБ-385-04-26-01", CODE)


def test_nested_group_catalog_finds_matching_record_without_other_cohorts():
    data = {
        "result": {
            "groups": [
                {"name": CODE + " (ЭМИТ)", "id": str(KNOWN)},
                {"name": "Э-ОБ-385-04-26-01", "id": str(OTHER)},
                {"name": CODE + "-1", "id": str(OTHER)},
                {"name": "ОБ-385-04-26-02", "id": str(OTHER)},
            ]
        }
    }
    assert resolve_group_uuid(data, CODE) == KNOWN


def test_href_links_count_as_ids_but_foreign_urls_do_not():
    payload = [
        {"title": CODE, "href": f"https://my.ranepa.ru/schedule/?by=group&f={KNOWN}"},
    ]
    assert resolve_group_uuid(payload, CODE) == KNOWN
    assert parse_uuid(f"https://example.org/?f={OTHER}") is None


def test_ambiguous_or_missing_code_is_never_accepted():
    with pytest.raises(DiscoveryError, match="group_not_found"):
        resolve_group_uuid([{"name": "Э-ОБ-385-04-26-01", "uuid": str(KNOWN)}], CODE)
    with pytest.raises(DiscoveryError, match="ambiguous_group"):
        resolve_group_uuid(
            [
                {"name": CODE, "uuid": str(KNOWN)},
                {"name": CODE + " / ЭМИТ", "uuid": str(OTHER)},
            ],
            CODE,
        )


def test_catalog_limits_protect_from_massive_malformed_responses():
    from studgroup.ranepa_discovery import find_candidates

    with pytest.raises(DiscoveryError, match="oversized_catalog"):
        find_candidates([{}, {}, {}, {}], CODE, max_nodes=2)
