"""Bounded deterministic selection of structured memory for one group."""

import json
import re

TOKEN = re.compile(r"[a-zа-яё0-9]+", re.IGNORECASE)


def tokens(value):
    return set(TOKEN.findall(value.casefold()))


def assemble_memory_context(group_id, query, facts, *, max_bytes=4000):
    query_tokens = tokens(query)
    candidates = []
    for fact in facts:
        if fact.group_id != group_id:
            raise ValueError("cross_group_memory_candidate")
        if fact.status not in {"proposed", "verified"}:
            continue
        value = json.loads(fact.value_json)
        serialized = json.dumps(value, ensure_ascii=False, sort_keys=True)
        overlap = len(query_tokens & tokens(f"{fact.normalized_key} {serialized}"))
        rank = (0 if fact.status == "verified" else 1, -overlap, fact.fact_type, fact.normalized_key)
        candidates.append((rank, fact, value))
    selected = []
    used = 2
    for _rank, fact, value in sorted(candidates, key=lambda row: row[0]):
        item = {
            "fact_type": fact.fact_type,
            "key": fact.normalized_key,
            "value": value,
            "status": fact.status,
            "revision": fact.revision,
        }
        size = len(json.dumps(item, ensure_ascii=False, sort_keys=True).encode())
        if used + size > max_bytes:
            continue
        selected.append(item)
        used += size
    return selected
