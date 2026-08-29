"""Discovery of the ephemeral Blender add-on session manifest."""

from __future__ import annotations

import ctypes
import json
import os
import tempfile
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from blender_research_mcp.constants import DEFAULT_HOST, DEFAULT_PORT, SESSION_DIRECTORY_NAME
from blender_research_mcp.errors import TransportError, transport_error


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
    launch_id: str | None = Field(default=None, min_length=1, max_length=128)


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


def manifest_candidates(port: int = DEFAULT_PORT) -> list[Path]:
    """Return the ordinary path plus narrowly-scoped Microsoft Store paths."""
    primary = manifest_path(port)
    candidates = [primary]
    local_app_data = os.environ.get("LOCALAPPDATA")
    if os.name != "nt" or not local_app_data:
        return candidates
    packages = Path(local_app_data) / "Packages"
    for package in sorted(packages.glob("BlenderFoundation.Blender*_*")):
        candidate = (
            package
            / "LocalCache"
            / "Local"
            / SESSION_DIRECTORY_NAME
            / "runtime"
            / f"session-{port}.json"
        )
        if candidate != primary:
            candidates.append(candidate)
    return candidates


def pid_exists(pid: int) -> bool:
    if os.name == "nt":
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        open_process = kernel32.OpenProcess
        open_process.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
        open_process.restype = ctypes.c_void_p
        close_handle = kernel32.CloseHandle
        close_handle.argtypes = [ctypes.c_void_p]
        close_handle.restype = ctypes.c_int
        handle = open_process(0x1000, 0, pid)
        if handle:
            close_handle(handle)
            return True
        return ctypes.get_last_error() == 5
    try:
        os.kill(pid, 0)
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _load_manifest_file(target: Path, port: int) -> SessionManifest:
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


def load_manifest(port: int = DEFAULT_PORT, path: Path | None = None) -> SessionManifest:
    if path is not None:
        return _load_manifest_file(path, port)

    candidates = manifest_candidates(port)
    existing = [candidate for candidate in candidates if candidate.is_file()]
    if not existing:
        raise transport_error(
            "SESSION_NOT_FOUND",
            f"Blender session manifest was not found at {candidates[0]}",
        )

    valid: dict[tuple[int, str], SessionManifest] = {}
    errors: list[TransportError] = []
    for candidate in existing:
        try:
            manifest = _load_manifest_file(candidate, port)
        except TransportError as exc:
            errors.append(exc)
            continue
        valid[(manifest.pid, manifest.instance_id)] = manifest

    if len(valid) == 1:
        return next(iter(valid.values()))
    if len(valid) > 1:
        raise transport_error(
            "SESSION_CONFLICT",
            f"Multiple live Blender sessions advertise loopback port {port}",
            retryable=False,
        )
    raise errors[0]
