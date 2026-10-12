"""Deterministic micro-batch planning and conservative cursor advancement."""

import json
from dataclasses import dataclass

from studgroup.analysis.contracts import SourceMessage


@dataclass(frozen=True)
class PlannedBatch:
    sequence: int
    messages: tuple[SourceMessage, ...]

    @property
    def source_versions(self):
        return tuple((row.telegram_message_id, row.revision) for row in self.messages)


def plan_batches(messages, *, first_sequence=1, max_messages=25, max_bytes=12500):
    batches = []
    current = []
    used = 0
    ordered = sorted(
        messages, key=lambda row: (row.message_date, row.telegram_message_id, row.revision)
    )
    for message in ordered:
        size = len(json.dumps(message.model_dump(mode="json"), ensure_ascii=False).encode())
        if size > max_bytes:
            raise ValueError("source_exceeds_batch_limit")
        if current and (len(current) >= max_messages or used + size > max_bytes):
            batches.append(tuple(current))
            current = []
            used = 0
        current.append(message)
        used += size
    if current:
        batches.append(tuple(current))
    return [
        PlannedBatch(sequence=first_sequence + index, messages=batch)
        for index, batch in enumerate(batches)
    ]


def advance_cursor(last_completed_sequence, ordered_batch_states):
    cursor = last_completed_sequence
    for sequence, state in sorted(ordered_batch_states):
        if sequence != cursor + 1 or state not in {"completed", "empty"}:
            break
        cursor = sequence
    return cursor
