"""Deterministic network-free replay of captured provider content."""

import hashlib
import json
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from studgroup.analysis.contracts import AnalysisInput, ApplicationOutcome, ValidationResult
from studgroup.analysis.normalization import normalize_validation
from studgroup.analysis.publication import ShadowPublisher
from studgroup.analysis.validation import validate_provider_content


class ReplayMode(StrEnum):
    VALIDATE_ONLY = "validate_only"
    RESOLVE_ONLY = "resolve_only"
    DRY_RUN = "dry_run"


class ReplayEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    analysis_input: AnalysisInput
    provider_content: str
    prompt_version: str

    @property
    def replay_key(self):
        material = {
            "group_id": str(self.analysis_input.group_id),
            "source_revision": self.analysis_input.source_revision,
            "schema_version": self.analysis_input.schema_version,
            "engine_version": self.analysis_input.engine_version,
            "prompt_version": self.prompt_version,
            "provider_content": self.provider_content,
        }
        return hashlib.sha256(
            json.dumps(material, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()


class ReplayResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    replay_key: str
    mode: ReplayMode
    validation: ValidationResult
    application_outcomes: list[ApplicationOutcome] = Field(default_factory=list)
    product_writes: int = 0
    notifications: int = 0
    network_calls: int = 0


async def replay(envelope: ReplayEnvelope, mode=ReplayMode.VALIDATE_ONLY):
    allowed = {
        row.telegram_message_id: row.revision for row in envelope.analysis_input.messages
    }
    validation = validate_provider_content(
        envelope.provider_content,
        allowed_sources=allowed,
        source_revision=envelope.analysis_input.source_revision,
    )
    if mode != ReplayMode.VALIDATE_ONLY:
        validation = normalize_validation(validation)
    application_outcomes = (
        await ShadowPublisher().apply(validation) if mode == ReplayMode.DRY_RUN else []
    )
    return ReplayResult(
        replay_key=envelope.replay_key,
        mode=mode,
        validation=validation,
        application_outcomes=application_outcomes,
    )
