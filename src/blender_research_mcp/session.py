"""Discovery of the ephemeral Blender add-on session manifest."""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from blender_research_mcp.constants import DEFAULT_HOST, DEFAULT_PORT, SESSION_DIRECTORY_NAME
from blender_research_mcp.errors import transport_error


class SessionManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    protocol: int
    host: str = DEFAULT_HOST
    port: int = Field(default=DEFAULT_PORT, ge=1, le=65535)
    pid: int = Field(gt=0)
    instance_id: str = Field(min_length=1, max_length=128)
    session_token: str = Field(min_length=32, max_length=512)
    addon_version: str
    created_at: datetime


def runtime_directory() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    if base:
        return Path(base) / SESSION_DIRECTORY_NAME / "runtime"
    xdg_runtime = os.environ.get("XDG_RUNTIME_DIR")
    if xdg_runtime:
        return Path(xdg_runtime) / SESSION_DIRECTORY_NAME
    user_id = getattr(os, "getuid", os.getpid)()
    return Path(tempfile.gettempdir()) / SESSION_DIRECTORY_NAME / str(user_id)


def manifest_path(port: int = DEFAULT_PORT) -> Path:
    return runtime_directory() / f"session-{port}.json"


def pid_exists(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def load_manifest(port: int = DEFAULT_PORT, path: Path | None = None) -> SessionManifest:
    target = path or manifest_path(port)
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
        manifest = SessionManifest.model_validate(raw)
    except FileNotFoundError as exc:
        raise transport_error(
            "SESSION_NOT_FOUND",
            f"Blender session manifest was not found at {target}",
        ) from exc
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise transport_error(
            "SESSION_INVALID",
            f"Blender session manifest is invalid: {target}",
            retryable=False,
        ) from exc
    if manifest.host != DEFAULT_HOST or manifest.port != port:
        raise transport_error(
            "SESSION_MISMATCH",
            "Blender session manifest does not match the requested loopback endpoint",
            retryable=False,
        )
    if manifest.protocol != 1:
        raise transport_error(
            "PROTOCOL_MISMATCH",
            f"Blender session uses unsupported protocol {manifest.protocol}",
            retryable=False,
        )
    if not pid_exists(manifest.pid):
        raise transport_error(
            "SESSION_STALE",
            f"Blender process {manifest.pid} is no longer running",
        )
    return manifest
