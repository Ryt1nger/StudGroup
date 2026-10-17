"""The single definition of a subject's identity.

``subject_id = uuid5(group_id, subject_name.casefold())`` is the formula that every writer
(AI processing, manual headman cards, imports, the C+ projection) has always used, so every stored
``Homework.subject_id`` follows it. It deliberately does NOT fold ё into е, strip or collapse
whitespace, or use any "normalised" form: changing that would give an existing subject a second id.

Matching a model's or a human's wording to a known subject is a separate, lenient concern (see
``match_key``) and must never be used to build an id. Renaming a subject gives a new id by design;
merging spelling variants is a data migration that needs its own, explicit strategy.
"""

import re
import uuid


def subject_uuid(group_id: uuid.UUID, name: str) -> uuid.UUID:
    """Identity of the subject called ``name`` in group ``group_id`` (exact legacy formula)."""
    return uuid.uuid5(group_id, name.casefold())


def match_key(text: str | None) -> str:
    """Lenient comparison form for MATCHING wording (casefold, ё=е, punctuation/spacing ignored).

    Never an id source: two names with the same match key keep their two different ids.
    """
    value = re.sub(r"[^\w]+", " ", (text or "").casefold().replace("ё", "е"))
    return " ".join(value.split())
