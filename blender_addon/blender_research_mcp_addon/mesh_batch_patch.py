"""Closed nested aliases for explicit patch operations."""

from __future__ import annotations

from typing import Any

from .mesh_resource_model import MeshResourceError


def patch_refs(operation: dict[str, Any]) -> list[tuple[str, str]]:
    result = []

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key == "selection_alias":
                    result.append((str(item), "EDGE"))
                elif key == "start_vertex_alias":
                    result.append((str(item), "VERTEX"))
                elif key in {"vertex_aliases", "corner_aliases"}:
                    if not isinstance(item, (list, tuple)):
                        raise MeshResourceError(
                            "MESH_BATCH_INVALID", "Vertex aliases must be an array"
                        )
                    result.extend((str(alias), "VERTEX") for alias in item)
                elif key in {"boundary", "paths"}:
                    visit(item)
        elif isinstance(value, (list, tuple)):
            for item in value:
                visit(item)

    visit(operation)
    return result


def explicit_patch(operation: dict[str, Any]) -> bool:
    return operation.get("type") in {"create_edge", "create_face"} or (
        operation.get("type") in {"bridge", "grid_fill"}
        and ("boundary" in operation or "paths" in operation)
    )


def resolve_patch_aliases(value: Any, selections: dict[str, Any]) -> Any:
    if isinstance(value, (list, tuple)):
        return [resolve_patch_aliases(item, selections) for item in value]
    if not isinstance(value, dict):
        return value
    result = {}
    for key, item in value.items():
        if key in {"selection_alias", "start_vertex_alias"}:
            result[
                {"selection_alias": "selection_id", "start_vertex_alias": "start_vertex"}[key]
            ] = selections[str(item)]["selection_id"]
        elif key in {"vertex_aliases", "corner_aliases"}:
            result["vertices" if key == "vertex_aliases" else "corners"] = [
                selections[str(alias)]["selection_id"] for alias in item
            ]
        else:
            result[key] = resolve_patch_aliases(item, selections)
    return result
