"""Explicit transitions for structured memory; model confidence never verifies a fact."""

VERIFICATION_SOURCES = {"headman_manual", "schedule_authoritative"}


def next_status(current, requested, verification_source=None):
    if requested == "verified" and verification_source not in VERIFICATION_SOURCES:
        raise ValueError("verification_requires_authoritative_source")
    allowed = {
        "proposed": {"proposed", "verified", "rejected", "expired"},
        "verified": {"verified", "expired"},
        "rejected": {"rejected"},
        "expired": {"expired"},
    }
    if requested not in allowed[current]:
        raise ValueError("invalid_knowledge_transition")
    return requested
