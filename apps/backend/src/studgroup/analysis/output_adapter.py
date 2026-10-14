"""Deterministic, fact-preserving adapter between raw provider JSON and the strict contract.

The Pydantic models in ``contracts`` remain the only source of truth and stay ``strict``. This
module only converts values whose meaning is unambiguous (ISO text -> aware ``datetime``, UUID
text -> ``UUID``, ``90.0`` -> ``90``, enum spelling, a unique revision lookup, ...). It never
invents a subject, deadline, source or fact path; an ambiguous proposal is rejected with a
value-free diagnostic code instead. Every normalization is counted in ``actions``.
"""

import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import get_args
from uuid import UUID
from zoneinfo import ZoneInfo

from studgroup.analysis.contracts import (
    DeadlineValue,
    ProposalSchemaIssue,
    ProposedAcademicItem,
)

_PROPOSAL_FIELDS = frozenset(ProposedAcademicItem.model_fields)
# Purely explanatory keys: dropping them cannot change any academic fact.
INFORMATIONAL_KEYS = frozenset(
    {"quote", "quotes", "reasoning", "explanation", "rationale", "notes", "comment", "comments"}
)
EVIDENCE_ALIASES = {"message_id": "telegram_message_id", "source_id": "telegram_message_id"}
EVIDENCE_INFORMATIONAL_KEYS = frozenset({"quote", "text", "excerpt"})
# Placeholders copied from historic prompt examples mean "no value".
NULL_STRINGS = frozenset({"null", "none", "n/a", "subject or null", "short specific title or null",
                          "clear evidenced actions or null"})
FACT_PATH_ROOTS = frozenset(
    {"subject", "title", "description", "deadline", "audience", "semantic_kind", "destination",
     "action"}
)

ACTIONS = frozenset(get_args(ProposedAcademicItem.model_fields["action"].annotation))
DESTINATIONS = frozenset(get_args(ProposedAcademicItem.model_fields["destination"].annotation))
SEMANTIC_KINDS = frozenset(get_args(ProposedAcademicItem.model_fields["semantic_kind"].annotation))
BASES = frozenset(get_args(DeadlineValue.model_fields["basis"].annotation))
SCOPES = frozenset(
    get_args(
        ProposedAcademicItem.model_fields["audience"].annotation.model_fields["scope"].annotation
    )
)

# Deadline sanity flags are attention reasons, not rejections.
SERIOUS_CONFLICT_REASON = "conflicting_deadlines"
PAST_DEADLINE_REASON = "deadline_before_source"
FAR_FUTURE_REASON = "deadline_far_future"
FAR_FUTURE_DAYS = 400


_MONTHS = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5, "июня": 6,
    "июля": 7, "августа": 8, "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
}
_NUMERIC_DATE = re.compile(r"(?<![\d.,])(\d{1,2})[./](\d{2})(?![\d])(?![.,]\d)")
_NAMED_DATE = re.compile(r"(?<!\d)(\d{1,2})\s+(" + "|".join(_MONTHS) + r")(?![а-яё])", re.IGNORECASE)


def explicit_source_dates(text):
    """(day, month) pairs literally written in a source message."""
    found = set()
    lowered = text.casefold().replace("ё", "е")
    for day, month in _NUMERIC_DATE.findall(lowered):
        if 1 <= int(day) <= 31 and 1 <= int(month) <= 12:
            found.add((int(day), int(month)))
    for day, name in _NAMED_DATE.findall(lowered):
        if 1 <= int(day) <= 31:
            found.add((int(day), _MONTHS[name]))
    return found


# Collective address: the instruction is for every student even when each does it alone.
_COLLECTIVE = re.compile(
    r"(?<![а-яе])(?:кажд(?:ый|ая|ое|ого|ому|ым|ую|ые|ых|ыми)(?!\s+(?:раз|день|дн\w*|недел\w*|пар\w*))"
    r"|всем|всех|вся\s+групп\w*|всей\s+групп\w*"
    r"|все\s+(?:студент\w*|должн\w*|делают|готовят|сдают|обязан\w*|присылают))(?![а-яе])"
)
# Direct address to one person or role keeps the instruction personal.
_PERSONAL_ADDRESS = re.compile(r"(?<![а-яе])(?:старосте|тебе|тебя|ты|лично)(?![а-яе])|@\w+")
_NAME_ADDRESS = re.compile(r"^\s*[А-ЯЁ][а-яё]{2,}(?:\s+[А-ЯЁ][а-яё]+)?\s*[,:—-]")


