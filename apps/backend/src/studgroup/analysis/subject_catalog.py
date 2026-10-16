"""Trusted per-group subject catalog: the internal contract C+ uses to tie a card to a subject.

What the model does
    It is given the group's real subjects (``id``, ``name``, ``aliases``, ``teachers``) next to the
    messages and decides from the meaning of the task and its context which subject it belongs to
    (or none). Nothing here guesses a subject from letters or keeps a table of group slang.

What the code does (deterministic, and only this)
    * accepts a catalog only for the group it was built for;
    * confirms the model's choice only when it names a subject of that catalog (by id, or by the
      exact catalog name/alias) and replaces the wording with the canonical name;
    * never turns an unknown id, a foreign subject or an ambiguous label into a confirmed subject.

Where the catalog comes from (integration point for Codex)
    ``load_subject_catalog`` is the single place that builds it. Today it derives it from
    ``SchedulePattern`` rows and verified ``subject_alias``/``subject_teacher`` memory facts. When
    Codex's subjects module exists, only the body of that function changes (stable ids, authoritative
    ``complete`` flag, version); the contract below and everything downstream stay as they are.
"""

import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass

from sqlalchemy import select

from studgroup.analysis.contracts import SubjectCatalog, SubjectEntry
from studgroup.models import SchedulePattern

MAX_ALIASES = 20
MAX_TEACHERS = 10
MAX_SUBJECTS = 80


def label_key(text):
    """Comparison form of a label: casefolded, ё folded, punctuation and spacing collapsed."""
    value = re.sub(r"[^\w]+", " ", (text or "").casefold().replace("ё", "е"))
    return " ".join(value.split())


@dataclass(frozen=True)
class SubjectResolution:
    # confirmed_id | confirmed_name | unknown_id | ambiguous | not_in_catalog | none
    status: str
    entry: SubjectEntry | None = None
    id_was_unknown: bool = False


def usable_catalog(catalog, group_id):
    """``(catalog_or_None, why_not)``: a catalog of another group or an empty one is not used."""
    if catalog is None:
        return None, None
    if catalog.group_id != group_id:
        return None, "foreign_group"
    if not catalog.subjects:
        return None, "empty"
    return catalog, None


def _index(catalog):
    by_id = {entry.id: entry for entry in catalog.subjects}
    by_label: dict[str, list[SubjectEntry]] = {}
    for entry in catalog.subjects:
        for label in {label_key(entry.name), *(label_key(alias) for alias in entry.aliases)}:
            if label:
                by_label.setdefault(label, []).append(entry)
    return by_id, by_label


def resolve_subject(catalog, subject_id, subject_text) -> SubjectResolution:
    """Check what the model named against the trusted catalog (``catalog`` is already usable)."""
    by_id, by_label = _index(catalog)
    sid = subject_id.strip() if isinstance(subject_id, str) and subject_id.strip() else None
    if sid is not None and sid in by_id:
        return SubjectResolution("confirmed_id", by_id[sid])
    unknown = sid is not None
    label = label_key(subject_text)
    if label:
        matches = by_label.get(label, [])
        if len(matches) == 1:
            return SubjectResolution("confirmed_name", matches[0], id_was_unknown=unknown)
        if len(matches) > 1:
            return SubjectResolution("ambiguous", None, id_was_unknown=unknown)
    if unknown:
        return SubjectResolution("unknown_id", None, id_was_unknown=True)
    return SubjectResolution("not_in_catalog" if label else "none")


def prompt_payload(catalog):
    """Compact catalog for the extraction request (no group id: it never leaves the process)."""
    return {
        "version": catalog.version,
        "complete": catalog.complete,
        "subjects": [
            {"id": row.id, "name": row.name}
            | ({"aliases": row.aliases} if row.aliases else {})
            | ({"teachers": row.teachers} if row.teachers else {})
            for row in catalog.subjects
        ],
    }


def _subject_id(key):
    return "sched-" + hashlib.sha1(key.encode()).hexdigest()[:12]


def build_catalog(group_id, patterns, facts=()):
    """Catalog from schedule rows (``subject``, ``teacher``) and verified knowledge facts."""
    spellings: dict[str, Counter] = {}
    teachers: dict[str, list[str]] = {}
    for row in patterns:
        key = label_key(row.subject)
        if not key:
            continue
        spellings.setdefault(key, Counter())[row.subject.strip()] += 1
        if row.teacher and row.teacher.strip():
            names = teachers.setdefault(key, [])
            if row.teacher.strip() not in names:
                names.append(row.teacher.strip())
    aliases: dict[str, list[str]] = {}
    for fact in facts:
        if fact.group_id != group_id or fact.status != "verified":
            continue
        try:
            value = json.loads(fact.value_json)
        except (TypeError, ValueError):
            continue
        if fact.fact_type == "subject_alias" and isinstance(value, dict):
            key, alias = label_key(value.get("subject")), (value.get("alias") or "").strip()
            if (
                key in spellings
                and alias
                and label_key(alias) != key
                and alias not in aliases.setdefault(key, [])
            ):
                aliases[key].append(alias)
        elif fact.fact_type == "subject_teacher" and isinstance(value, dict):
            key, teacher = label_key(value.get("subject")), (value.get("teacher") or "").strip()
            if key in spellings and teacher and teacher not in teachers.setdefault(key, []):
                teachers[key].append(teacher)
    entries = []
    for key in sorted(spellings):
        name = min(spellings[key].items(), key=lambda pair: (-pair[1], pair[0]))[0]
        entries.append(
            SubjectEntry(
                id=_subject_id(key),
                name=name[:255],
                aliases=[alias[:255] for alias in aliases.get(key, [])[:MAX_ALIASES]],
                teachers=[teacher[:255] for teacher in teachers.get(key, [])[:MAX_TEACHERS]],
            )
        )
    entries = entries[:MAX_SUBJECTS]
    version = hashlib.sha1(
        json.dumps([row.model_dump() for row in entries], ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()[:16]
    # The schedule shows the subjects that have lessons; it does not prove that nothing else
    # exists, so it never claims completeness. Codex's authoritative source may.
    return SubjectCatalog(group_id=group_id, version=version, complete=False, subjects=entries)


async def load_subject_catalog(db, group_id, facts=()):
    """The one place that produces a group's catalog (replace its body to plug in subjects.py).

    Returns ``None`` when the group has no known subjects.
    """
    rows = (
        await db.scalars(select(SchedulePattern).where(SchedulePattern.group_id == group_id))
    ).all()
    catalog = build_catalog(group_id, rows, facts)
    return catalog if catalog.subjects else None
