"""One-revision ComponentMap records and SelectionSet remapping."""

from __future__ import annotations

from typing import Any

import bpy

from .mesh_component_map_model import (
    DOMAINS,
    ComponentMapRecord,
    remap_relation_values,
    reverse_relation_values,
)
from .mesh_ops import mesh_fingerprint, mesh_revision_id, mesh_user_refs
from .mesh_resource_model import MeshResourceBook, MeshResourceError, SelectionRecord
from .structural_ops import session_identity


def _page(items: tuple[Any, ...], offset: int, limit: int) -> tuple[Any, ...]:
    return items[offset : offset + limit]


def inspect_component_map(book: MeshResourceBook, params: dict[str, Any]) -> dict[str, Any]:
    record = book.component_map(str(params.get("component_map_id", "")))
    domain = str(params.get("domain", "SUMMARY"))
    direction = str(params.get("direction", "FORWARD"))
    offset = int(params.get("offset", 0))
    limit = int(params.get("limit", 256))
    if domain == "SUMMARY":
        return {"component_map": record.summary(), "page": None}
    if domain not in DOMAINS or direction not in {"FORWARD", "REVERSE", "CREATED", "DELETED"}:
        raise MeshResourceError("MESH_COMPONENT_MAP_INVALID", "Invalid map domain or direction")
    if offset < 0 or not 1 <= limit <= 4096:
        raise MeshResourceError("MESH_COMPONENT_MAP_INVALID", "Invalid map page")

    if direction == "FORWARD":
        values: tuple[Any, ...] = tuple(
            {
                "source_index": item.source_index,
                "target_indices": list(item.target_indices),
                "relation": item.relation,
            }
            for item in record.relations.get(domain, ())
        )
    elif direction == "REVERSE":
        values = reverse_relation_values(record.relations.get(domain, ()))
    elif direction == "CREATED":
        values = tuple(record.created.get(domain, ()))
    else:
        values = tuple(record.deleted.get(domain, ()))
    page = _page(values, offset, limit)
    return {
        "component_map": record.summary(),
        "domain": domain,
        "direction": direction,
        "items": list(page),
        "pagination": {
            "offset": offset,
            "limit": limit,
            "returned": len(page),
            "total": len(values),
            "truncated": offset + len(page) < len(values),
        },
    }


def release_component_map(book: MeshResourceBook, params: dict[str, Any]) -> dict[str, Any]:
    component_map_id = str(params.get("component_map_id", ""))
    return {
        "component_map_id": component_map_id,
        "released": book.release_component_map(component_map_id),
    }


def _validate_live_after(record: ComponentMapRecord) -> tuple[Any, Any]:
    obj = bpy.data.objects.get(record.after_object_name)
    if obj is None or session_identity("object", obj) != record.after_object_identity:
        raise MeshResourceError(
            "MESH_COMPONENT_MAP_STALE",
            "ComponentMap after-object no longer exists",
            kind="conflict",
        )
    mesh = getattr(obj, "data", None)
    if (
        mesh is None
        or session_identity("mesh", mesh) != record.after_mesh_identity
        or mesh_fingerprint(mesh) != record.after_mesh_fingerprint
        or mesh_revision_id(mesh) != record.after_mesh_revision_id
        or int(mesh.users) != record.after_users
        or mesh_user_refs(mesh) != record.after_user_objects
    ):
        raise MeshResourceError(
            "MESH_COMPONENT_MAP_STALE",
            "ComponentMap after-revision no longer matches the live Mesh",
            kind="conflict",
        )
    return obj, mesh


def remap_selection(book: MeshResourceBook, params: dict[str, Any]) -> dict[str, Any]:
    selection: SelectionRecord = book.selection(str(params.get("selection_id", "")))
    record = book.component_map(str(params.get("component_map_id", "")))
    mode = str(params.get("mode", "ALL_MAPPED"))
    weight_merge = str(params.get("weight_merge", "MAX"))
    if mode not in {"ALL_MAPPED", "EXACT_SURVIVORS", "STRICT"}:
        raise MeshResourceError("MESH_COMPONENT_MAP_INVALID", "Invalid remap mode")
    if weight_merge not in {"MAX", "AVERAGE"}:
        raise MeshResourceError("MESH_COMPONENT_MAP_INVALID", "Invalid weight merge mode")
    if (
        selection.object_identity != record.before_object_identity
        or selection.mesh_identity != record.before_mesh_identity
        or selection.mesh_revision_id != record.before_mesh_revision_id
        or selection.mesh_fingerprint != record.before_mesh_fingerprint
    ):
        raise MeshResourceError(
            "MESH_COMPONENT_MAP_REVISION_MISMATCH",
            "SelectionSet does not target the ComponentMap before-revision",
        )
    obj, mesh = _validate_live_after(record)
    indices, remapped_weights, missing = remap_relation_values(
        source_indices=selection.indices,
        source_weights=selection.weights,
        relations=record.relations.get(selection.domain, ()),
        mode=mode,
        weight_merge=weight_merge,
    )
    if mode == "STRICT" and missing:
        raise MeshResourceError(
            "MESH_SELECTION_REMAP_INCOMPLETE",
            "Strict SelectionSet remap has unmapped components",
            details={"missing_count": len(missing), "missing_sample": missing[:64]},
        )
    rebound = book.add_selection(
        object_name=obj.name,
        object_identity=session_identity("object", obj),
        mesh_name=mesh.name,
        mesh_identity=session_identity("mesh", mesh),
        mesh_revision_id=mesh_revision_id(mesh),
        mesh_fingerprint=mesh_fingerprint(mesh),
        expected_users=int(mesh.users),
        expected_user_objects=mesh_user_refs(mesh),
        domain=selection.domain,
        indices=indices,
        weights=remapped_weights,
        source_query={
            "type": "component_map_remap",
            "selection_id": selection.selection_id,
            "component_map_id": record.component_map_id,
            "mode": mode,
            "weight_merge": weight_merge,
        },
    )
    return {
        "component_map": record.summary(),
        "source_selection": selection.summary(),
        "selection": rebound.summary(),
        "unmapped_count": len(missing),
        "unmapped_sample": missing[:64],
    }
