"""Versioned internal image evidence; no changes to public card contracts."""

from datetime import date, time
from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

IMAGE_VERSION = "image-analysis-1"
ImageState = Literal["pending", "running", "retry", "budget_deferred", "ready", "failed", "stale"]
SemanticType = Literal[
    "homework",
    "in_class_task",
    "material",
    "schedule",
    "schedule_change",
    "event",
    "assessment",
    "organizational",
    "irrelevant",
    "unknown",
]


class ImageDTO(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ImageFact(ImageDTO):
    text: str = Field(min_length=1, max_length=4000)
    certainty: Literal["confirmed", "uncertain"]
    region: str | None = Field(default=None, max_length=500)


class ImageBlock(ImageDTO):
    kind: Literal["text", "table", "list", "diagram", "visual"]
    content: str = Field(max_length=20000)
    rows: list[list[str]] = Field(default_factory=list, max_length=150)


class ScheduleRow(ImageDTO):
    weekday: int = Field(ge=0, le=6)
    starts: time
    ends: time
    subject: str = Field(min_length=1, max_length=255)
    room: str | None = Field(default=None, max_length=255)
    teacher: str | None = Field(default=None, max_length=255)
    subgroup: str | None = Field(default=None, max_length=255)
    week: Literal["all", "first", "second"] = "all"
    valid_from: date | None = None
    valid_until: date | None = None

    @model_validator(mode="after")
    def ordered(self):
        if self.ends <= self.starts:
            raise ValueError("schedule_time_order")
        if self.valid_from and self.valid_until and self.valid_until < self.valid_from:
            raise ValueError("schedule_date_order")
        return self


class ImageEvent(ImageDTO):
    title: str = Field(min_length=1, max_length=255)
    description: str = Field(default="", max_length=8000)
    when_text: str | None = Field(default=None, max_length=1000)
    starts_at: AwareDatetime | None = None
    ends_at: AwareDatetime | None = None
    starts_on: date | None = None
    ends_on: date | None = None
    multi_day: bool = False
    place: str | None = Field(default=None, max_length=500)
    conditions: str | None = Field(default=None, max_length=2000)
    audience: Literal["group", "subgroup", "personal", "unknown"] = "unknown"
    audience_label: str | None = Field(default=None, max_length=255)
    action: Literal["create", "update", "cancel"] = "create"
    existing_event_id: UUID | None = None
    expected_revision: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def identity(self):
        if self.starts_at and self.ends_at and self.ends_at < self.starts_at:
            raise ValueError("event_end_before_start")
        if self.starts_on and self.ends_on and self.ends_on < self.starts_on:
            raise ValueError("event_end_before_start")
        if self.action != "create" and (
            self.existing_event_id is None or self.expected_revision is None
        ):
            raise ValueError("event_mutation_requires_identity")
        if self.action == "create" and (
            self.existing_event_id is not None or self.expected_revision is not None
        ):
            raise ValueError("event_create_cannot_target_identity")
        return self


class VisualReading(ImageDTO):
    version: Literal["image-analysis-1"] = IMAGE_VERSION
    summary: str = Field(max_length=2000)
    extracted_text: str = Field(max_length=50000)
    blocks: list[ImageBlock] = Field(default_factory=list, max_length=200)
    facts: list[ImageFact] = Field(default_factory=list, max_length=200)
    uncertain_fragments: list[str] = Field(default_factory=list, max_length=100)
    # An incomplete result is retained for diagnosis, never published as complete evidence.
    complete: bool
    semantic_types: list[SemanticType] = Field(default_factory=list, max_length=10)
    required_actions: list[str] = Field(default_factory=list, max_length=100)
    academic_context: str | None = Field(default=None, max_length=4000)
    schedule: list[ScheduleRow] = Field(default_factory=list, max_length=100)
    events: list[ImageEvent] = Field(default_factory=list, max_length=40)


class ImageEvidence(ImageDTO):
    attachment_id: UUID
    telegram_message_id: int
    source_revision: int = Field(ge=1)
    context_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    state: ImageState
    reading: VisualReading | None = None
    error: str | None = Field(default=None, max_length=100)

    @model_validator(mode="after")
    def ready_requires_complete_reading(self):
        if self.state == "ready" and (self.reading is None or not self.reading.complete):
            raise ValueError("ready_image_requires_complete_reading")
        if self.state != "ready" and self.reading is not None:
            raise ValueError("unfinished_image_cannot_confirm_facts")
        return self


def fact_reference_index(evidence):
    """Copyable exact references; no inference or change to stored facts."""
    return [
        {
            "reference": f"image:{image.attachment_id}:{index}",
            "telegram_message_id": image.telegram_message_id,
            "revision": image.source_revision,
            "text": fact.text,
        }
        for image in evidence
        if image.state == "ready" and image.reading
        for index, fact in enumerate(image.reading.facts)
        if fact.certainty == "confirmed"
    ]
