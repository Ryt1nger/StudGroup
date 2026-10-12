from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: str = Field(min_length=1)
    source_id: int
    revision: int = Field(ge=1)


class Source(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_id: int
    revision: int = Field(ge=1)
    message_time: datetime
    author_alias: str | None = None
    reply_to_source_id: int | None = None
    text: str | None
    attachment_kinds: list[str] = Field(default_factory=list)


class ExpectedItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    entity_id: str = Field(min_length=1)
    destination: Literal["task", "deadline"]
    action: Literal["create", "enrich", "correct", "reschedule", "cancel", "reopen", "no_change"]
    subject: str | None
    semantic_label: str | None = None
    content: str = Field(min_length=1)
    deadline: datetime | None = None
    date_basis: Literal["explicit", "relative_resolved", "schedule_inferred", "unknown", "conflict"]
    audience_label: str | None = None
    publication: Literal["normal", "needs_attention", "review"] = "normal"
    display_title: str | None = None
    display_description: str | None = None
    evidence: list[Evidence] = Field(min_length=1)


class CorpusEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str = Field(min_length=1)
    split: Literal["development", "evaluation"]
    discussion_id: str = Field(min_length=1)
    notes: str | None = None
    sources: list[Source] = Field(min_length=1)
    expected_items: list[ExpectedItem]
    negative_reason: str | None = None

    @model_validator(mode="after")
    def validate_evidence_and_negative_case(self) -> CorpusEntry:
        source_versions = {(row.source_id, row.revision) for row in self.sources}
        if len(source_versions) != len(self.sources):
            raise ValueError("duplicate source revision in case")
        entity_ids = [row.entity_id for row in self.expected_items]
        if len(entity_ids) != len(set(entity_ids)):
            raise ValueError("duplicate entity_id in case")
        for item in self.expected_items:
            for evidence in item.evidence:
                if (evidence.source_id, evidence.revision) not in source_versions:
                    raise ValueError("evidence references a missing source revision")
        if not self.expected_items and not self.negative_reason:
            raise ValueError("negative case requires negative_reason")
        return self


def load_corpus(path: Path) -> list[CorpusEntry]:
    entries = [
        CorpusEntry.model_validate(json.loads(line))
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    case_ids = [entry.case_id for entry in entries]
    if len(case_ids) != len(set(case_ids)):
        raise ValueError("duplicate case_id in corpus")
    entity_ids = [item.entity_id for entry in entries for item in entry.expected_items]
    if len(entity_ids) != len(set(entity_ids)):
        raise ValueError("duplicate entity_id in corpus")
    discussion_splits: dict[str, set[str]] = {}
    for entry in entries:
        discussion_splits.setdefault(entry.discussion_id, set()).add(entry.split)
    leaked = sorted(key for key, splits in discussion_splits.items() if len(splits) > 1)
    if leaked:
        raise ValueError(f"discussion split leakage: {', '.join(leaked)}")
    return entries
