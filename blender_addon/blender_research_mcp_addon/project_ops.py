"""Thin Blender main-thread project and application lifecycle operations."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import bpy


class ProjectOperationError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        kind: str = "precondition",
        retryable: bool = False,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.kind = kind
        self.retryable = retryable
        self.details = details or {}


def normalized_path(path: str) -> str:
    return os.path.normcase(os.path.realpath(path))


def _absolute_blend_path(value: Any) -> Path:
    if not isinstance(value, str) or not value:
        raise ProjectOperationError(
            "PROJECT_PATH_INVALID",
            "Project path must be a non-empty absolute path",
            kind="validation",
        )
    path = Path(value)
    if not path.is_absolute() or path.suffix.lower() != ".blend":
        raise ProjectOperationError(
            "PROJECT_PATH_INVALID",
            "Project path must be absolute and end in .blend",
            kind="validation",
            details={"path": value},
        )
    return Path(os.path.realpath(path))


def validate_open_path(value: Any) -> Path:
    path = _absolute_blend_path(value)
    if not path.is_file():
        raise ProjectOperationError(
            "PROJECT_NOT_FOUND",
            f"Blender project does not exist: {path}",
            kind="not_found",
            details={"path": str(path)},
        )
    return path


def validate_save_path(value: Any) -> Path:
    path = _absolute_blend_path(value)
    if not path.parent.is_dir():
        raise ProjectOperationError(
            "PROJECT_PATH_INVALID",
            f"Project parent directory does not exist: {path.parent}",
            kind="validation",
            details={"path": str(path)},
        )
    return path


def project_status(
    scene_generation: int,
    active_transaction: dict[str, Any] | None,
    last_operation: dict[str, Any] | None,
) -> dict[str, Any]:
    filepath = str(bpy.data.filepath or "")
    if filepath:
        filepath = str(Path(os.path.realpath(filepath)))
    return {
        "filepath": filepath,
        "is_saved": bool(getattr(bpy.data, "is_saved", bool(filepath))),
        "is_dirty": bool(getattr(bpy.data, "is_dirty", False)),
        "scene_generation": scene_generation,
        "active_transaction": active_transaction,
        "last_operation": last_operation,
    }


def _require_finished(result: Any, code: str, operation: str) -> None:
    if not isinstance(result, set) or "FINISHED" not in result:
        raise ProjectOperationError(
            code,
            f"Blender {operation} operator did not finish",
            kind="blender_api",
            retryable=True,
            details={"operator_result": sorted(result) if isinstance(result, set) else result},
        )


def save_project(path: str | None = None) -> dict[str, Any]:
    before = project_status(0, None, None)
    current = str(bpy.data.filepath or "")
    if path is None:
        if not current:
            raise ProjectOperationError(
                "CURRENT_PROJECT_UNTITLED",
                "The current project has no file path; provide a save path",
            )
        target = Path(os.path.realpath(current))
    else:
        target = validate_save_path(path)
    try:
        if current and normalized_path(current) == normalized_path(str(target)):
            result = bpy.ops.wm.save_mainfile()
            mode = "save"
        else:
            result = bpy.ops.wm.save_as_mainfile(
                filepath=str(target),
                check_existing=False,
            )
            mode = "save_as"
    except Exception as exc:
        raise ProjectOperationError(
            "PROJECT_SAVE_FAILED",
            f"Blender could not save the project: {type(exc).__name__}",
            kind="blender_api",
            retryable=True,
            details={"path": str(target)},
        ) from exc
    _require_finished(result, "PROJECT_SAVE_FAILED", "save")
    after = project_status(0, None, None)
    return {
        "status": "saved",
        "mode": mode,
        "path": after["filepath"],
        "before": before,
        "after": after,
    }


def open_project(path: str, *, use_scripts: bool, load_ui: bool) -> None:
    try:
        result = bpy.ops.wm.open_mainfile(
            filepath=path,
            display_file_selector=False,
            use_scripts=use_scripts,
            load_ui=load_ui,
        )
    except Exception as exc:
        raise ProjectOperationError(
            "PROJECT_OPEN_FAILED",
            f"Blender could not open the project: {type(exc).__name__}",
            kind="blender_api",
            retryable=True,
            details={"path": path},
        ) from exc
    _require_finished(result, "PROJECT_OPEN_FAILED", "open")


def quit_application() -> None:
    try:
        result = bpy.ops.wm.quit_blender()
    except Exception as exc:
        raise ProjectOperationError(
            "APPLICATION_QUIT_FAILED",
            f"Blender could not quit: {type(exc).__name__}",
            kind="blender_api",
            retryable=True,
        ) from exc
    _require_finished(result, "APPLICATION_QUIT_FAILED", "quit")
