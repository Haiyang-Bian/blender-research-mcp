"""Classification of Blender dependency-graph updates."""

from __future__ import annotations

from typing import Any


def has_persistent_scene_update(depsgraph: Any) -> bool:
    """Ignore UI-only updates while retaining evaluated scene changes."""
    return any(
        bool(getattr(update, "is_updated_transform", False))
        or bool(getattr(update, "is_updated_geometry", False))
        or bool(getattr(update, "is_updated_shading", False))
        for update in getattr(depsgraph, "updates", ())
    )
