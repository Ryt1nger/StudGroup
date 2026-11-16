"""Product-level outcomes for every package target, without message text.

A technical "completed" run is not a product success. Outcome attribution
uses only explicit evidence; missing citations cannot be called "published".
"""

from collections import Counter

FINAL_OUTCOMES = frozenset(
    {
        "published",
        "updated",
        "no_academic_signal",
        "rejected",
        "blocked_by_image",
        "needs_review",
        "failed",
    }
)


def target_outcomes(targets, validation=None, *, promotion=None, blocked_image_ids=(), failed=False):
    """Classify every target exactly once with a conservative precedence.

    No accepted item is treated as a published card unless the writer reported
    a visible create/update. Unmatched proposals become review, not success.
    """
    targets = tuple(dict.fromkeys(targets))
    if failed:
        return {target: "failed" for target in targets}
    if validation is None:
        raise ValueError("validation_required_for_completed_run")
    promotion = promotion or {}
    published = promotion.get("created", 0) > 0
    updated = promotion.get("updated", 0) > 0
    accepted = {
        ref.telegram_message_id
        for item in validation.accepted
        for ref in item.evidence
    }
    reviewed = {
        ref.telegram_message_id
        for item in validation.candidates
        for ref in item.evidence
        if item.source_status != "defaulted_to_target"
    }
    blocked = set(blocked_image_ids)
    has_rejections = bool(validation.rejected)
    output = {}
    for target in targets:
        if target in accepted:
            # The individual create/update mapping is not persisted by the
            # promoter; if mixed actions occurred, do not guess the target.
            output[target] = (
                "published" if published and not updated
                else "updated" if updated and not published
                else "needs_review"
            )
        elif target in reviewed:
            output[target] = "needs_review"
        elif target in blocked:
            output[target] = "blocked_by_image"
        elif has_rejections:
            # Validation has no reliable rejected-proposal citation; this
            # package-level reason is conservative, not a proven miss.
            output[target] = "rejected"
        else:
            output[target] = "no_academic_signal"
    assert set(output.values()).issubset(FINAL_OUTCOMES)
    return output


def counts(outcomes):
    return dict(sorted(Counter(outcomes.values()).items()))
