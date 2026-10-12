"""Provider transport port; domain publication is intentionally absent."""

from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field


class PreparedRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    system_prompt: str
    user_payload: dict[str, Any]
    schema_version: str
    prompt_version: str
    max_output_tokens: int = Field(gt=0, le=8000)


class ProviderUsage(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    prompt_tokens: int = Field(ge=0)
    completion_tokens: int = Field(ge=0)
    prompt_cache_hit_tokens: int = Field(default=0, ge=0)


class ProviderResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    content: str
    usage: ProviderUsage
    model: str
    finish_reason: str
    payload_sha256: str


class AIProvider(Protocol):
    async def complete(self, request: PreparedRequest) -> ProviderResponse: ...
