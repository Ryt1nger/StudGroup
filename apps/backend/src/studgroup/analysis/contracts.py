"""Stable DTO boundary between context, providers, validation and publication."""

from datetime import datetime
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, model_validator

ANALYSIS_SCHEMA_VERSION = "analysis-contract-1"
ANALYSIS_ENGINE_VERSION = "cplus-shadow-1"


# Stable codes raised by our own model validators. They are fixed constants (never derived from
# provider text), so they are safe to persist as value-free diagnostics for `value_error` issues.
CONTRACT_RULE_CODES = frozenset(
    {
        "source_timestamp_requires_offset",
        "source_requires_text_or_attachment",
        "duplicate_source_message_id",
        "target_outside_source_envelope",
        "deadline_requires_offset",
        "unknown_deadline_cannot_be_date_only",
        "deadline_basis_requires_value",
        "subgroup_requires_label",
        "destination_kind_mismatch",
        "blank_display_field",
        "create_cannot_target_existing_item",
        "mutation_requires_item_identity_and_revision",
        "explicit_empty_mismatch",
    }
)
# Rules whose repair would require choosing a business meaning or an item identity.
NON_REPAIRABLE_RULE_CODES = frozenset(
    {
        "destination_kind_mismatch",
        "create_cannot_target_existing_item",
        "mutation_requires_item_identity_and_revision",
        "subgroup_requires_label",
    }
)


class StrictDTO(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class SourceAttachment(StrictDTO):
    kind: Literal["photo", "document", "video", "audio", "voice", "animation"]
    mime_type: str | None = Field(default=None, max_length=255)
    size_bytes: int | None = Field(default=None, ge=0)
    media_group_id: str | None = Field(default=None, max_length=255)
    processing_state: Literal["metadata_only", "ready", "failed", "irrelevant"] = "metadata_only"
    # Metadata only (what the vision pass read on a photo goes into the message text). Optional for old frozen snapshots.
    file_name: str | None = Field(default=None, max_length=255)


class SourceMessage(StrictDTO):
    telegram_message_id: int
    revision: int = Field(ge=1)
    message_date: datetime
    text: str = Field(max_length=12000)
    reply_to_message_id: int | None = None
    topic_id: int | None = None
    topic_name: str | None = Field(default=None, max_length=255)
    author_alias: str | None = Field(default=None, max_length=64)
    # Group role of the sender (headman clarifications outrank ordinary messages).
    author_role: Literal["student", "deputy", "headman"] | None = None
    attachments: list[SourceAttachment] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def require_aware_timestamp(self):
        if self.message_date.utcoffset() is None:
            raise ValueError("source_timestamp_requires_offset")
        if not self.text and not self.attachments:
            raise ValueError("source_requires_text_or_attachment")
        return self


class SubgroupEntry(StrictDTO):
    """One subgroup of a subject taught in subgroups, with the headman's recognition signals."""

    name: str = Field(min_length=1, max_length=80)
    signals: list[str] = Field(default_factory=list, max_length=12)


class SubjectEntry(StrictDTO):
    """One real subject of a group, as the trusted catalog states it."""

    id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=255)
    aliases: list[str] = Field(default_factory=list, max_length=20)
    teachers: list[str] = Field(default_factory=list, max_length=10)
    subgroups: list[SubgroupEntry] = Field(default_factory=list, max_length=6)

    @model_validator(mode="after")
    def validate_labels(self):
        if not self.name.strip() or any(
            not item.strip() or len(item) > 255 for item in [*self.aliases, *self.teachers]
        ):
            raise ValueError("blank_or_long_catalog_label")
        return self


class SubjectCatalog(StrictDTO):
    """Trusted list of the real subjects of ONE group (internal contract, see subject_catalog.py).

    ``complete`` is true only when the source guarantees that no subject of the group is missing;
    an incomplete catalog never makes a subject outside it wrong, only unconfirmed.
    """

    group_id: UUID
    version: str | None = Field(default=None, max_length=64)
    complete: bool = False
    subjects: list[SubjectEntry] = Field(default_factory=list, max_length=80)

    @model_validator(mode="after")
    def validate_unique_ids(self):
        ids = [row.id for row in self.subjects]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate_subject_id")
        return self


class AnalysisInput(StrictDTO):
    schema_version: Literal[ANALYSIS_SCHEMA_VERSION] = ANALYSIS_SCHEMA_VERSION
    engine_version: Literal[ANALYSIS_ENGINE_VERSION] = ANALYSIS_ENGINE_VERSION
    group_id: UUID
    group_timezone: str
    source_revision: int = Field(ge=1)
    target_message_ids: list[int] = Field(min_length=1, max_length=80)
    messages: list[SourceMessage] = Field(min_length=1, max_length=80)
    # Not part of the source snapshot (see shadow.source_snapshot_sha256): reference data the
    # frozen run was analysed with. Absent on every run created before the catalog existed.
    subject_catalog: SubjectCatalog | None = None

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


