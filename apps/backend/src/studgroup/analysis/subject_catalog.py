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
    ``SchedulePattern`` rows and verified ``subject_alias``/``subject_teacher`` memory facts. Ids are
    the shared ``studgroup.subject_identity.subject_uuid`` (the casefold-only formula of every
    writer). When Codex
    exposes an authoritative subject source, only the body of that function changes (authoritative
    ``complete`` flag, version); the contract below and everything downstream stay as they are.

Versioning
    The catalog is reference data, not source: it is NOT part of the run identity
    (``(raw_message, revision, generation)``) or of ``source_snapshot_sha256``, so a schedule update
    never creates a second run or a second card. What it is analysed with is recorded instead:
    ``catalog_fingerprint`` goes into every run's metrics, and an explicit owner requeue refreshes the
    stored catalog (the earlier outcome and its fingerprint stay in ``metrics.history``).
"""

import hashlib
import json
import re
from collections import Counter
from dataclasses import dataclass

from sqlalchemy import select

from studgroup.analysis.contracts import (
    AnalysisInput,
    SubgroupEntry,
    SubjectCatalog,
    SubjectEntry,
)
from studgroup.models import SchedulePattern
from studgroup.subgroups import signals_of, splits
from studgroup.subject_identity import match_key, subject_uuid

MAX_ALIASES = 20
MAX_TEACHERS = 10
MAX_SUBJECTS = 80


def label_key(text):
    """Comparison form of a label: casefolded, ё folded, punctuation and spacing collapsed."""
    value = re.sub(r"[^\w]+", " ", (text or "").casefold().replace("ё", "е"))
    return " ".join(value.split())


def exact_key(name):
    """Grouping key of the catalog: exactly the identity formula, so no two spellings are merged."""
    return (name or "").casefold()


@dataclass(frozen=True)
class SubjectResolution:
    # confirmed_id | confirmed_name | unknown_id | ambiguous | mismatch | not_in_catalog | none
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
        # The id decides, but the wording must not point at a DIFFERENT catalog subject: an id for
        # algebra next to the exact name "Физика" is a contradiction, not a confirmation. An
        # unlisted abbreviation ("матан") names no other subject and is simply replaced.
        named = by_label.get(label_key(subject_text), [])
        if named and by_id[sid] not in named:
            return SubjectResolution("mismatch", None)
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
            | (
                {"subgroups": [{"name": g.name, "signals": g.signals} for g in row.subgroups]}
                if row.subgroups
                else {}
            )
            for row in catalog.subjects
        ],
    }


def catalog_fingerprint(catalog):
    """Content identity of a catalog (order and ``version`` label independent)."""
    if catalog is None:
        return None
    body = {
        "complete": catalog.complete,
        "subjects": sorted(
            (
                {
                    "id": row.id,
                    "name": row.name,
                    "aliases": sorted(row.aliases),
                    "teachers": sorted(row.teachers),
                }
                | ({"subgroups": [g.model_dump() for g in row.subgroups]} if row.subgroups else {})
                for row in catalog.subjects
            ),
            key=lambda row: row["id"],
        ),
    }
    return hashlib.sha256(
        json.dumps(body, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()[:16]


def catalog_summary(catalog):
    """What a run records about the catalog it was analysed with (metrics, comparisons)."""
    if catalog is None:
        return {"present": False}
    return {
        "present": True,
        "fingerprint": catalog_fingerprint(catalog),
        "version": catalog.version,
        "complete": catalog.complete,
        "subjects": len(catalog.subjects),
    }


def with_catalog(input_json, catalog):
    """The stored frozen input with its catalog replaced (messages untouched), or ``None``."""
    try:
        data = AnalysisInput.model_validate_json(input_json)
    except (TypeError, ValueError):
        return None
    refreshed = data.model_copy(update={"subject_catalog": catalog})
    return json.dumps(refreshed.model_dump(mode="json"), ensure_ascii=False, sort_keys=True)


def build_catalog(group_id, patterns, facts=()):
    """Catalog from schedule rows (``subject``, ``teacher``) and verified knowledge facts."""
    spellings: dict[str, Counter] = {}
    teachers: dict[str, list[str]] = {}
    for row in patterns:
        key = exact_key(row.subject.strip())
        if not key:
            continue
        spellings.setdefault(key, Counter())[row.subject.strip()] += 1
        if row.teacher and row.teacher.strip():
            names = teachers.setdefault(key, [])
            if row.teacher.strip() not in names:
                names.append(row.teacher.strip())
    aliases: dict[str, list[str]] = {}

    def known_key(subject):
        """Catalog key a fact refers to: the exact spelling, else the one subject that only differs
        in e/yo, punctuation or spacing. Several candidates means ambiguous: attach to none."""
        key = exact_key((subject or "").strip())
        if key in spellings:
            return key
        similar = [other for other in spellings if match_key(other) == match_key(subject)]
        return similar[0] if len(similar) == 1 and match_key(subject) else None

    for fact in facts:
        if fact.group_id != group_id or fact.status != "verified":
            continue
        try:
            value = json.loads(fact.value_json)
        except (TypeError, ValueError):
            continue
        if fact.fact_type == "subject_alias" and isinstance(value, dict):
            key, alias = known_key(value.get("subject")), (value.get("alias") or "").strip()
            if (
                key is not None
                and alias
                and exact_key(alias) != key
                and alias not in aliases.setdefault(key, [])
            ):
                aliases[key].append(alias)
        elif fact.fact_type == "subject_teacher" and isinstance(value, dict):
            key, teacher = known_key(value.get("subject")), (value.get("teacher") or "").strip()
            if key is not None and teacher and teacher not in teachers.setdefault(key, []):
                teachers[key].append(teacher)
    entries = []
    for key in sorted(spellings):
        name = min(spellings[key].items(), key=lambda pair: (-pair[1], pair[0]))[0]
        entries.append(
            SubjectEntry(
                id=str(subject_uuid(group_id, name)),
                name=name[:255],
                aliases=[alias[:255] for alias in aliases.get(key, [])[:MAX_ALIASES]],
                teachers=[teacher[:255] for teacher in teachers.get(key, [])[:MAX_TEACHERS]],
            )
        )
    entries = entries[:MAX_SUBJECTS]
    version = hashlib.sha1(
        json.dumps(
            [row.model_dump() for row in entries], ensure_ascii=False, sort_keys=True
        ).encode()
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
    catalog = with_subgroups(build_catalog(group_id, rows, facts), await splits(db, group_id))
    return catalog if catalog.subjects else None


def with_subgroups(catalog, group_splits):
    """The catalog with each split subject's subgroups (a split subject missing from the schedule
    is added, since the headman named it)."""
    if not group_splits:
        return catalog
    entries = list(catalog.subjects)
    for split, members in group_splits:
        subgroups = [
            SubgroupEntry(name=m.name, signals=[s[:80] for s in signals_of(m)][:12])
            for m in members[:6]
        ]
        sid = str(split.subject_id)
        index = next((n for n, e in enumerate(entries) if e.id == sid), None)
        if index is None:
            if len(entries) >= MAX_SUBJECTS:
                continue
            entries.append(SubjectEntry(id=sid, name=split.subject_name[:255], subgroups=subgroups))
        else:
            entries[index] = entries[index].model_copy(update={"subgroups": subgroups})
    return catalog.model_copy(update={"subjects": entries})


async def load_current_catalog(db, group_id):
    """Catalog as of now (loads the verified memory facts itself); used when a run is requeued."""
    from studgroup.models import GroupKnowledgeFact

    facts = (
        await db.scalars(select(GroupKnowledgeFact).where(GroupKnowledgeFact.group_id == group_id))
    ).all()
    return await load_subject_catalog(db, group_id, facts)
