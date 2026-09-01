"""Blender-side revision-bound FACE ComponentCatalog operations."""

from __future__ import annotations

from collections import Counter
from typing import Any

import bpy

from .lookdev_ops import session_identity
from .mesh_component_catalog_model import (
    CATALOG_METRICS,
    ComponentCatalogRecord,
    connected_face_components,
    make_component_catalog,
)
from .mesh_ops import mesh_fingerprint, mesh_revision_id, mesh_user_refs
from .mesh_query_ops import validate_selection
from .mesh_resource_model import MeshResourceBook, MeshResourceError


def _include(raw: Any) -> tuple[str, ...]:
    if not isinstance(raw, list) or not raw:
        raise MeshResourceError(
            "MESH_COMPONENT_CATALOG_INVALID", "include must contain one or more metrics"
        )
    if any(not isinstance(value, str) or value not in CATALOG_METRICS for value in raw):
        raise MeshResourceError(
            "MESH_COMPONENT_CATALOG_INVALID", "include contains an unsupported metric"
        )
    result = tuple(raw)
    if len(result) != len(set(result)):
        raise MeshResourceError(
            "MESH_COMPONENT_CATALOG_INVALID", "include metrics must be unique"
        )
    return result


def _face_edges(mesh: Any) -> tuple[tuple[int, ...], ...]:
    return tuple(
        tuple(
            int(mesh.loops[loop_index].edge_index)
            for loop_index in range(face.loop_start, face.loop_start + face.loop_total)
        )
        for face in mesh.polygons
    )


def _component_metric(
    mesh: Any,
    face_edges: tuple[tuple[int, ...], ...],
    face_indices: tuple[int, ...],
) -> tuple[
    tuple[int, ...],
    float,
    tuple[float, float, float],
    tuple[float, float, float],
    int,
    tuple[int, ...],
]:
    area = sum(float(mesh.polygons[index].area) for index in face_indices)
    vertex_indices = sorted(
        {
            int(vertex_index)
            for face_index in face_indices
            for vertex_index in mesh.polygons[face_index].vertices
        }
    )
    coordinates = [mesh.vertices[index].co for index in vertex_indices]
    bounds_min = tuple(round(min(float(co[axis]) for co in coordinates), 9) for axis in range(3))
    bounds_max = tuple(round(max(float(co[axis]) for co in coordinates), 9) for axis in range(3))
    edge_counts = Counter(
        edge_index for face_index in face_indices for edge_index in face_edges[face_index]
    )
    boundary_count = sum(count == 1 for count in edge_counts.values())
    material_slots = tuple(
        sorted({int(mesh.polygons[index].material_index) for index in face_indices})
    )
    return (
        face_indices,
        round(area, 9),
        bounds_min,
        bounds_max,
        boundary_count,
        material_slots,
    )


def validate_component_catalog(record: ComponentCatalogRecord) -> tuple[Any, Any]:
    obj = bpy.data.objects.get(record.object_name)
    mesh = bpy.data.meshes.get(record.mesh_name)
    if obj is None or session_identity("object", obj) != record.object_identity:
        raise MeshResourceError(
            "MESH_COMPONENT_CATALOG_STALE",
            "ComponentCatalog object identity changed",
            kind="conflict",
        )
    if (
        mesh is None
        or obj.data is not mesh
        or session_identity("mesh", mesh) != record.mesh_identity
    ):
        raise MeshResourceError(
            "MESH_COMPONENT_CATALOG_STALE",
            "ComponentCatalog Mesh identity changed",
            kind="conflict",
        )
    actual_revision = mesh_revision_id(mesh)
    if (
        actual_revision != record.mesh_revision_id
        or mesh_fingerprint(mesh) != record.mesh_fingerprint
        or int(mesh.users) != record.expected_users
        or mesh_user_refs(mesh) != record.expected_user_objects
    ):
        raise MeshResourceError(
            "MESH_COMPONENT_CATALOG_STALE",
            "ComponentCatalog no longer matches the exact Mesh revision",
            kind="conflict",
            details={
                "expected_mesh_revision_id": record.mesh_revision_id,
                "actual_mesh_revision_id": actual_revision,
            },
        )
    return obj, mesh


