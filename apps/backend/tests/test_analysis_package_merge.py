"""Merging package results: dedupe across overlapping packages, card beats candidate."""

from studgroup.analysis.package_merge import merge, pick_packages


def ev(*ids):
    return [{"telegram_message_id": i, "revision": 1, "fact_paths": ["x"]} for i in ids]


def card(*ids, confidence=80):
    return {"title": "карточка", "confidence": confidence, "evidence": ev(*ids)}


def cand(*ids, confidence=50, source="verified"):
    return {
        "title": "кандидат",
        "confidence": confidence,
        "source_status": source,
        "evidence": ev(*ids),
    }


def package(sequence, targets, state="completed", accepted=(), candidates=()):
    return {
        "sequence": sequence,
        "target_ids": list(targets),
        "state": state,
        "accepted": list(accepted),
        "candidates": list(candidates),
    }


def test_same_finding_in_two_overlapping_packages_is_kept_once_where_it_is_a_target():
    first = package(1, range(1, 11), accepted=[card(9, 10, 11)])
    second = package(2, range(11, 21), accepted=[card(10, 11)])
    items, per_target = merge([first, second])
    assert len(items) == 1
    # 2 of 3 sources are targets of package 1; 1 of 2 are targets of package 2
    assert items[0]["package"] == 1 and items[0]["seen_in"] == [1, 2]
    assert per_target[10] == "card" and per_target[11] == "card" and per_target[12] == "empty"


def test_card_replaces_a_candidate_about_the_same_messages():
    first = package(1, range(1, 11), candidates=[cand(5, 6)])
    second = package(2, range(1, 11), accepted=[card(5, 6)])
    items, per_target = merge([first, second])
    assert [row["status"] for row in items] == ["card"]
    assert per_target[5] == "card"


def test_unrelated_items_stay_separate_and_unconfirmed_candidates_attribute_nothing():
    pk = package(
        1,
        range(1, 11),
        accepted=[card(1)],
        candidates=[cand(3), cand(4, source="defaulted_to_target")],
    )
    items, per_target = merge([pk])
    assert [row["status"] for row in items] == ["card", "candidate"]
    assert per_target[1] == "card" and per_target[3] == "candidate" and per_target[4] == "empty"


def test_failed_package_targets_are_not_analysed_and_pick_prefers_completed():
    failed = package(1, range(1, 4), state="failed")
    items, per_target = merge([failed])
    assert items == [] and set(per_target.values()) == {"not_analysed"}
    older = {"label": "a", "packages": [failed, package(2, [9], accepted=[card(9)])]}
    newer = {"label": "b", "packages": [package(1, range(1, 4), candidates=[cand(2)])]}
    picked = pick_packages([older, newer])
    assert [p["state"] for p in picked] == ["completed", "completed"]
    assert picked[0]["_label"] == "b"
    again = pick_packages([newer, {"label": "c", "packages": [failed]}])
    assert again[0]["state"] == "completed"
