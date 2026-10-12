"""Stable DTO boundary between context, providers, validation and publication."""

from datetime import datetime
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, model_validator

ANALYSIS_SCHEMA_VERSION = "analysis-contract-1"
ANALYSIS_ENGINE_VERSION = "cplus-shadow-1"


class StrictDTO(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class SourceAttachment(StrictDTO):
    kind: Literal["photo", "document", "video", "audio", "voice", "animation"]
    mime_type: str | None = Field(default=None, max_length=255)
    size_bytes: int | None = Field(default=None, ge=0)
    media_group_id: str | None = Field(default=None, max_length=255)
    processing_state: Literal["metadata_only", "ready", "failed"] = "metadata_only"


class SourceMessage(StrictDTO):
    telegram_message_id: int
    revision: int = Field(ge=1)
    message_date: datetime
    text: str = Field(max_length=12000)
    reply_to_message_id: int | None = None
    topic_id: int | None = None
    topic_name: str | None = Field(default=None, max_length=255)
    attachments: list[SourceAttachment] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def require_aware_timestamp(self):
        if self.message_date.utcoffset() is None:
            raise ValueError("source_timestamp_requires_offset")
        if not self.text and not self.attachments:
            raise ValueError("source_requires_text_or_attachment")
        return self


class AnalysisInput(StrictDTO):
    schema_version: Literal[ANALYSIS_SCHEMA_VERSION] = ANALYSIS_SCHEMA_VERSION
    engine_version: Literal[ANALYSIS_ENGINE_VERSION] = ANALYSIS_ENGINE_VERSION
    group_id: UUID
    group_timezone: str
    source_revision: int = Field(ge=1)
    target_message_ids: list[int] = Field(min_length=1, max_length=80)
    messages: list[SourceMessage] = Field(min_length=1, max_length=80)

    @model_validator(mode="after")
    def validate_scope(self):
        ZoneInfo(self.group_timezone)
        ids = [row.telegram_message_id for row in self.messages]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate_source_message_id")
        if not set(self.target_message_ids).issubset(ids):
            raise ValueError("target_outside_source_envelope")
        return self


class EvidenceRef(StrictDTO):
    telegram_message_id: int
    revision: int = Field(ge=1)
    fact_paths: list[str] = Field(min_length=1, max_length=20)


class DeadlineValue(StrictDTO):
    at: datetime | None = None
    date_only: bool = False
    basis: Literal[
        "explicit",
        "schedule_inferred",
        "ambiguous_range",
        "unknown",
    ] = "unknown"

    @model_validator(mode="after")
    def validate_deadline(self):
        if self.at is not None and self.at.utcoffset() is None:
            raise ValueError("deadline_requires_offset")
        if self.at is None and self.date_only:
            raise ValueError("unknown_deadline_cannot_be_date_only")
        if self.basis in {"explicit", "schedule_inferred"} and self.at is None:
            raise ValueError("deadline_basis_requires_value")
        return self


class Audience(StrictDTO):
    scope: Literal["group", "subgroup", "personal", "unknown"] = "group"
    label: str | None = Field(default=None, max_length=255)

    @model_validator(mode="after")
    def validate_label(self):
        if self.scope == "subgroup" and not self.label:
            raise ValueError("subgroup_requires_label")
        return self


class ProposedAcademicItem(StrictDTO):
    destination: Literal["task", "deadline"]
    semantic_kind: Literal[
        "homework",
        "test",
        "control_point",
        "assessment",
        "credit",
        "exam",
        "other_academic_deadline",
    ]
    subject: str | None = Field(default=None, max_length=255)
    title: str | None = Field(default=None, max_length=255)
    description: str | None = Field(default=None, max_length=8000)
    deadline: DeadlineValue = Field(default_factory=DeadlineValue)
    audience: Audience = Field(default_factory=Audience)
    confidence: int = Field(ge=0, le=100)
    attention_reasons: list[str] = Field(default_factory=list, max_length=20)
    evidence: list[EvidenceRef] = Field(min_length=1, max_length=24)

    @model_validator(mode="after")
    def validate_destination(self):
        expected = "task" if self.semantic_kind == "homework" else "deadline"
        if self.destination != expected:
            raise ValueError("destination_kind_mismatch")
        for field in ("subject", "title", "description"):
            value = getattr(self, field)
            if value is not None and not value.strip():
                raise ValueError("blank_display_field")
        return self


class RejectedItem(StrictDTO):
    proposal_ordinal: int = Field(ge=0)
    reason_code: str = Field(max_length=64)


class AnalysisOutput(StrictDTO):
    schema_version: Literal[ANALYSIS_SCHEMA_VERSION] = ANALYSIS_SCHEMA_VERSION
    engine_version: Literal[ANALYSIS_ENGINE_VERSION] = ANALYSIS_ENGINE_VERSION
    source_revision: int = Field(ge=1)
    proposals: list[ProposedAcademicItem] = Field(default_factory=list, max_length=40)
    explicit_empty: bool = False

    @model_validator(mode="after")
    def validate_empty_marker(self):
        if self.explicit_empty != (not self.proposals):
            raise ValueError("explicit_empty_mismatch")
        return self


class ValidationResult(StrictDTO):
    source_revision: int = Field(ge=1)
    accepted: list[ProposedAcademicItem] = Field(default_factory=list)
    rejected: list[RejectedItem] = Field(default_factory=list)
    raw_proposal_count: int = Field(ge=0)
    explicit_empty: bool


class ApplicationOutcome(StrictDTO):
    source_revision: int = Field(ge=1)
    proposal_ordinal: int = Field(ge=-1)
    technical_status: Literal["parsed", "rejected", "failed", "stale"]
    application_status: Literal[
        "not_applied",
        "shadow_only",
        "created",
        "updated",
        "unchanged",
        "review",
        "blocked_by_lock",
        "stale",
    ]
    reason_code: str = Field(max_length=64)