def prepare_component_catalog(
    book: MeshResourceBook, params: dict[str, Any]
) -> dict[str, Any]:
    selection_id = params.get("selection_id")
    if not isinstance(selection_id, str) or not selection_id:
        raise MeshResourceError(
            "MESH_COMPONENT_CATALOG_INVALID", "selection_id is required"
        )
    selection = book.selection(selection_id)
    _obj, mesh = validate_selection(selection)
    if selection.domain != "FACE" or not selection.indices:
        raise MeshResourceError(
            "MESH_COMPONENT_CATALOG_SELECTION_INVALID",
            "ComponentCatalog requires a non-empty FACE SelectionSet",
        )
    include = _include(params.get("include"))
    face_edges = _face_edges(mesh)
    components = connected_face_components(face_edges, selection.indices)
    metrics = tuple(_component_metric(mesh, face_edges, component) for component in components)
    record = make_component_catalog(
        object_name=selection.object_name,
        object_identity=selection.object_identity,
        mesh_name=selection.mesh_name,
        mesh_identity=selection.mesh_identity,
        mesh_revision_id=selection.mesh_revision_id,
        mesh_fingerprint=selection.mesh_fingerprint,
        expected_users=selection.expected_users,
        expected_user_objects=selection.expected_user_objects,
        source_selection_id=selection.selection_id,
        source_selection_sha256=selection.content_sha256,
        source_indices=selection.indices,
        source_weights=selection.weights,
        include=include,
        component_metrics=metrics,
    )
    book.add_component_catalog(record)
    result = record.summary()
    result["resource_counts"] = {
        "selections": book.selection_count,
        "surfaces": book.surface_count,
        "component_maps": book.component_map_count,
        "component_catalogs": book.component_catalog_count,
    }
    return result


def inspect_component_catalog(
    book: MeshResourceBook, params: dict[str, Any]
) -> dict[str, Any]:
    component_catalog_id = params.get("component_catalog_id")
    if not isinstance(component_catalog_id, str) or not component_catalog_id:
        raise MeshResourceError(
            "MESH_COMPONENT_CATALOG_NOT_FOUND", "component_catalog_id is required"
        )
    record = book.component_catalog(component_catalog_id)
    validate_component_catalog(record)
    offset = int(params.get("offset", 0))
    limit = int(params.get("limit", 128))
    stop = min(len(record.components), offset + limit)
    result = record.summary()
    result["items"] = [item.report(record.include) for item in record.components[offset:stop]]
    result["pagination"] = {
        "offset": offset,
        "limit": limit,
        "total": len(record.components),
        "returned": max(0, stop - offset),
        "truncated": stop < len(record.components),
        "next_offset": stop if stop < len(record.components) else None,
    }
    return result


def select_component_catalog(
    book: MeshResourceBook, params: dict[str, Any]
) -> dict[str, Any]:
    component_catalog_id = params.get("component_catalog_id")
    if not isinstance(component_catalog_id, str) or not component_catalog_id:
        raise MeshResourceError(
            "MESH_COMPONENT_CATALOG_NOT_FOUND", "component_catalog_id is required"
        )
    raw_identities = params.get("component_identities")
    if not isinstance(raw_identities, list) or not raw_identities:
        raise MeshResourceError(
            "MESH_COMPONENT_SELECTION_INVALID", "component_identities must be non-empty"
        )
    if any(not isinstance(value, str) or not value for value in raw_identities):
        raise MeshResourceError(
            "MESH_COMPONENT_SELECTION_INVALID", "component identities must be strings"
        )
    if len(raw_identities) > 4096 or len(raw_identities) != len(set(raw_identities)):
        raise MeshResourceError(
            "MESH_COMPONENT_SELECTION_INVALID",
            "component identities must be unique and contain at most 4096 values",
        )
    record = book.component_catalog(component_catalog_id)
    validate_component_catalog(record)
    by_identity = {item.component_identity: item for item in record.components}
    missing = [identity for identity in raw_identities if identity not in by_identity]
    if missing:
        raise MeshResourceError(
            "MESH_COMPONENT_SELECTION_INVALID",
            "One or more component identities do not belong to the catalog",
            details={"missing_component_identities": missing[:32]},
        )
    indices = tuple(
        sorted(
            {
                face_index
                for identity in raw_identities
                for face_index in by_identity[identity].face_indices
            }
        )
    )
    weights = None
    if record.source_weights is not None:
        weight_by_index = dict(zip(record.source_indices, record.source_weights, strict=True))
        weights = tuple(weight_by_index[index] for index in indices)
    selection = book.add_selection(
        object_name=record.object_name,
        object_identity=record.object_identity,
        mesh_name=record.mesh_name,
        mesh_identity=record.mesh_identity,
        mesh_revision_id=record.mesh_revision_id,
        mesh_fingerprint=record.mesh_fingerprint,
        expected_users=record.expected_users,
        expected_user_objects=record.expected_user_objects,
        domain="FACE",
        indices=indices,
        weights=weights,
        source_query={
            "type": "component_catalog",
            "component_catalog_id": component_catalog_id,
            "component_identities": list(raw_identities),
        },
    )
    result = selection.summary()
    result["component_catalog_id"] = component_catalog_id
    result["selected_component_identities"] = list(raw_identities)
    return result


def release_component_catalog(
    book: MeshResourceBook, params: dict[str, Any]
) -> dict[str, Any]:
    component_catalog_id = params.get("component_catalog_id")
    if not isinstance(component_catalog_id, str) or not component_catalog_id:
        raise MeshResourceError(
            "MESH_COMPONENT_CATALOG_NOT_FOUND", "component_catalog_id is required"
        )
    return {
        "component_catalog_id": component_catalog_id,
        "released": book.release_component_catalog(component_catalog_id),
    }
