"""Subject identity shared by C+ and the student API (no dependencies, importable anywhere).

One formula, the one ``studgroup.subjects`` (Codex) uses for a subject that has no published card
yet: ``uuid5(group_id, normalized_name)`` where the name is casefolded, ё folded and whitespace
collapsed. The catalog id, the ``subject_id`` of a projected card and the student API id are all
this value, so a subject has one identity wherever it is mentioned.

Known limit (inherent in name-derived ids): renaming a subject creates a new id; spelling variants
that normalise to the same string share one.
"""

import uuid


def subject_key(name):
    """Normalised name: the exact ``normalized`` of ``studgroup.subjects``."""
    return " ".join((name or "").casefold().replace("ё", "е").split())


def subject_uuid(group_id, name):
    return uuid.uuid5(group_id, subject_key(name))