class ContextFact(StrictDTO):
    fact: str = Field(min_length=1, max_length=1000)
    evidence: list[EvidenceRef] = Field(min_length=1, max_length=24)


class MemoryProposal(StrictDTO):
    fact_type: Literal["subject_alias", "teacher_alias", "subject_teacher"]
    normalized_key: str = Field(min_length=1, max_length=255)
    value: dict[str, str]
    confidence: int = Field(ge=0, le=100)
    evidence: list[EvidenceRef] = Field(min_length=1, max_length=24)


class ContextUnderstandingOutput(StrictDTO):
    schema_version: Literal[ANALYSIS_SCHEMA_VERSION] = ANALYSIS_SCHEMA_VERSION
    engine_version: Literal[ANALYSIS_ENGINE_VERSION] = ANALYSIS_ENGINE_VERSION
    source_revision: int = Field(ge=1)
    relevant_source_ids: list[int] = Field(default_factory=list, max_length=80)
    discussion_facts: list[ContextFact] = Field(default_factory=list, max_length=80)
    conflicts: list[ContextFact] = Field(default_factory=list, max_length=40)
    memory_proposals: list[MemoryProposal] = Field(default_factory=list, max_length=40)


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
    action: Literal["create", "update", "reschedule", "cancel"] = "create"
    existing_item_id: UUID | None = None
    expected_item_revision: int | None = Field(default=None, ge=1)
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
    # The model may name a catalog subject by id. The adapter checks it against the group's trusted
    # catalog and keeps it only when it is there; ``subject_source`` is set by code alone (never
    # accepted from the model) and says the subject was confirmed by that catalog.
    subject_id: str | None = Field(default=None, max_length=64)
    subject_source: Literal["catalog"] | None = Field(
        default=None, json_schema_extra={"internal": True}
    )
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
        if self.action == "create" and (
            self.existing_item_id is not None or self.expected_item_revision is not None
        ):
            raise ValueError("create_cannot_target_existing_item")
        if self.action != "create" and (
            self.existing_item_id is None or self.expected_item_revision is None
        ):
            raise ValueError("mutation_requires_item_identity_and_revision")
        return self


class ProposalSchemaIssue(StrictDTO):
    path: str = Field(
        max_length=160,
        pattern=r"^[A-Za-z0-9_<>.\[\]*-]+$",
    )
    error_type: str = Field(max_length=64, pattern=r"^[a-z][a-z0-9_]{0,63}$")
    expected: (
        Literal[
            "required_field",
            "field_not_allowed",
            "string",
            "integer",
            "boolean",
            "array",
            "object",
            "allowed_literal",
            "timezone_aware_datetime",
            "valid_identifier",
            "valid_value",
        ]
        | None
    ) = None
    received_type: (
        Literal[
            "missing",
            "null",
            "string",
            "integer",
            "number",
            "boolean",
            "array",
            "object",
            "other",
        ]
        | None
    ) = None


# The three product categories and how the published contract maps onto them. A proposal is only
# ever a "homework" (destination task) or a "check" (destination deadline); events and everything
# else the model finds travel as UnpublishedCandidate and are never shown to students.
PRODUCT_CATEGORY = {
    "homework": "homework",
    "test": "check",
    "control_point": "check",
    "assessment": "check",
    "credit": "check",
    "exam": "check",
    "other_academic_deadline": "other_deadline",
}


class CandidateProposal(StrictDTO):
    """What the model may report besides publishable proposals (never published).

    Free discovery: an event, an organisational instruction, other useful information or an item
    the model cannot confidently categorise. It must still be supported by cited messages.
    """

    category: Literal["homework", "check", "event", "organizational", "information", "other"]
    title: str | None = Field(default=None, max_length=255)
    description: str | None = Field(default=None, max_length=8000)
    subject: str | None = Field(default=None, max_length=255)
    when_text: str | None = Field(default=None, max_length=255)
    reason: str | None = Field(default=None, max_length=300)
    audience: Audience = Field(default_factory=Audience)
    confidence: int = Field(default=50, ge=0, le=100)
    evidence: list[EvidenceRef] = Field(min_length=1, max_length=24)


