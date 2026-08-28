"""Versioned JSON request and response envelopes."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from blender_research_mcp.constants import (
    DEFAULT_DEADLINE_MS,
    MAX_DEADLINE_MS,
    MIN_DEADLINE_MS,
    PROTOCOL_VERSION,
)
from blender_research_mcp.errors import ErrorInfo


class RequestEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    protocol: int = PROTOCOL_VERSION
    request_id: UUID
    session_token: str = Field(min_length=32, max_length=512)
    command: str = Field(min_length=1, max_length=128)
    params: dict[str, Any] = Field(default_factory=dict)
    deadline_ms: int = Field(
        default=DEFAULT_DEADLINE_MS,
        ge=MIN_DEADLINE_MS,
        le=MAX_DEADLINE_MS,
    )
    expected_scene_generation: int | None = Field(default=None, ge=0)
    idempotency_key: str | None = Field(default=None, min_length=1, max_length=128)


class ResponseEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    protocol: int = PROTOCOL_VERSION
    request_id: UUID
    ok: bool
    scene_generation: int = Field(ge=0)
    result: dict[str, Any] | None = None
    error: ErrorInfo | None = None

    @model_validator(mode="after")
    def validate_result_or_error(self) -> ResponseEnvelope:
        if self.ok and self.error is not None:
            raise ValueError("successful responses cannot contain an error")
        if not self.ok and self.error is None:
            raise ValueError("failed responses must contain an error")
        return self


class HandshakeResult(BaseModel):
    model_config = ConfigDict(extra="allow")

    protocol: int
    instance_id: str
    blender_version: str
    addon_version: str
    capabilities: list[str]
