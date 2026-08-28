"""Structured bridge errors shared by the client and MCP surface."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ErrorKind(StrEnum):
    AUTHENTICATION = "authentication"
    PROTOCOL_VERSION = "protocol_version"
    VALIDATION = "validation"
    PRECONDITION = "precondition"
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"
    TIMEOUT = "timeout"
    UNAVAILABLE = "unavailable"
    BLENDER_API = "blender_api"
    INTERNAL = "internal"


class ErrorInfo(BaseModel):
    """Wire representation of a bridge failure."""

    model_config = ConfigDict(extra="forbid")

    kind: ErrorKind
    code: str
    message: str
    retryable: bool = False
    details: dict[str, Any] = Field(default_factory=dict)


class BridgeError(RuntimeError):
    """Raised when Blender returned a structured error response."""

    def __init__(self, error: ErrorInfo) -> None:
        super().__init__(f"{error.code}: {error.message}")
        self.error = error


class TransportError(BridgeError):
    """Raised when the local authenticated transport is unavailable."""


def transport_error(code: str, message: str, *, retryable: bool = True) -> TransportError:
    return TransportError(
        ErrorInfo(
            kind=ErrorKind.UNAVAILABLE,
            code=code,
            message=message,
            retryable=retryable,
        )
    )