def _fold(text):
    return text.casefold().replace("ё", "е")


def collective_address_only(texts):
    """True when the cited text addresses everyone and never a single person or role."""
    collective = personal = False
    for text in texts:
        folded = _fold(text)
        collective = collective or bool(_COLLECTIVE.search(folded))
        named = _NAME_ADDRESS.match(text)
        personal = personal or bool(_PERSONAL_ADDRESS.search(folded)) or bool(
            named and not _COLLECTIVE.search(_fold(named.group(0)))
        )
    return collective and not personal


_WEEKDAY_WORDS = (
    r"понедельник\w*", r"вторник\w*", r"сред(?:а|е|у|ы)", r"четверг\w*", r"пятниц\w*",
    r"суббот\w*", r"воскресень\w*",
)
UNRESOLVED_RELATIVE_BASES = frozenset(
    {"next_subject_lesson_reference", "unresolved_explicit_deadline", "ambiguous_weekday"}
)
UNCONFIRMED_CHANGE_REASON = "unconfirmed_change"
RELATIVE_UNRESOLVED_REASON = "relative_deadline_unresolved"
DEADLINE_NOT_IN_SOURCE_REASON = "deadline_not_in_source"


def mentions_weekday(text):
    folded = _fold(text)
    return any(re.search(r"(?<![а-яе])" + word, folded) for word in _WEEKDAY_WORDS)


def weekday_supports(texts, local_moment, first_sent):
    """A named weekday (any qualifier) that equals the model date within two weeks of sending."""
    delta = (local_moment.date() - first_sent.date()).days
    if not 0 <= delta <= 14:
        return False
    folded = " ".join(_fold(text) for text in texts)
    return bool(re.search(r"(?<![а-яе])" + _WEEKDAY_WORDS[local_moment.weekday()], folded))


class AdapterReject(Exception):
    """A single proposal cannot be safely normalized."""

    def __init__(self, code, path, *, expected="valid_value", received=None, repairable=True):
        self.code = code
        self.path = path
        self.expected = expected
        self.received = received
        self.repairable = repairable
        super().__init__(code)


@dataclass
class AdaptedProposal:
    ordinal: int
    value: dict | None = None
    issues: list[ProposalSchemaIssue] = field(default_factory=list)
    repairable: bool = False
    actions: Counter = field(default_factory=Counter)

    @property
    def ok(self):
        return self.value is not None


def received_type(value):
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, str):
        return "string"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, (list, tuple)):
        return "array"
    if isinstance(value, dict):
        return "object"
    return "other"


def _reject(code, path, value=None, *, expected="valid_value", repairable=True):
    raise AdapterReject(
        code, path, expected=expected, received=received_type(value), repairable=repairable
    )


