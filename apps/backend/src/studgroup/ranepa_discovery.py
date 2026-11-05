"""Find a RANEPA university group UUID in catalog data without guessing.

Only a unique, exact official code match can identify a cohort. Similar groups,
departments or subgroups must not be silently accepted.
"""

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlparse
from uuid import UUID

from studgroup.ranepa_schedule import group_code, normalized

UUID_RE = re.compile(
    r"(?i)\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b"
)
TEXT_KEYS = {"name", "label", "title", "text", "group_name", "full_name", "fullname"}
ID_KEYS = {"id", "uuid", "value", "ref", "group_id", "guid", "code"}
URL_KEYS = {"url", "href", "link"}


class DiscoveryError(ValueError):
    """No matching group or multiple groups with the same official code."""


@dataclass(frozen=True)
class GroupCandidate:
    code: str
    upstream_uuid: UUID
    label: str


def exact_code_match(label: str, code: str) -> bool:
    """Code may be followed by a department suffix, not extra code characters."""
    label = normalized(label)
    code = normalized(group_code(code))
    # Full academic code is frequently displayed as 'Э-ОБ-... / ЭМИТ'.
    if label == code:
        return True
    return label.startswith(code) and len(label) > len(code) and label[len(code)] in {
        " ", "/", "(", "[", ",", ";", "—", "–"
    }


def parse_uuid(value: object) -> UUID | None:
    if isinstance(value, UUID):
        return value
    if not isinstance(value, str):
        return None
    value = value.strip()
    try:
        return UUID(value)
    except ValueError:
        pass
    parsed = urlparse(value)
    if parsed.hostname not in {"my.ranepa.ru", None}:
        return None
    f_values = parse_qs(parsed.query).get("f", [])
    if len(f_values) != 1:
        return None
    try:
        return UUID(f_values[0])
    except ValueError:
        return None


def candidate_from_row(row: dict, code: str) -> GroupCandidate | None:
    labels = [
        v
        for k, v in row.items()
        if str(k).lower() in TEXT_KEYS and isinstance(v, str) and exact_code_match(v, code)
    ]
    if not labels:
        return None
    ids = {
        uuid_value
        for key, value in row.items()
        if str(key).lower() in ID_KEYS | URL_KEYS
        if (uuid_value := parse_uuid(value)) is not None
    }
    if len(ids) != 1:
        return None
    return GroupCandidate(code, next(iter(ids)), labels[0])


def find_candidates(catalog: object, code: str, *, max_nodes: int = 25000) -> set[GroupCandidate]:
    """Find exact pairs (label, uuid) in finite nested JSON; never guess from order."""
    group_code(code)
    queue = [catalog]
    found = set()
    nodes = 0
    while queue:
        value = queue.pop()
        nodes += 1
        if nodes > max_nodes:
            raise DiscoveryError("oversized_catalog")
        if isinstance(value, dict):
            match = candidate_from_row(value, code)
            if match is not None:
                found.add(match)
            queue.extend(v for v in value.values() if isinstance(v, (dict, list)))
        elif isinstance(value, list):
            queue.extend(value)
    return found


def resolve_group_uuid(catalog: object, code: str) -> UUID:
    candidates = find_candidates(catalog, code)
    ids = {item.upstream_uuid for item in candidates}
    if not ids:
        raise DiscoveryError("group_not_found")
    if len(ids) > 1:
        raise DiscoveryError("ambiguous_group")
    return next(iter(ids))
