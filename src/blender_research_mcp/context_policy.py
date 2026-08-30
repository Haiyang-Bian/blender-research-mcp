"""Shared external policy for separating scene guards from collaborative UI state."""

from __future__ import annotations

from typing import Any

GUARDED_CONTEXT_KEYS = (
    "scene",
    "view_layer",
    "mode",
    "frame_current",
    "active_camera",
)
USER_UI_CONTEXT_KEYS = (
    "workspace",
    "window_id",
    "area_id",
    "region_id",
    "viewport_id",
    "active_object",
    "selected_objects",
    "view",
)


def _projection(value: dict[str, Any], keys: tuple[str, ...]) -> dict[str, Any]:
    return {key: value[key] for key in keys if key in value}


def guarded_context_identity(value: dict[str, Any]) -> dict[str, Any]:
    """Return context that must stay fixed for scene-data evidence."""

    return _projection(value, GUARDED_CONTEXT_KEYS)


def user_ui_context(value: dict[str, Any]) -> dict[str, Any]:
    """Return navigation and selection state that the user may change concurrently."""

    return _projection(value, USER_UI_CONTEXT_KEYS)


def changed_paths(before: Any, after: Any, prefix: str = "") -> list[str]:
    if isinstance(before, dict) and isinstance(after, dict):
        changed: list[str] = []
        for key in sorted(before.keys() | after.keys()):
            path = f"{prefix}.{key}" if prefix else str(key)
            if key not in before or key not in after:
                changed.append(path)
            else:
                changed.extend(changed_paths(before[key], after[key], path))
        return changed
    return [] if before == after else [prefix or "context"]