class UnpublishedCandidate(CandidateProposal):
    """A stored, non-publishable finding. The fields below are set by code, never by the model.

    It keeps the content (not just a reason code) of anything that is not a card so it can be
    reviewed or re-evaluated later: model candidates, and proposals the strict contract rejected
    (for example an unknown kind). Nothing here is applied, projected or shown to students.
    """

    hold_code: str = Field(default="category_not_published", max_length=64)
    # How far the cited sources are confirmed: verified = every cited message exists in the request
    # at the cited revision; partially_verified = some claimed ids were unusable and dropped;
    # defaulted_to_target = none was usable, the evidence below is the run's target message put
    # there by code and is NOT a source the model confirmed (claimed_source_ids is what it cited).
    source_status: Literal["verified", "partially_verified", "defaulted_to_target"] = "verified"
    claimed_source_ids: list[int] = Field(default_factory=list, max_length=24)
    origin: Literal["model_candidate", "rejected_proposal"] = "model_candidate"
    model_label: str | None = Field(default=None, max_length=64)
    proposal_ordinal: int | None = Field(default=None, ge=0)
    issues: list[ProposalSchemaIssue] = Field(default_factory=list, max_length=12)
    attention_reasons: list[str] = Field(default_factory=list, max_length=20)


class EventRecord(StrictDTO):
    """A stored event finding as a self-contained structured record (never published).

    Built from a held candidate of category ``event`` by ``candidates.event_records``; everything a
    future Mini App event needs is here, so nothing has to re-read the chat. ``when_text`` is the
    date/time as the messages worded it (no computed date). ``promotion_blockers`` lists why the
    record must not be published as it stands; an empty list means the data is complete, not that
    publishing is approved.
    """

    record_type: Literal["event"] = "event"
    fingerprint: str = Field(min_length=64, max_length=64)
    title: str | None = Field(default=None, max_length=255)
    description: str | None = Field(default=None, max_length=8000)
    when_text: str | None = Field(default=None, max_length=255)
    classification_reason: str | None = Field(default=None, max_length=300)
    audience: Audience = Field(default_factory=Audience)
    confidence: int = Field(ge=0, le=100)
    source_message_ids: list[int] = Field(min_length=1, max_length=24)
    evidence: list[EvidenceRef] = Field(min_length=1, max_length=24)
    source_status: Literal["verified", "partially_verified", "defaulted_to_target"]
    origin: Literal["model_candidate", "rejected_proposal"]
    published: Literal[False] = False
    promotion_blockers: list[str] = Field(default_factory=list, max_length=8)


class RejectedItem(StrictDTO):
    proposal_ordinal: int = Field(ge=0)
    reason_code: str = Field(max_length=64)
    schema_issues: list[ProposalSchemaIssue] = Field(default_factory=list, max_length=12)
    schema_issues_truncated: bool = False
    repair_outcome: str | None = Field(default=None, max_length=64, pattern=r"^[a-z][a-z0-9_]*$")
    # True when the rejected proposal's content was kept as an UnpublishedCandidate.
    salvaged: bool = False


class AnalysisOutput(StrictDTO):
    schema_version: Literal[ANALYSIS_SCHEMA_VERSION] = ANALYSIS_SCHEMA_VERSION
    engine_version: Literal[ANALYSIS_ENGINE_VERSION] = ANALYSIS_ENGINE_VERSION
    source_revision: int = Field(ge=1)
    proposals: list[ProposedAcademicItem] = Field(default_factory=list, max_length=40)
    candidates: list[CandidateProposal] = Field(default_factory=list, max_length=40)
    explicit_empty: bool = False

    @model_validator(mode="after")
    def validate_empty_marker(self):
        if self.explicit_empty != (not self.proposals):
            raise ValueError("explicit_empty_mismatch")
        return self


class ValidationResult(StrictDTO):
    source_revision: int = Field(ge=1)
    accepted: list[ProposedAcademicItem] = Field(default_factory=list)
    accepted_ordinals: list[int] = Field(default_factory=list)
    rejected: list[RejectedItem] = Field(default_factory=list)
    raw_proposal_count: int = Field(ge=0)
    explicit_empty: bool
    # Value-free counters of deterministic normalizations applied before strict validation.
    adapter_actions: dict[str, int] = Field(default_factory=dict)
    # Non-publishable findings kept for review/re-evaluation (older stored results have none).
    candidates: list[UnpublishedCandidate] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_accepted_ordinals(self):
        if len(self.accepted) != len(self.accepted_ordinals):
            raise ValueError("accepted_ordinal_count_mismatch")
        if len(set(self.accepted_ordinals)) != len(self.accepted_ordinals):
            raise ValueError("duplicate_accepted_ordinal")
        return self


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