class _Normalizer:
    def __init__(self, ordinal, *, group_timezone, messages, allowed_sources, existing_items):
        self.ordinal = ordinal
        self.tz = ZoneInfo(group_timezone)
        self.messages = messages  # {telegram_message_id: SourceMessage}
        self.allowed_sources = allowed_sources
        self.existing_items = existing_items
        self.actions = Counter()
        self.extra_reasons: list[str] = []

    def note(self, name):
        self.actions[name] += 1

    # -- scalar helpers -------------------------------------------------------------------
    def enum(self, value, allowed, path):
        if not isinstance(value, str):
            _reject("enum_not_string", path, value, expected="allowed_literal")
        text = "_".join(value.strip().casefold().replace("-", " ").split())
        if text != value:
            self.note("enum_spelling_normalized")
        if text not in allowed:
            _reject("enum_value_not_allowed", path, value, expected="allowed_literal")
        return text

    def optional_text(self, value, path):
        if value is None:
            return None
        if not isinstance(value, str):
            _reject("text_not_string", path, value, expected="string")
        stripped = value.strip()
        if not stripped or stripped.casefold() in NULL_STRINGS:
            self.note("blank_text_to_null")
            return None
        if stripped != value:
            self.note("text_trimmed")
        return stripped

    def integer(self, value, path, *, scale_ambiguous=False):
        if isinstance(value, bool):
            _reject("integer_not_number", path, value, expected="integer")
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            if scale_ambiguous and 0 < value <= 1 and value != 1.0:
                _reject("confidence_scale_ambiguous", path, value, expected="integer")
            if value != int(value):
                _reject("integer_not_integral", path, value, expected="integer")
            self.note("float_to_integer")
            return int(value)
        if isinstance(value, str) and value.strip().lstrip("+").isdecimal():
            self.note("numeric_string_to_integer")
            return int(value.strip())
        _reject("integer_not_number", path, value, expected="integer")

    def uuid(self, value, path):
        if value is None or isinstance(value, UUID):
            return value
        if isinstance(value, str):
            try:
                parsed = UUID(value.strip())
            except ValueError:
                _reject("identifier_invalid", path, value, expected="valid_identifier")
            self.note("uuid_text_parsed")
            return parsed
        _reject("identifier_invalid", path, value, expected="valid_identifier")

    # -- deadline -------------------------------------------------------------------------
    def parse_moment(self, value, path):
        """Return (aware datetime, text_had_time)."""
        if isinstance(value, datetime):
            return (value if value.utcoffset() else value.replace(tzinfo=self.tz)), True
        if not isinstance(value, str) or not value.strip():
            _reject("deadline_at_invalid", path, value, expected="timezone_aware_datetime")
        text = value.strip()
        try:
            if len(text) == 10:
                return datetime.combine(date.fromisoformat(text), time(0), tzinfo=self.tz), False
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00").replace(" ", "T", 1))
        except ValueError:
            _reject("deadline_at_invalid", path, value, expected="timezone_aware_datetime")
        if parsed.utcoffset() is None:
            self.note("naive_deadline_localized")
            parsed = parsed.replace(tzinfo=self.tz)
        return parsed, True

    def deadline(self, raw, base):
        path = "deadline"
        if raw is None:
            self.note("deadline_defaulted")
            return {"at": None, "date_only": False, "basis": "unknown"}
        if not isinstance(raw, dict):
            _reject("deadline_not_object", path, raw, expected="object")
        extra = set(raw) - {"at", "date_only", "basis"}
        if extra:
            _reject("deadline_field_not_allowed", path, raw, expected="field_not_allowed")
        raw_at = raw.get("at")
        if isinstance(raw_at, str) and (not raw_at.strip() or raw_at.strip().casefold() in
                                        NULL_STRINGS):
            raw_at = None
            self.note("blank_text_to_null")
        date_only = raw.get("date_only", False)
        if date_only is None:
            date_only = False
        if isinstance(date_only, str) and date_only.strip().casefold() in {"true", "false"}:
            date_only = date_only.strip().casefold() == "true"
            self.note("boolean_text_parsed")
        if not isinstance(date_only, bool):
            _reject("date_only_not_boolean", "deadline.date_only", date_only, expected="boolean")
        basis_raw = raw.get("basis")
        at = None
        if raw_at is not None:
            at, had_time = self.parse_moment(raw_at, "deadline.at")
            local = at.astimezone(self.tz)
            local_has_time = any((local.hour, local.minute, local.second, local.microsecond))
            if not had_time:
                if not date_only:
                    self.note("date_text_marked_date_only")
                date_only = True
            elif date_only and local_has_time:
                _reject("date_only_with_time", "deadline.date_only", raw, expected="valid_value")
            if date_only:
                at = at.astimezone(self.tz).replace(hour=0, minute=0, second=0, microsecond=0)
        if basis_raw is None:
            if at is not None:
                _reject("deadline_basis_missing", "deadline.basis", None,
                        expected="required_field")
            basis = "unknown"
            self.note("basis_defaulted_unknown")
        else:
            basis = self.enum(basis_raw, BASES, "deadline.basis")
        if at is None:
            if date_only:
                self.note("date_only_cleared_without_value")
                date_only = False
            if basis in {"explicit", "schedule_inferred"}:
                _reject("deadline_basis_requires_value", "deadline", raw,
                        expected="valid_value")
        else:
            self.sanity_flags(at, base)
        return {"at": at, "date_only": date_only, "basis": basis}

    def sanity_flags(self, at, base):
        starts = [self.messages[i].message_date for i in base if i in self.messages]
        if not starts:
            return
        earliest, latest = min(starts), max(starts)
        if at < earliest - timedelta(days=1):
            self.extra_reasons.append(PAST_DEADLINE_REASON)
        elif at > latest + timedelta(days=FAR_FUTURE_DAYS):
            self.extra_reasons.append(FAR_FUTURE_REASON)

    def deterministic_conflict(self, deadline, base):
        """Verify the origin of an ``explicit`` date; flag a contradiction, clear a guess.

        A calendar date written in the cited text outranks every heuristic. A date that no
        written date, relative word or named weekday supports (for example "к следующей паре"
        needs a trusted timetable that shadow input does not carry) is not an explicit date: it
        is cleared to unknown and routed to clarification instead of being shown as a fact.
        """
        if deadline["at"] is None or deadline["basis"] != "explicit":
            return deadline
        ordered = sorted((self.messages[i] for i in base if i in self.messages),
                         key=lambda row: row.message_date)
        local = deadline["at"].astimezone(self.tz)
        literal = set()
        for source in ordered:
            literal |= explicit_source_dates(source.text)
        if literal:
            # A proposal that ignores every written date is held for review, not published.
            if (local.day, local.month) not in literal:
                self.extra_reasons.append(SERIOUS_CONFLICT_REASON)
            return deadline
        from studgroup.deadlines import contextual_deadline

        resolved = None
        unresolved_basis = None
        for source in ordered:
            try:
                candidate = contextual_deadline(source.text, source.message_date, str(self.tz))
            except (ValueError, TypeError):
                continue
            if candidate is None:
                continue
            if candidate.at is not None:
                resolved = candidate
            else:
                unresolved_basis = candidate.basis
        if resolved is not None:
            if resolved.at.astimezone(self.tz).date() != local.date():
                self.extra_reasons.append(SERIOUS_CONFLICT_REASON)
            return deadline
        if ordered and weekday_supports(
            [row.text for row in ordered], local, ordered[0].message_date.astimezone(self.tz)
        ):
            return deadline
        self.note("unsupported_explicit_deadline_cleared")
        self.extra_reasons.append(
            RELATIVE_UNRESOLVED_REASON
            if unresolved_basis in UNRESOLVED_RELATIVE_BASES
            else DEADLINE_NOT_IN_SOURCE_REASON
        )
        return {"at": None, "date_only": False, "basis": "unknown"}

    # -- evidence -------------------------------------------------------------------------
    def fact_paths(self, value, path):
        if isinstance(value, str):
            value = [value]
            self.note("single_fact_path_to_list")
        if not isinstance(value, list) or not value:
            _reject("evidence_fact_paths_missing", path, value, expected="required_field")
        kept = []
        for item in value:
            if not isinstance(item, str):
                _reject("evidence_fact_path_invalid", path, item, expected="string")
            text = item.strip()
            root = text.split(".", 1)[0]
            if root not in FACT_PATH_ROOTS:
                self.note("unsupported_fact_path_dropped")
                continue
            if text not in kept:
                kept.append(text)
        if not kept:
            _reject("evidence_fact_paths_missing", path, value, expected="required_field")
        return kept

    def evidence_refs(self, raw):
        """Parse ids/revisions only; fact paths need the finished proposal (see fact_paths)."""
        if not isinstance(raw, list) or not raw:
            _reject("evidence_missing", "evidence", raw, expected="array")
        result = []
        for entry in raw:
            path = "evidence"
            if not isinstance(entry, dict):
                _reject("evidence_not_object", path, entry, expected="object")
            entry = dict(entry)
            for key in EVIDENCE_INFORMATIONAL_KEYS & set(entry):
                entry.pop(key)
                self.note("evidence_informational_key_dropped")
            for alias, canonical in EVIDENCE_ALIASES.items():
                if alias in entry:
                    value = entry.pop(alias)
                    if canonical in entry and entry[canonical] != value:
                        _reject("evidence_alias_conflict", path, entry, repairable=False)
                    entry[canonical] = value
                    self.note("evidence_alias_normalized")
            if set(entry) - {"telegram_message_id", "revision", "fact_paths"}:
                _reject("evidence_field_not_allowed", path, entry, expected="field_not_allowed")
            if entry.get("telegram_message_id") is None:
                _reject("evidence_message_id_missing", path, None, expected="required_field",
                        repairable=False)
            message_id = self.integer(entry["telegram_message_id"], "evidence.telegram_message_id")
            if entry.get("revision") is not None:
                revision = self.integer(entry["revision"], "evidence.revision")
            elif message_id in self.allowed_sources:
                revision = self.allowed_sources[message_id]
                self.note("revision_looked_up")
            else:
                _reject("evidence_revision_missing", path, None, expected="required_field",
                        repairable=False)
            result.append((entry.get("fact_paths"), message_id, revision))
        return result

    def semantic_rules(self, value, evidence_ids):
        """Deterministic source-checked rules; also re-applied to stored proposals on read.

        Works on the plain value dict: deadline origin, collective address -> group audience,
        service tag is not a subject. Idempotent by construction.
        """
        value["deadline"] = self.deterministic_conflict(value["deadline"], evidence_ids)
        if value["audience"]["scope"] == "personal" and collective_address_only(
            self.messages[i].text for i in evidence_ids if i in self.messages
        ):
            value["audience"] = {"scope": "group", "label": None}
            self.note("audience_collective_to_group")
        from studgroup.analysis.text_hygiene import is_service_subject

        if isinstance(value.get("subject"), str) and is_service_subject(value["subject"]):
            value["subject"] = None
            self.note("service_tag_subject_cleared")
        return value

    # -- proposal -------------------------------------------------------------------------
    def proposal(self, raw):
        if not isinstance(raw, dict):
            _reject("proposal_not_object", "proposal", raw, expected="object", repairable=False)
        raw = dict(raw)
        for key in INFORMATIONAL_KEYS & set(raw):
            raw.pop(key)
            self.note("informational_key_dropped")
        unknown = set(raw) - _PROPOSAL_FIELDS
        if unknown:
            _reject("proposal_field_not_allowed", "proposal", raw, expected="field_not_allowed")
        value = {}
        value["action"] = (
            self.enum(raw["action"], ACTIONS, "action") if raw.get("action") is not None
            else "create"
        )
        if raw.get("action") is None:
            self.note("action_defaulted_create")
        value["existing_item_id"] = self.uuid(raw.get("existing_item_id"), "existing_item_id")
        revision = raw.get("expected_item_revision")
        value["expected_item_revision"] = (
            None if revision is None else self.integer(revision, "expected_item_revision")
        )
        if value["action"] == "create":
            # The contract forbids these on create; null-like leftovers are harmless, a real
            # identity on a create is contradictory and left for strict validation to reject.
            pass
        else:
            if (
                self.existing_items is None
                and value["action"] in {"update", "reschedule"}
                and (value["existing_item_id"] is None or value["expected_item_revision"] is None)
            ):
                # Shadow carries no trusted item identity. A claimed change is kept as a
                # held proposal (never a silent edit) so the owner can confirm or discard it.
                value["action"] = "create"
                value["existing_item_id"] = None
                value["expected_item_revision"] = None
                self.extra_reasons.append(UNCONFIRMED_CHANGE_REASON)
                self.note("change_without_identity_held")
            elif value["existing_item_id"] is None or value["expected_item_revision"] is None:
                _reject("mutation_identity_missing", "existing_item_id", None,
                        expected="required_field", repairable=False)
            if self.existing_items is not None and (
                self.existing_items.get(value["existing_item_id"]) != value["expected_item_revision"]
            ):
                _reject("existing_item_not_in_trusted_context", "existing_item_id", None,
                        repairable=False)
        kind = raw.get("semantic_kind")
        if kind is None:
            _reject("semantic_kind_missing", "semantic_kind", None, expected="required_field")
        value["semantic_kind"] = self.enum(kind, SEMANTIC_KINDS, "semantic_kind")
        derived = "task" if value["semantic_kind"] == "homework" else "deadline"
        if raw.get("destination") is None:
            value["destination"] = derived
            self.note("destination_derived_from_kind")
        else:
            value["destination"] = self.enum(raw["destination"], DESTINATIONS, "destination")
        for name in ("subject", "title", "description"):
            value[name] = self.optional_text(raw.get(name), name)
        confidence = raw.get("confidence")
        if confidence is None:
            _reject("confidence_missing", "confidence", None, expected="required_field")
        value["confidence"] = self.integer(confidence, "confidence", scale_ambiguous=True)

        audience = raw.get("audience")
        if audience is None:
            value["audience"] = {"scope": "group", "label": None}
            self.note("audience_defaulted_group")
        elif isinstance(audience, dict):
            if set(audience) - {"scope", "label"}:
                _reject("audience_field_not_allowed", "audience", audience,
                        expected="field_not_allowed")
            scope = audience.get("scope")
            value["audience"] = {
                "scope": "group" if scope is None else self.enum(scope, SCOPES, "audience.scope"),
                "label": self.optional_text(audience.get("label"), "audience.label"),
            }
            if scope is None:
                self.note("audience_defaulted_group")
        else:
            _reject("audience_not_object", "audience", audience, expected="object")

        reasons = raw.get("attention_reasons")
        if reasons is None:
            reasons = []
        elif isinstance(reasons, str):
            reasons = [reasons]
            self.note("single_reason_to_list")
        if not isinstance(reasons, list) or any(not isinstance(item, str) for item in reasons):
            _reject("attention_reasons_invalid", "attention_reasons", reasons, expected="array")
        reasons = [item.strip() for item in reasons if item.strip()]

        refs = self.evidence_refs(raw.get("evidence"))
        evidence_ids = [message_id for _paths, message_id, _revision in refs]
        value["deadline"] = self.deadline(raw.get("deadline"), evidence_ids)
        value["evidence"] = [
            {
                "telegram_message_id": message_id,
                "revision": revision,
                "fact_paths": self.fact_paths(paths, "evidence.fact_paths"),
            }
            for paths, message_id, revision in refs
        ]
        self.semantic_rules(value, evidence_ids)
        for reason in self.extra_reasons:
            if reason not in reasons:
                reasons.append(reason)
        value["attention_reasons"] = reasons
        return value


def adapt_proposal(
    ordinal,
    raw,
    *,
    group_timezone,
    messages,
    allowed_sources,
    existing_items=None,
) -> AdaptedProposal:
    """Normalize one provider proposal; failure never affects sibling proposals."""
    normalizer = _Normalizer(
        ordinal,
        group_timezone=group_timezone,
        messages=messages,
        allowed_sources=allowed_sources,
        existing_items=existing_items,
    )
    try:
        value = normalizer.proposal(raw)
    except AdapterReject as error:
        return AdaptedProposal(
            ordinal=ordinal,
            issues=[
                ProposalSchemaIssue(
                    path=_issue_path(ordinal, error.path),
                    error_type=error.code,
                    expected=error.expected,
                    received_type=error.received,
                )
            ],
            repairable=error.repairable,
            actions=normalizer.actions,
        )
    return AdaptedProposal(ordinal=ordinal, value=value, actions=normalizer.actions)


def _issue_path(ordinal, path):
    cleaned = "".join(c for c in path if c.isalnum() or c in "_.")
    return f"proposals[{ordinal}]" + (f".{cleaned}" if cleaned and cleaned != "proposal" else "")
