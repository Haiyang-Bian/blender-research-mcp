"""Exact base-Mesh inspection and bounded semantic BMesh editing."""

from __future__ import annotations

import contextlib
import hashlib
import math
import struct
from array import array
from typing import Any

import bmesh
import bpy
from mathutils import Euler, Vector

from .lookdev_ops import session_identity
from .mesh_resource_model import MeshResourceError
from .structural_ops import refresh_structure_guard_if_present
from .transaction_model import MeshEditDelta, MeshSnapshotGuard, Transaction

MAX_VERTICES = 500_000
MAX_EDGES = 1_000_000
MAX_FACES = 500_000
MAX_LOOPS = 2_000_000
MAX_COMPONENT_TARGETS = 4096
SUPPORTED_ATTRIBUTE_TYPES = {
    "FLOAT",
    "INT",
    "BOOLEAN",
    "FLOAT_VECTOR",
    "FLOAT_COLOR",
    "BYTE_COLOR",
    "FLOAT2",
    "INT32_2D",
}
_ATTRIBUTE_LAYOUTS = {
    "FLOAT": ("value", "f", 1),
    "INT": ("value", "i", 1),
    "BOOLEAN": ("value", "b", 1),
    "FLOAT_VECTOR": ("vector", "f", 3),
    "FLOAT_COLOR": ("color", "f", 4),
    "BYTE_COLOR": ("color", "f", 4),
    "FLOAT2": ("vector", "f", 2),
    "INT32_2D": ("value", "i", 2),
}
_BUILTIN_ATTRIBUTE_NAMES = {
    "position",
    "material_index",
    "sharp_edge",
    "sharp_face",
}


class MeshOperationError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        kind: str = "validation",
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.kind = kind
        self.details = details or {}


def _hash_text(hasher: Any, value: Any) -> None:
    encoded = str(value).encode("utf-8")
    hasher.update(struct.pack("<I", len(encoded)))
    hasher.update(encoded)


def _hash_foreach(
    hasher: Any,
    collection: Any,
    prop: str,
    typecode: str,
    width: int,
) -> None:
    size = len(collection) * width
    values = array(typecode, [0]) * size
    if size:
        collection.foreach_get(prop, values)
        hasher.update(values.tobytes())


def topology_fingerprint(mesh: Any) -> str:
    hasher = hashlib.sha256()
    hasher.update(
        struct.pack(
            "<QQQQ",
            len(mesh.vertices),
            len(mesh.edges),
            len(mesh.polygons),
            len(mesh.loops),
        )
    )
    edge_vertices = array("i", [0]) * (len(mesh.edges) * 2)
    if edge_vertices:
        mesh.edges.foreach_get("vertices", edge_vertices)
        for index in range(0, len(edge_vertices), 2):
            first = edge_vertices[index]
            second = edge_vertices[index + 1]
            if first > second:
                edge_vertices[index] = second
                edge_vertices[index + 1] = first
        hasher.update(edge_vertices.tobytes())
    _hash_foreach(hasher, mesh.loops, "vertex_index", "i", 1)
    _hash_foreach(hasher, mesh.loops, "edge_index", "i", 1)
    _hash_foreach(hasher, mesh.polygons, "loop_start", "i", 1)
    _hash_foreach(hasher, mesh.polygons, "loop_total", "i", 1)
    return hasher.hexdigest()


def _is_protected_attribute(attribute: Any) -> bool:
    return not attribute.name.startswith(".") and attribute.name not in _BUILTIN_ATTRIBUTE_NAMES


def _hash_attributes(hasher: Any, mesh: Any) -> tuple[str, ...]:
    unsupported: list[str] = []
    attributes = (attribute for attribute in mesh.attributes if _is_protected_attribute(attribute))
    for attribute in sorted(attributes, key=lambda item: item.name):
        data_type = str(attribute.data_type)
        _hash_text(hasher, attribute.name)
        _hash_text(hasher, attribute.domain)
        _hash_text(hasher, data_type)
        layout = _ATTRIBUTE_LAYOUTS.get(data_type)
        if layout is None:
            unsupported.append(f"{attribute.name}:{data_type}")
            for item in attribute.data:
                value = next(
                    (
                        getattr(item, name)
                        for name in ("value", "vector", "color")
                        if hasattr(item, name)
                    ),
                    None,
                )
                _hash_text(hasher, repr(value))
            continue
        prop, typecode, width = layout
        _hash_foreach(hasher, attribute.data, prop, typecode, width)
    return tuple(unsupported)


def mesh_fingerprint(mesh: Any) -> str:
    hasher = hashlib.sha256()
    hasher.update(bytes.fromhex(topology_fingerprint(mesh)))
    _hash_foreach(hasher, mesh.vertices, "co", "f", 3)
    _hash_foreach(hasher, mesh.vertices, "select", "b", 1)
    _hash_foreach(hasher, mesh.vertices, "hide", "b", 1)
    _hash_foreach(hasher, mesh.edges, "select", "b", 1)
    _hash_foreach(hasher, mesh.edges, "hide", "b", 1)
    _hash_foreach(hasher, mesh.edges, "use_edge_sharp", "b", 1)
    _hash_foreach(hasher, mesh.edges, "use_seam", "b", 1)
    _hash_foreach(hasher, mesh.polygons, "material_index", "i", 1)
    _hash_foreach(hasher, mesh.polygons, "use_smooth", "b", 1)
    _hash_foreach(hasher, mesh.polygons, "select", "b", 1)
    _hash_foreach(hasher, mesh.polygons, "hide", "b", 1)
    hasher.update(struct.pack("<I", len(mesh.materials)))
    for material in mesh.materials:
        _hash_text(
            hasher,
            session_identity("material", material) if material is not None else None,
        )
    _hash_attributes(hasher, mesh)
    _hash_text(hasher, int(getattr(mesh.uv_layers, "active_index", -1)))
    for layer in mesh.uv_layers:
        _hash_text(hasher, layer.name)
        _hash_text(hasher, bool(getattr(layer, "active_render", False)))
        _hash_text(hasher, bool(getattr(layer, "active_clone", False)))
        for prop in ("pin_uv",):
            if len(layer.data) and hasattr(layer.data[0], prop):
                _hash_foreach(hasher, layer.data, prop, "b", 1)
    return hasher.hexdigest()


def mesh_revision_id(mesh: Any) -> str:
    """Return session-scoped content evidence for one exact Mesh revision."""

    hasher = hashlib.sha256()
    _hash_text(hasher, session_identity("mesh", mesh))
    _hash_text(hasher, mesh_fingerprint(mesh))
    _hash_text(hasher, int(mesh.users))
    for object_name, object_identity in mesh_user_refs(mesh):
        _hash_text(hasher, object_name)
        _hash_text(hasher, object_identity)
    return hasher.hexdigest()


def unsupported_attributes(mesh: Any) -> tuple[str, ...]:
    return tuple(
        f"{attribute.name}:{attribute.data_type}"
        for attribute in mesh.attributes
        if _is_protected_attribute(attribute)
        and str(attribute.data_type) not in SUPPORTED_ATTRIBUTE_TYPES
    )


def mesh_user_objects(mesh: Any) -> tuple[Any, ...]:
    return tuple(
        sorted((obj for obj in bpy.data.objects if obj.data is mesh), key=lambda obj: obj.name)
    )


def mesh_user_refs(mesh: Any) -> tuple[tuple[str, str], ...]:
    return tuple((obj.name, session_identity("object", obj)) for obj in mesh_user_objects(mesh))


def mesh_counts(mesh: Any) -> dict[str, int]:
    return {
        "vertices": len(mesh.vertices),
        "edges": len(mesh.edges),
        "faces": len(mesh.polygons),
        "loops": len(mesh.loops),
    }


def _mesh_reference(mesh: Any) -> dict[str, Any]:
    return {
        "name": mesh.name,
        "session_identity": session_identity("mesh", mesh),
        "users": int(mesh.users),
        "user_objects": [
            {"object_name": name, "session_identity": identity}
            for name, identity in mesh_user_refs(mesh)
        ],
    }


def _budget_details(mesh: Any) -> dict[str, Any]:
    counts = mesh_counts(mesh)
    limits = {
        "vertices": MAX_VERTICES,
        "edges": MAX_EDGES,
        "faces": MAX_FACES,
        "loops": MAX_LOOPS,
    }
    return {
        "counts": counts,
        "limits": limits,
        "within_budget": all(counts[name] <= limits[name] for name in limits),
    }


def _ensure_budget(mesh: Any) -> None:
    details = _budget_details(mesh)
    if not details["within_budget"]:
        raise MeshOperationError(
            "MESH_BUDGET_EXCEEDED",
            f"Mesh exceeds the bounded topology budget: {mesh.name}",
            details=details,
        )


def _mesh_object(object_name: str) -> tuple[Any, Any]:
    obj = bpy.data.objects.get(object_name)
    if obj is None:
        raise MeshOperationError(
            "OBJECT_NOT_FOUND",
            f"Object does not exist: {object_name}",
            kind="not_found",
        )
    if obj.type != "MESH" or obj.data is None:
        raise MeshOperationError(
            "MESH_OBJECT_UNSUPPORTED",
            f"Semantic Mesh operations require a MESH object: {object_name}",
        )
    return obj, obj.data


def _writable_reasons(obj: Any, mesh: Any) -> list[str]:
    reasons = []
    if obj.library is not None and obj.override_library is None:
        reasons.append("MESH_LINKED")
    if mesh.library is not None and mesh.override_library is None:
        reasons.append("MESH_LINKED")
    if mesh.shape_keys is not None:
        reasons.append("MESH_SHAPE_KEYS_UNSUPPORTED")
    if bool(getattr(mesh, "is_editmode", False)):
        reasons.append("MESH_EDIT_MODE_CONFLICT")
    if not obj.users_collection:
        reasons.append("OBJECT_PENDING_DELETE")
    if len(mesh_user_objects(mesh)) != int(mesh.users):
        reasons.append("MESH_NON_OBJECT_USERS_UNSUPPORTED")
    if not _budget_details(mesh)["within_budget"]:
        reasons.append("MESH_BUDGET_EXCEEDED")
    if unsupported_attributes(mesh):
        reasons.append("MESH_ATTRIBUTE_UNSUPPORTED")
    return reasons


def _attribute_writable_reasons(obj: Any, mesh: Any) -> list[str]:
    """Return blockers for topology-stable UV and deform-weight authoring."""

    reasons = []
    if obj.library is not None and obj.override_library is None:
        reasons.append("MESH_LINKED")
    if mesh.library is not None and mesh.override_library is None:
        reasons.append("MESH_LINKED")
    if bool(getattr(mesh, "is_editmode", False)):
        reasons.append("MESH_EDIT_MODE_CONFLICT")
    if not obj.users_collection:
        reasons.append("OBJECT_PENDING_DELETE")
    if len(mesh_user_objects(mesh)) != int(mesh.users):
        reasons.append("MESH_NON_OBJECT_USERS_UNSUPPORTED")
    if not _budget_details(mesh)["within_budget"]:
        reasons.append("MESH_BUDGET_EXCEEDED")
    return reasons


def inspect_mesh(
    object_name: str,
    component: str,
    offset: int,
    limit: int,
) -> dict[str, Any]:
    obj, mesh = _mesh_object(object_name)
    if component not in {"summary", "vertices", "edges", "faces"}:
        raise MeshOperationError("MESH_COMPONENT_INVALID", f"Unsupported component: {component}")
    if offset < 0 or not 1 <= limit <= 512:
        raise MeshOperationError(
            "MESH_PAGINATION_INVALID",
            "offset must be non-negative and limit must be between 1 and 512",
        )
    users = mesh_user_objects(mesh)
    attribute_summaries = [
        {
            "name": attribute.name,
            "domain": str(attribute.domain),
            "data_type": str(attribute.data_type),
            "length": len(attribute.data),
            "protected": _is_protected_attribute(attribute),
            "supported": not _is_protected_attribute(attribute)
            or str(attribute.data_type) in SUPPORTED_ATTRIBUTE_TYPES,
        }
        for attribute in mesh.attributes
    ]
    result: dict[str, Any] = {
        "object": {
            "name": obj.name,
            "session_identity": session_identity("object", obj),
            "library": obj.library.filepath if obj.library else None,
        },
        "mesh": {
            "name": mesh.name,
            "session_identity": session_identity("mesh", mesh),
            "users": int(mesh.users),
            "library": mesh.library.filepath if mesh.library else None,
            "shape_keys": mesh.shape_keys is not None,
            "uv_layers": [layer.name for layer in mesh.uv_layers],
            "color_attributes": [attribute.name for attribute in mesh.color_attributes],
            "material_slots": [
                {
                    "slot_index": index,
                    "material_name": material.name if material is not None else None,
                    "material_identity": (
                        session_identity("material", material) if material is not None else None
                    ),
                }
                for index, material in enumerate(mesh.materials)
            ],
            "attributes": attribute_summaries,
        },
        "user_objects": [
            {"object_name": user.name, "session_identity": session_identity("object", user)}
            for user in users
        ],
        "counts": mesh_counts(mesh),
        "budget": _budget_details(mesh),
        "topology_fingerprint": topology_fingerprint(mesh),
        "mesh_fingerprint": mesh_fingerprint(mesh),
        "mesh_revision_id": mesh_revision_id(mesh),
        "component": component,
        "writable": not _writable_reasons(obj, mesh),
        "write_blockers": _writable_reasons(obj, mesh),
        "writable_domains": {
            "geometry": not _writable_reasons(obj, mesh),
            "uv": not _attribute_writable_reasons(obj, mesh),
            "weights": not _attribute_writable_reasons(obj, mesh),
        },
        "domain_write_blockers": {
            "geometry": _writable_reasons(obj, mesh),
            "uv": _attribute_writable_reasons(obj, mesh),
            "weights": _attribute_writable_reasons(obj, mesh),
        },
        "warnings": [],
    }
    if component == "summary":
        result["pagination"] = {
            "offset": 0,
            "limit": limit,
            "total": 0,
            "returned": 0,
            "truncated": False,
            "next_offset": None,
        }
        result["items"] = []
        return result
    collection = {
        "vertices": mesh.vertices,
        "edges": mesh.edges,
        "faces": mesh.polygons,
    }[component]
    total = len(collection)
    if offset > total:
        raise MeshOperationError(
            "MESH_PAGINATION_INVALID",
            f"offset {offset} exceeds {component} count {total}",
        )
    stop = min(total, offset + limit)
    edge_face_counts: list[int] | None = None
    if component == "edges":
        edge_face_counts = [0] * len(mesh.edges)
        for loop in mesh.loops:
            edge_face_counts[int(loop.edge_index)] += 1
    items = []
    for index in range(offset, stop):
        item = collection[index]
        if component == "vertices":
            items.append(
                {
                    "index": index,
                    "co": list(item.co),
                    "normal": list(item.normal),
                    "select": bool(item.select),
                    "hide": bool(item.hide),
                }
            )
        elif component == "edges":
            face_count = edge_face_counts[index] if edge_face_counts is not None else 0
            items.append(
                {
                    "index": index,
                    "vertices": list(item.vertices),
                    "is_boundary": face_count == 1,
                    "is_manifold": face_count == 2,
                    "sharp": bool(item.use_edge_sharp),
                    "select": bool(item.select),
                    "hide": bool(item.hide),
                }
            )
        else:
            edge_indices = [
                int(mesh.loops[loop_index].edge_index)
                for loop_index in range(item.loop_start, item.loop_start + item.loop_total)
            ]
            items.append(
                {
                    "index": index,
                    "vertices": list(item.vertices),
                    "edges": edge_indices,
                    "center": list(item.center),
                    "normal": list(item.normal),
                    "area": float(item.area),
                    "material_slot_index": int(item.material_index),
                    "smooth": bool(item.use_smooth),
                    "select": bool(item.select),
                    "hide": bool(item.hide),
                }
            )
    truncated = stop < total
    result["items"] = items
    result["pagination"] = {
        "offset": offset,
        "limit": limit,
        "total": total,
        "returned": len(items),
        "truncated": truncated,
        "next_offset": stop if truncated else None,
    }
    if truncated:
        result["warnings"].append({"code": "MESH_COMPONENTS_TRUNCATED", "next_offset": stop})
    return result


def _expected_user_refs(raw: Any) -> tuple[tuple[str, str], ...]:
    if not isinstance(raw, list) or not raw:
        raise MeshOperationError(
            "MESH_USER_SET_MISMATCH",
            "expected_mesh_user_objects must be a non-empty list",
        )
    refs = []
    for item in raw:
        if not isinstance(item, dict):
            raise MeshOperationError("MESH_USER_SET_MISMATCH", "Mesh user entries must be objects")
        name = item.get("object_name")
        identity = item.get("expected_object_identity")
        if not isinstance(name, str) or not name or not isinstance(identity, str) or not identity:
            raise MeshOperationError(
                "MESH_USER_SET_MISMATCH",
                "Mesh user entries require object_name and expected_object_identity",
            )
        refs.append((name, identity))
    result = tuple(sorted(refs))
    if len(set(result)) != len(result):
        raise MeshOperationError("MESH_USER_SET_MISMATCH", "Mesh user entries must be unique")
    return result


def _validate_mesh_target(
    params: dict[str, Any],
) -> tuple[Any, Any, str, tuple[tuple[str, str], ...]]:
    object_name = params.get("object_name")
    if not isinstance(object_name, str) or not object_name:
        raise MeshOperationError("OBJECT_NAME_INVALID", "object_name must be non-empty")
    obj, mesh = _mesh_object(object_name)
    if session_identity("object", obj) != params.get("expected_object_identity"):
        raise MeshOperationError(
            "OBJECT_IDENTITY_MISMATCH",
            f"Object identity changed: {object_name}",
            kind="conflict",
        )
    if session_identity("mesh", mesh) != params.get("expected_mesh_identity"):
        raise MeshOperationError(
            "MESH_IDENTITY_MISMATCH",
            f"Mesh identity changed: {mesh.name}",
            kind="conflict",
        )
    reasons = _writable_reasons(obj, mesh)
    if reasons:
        code = reasons[0]
        raise MeshOperationError(
            code, f"Mesh is not writable: {mesh.name}", details={"reasons": reasons}
        )
    expected_users = params.get("expected_mesh_users")
    if (
        isinstance(expected_users, bool)
        or not isinstance(expected_users, int)
        or expected_users < 1
    ):
        raise MeshOperationError("MESH_USER_SET_MISMATCH", "expected_mesh_users must be positive")
    actual_refs = mesh_user_refs(mesh)
    expected_refs = _expected_user_refs(params.get("expected_mesh_user_objects"))
    if int(mesh.users) != expected_users or actual_refs != expected_refs:
        raise MeshOperationError(
            "MESH_USER_SET_MISMATCH",
            f"Mesh users changed: {mesh.name}",
            kind="conflict",
            details={
                "expected_users": expected_users,
                "actual_users": int(mesh.users),
                "expected_user_objects": list(expected_refs),
                "actual_user_objects": list(actual_refs),
            },
        )
    fingerprint = mesh_fingerprint(mesh)
    if fingerprint != params.get("expected_mesh_fingerprint"):
        raise MeshOperationError(
            "MESH_FINGERPRINT_MISMATCH",
            f"Mesh fingerprint changed: {mesh.name}",
            kind="conflict",
            details={"expected": params.get("expected_mesh_fingerprint"), "actual": fingerprint},
        )
    data_scope = params.get("data_scope")
    if data_scope not in {"OBJECT", "SHARED_DATA"}:
        raise MeshOperationError(
            "MESH_OPERATION_INVALID", "data_scope must be OBJECT or SHARED_DATA"
        )
    return obj, mesh, data_scope, actual_refs


def validate_mesh_attribute_target(
    params: dict[str, Any],
) -> tuple[Any, Any, str, tuple[tuple[str, str], ...]]:
    """Validate exact Mesh evidence without rejecting topology-stable Shape Key writes."""

    object_name = params.get("object_name")
    if not isinstance(object_name, str) or not object_name:
        raise MeshOperationError("OBJECT_NAME_INVALID", "object_name must be non-empty")
    obj, mesh = _mesh_object(object_name)
    if session_identity("object", obj) != params.get("expected_object_identity"):
        raise MeshOperationError(
            "OBJECT_IDENTITY_MISMATCH", f"Object identity changed: {object_name}", kind="conflict"
        )
    if session_identity("mesh", mesh) != params.get("expected_mesh_identity"):
        raise MeshOperationError(
            "MESH_IDENTITY_MISMATCH", f"Mesh identity changed: {mesh.name}", kind="conflict"
        )
    reasons = _attribute_writable_reasons(obj, mesh)
    if reasons:
        raise MeshOperationError(
            reasons[0],
            f"Mesh attributes are not writable: {mesh.name}",
            details={"reasons": reasons},
        )
    expected_users = params.get("expected_mesh_users")
    if (
        isinstance(expected_users, bool)
        or not isinstance(expected_users, int)
        or expected_users < 1
    ):
        raise MeshOperationError("MESH_USER_SET_MISMATCH", "expected_mesh_users must be positive")
    actual_refs = mesh_user_refs(mesh)
    expected_refs = _expected_user_refs(params.get("expected_mesh_user_objects"))
    if int(mesh.users) != expected_users or actual_refs != expected_refs:
        raise MeshOperationError(
            "MESH_USER_SET_MISMATCH",
            f"Mesh users changed: {mesh.name}",
            kind="conflict",
            details={
                "expected_users": expected_users,
                "actual_users": int(mesh.users),
                "expected_user_objects": list(expected_refs),
                "actual_user_objects": list(actual_refs),
            },
        )
    fingerprint = mesh_fingerprint(mesh)
    if fingerprint != params.get("expected_mesh_fingerprint"):
        raise MeshOperationError(
            "MESH_FINGERPRINT_MISMATCH",
            f"Mesh fingerprint changed: {mesh.name}",
            kind="conflict",
            details={"expected": params.get("expected_mesh_fingerprint"), "actual": fingerprint},
        )
    data_scope = params.get("data_scope")
    if data_scope not in {"OBJECT", "SHARED_DATA"}:
        raise MeshOperationError(
            "MESH_OPERATION_INVALID", "data_scope must be OBJECT or SHARED_DATA"
        )
    return obj, mesh, data_scope, actual_refs


def _validate_guard(guard: MeshSnapshotGuard) -> Any:
    mesh = bpy.data.meshes.get(guard.mesh_name)
    if mesh is None or session_identity("mesh", mesh) != guard.mesh_identity:
        raise MeshOperationError(
            "MESH_DATA_CONFLICT",
            f"Guarded Mesh identity changed: {guard.mesh_name}",
            kind="conflict",
        )
    actual_refs = mesh_user_refs(mesh)
    actual_fingerprint = mesh_fingerprint(mesh)
    if (
        int(mesh.users) != guard.expected_users
        or actual_refs != guard.expected_user_objects
        or actual_fingerprint != guard.expected_fingerprint
    ):
        raise MeshOperationError(
            "MESH_DATA_CONFLICT",
            f"Mesh changed outside the transaction: {guard.mesh_name}",
            kind="conflict",
            details={
                "expected_users": guard.expected_users,
                "actual_users": int(mesh.users),
                "expected_user_objects": list(guard.expected_user_objects),
                "actual_user_objects": list(actual_refs),
                "expected_fingerprint": guard.expected_fingerprint,
                "actual_fingerprint": actual_fingerprint,
            },
        )
    if guard.source_mesh is not None:
        source = bpy.data.meshes.get(str(guard.source_mesh_name))
        if source is None or session_identity("mesh", source) != guard.source_mesh_identity:
            raise MeshOperationError(
                "MESH_DATA_CONFLICT",
                "The shared source Mesh identity changed",
                kind="conflict",
            )
        if (
            int(source.users) != guard.source_expected_users
            or mesh_user_refs(source) != guard.source_expected_user_objects
            or mesh_fingerprint(source) != guard.source_fingerprint
        ):
            raise MeshOperationError(
                "MESH_DATA_CONFLICT",
                "The shared source Mesh changed outside the transaction",
                kind="conflict",
            )
    return mesh


def validate_mesh_snapshot_guards(transaction: Transaction) -> None:
    for guard in transaction.mesh_snapshot_guards.values():
        _validate_guard(guard)


def _foreach_values(collection: Any, prop: str, typecode: str, width: int) -> array[Any]:
    values = array(typecode, [0]) * (len(collection) * width)
    if values:
        collection.foreach_get(prop, values)
    return values


def _remove_protected_attributes(mesh: Any) -> None:
    for layer in tuple(mesh.uv_layers):
        mesh.uv_layers.remove(layer)
    for color in tuple(mesh.color_attributes):
        mesh.color_attributes.remove(color)
    for attribute in tuple(mesh.attributes):
        if _is_protected_attribute(attribute):
            mesh.attributes.remove(attribute)


def _restore_attributes(mesh: Any, snapshot: Any) -> None:
    uv_names = {layer.name for layer in snapshot.uv_layers}
    color_names = {attribute.name for attribute in snapshot.color_attributes}
    attribute_specs = []
    for attribute in snapshot.attributes:
        if not _is_protected_attribute(attribute):
            continue
        data_type = str(attribute.data_type)
        layout = _ATTRIBUTE_LAYOUTS.get(data_type)
        if layout is None:
            raise MeshOperationError(
                "MESH_EDIT_RESTORE_FAILED",
                f"Snapshot contains an unsupported attribute: {attribute.name}:{data_type}",
                kind="internal",
            )
        prop, typecode, width = layout
        attribute_specs.append(
            {
                "name": attribute.name,
                "domain": str(attribute.domain),
                "data_type": data_type,
                "values": _foreach_values(attribute.data, prop, typecode, width),
                "layout": layout,
                "uv": attribute.name in uv_names,
                "color": attribute.name in color_names,
            }
        )

    _remove_protected_attributes(mesh)
    for spec in attribute_specs:
        if spec["uv"]:
            mesh.uv_layers.new(name=spec["name"], do_init=False)
            attribute = mesh.attributes.get(spec["name"])
        elif spec["color"]:
            attribute = mesh.color_attributes.new(
                name=spec["name"],
                type=spec["data_type"],
                domain=spec["domain"],
            )
        else:
            attribute = mesh.attributes.new(
                name=spec["name"],
                type=spec["data_type"],
                domain=spec["domain"],
            )
        if attribute is None:
            raise MeshOperationError(
                "MESH_EDIT_RESTORE_FAILED",
                f"Could not restore attribute: {spec['name']}",
                kind="blender_api",
            )
        prop, _typecode, _width = spec["layout"]
        if spec["values"]:
            attribute.data.foreach_set(prop, spec["values"])


def _restore_uv_layers(mesh: Any, snapshot: Any) -> None:
    """Restore UV schema, coordinates, pins, and roles without touching other attributes."""

    source_layers = tuple(snapshot.uv_layers)
    source_names = tuple(layer.name for layer in source_layers)
    target_names = tuple(layer.name for layer in mesh.uv_layers)
    if source_names != target_names:
        for layer in tuple(mesh.uv_layers):
            mesh.uv_layers.remove(layer)
        for source in source_layers:
            mesh.uv_layers.new(name=source.name, do_init=False)
    for index, source in enumerate(source_layers):
        target = mesh.uv_layers[index]
        for prop, typecode, width in (
            ("uv", "f", 2),
            ("pin_uv", "b", 1),
            ("select", "b", 1),
            ("select_edge", "b", 1),
        ):
            if len(source.data) and not hasattr(source.data[0], prop):
                continue
            values = _foreach_values(source.data, prop, typecode, width)
            if values:
                target.data.foreach_set(prop, values)
        if hasattr(target, "active_render"):
            target.active_render = bool(getattr(source, "active_render", False))
        if hasattr(target, "active_clone"):
            target.active_clone = bool(getattr(source, "active_clone", False))
    if source_layers:
        mesh.uv_layers.active_index = min(
            int(getattr(snapshot.uv_layers, "active_index", 0)), len(source_layers) - 1
        )


def _restore_seams(mesh: Any, snapshot: Any) -> None:
    values = _foreach_values(snapshot.edges, "use_seam", "b", 1)
    if values:
        mesh.edges.foreach_set("use_seam", values)


def _copy_mesh_snapshot(mesh: Any, snapshot: Any) -> None:
    vertices = {
        prop: _foreach_values(snapshot.vertices, prop, typecode, width)
        for prop, typecode, width in (
            ("co", "f", 3),
            ("select", "b", 1),
            ("hide", "b", 1),
        )
    }
    edges = {
        prop: _foreach_values(snapshot.edges, prop, typecode, width)
        for prop, typecode, width in (
            ("vertices", "i", 2),
            ("select", "b", 1),
            ("hide", "b", 1),
            ("use_edge_sharp", "b", 1),
        )
    }
    loops = _foreach_values(snapshot.loops, "vertex_index", "i", 1)
    polygons = {
        prop: _foreach_values(snapshot.polygons, prop, typecode, width)
        for prop, typecode, width in (
            ("loop_start", "i", 1),
            ("loop_total", "i", 1),
            ("material_index", "i", 1),
            ("use_smooth", "b", 1),
            ("select", "b", 1),
            ("hide", "b", 1),
        )
    }
    materials = tuple(snapshot.materials)

    if topology_fingerprint(mesh) == topology_fingerprint(snapshot):
        if vertices["co"]:
            mesh.vertices.foreach_set("co", vertices["co"])
        mesh.materials.clear()
        for material in materials:
            mesh.materials.append(material)
        _restore_uv_layers(mesh, snapshot)
        _restore_seams(mesh, snapshot)
        mesh.update()
        for prop in ("select", "hide"):
            mesh.vertices.foreach_set(prop, vertices[prop])
            mesh.edges.foreach_set(prop, edges[prop])
            mesh.polygons.foreach_set(prop, polygons[prop])
        mesh.edges.foreach_set("use_edge_sharp", edges["use_edge_sharp"])
        for prop in ("material_index", "use_smooth"):
            mesh.polygons.foreach_set(prop, polygons[prop])
        return

    _remove_protected_attributes(mesh)
    mesh.clear_geometry()
    mesh.vertices.add(len(snapshot.vertices))
    mesh.edges.add(len(snapshot.edges))
    mesh.loops.add(len(snapshot.loops))
    mesh.polygons.add(len(snapshot.polygons))
    mesh.vertices.foreach_set("co", vertices["co"])
    mesh.edges.foreach_set("vertices", edges["vertices"])
    mesh.loops.foreach_set("vertex_index", loops)
    mesh.polygons.foreach_set("loop_start", polygons["loop_start"])
    mesh.polygons.foreach_set("loop_total", polygons["loop_total"])
    mesh.update(calc_edges=True, calc_edges_loose=True)

    mesh.materials.clear()
    for material in materials:
        mesh.materials.append(material)
    _restore_attributes(mesh, snapshot)
    _restore_uv_layers(mesh, snapshot)
    _restore_seams(mesh, snapshot)
    mesh.update(calc_edges=True, calc_edges_loose=True)
    for prop in ("select", "hide"):
        mesh.vertices.foreach_set(prop, vertices[prop])
        mesh.edges.foreach_set(prop, edges[prop])
        mesh.polygons.foreach_set(prop, polygons[prop])
    mesh.edges.foreach_set("use_edge_sharp", edges["use_edge_sharp"])
    for prop in ("material_index", "use_smooth"):
        mesh.polygons.foreach_set(prop, polygons[prop])


def _restore_mesh_geometry(mesh: Any, snapshot: Any, expected: str) -> None:
    _copy_mesh_snapshot(mesh, snapshot)
    actual = mesh_fingerprint(mesh)
    if actual != expected:
        raise MeshOperationError(
            "MESH_EDIT_RESTORE_FAILED",
            f"Mesh snapshot verification failed: {mesh.name}",
            kind="blender_api",
            details={"expected": expected, "actual": actual},
        )


def restore_mesh_snapshots(transaction: Transaction) -> list[dict[str, Any]]:
    restored = []
    for guard in reversed(tuple(transaction.mesh_snapshot_guards.values())):
        mesh = _validate_guard(guard)
        if guard.source_mesh is not None:
            obj = bpy.data.objects.get(guard.object_name)
            if obj is None or session_identity("object", obj) != guard.object_identity:
                raise MeshOperationError(
                    "MESH_DATA_CONFLICT",
                    f"Target object identity changed: {guard.object_name}",
                    kind="conflict",
                )
            source = guard.source_mesh
            obj.data = source
            working_name = mesh.name
            if int(mesh.users) == 0:
                bpy.data.meshes.remove(mesh)
            restored.append(
                {
                    "kind": "mesh_edit",
                    "action": "restore_shared_link",
                    "object_name": obj.name,
                    "removed_mesh": working_name,
                    "mesh_identity": session_identity("mesh", source),
                }
            )
            continue
        snapshot = guard.snapshot
        if snapshot is None:
            raise MeshOperationError(
                "MESH_EDIT_RESTORE_FAILED",
                f"Mesh snapshot is missing: {guard.mesh_name}",
                kind="internal",
            )
        _restore_mesh_geometry(mesh, snapshot, guard.baseline_fingerprint)
        snapshot_name = snapshot.name
        bpy.data.meshes.remove(snapshot)
        restored.append(
            {
                "kind": "mesh_edit",
                "action": "restore_snapshot",
                "mesh_name": mesh.name,
                "mesh_identity": session_identity("mesh", mesh),
                "removed_snapshot": snapshot_name,
            }
        )
    return restored


def finalize_mesh_snapshots(transaction: Transaction) -> list[dict[str, Any]]:
    finalized = []
    for guard in transaction.mesh_snapshot_guards.values():
        _validate_guard(guard)
        if guard.snapshot is not None:
            snapshot_name = guard.snapshot.name
            bpy.data.meshes.remove(guard.snapshot)
            finalized.append(
                {"kind": "mesh_edit", "action": "discard_snapshot", "snapshot": snapshot_name}
            )
        elif guard.source_mesh is not None:
            finalized.append(
                {
                    "kind": "mesh_edit",
                    "action": "commit_single_user",
                    "object_name": guard.object_name,
                    "mesh_name": guard.mesh_name,
                }
            )
    return finalized


def adopt_mesh_snapshots_for_native_save(transaction: Transaction) -> list[dict[str, Any]]:
    """Discard only private unused snapshots without validating the user's live Mesh."""

    adopted = []
    for guard in transaction.mesh_snapshot_guards.values():
        snapshot = guard.snapshot
        if snapshot is not None:
            existing = bpy.data.meshes.get(str(snapshot.name))
            if existing is snapshot and int(snapshot.users) == 0:
                snapshot_name = str(snapshot.name)
                bpy.data.meshes.remove(snapshot)
                adopted.append(
                    {
                        "kind": "mesh_edit",
                        "action": "discard_snapshot_native_save",
                        "snapshot": snapshot_name,
                    }
                )
            else:
                adopted.append(
                    {
                        "kind": "mesh_edit",
                        "action": "preserved_user_snapshot",
                        "snapshot": str(getattr(snapshot, "name", guard.mesh_name)),
                    }
                )
        elif guard.source_mesh is not None:
            adopted.append(
                {
                    "kind": "mesh_edit",
                    "action": "commit_single_user_native_save",
                    "object_name": guard.object_name,
                    "mesh_name": guard.mesh_name,
                }
            )
    return adopted


def _remove_new_guard(transaction: Transaction, guard: MeshSnapshotGuard) -> None:
    mesh = bpy.data.meshes.get(guard.mesh_name)
    if guard.source_mesh is not None:
        obj = bpy.data.objects.get(guard.object_name)
        if obj is not None and mesh is not None and obj.data is mesh:
            obj.data = guard.source_mesh
        if mesh is not None and int(mesh.users) == 0:
            bpy.data.meshes.remove(mesh)
    elif guard.snapshot is not None:
        bpy.data.meshes.remove(guard.snapshot)
    transaction.remove_mesh_snapshot_guard(guard)


def _create_guard(
    transaction: Transaction,
    obj: Any,
    mesh: Any,
    data_scope: str,
) -> MeshSnapshotGuard:
    for existing in transaction.mesh_snapshot_guards.values():
        if existing.source_mesh is mesh:
            raise MeshOperationError(
                "MESH_DATA_CONFLICT",
                "A shared source Mesh cannot also be edited in the same transaction",
                kind="conflict",
            )
    baseline = mesh_fingerprint(mesh)
    if data_scope == "OBJECT" and int(mesh.users) > 1:
        source = mesh
        working = mesh.copy()
        working.name = f"{source.name}.MCP"
        obj.data = working
        guard = MeshSnapshotGuard(
            object_name=obj.name,
            object_identity=session_identity("object", obj),
            mesh_name=working.name,
            mesh_identity=session_identity("mesh", working),
            baseline_fingerprint=baseline,
            expected_fingerprint=mesh_fingerprint(working),
            expected_users=int(working.users),
            expected_user_objects=mesh_user_refs(working),
            data_scope=data_scope,
            source_mesh=source,
            source_mesh_name=source.name,
            source_mesh_identity=session_identity("mesh", source),
            source_fingerprint=baseline,
            source_expected_users=int(source.users),
            source_expected_user_objects=mesh_user_refs(source),
        )
        refresh_structure_guard_if_present(transaction, "object", obj)
        refresh_structure_guard_if_present(transaction, "mesh", source)
    else:
        snapshot = mesh.copy()
        snapshot.name = f"{mesh.name}.MCP-Snapshot"
        guard = MeshSnapshotGuard(
            object_name=obj.name,
            object_identity=session_identity("object", obj),
            mesh_name=mesh.name,
            mesh_identity=session_identity("mesh", mesh),
            baseline_fingerprint=baseline,
            expected_fingerprint=baseline,
            expected_users=int(mesh.users),
            expected_user_objects=mesh_user_refs(mesh),
            data_scope=data_scope,
            snapshot=snapshot,
        )
    transaction.add_mesh_snapshot_guard(guard)
    return guard


def _indices(raw: Any, field: str) -> list[int]:
    if not isinstance(raw, list) or not raw or len(raw) > MAX_COMPONENT_TARGETS:
        raise MeshOperationError(
            "MESH_COMPONENT_INDEX_INVALID",
            f"{field} must contain 1-{MAX_COMPONENT_TARGETS} indices",
        )
    if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in raw):
        raise MeshOperationError(
            "MESH_COMPONENT_INDEX_INVALID", f"{field} contains invalid indices"
        )
    if len(set(raw)) != len(raw):
        raise MeshOperationError("MESH_COMPONENT_INDEX_INVALID", f"{field} must be unique")
    return raw


def _elements(sequence: Any, indices: list[int], field: str) -> list[Any]:
    sequence.ensure_lookup_table()
    if any(index >= len(sequence) for index in indices):
        raise MeshOperationError(
            "MESH_COMPONENT_INDEX_INVALID",
            f"{field} contains an index outside the current Mesh",
        )
    return [sequence[index] for index in indices]


def _bmesh_baseline(bm: Any) -> dict[str, list[Any]]:
    bm.verts.ensure_lookup_table()
    bm.edges.ensure_lookup_table()
    bm.faces.ensure_lookup_table()
    return {
        "vertices": list(bm.verts),
        "edges": list(bm.edges),
        "faces": list(bm.faces),
    }


def _index_page(indices: list[int]) -> dict[str, Any]:
    ordered = sorted(indices)
    return {
        "indices": ordered[:MAX_COMPONENT_TARGETS],
        "count": len(ordered),
        "truncated": len(ordered) > MAX_COMPONENT_TARGETS,
    }


def _component_changes(bm: Any, baseline: dict[str, list[Any]]) -> dict[str, Any]:
    bm.verts.index_update()
    bm.edges.index_update()
    bm.faces.index_update()
    current = {
        "vertices": list(bm.verts),
        "edges": list(bm.edges),
        "faces": list(bm.faces),
    }
    created = {}
    deleted = {}
    for kind in ("vertices", "edges", "faces"):
        before_items = baseline[kind]
        before_set = set(before_items)
        created[kind] = _index_page(
            [int(item.index) for item in current[kind] if item not in before_set]
        )
        deleted[kind] = _index_page(
            [index for index, item in enumerate(before_items) if not item.is_valid]
        )
    return {"created": created, "deleted": deleted}


def _requested_components(
    operation: dict[str, Any], baseline: dict[str, list[Any]]
) -> dict[str, Any]:
    operation_type = operation["type"]
    if operation_type == "transform":
        target = operation["target"]
        target_type = str(target["type"])
        indices = list(target["indices"])
        result = {target_type: _index_page(indices)}
        if target_type == "vertices":
            expanded = indices
        elif target_type == "edges":
            expanded = sorted(
                {
                    int(vertex.index)
                    for index in indices
                    for vertex in baseline["edges"][index].verts
                }
            )
        else:
            expanded = sorted(
                {
                    int(vertex.index)
                    for index in indices
                    for vertex in baseline["faces"][index].verts
                }
            )
        result["expanded_vertices"] = _index_page(expanded)
        return result
    if operation_type in {"delete", "dissolve"}:
        target = operation["target"]
        return {str(target["type"]): _index_page(list(target["indices"]))}
    if operation_type in {"extrude_faces", "inset_faces", "face_settings"}:
        return {"faces": _index_page(list(operation["face_indices"]))}
    if operation_type == "bevel_edges":
        return {"edges": _index_page(list(operation["edge_indices"]))}
    if operation_type == "merge_vertices":
        return {"vertices": _index_page(list(operation["vertex_indices"]))}
    if operation_type == "normals" and operation["mode"] == "FLIP":
        return {"faces": _index_page(list(operation["face_indices"]))}
    if operation_type == "normals":
        return {
            "faces": _index_page(list(range(len(baseline["faces"])))),
            "all": True,
        }
    return {}


def _component_warnings(components: dict[str, Any]) -> list[dict[str, Any]]:
    warnings = []
    for section in ("created", "deleted", "affected"):
        for kind, page in components[section].items():
            if isinstance(page, dict) and page.get("truncated") is True:
                warnings.append(
                    {
                        "code": "MESH_COMPONENT_INDICES_TRUNCATED",
                        "section": section,
                        "component": kind,
                        "count": page["count"],
                    }
                )
    return warnings


def _target_geometry(bm: Any, target: dict[str, Any]) -> tuple[list[Any], list[Any]]:
    target_type = target.get("type")
    indices = _indices(target.get("indices"), "target.indices")
    if target_type == "vertices":
        geom = _elements(bm.verts, indices, "target.indices")
        verts = geom
    elif target_type == "edges":
        geom = _elements(bm.edges, indices, "target.indices")
        verts = list({vert for edge in geom for vert in edge.verts})
    elif target_type == "faces":
        geom = _elements(bm.faces, indices, "target.indices")
        verts = list({vert for face in geom for vert in face.verts})
    else:
        raise MeshOperationError(
            "MESH_OPERATION_INVALID", f"Unsupported target type: {target_type}"
        )
    return geom, verts


def _vector(raw: Any, field: str) -> Vector:
    if not isinstance(raw, dict) or set(raw) != {"x", "y", "z"}:
        raise MeshOperationError("MESH_OPERATION_INVALID", f"{field} must contain x, y, and z")
    values = [raw[axis] for axis in ("x", "y", "z")]
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in values):
        raise MeshOperationError("MESH_OPERATION_INVALID", f"{field} components must be numbers")
    result = Vector(tuple(float(value) for value in values))
    if not all(math.isfinite(value) for value in result):
        raise MeshOperationError("MESH_OPERATION_INVALID", f"{field} components must be finite")
    return result


def _closed_payload(
    value: Any,
    field: str,
    *,
    required: set[str],
    optional: set[str] = frozenset(),
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise MeshOperationError("MESH_OPERATION_INVALID", f"{field} must be an object")
    keys = set(value)
    missing = required - keys
    extra = keys - required - optional
    if missing or extra:
        raise MeshOperationError(
            "MESH_OPERATION_INVALID",
            f"{field} has invalid fields",
            details={"missing": sorted(missing), "extra": sorted(extra)},
        )
    return value


def _number(
    value: Any,
    field: str,
    *,
    minimum: float,
    maximum: float,
    minimum_exclusive: bool = False,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MeshOperationError("MESH_OPERATION_INVALID", f"{field} must be a JSON number")
    result = float(value)
    valid_minimum = result > minimum if minimum_exclusive else result >= minimum
    if not math.isfinite(result) or not valid_minimum or result > maximum:
        boundary = ">" if minimum_exclusive else ">="
        raise MeshOperationError(
            "MESH_OPERATION_INVALID",
            f"{field} must be finite and {boundary} {minimum} and <= {maximum}",
        )
    return result


def _strict_bool(value: Any, field: str) -> bool:
    if type(value) is not bool:
        raise MeshOperationError("MESH_OPERATION_INVALID", f"{field} must be a boolean")
    return value


def _validate_target_payload(value: Any, field: str) -> dict[str, Any]:
    target = _closed_payload(value, field, required={"type", "indices"})
    if target["type"] not in {"vertices", "edges", "faces"}:
        raise MeshOperationError(
            "MESH_OPERATION_INVALID", f"{field}.type must be vertices, edges, or faces"
        )
    _indices(target["indices"], f"{field}.indices")
    return target


def _validate_vector_range(
    value: Any,
    field: str,
    *,
    minimum: float,
    maximum: float,
) -> Vector:
    result = _vector(value, field)
    if any(component < minimum or component > maximum for component in result):
        raise MeshOperationError(
            "MESH_OPERATION_INVALID",
            f"{field} components must be between {minimum} and {maximum}",
        )
    return result


def _validate_operation(operation: Any) -> dict[str, Any]:
    if not isinstance(operation, dict):
        raise MeshOperationError("MESH_OPERATION_INVALID", "operation must be an object")
    operation_type = operation.get("type")
    if operation_type == "transform":
        payload = _closed_payload(
            operation,
            "operation",
            required={"type", "target"},
            optional={"translation", "rotation_euler_degrees", "scale", "pivot"},
        )
        _validate_target_payload(payload["target"], "operation.target")
        patches = [
            field
            for field in ("translation", "rotation_euler_degrees", "scale")
            if field in payload
        ]
        if not patches:
            raise MeshOperationError(
                "MESH_OPERATION_INVALID", "transform requires at least one transform field"
            )
        if "translation" in payload:
            _validate_vector_range(
                payload["translation"], "translation", minimum=-1_000_000, maximum=1_000_000
            )
        if "rotation_euler_degrees" in payload:
            _validate_vector_range(
                payload["rotation_euler_degrees"],
                "rotation_euler_degrees",
                minimum=-360_000,
                maximum=360_000,
            )
        if "scale" in payload:
            _validate_vector_range(payload["scale"], "scale", minimum=-1000, maximum=1000)
        pivot = payload.get("pivot", {"type": "MEDIAN"})
        if not isinstance(pivot, dict) or pivot.get("type") not in {"MEDIAN", "POINT"}:
            raise MeshOperationError("MESH_OPERATION_INVALID", "pivot must be MEDIAN or POINT")
        if pivot["type"] == "MEDIAN":
            _closed_payload(pivot, "pivot", required={"type"})
        else:
            _closed_payload(pivot, "pivot", required={"type", "value"})
            _validate_vector_range(
                pivot["value"], "pivot.value", minimum=-1_000_000, maximum=1_000_000
            )
        return payload
    if operation_type == "extrude_faces":
        payload = _closed_payload(
            operation,
            "operation",
            required={"type", "face_indices", "offset"},
        )
        _indices(payload["face_indices"], "face_indices")
        offset = _validate_vector_range(
            payload["offset"], "offset", minimum=-1_000_000, maximum=1_000_000
        )
        if not any(offset):
            raise MeshOperationError("MESH_OPERATION_INVALID", "extrude offset must be non-zero")
        return payload
    if operation_type == "inset_faces":
        payload = _closed_payload(
            operation,
            "operation",
            required={"type", "face_indices", "thickness"},
            optional={"depth", "individual", "even_offset"},
        )
        _indices(payload["face_indices"], "face_indices")
        thickness = _number(payload["thickness"], "thickness", minimum=0, maximum=100_000)
        depth = _number(payload.get("depth", 0), "depth", minimum=-100_000, maximum=100_000)
        if thickness == 0 and depth == 0:
            raise MeshOperationError(
                "MESH_OPERATION_INVALID", "inset thickness and depth cannot both be zero"
            )
        _strict_bool(payload.get("individual", False), "individual")
        _strict_bool(payload.get("even_offset", True), "even_offset")
        return payload
    if operation_type == "bevel_edges":
        payload = _closed_payload(
            operation,
            "operation",
            required={"type", "edge_indices", "width"},
            optional={"segments", "profile", "clamp_overlap"},
        )
        _indices(payload["edge_indices"], "edge_indices")
        _number(payload["width"], "width", minimum=0, maximum=100_000, minimum_exclusive=True)
        segments = payload.get("segments", 1)
        if isinstance(segments, bool) or not isinstance(segments, int) or not 1 <= segments <= 32:
            raise MeshOperationError(
                "MESH_OPERATION_INVALID", "segments must be an integer between 1 and 32"
            )
        _number(payload.get("profile", 0.5), "profile", minimum=0, maximum=1)
        _strict_bool(payload.get("clamp_overlap", True), "clamp_overlap")
        return payload
    if operation_type == "delete":
        payload = _closed_payload(operation, "operation", required={"type", "target"})
        _validate_target_payload(payload["target"], "operation.target")
        return payload
    if operation_type == "dissolve":
        payload = _closed_payload(
            operation,
            "operation",
            required={"type", "target"},
            optional={"use_face_split", "use_boundary_tear", "use_verts"},
        )
        target = _validate_target_payload(payload["target"], "operation.target")
        flags = {
            name: _strict_bool(payload.get(name, False), name)
            for name in ("use_face_split", "use_boundary_tear", "use_verts")
        }
        invalid_true = {
            "vertices": {"use_verts"},
            "edges": {"use_boundary_tear"},
            "faces": {"use_face_split", "use_boundary_tear"},
        }[target["type"]]
        if any(flags[name] for name in invalid_true):
            raise MeshOperationError(
                "MESH_OPERATION_INVALID",
                f"Dissolve options do not match target type {target['type']}",
            )
        return payload
    if operation_type == "merge_vertices":
        payload = _closed_payload(
            operation,
            "operation",
            required={"type", "vertex_indices"},
            optional={"destination", "target_index"},
        )
        indices = _indices(payload["vertex_indices"], "vertex_indices")
        if len(indices) < 2:
            raise MeshOperationError(
                "MESH_OPERATION_INVALID", "merge_vertices requires at least two vertices"
            )
        destination = payload.get("destination", "CENTER")
        target_index = payload.get("target_index")
        if destination not in {"CENTER", "TARGET"}:
            raise MeshOperationError(
                "MESH_OPERATION_INVALID", "destination must be CENTER or TARGET"
            )
        if destination == "TARGET":
            if (
                isinstance(target_index, bool)
                or not isinstance(target_index, int)
                or target_index not in indices
            ):
                raise MeshOperationError(
                    "MESH_OPERATION_INVALID", "TARGET requires target_index in vertex_indices"
                )
        elif target_index is not None:
            raise MeshOperationError(
                "MESH_OPERATION_INVALID", "target_index is only valid for TARGET"
            )
        return payload
    if operation_type == "face_settings":
        payload = _closed_payload(
            operation,
            "operation",
            required={"type", "face_indices"},
            optional={"material_slot_index", "smooth"},
        )
        _indices(payload["face_indices"], "face_indices")
        if "material_slot_index" not in payload and "smooth" not in payload:
            raise MeshOperationError(
                "MESH_OPERATION_INVALID", "face_settings requires material and/or smooth"
            )
        material = payload.get("material_slot_index")
        if material is not None and (
            isinstance(material, bool) or not isinstance(material, int) or not 0 <= material <= 63
        ):
            raise MeshOperationError(
                "MESH_OPERATION_INVALID", "material_slot_index must be between 0 and 63"
            )
        if "smooth" in payload:
            _strict_bool(payload["smooth"], "smooth")
        return payload
    if operation_type == "normals":
        payload = _closed_payload(
            operation,
            "operation",
            required={"type", "mode"},
            optional={"face_indices"},
        )
        mode = payload["mode"]
        if mode == "FLIP":
            if "face_indices" not in payload:
                raise MeshOperationError("MESH_OPERATION_INVALID", "FLIP requires face_indices")
            _indices(payload["face_indices"], "face_indices")
        elif mode == "RECALCULATE_OUTSIDE":
            if "face_indices" in payload:
                raise MeshOperationError(
                    "MESH_OPERATION_INVALID", "RECALCULATE_OUTSIDE targets the complete Mesh"
                )
        else:
            raise MeshOperationError("MESH_OPERATION_INVALID", f"Unsupported normals mode: {mode}")
        return payload
    raise MeshOperationError("MESH_OPERATION_INVALID", f"Unsupported operation: {operation_type}")


def _operation_transform(bm: Any, operation: dict[str, Any]) -> dict[str, Any]:
    _geom, verts = _target_geometry(bm, operation["target"])
    pivot_spec = operation.get("pivot", {"type": "MEDIAN"})
    if pivot_spec.get("type") == "POINT":
        pivot = _vector(pivot_spec.get("value"), "pivot.value")
    elif pivot_spec.get("type") == "MEDIAN":
        pivot = sum((vert.co for vert in verts), Vector()) / len(verts)
    else:
        raise MeshOperationError("MESH_OPERATION_INVALID", "Unsupported pivot type")
    scale = operation.get("scale")
    rotation = operation.get("rotation_euler_degrees")
    translation = operation.get("translation")
    if scale is not None:
        value = _vector(scale, "scale")
        for vert in verts:
            relative = vert.co - pivot
            vert.co = pivot + Vector(
                (relative.x * value.x, relative.y * value.y, relative.z * value.z)
            )
    if rotation is not None:
        value = _vector(rotation, "rotation_euler_degrees")
        matrix = Euler(tuple(math.radians(component) for component in value), "XYZ").to_matrix()
        for vert in verts:
            vert.co = pivot + matrix @ (vert.co - pivot)
    if translation is not None:
        value = _vector(translation, "translation")
        for vert in verts:
            vert.co += value
    return {"affected_vertices": len(verts)}


def _operation_extrude(bm: Any, operation: dict[str, Any]) -> dict[str, Any]:
    faces = _elements(
        bm.faces, _indices(operation.get("face_indices"), "face_indices"), "face_indices"
    )
    before_verts = set(bm.verts)
    before_edges = set(bm.edges)
    before_faces = set(bm.faces)
    result = bmesh.ops.extrude_face_region(bm, geom=faces)
    new_verts = [
        item
        for item in result["geom"]
        if isinstance(item, bmesh.types.BMVert) and item not in before_verts
    ]
    offset = _vector(operation.get("offset"), "offset")
    bmesh.ops.translate(bm, verts=new_verts, vec=offset)
    return {
        "created_vertices": len(set(bm.verts) - before_verts),
        "created_edges": len(set(bm.edges) - before_edges),
        "created_faces": len(set(bm.faces) - before_faces),
    }


def _operation_inset(bm: Any, operation: dict[str, Any]) -> dict[str, Any]:
    faces = _elements(
        bm.faces, _indices(operation.get("face_indices"), "face_indices"), "face_indices"
    )
    before = (len(bm.verts), len(bm.edges), len(bm.faces))
    kwargs = {
        "faces": faces,
        "thickness": float(operation["thickness"]),
        "depth": float(operation.get("depth", 0.0)),
        "use_even_offset": bool(operation.get("even_offset", True)),
    }
    if operation.get("individual") is True:
        bmesh.ops.inset_individual(bm, **kwargs)
    else:
        bmesh.ops.inset_region(
            bm,
            **kwargs,
            use_boundary=True,
            use_interpolate=True,
            use_relative_offset=False,
            use_edge_rail=False,
            use_outset=False,
        )
    return {
        "created_vertices": len(bm.verts) - before[0],
        "created_edges": len(bm.edges) - before[1],
        "created_faces": len(bm.faces) - before[2],
    }


def _operation_bevel(bm: Any, operation: dict[str, Any]) -> dict[str, Any]:
    edges = _elements(
        bm.edges, _indices(operation.get("edge_indices"), "edge_indices"), "edge_indices"
    )
    before = (len(bm.verts), len(bm.edges), len(bm.faces))
    bmesh.ops.bevel(
        bm,
        geom=edges,
        offset=float(operation["width"]),
        offset_type="OFFSET",
        segments=int(operation.get("segments", 1)),
        profile=float(operation.get("profile", 0.5)),
        affect="EDGES",
        clamp_overlap=bool(operation.get("clamp_overlap", True)),
    )
    return {
        "created_vertices": len(bm.verts) - before[0],
        "created_edges": len(bm.edges) - before[1],
        "created_faces": len(bm.faces) - before[2],
    }


def _operation_delete(bm: Any, operation: dict[str, Any]) -> dict[str, Any]:
    geom, _verts = _target_geometry(bm, operation["target"])
    target_type = operation["target"]["type"]
    context = {"vertices": "VERTS", "edges": "EDGES_FACES", "faces": "FACES_ONLY"}[target_type]
    before = (len(bm.verts), len(bm.edges), len(bm.faces))
    bmesh.ops.delete(bm, geom=geom, context=context)
    return {
        "deleted_vertices": before[0] - len(bm.verts),
        "deleted_edges": before[1] - len(bm.edges),
        "deleted_faces": before[2] - len(bm.faces),
    }


def _operation_dissolve(bm: Any, operation: dict[str, Any]) -> dict[str, Any]:
    geom, _verts = _target_geometry(bm, operation["target"])
    target_type = operation["target"]["type"]
    before = (len(bm.verts), len(bm.edges), len(bm.faces))
    if target_type == "vertices":
        bmesh.ops.dissolve_verts(
            bm,
            verts=geom,
            use_face_split=bool(operation.get("use_face_split", False)),
            use_boundary_tear=bool(operation.get("use_boundary_tear", False)),
        )
    elif target_type == "edges":
        bmesh.ops.dissolve_edges(
            bm,
            edges=geom,
            use_verts=bool(operation.get("use_verts", False)),
            use_face_split=bool(operation.get("use_face_split", False)),
        )
    else:
        bmesh.ops.dissolve_faces(bm, faces=geom, use_verts=bool(operation.get("use_verts", False)))
    return {
        "deleted_vertices": before[0] - len(bm.verts),
        "deleted_edges": before[1] - len(bm.edges),
        "deleted_faces": before[2] - len(bm.faces),
    }


def _operation_merge(bm: Any, operation: dict[str, Any]) -> dict[str, Any]:
    indices = _indices(operation.get("vertex_indices"), "vertex_indices")
    if len(indices) < 2:
        raise MeshOperationError(
            "MESH_OPERATION_INVALID", "merge_vertices requires at least two vertices"
        )
    verts = _elements(bm.verts, indices, "vertex_indices")
    if operation.get("destination", "CENTER") == "TARGET":
        target_index = operation.get("target_index")
        if target_index not in indices:
            raise MeshOperationError(
                "MESH_OPERATION_INVALID", "target_index must be in vertex_indices"
            )
        target = bm.verts[target_index]
        target_source_index = int(target_index)
        merge_co = target.co.copy()
    else:
        merge_co = sum((vert.co for vert in verts), Vector()) / len(verts)
        target = verts[0]
        target_source_index = int(indices[0])
    before = (len(bm.verts), len(bm.edges), len(bm.faces))
    target.co = merge_co
    bmesh.ops.weld_verts(
        bm,
        targetmap={vertex: target for vertex in verts if vertex is not target},
    )
    return {
        "deleted_vertices": before[0] - len(bm.verts),
        "deleted_edges": before[1] - len(bm.edges),
        "deleted_faces": before[2] - len(bm.faces),
        "_merged_target_source": target_source_index,
    }


def _operation_face_settings(
    bm: Any, operation: dict[str, Any], material_count: int
) -> dict[str, Any]:
    faces = _elements(
        bm.faces, _indices(operation.get("face_indices"), "face_indices"), "face_indices"
    )
    material = operation.get("material_slot_index")
    smooth = operation.get("smooth")
    if material is not None and not 0 <= int(material) < material_count:
        raise MeshOperationError(
            "MESH_OPERATION_INVALID",
            f"material_slot_index must be less than the Mesh material count {material_count}",
        )
    changed = 0
    for face in faces:
        if material is not None and face.material_index != int(material):
            face.material_index = int(material)
            changed += 1
        if smooth is not None and face.smooth != bool(smooth):
            face.smooth = bool(smooth)
            changed += 1
    return {"affected_faces": len(faces), "changed_fields": changed}


def _operation_normals(bm: Any, operation: dict[str, Any]) -> dict[str, Any]:
    mode = operation.get("mode")
    if mode == "FLIP":
        faces = _elements(
            bm.faces, _indices(operation.get("face_indices"), "face_indices"), "face_indices"
        )
        bmesh.ops.reverse_faces(bm, faces=faces)
    elif mode == "RECALCULATE_OUTSIDE":
        faces = list(bm.faces)
        bmesh.ops.recalc_face_normals(bm, faces=faces)
    else:
        raise MeshOperationError("MESH_OPERATION_INVALID", f"Unsupported normals mode: {mode}")
    return {"affected_faces": len(faces)}


_OPERATION_HANDLERS = {
    "transform": _operation_transform,
    "extrude_faces": _operation_extrude,
    "inset_faces": _operation_inset,
    "bevel_edges": _operation_bevel,
    "delete": _operation_delete,
    "dissolve": _operation_dissolve,
    "merge_vertices": _operation_merge,
    "normals": _operation_normals,
}


def _identity_transform(operation: dict[str, Any]) -> bool:
    def matches(vector: Any, expected: float, *, rotation: bool = False) -> bool:
        if vector is None:
            return True
        values = (float(vector[axis]) for axis in ("x", "y", "z"))
        if rotation:
            return all(math.fmod(value, 360.0) == 0.0 for value in values)
        return all(value == expected for value in values)

    return (
        operation.get("type") == "transform"
        and matches(operation.get("translation"), 0.0)
        and matches(operation.get("rotation_euler_degrees"), 0.0, rotation=True)
        and matches(operation.get("scale"), 1.0)
    )


def _identity_transform_result(
    transaction: Transaction,
    obj: Any,
    mesh: Any,
    initial_mesh_reference: dict[str, Any],
    data_scope: str,
    operation: dict[str, Any],
) -> dict[str, Any]:
    fingerprint = mesh_fingerprint(mesh)
    topology = topology_fingerprint(mesh)
    counts = mesh_counts(mesh)
    bm = bmesh.new()
    try:
        bm.from_mesh(mesh)
        component_baseline = _bmesh_baseline(bm)
        requested = _requested_components(operation, component_baseline)
        evidence = _operation_transform(bm, operation)
        components = _component_changes(bm, component_baseline)
        components["affected"] = requested
    finally:
        bm.free()
    return {
        "transaction_id": transaction.transaction_id,
        "changed": False,
        "operation": "transform",
        "data_scope": data_scope,
        "object": {
            "name": obj.name,
            "session_identity": session_identity("object", obj),
        },
        "mesh": _mesh_reference(mesh),
        "before_mesh": initial_mesh_reference,
        "after_mesh": _mesh_reference(mesh),
        "before_mesh_fingerprint": fingerprint,
        "after_mesh_fingerprint": fingerprint,
        "before_topology_fingerprint": topology,
        "after_topology_fingerprint": topology,
        "before_counts": counts,
        "after_counts": counts,
        "components": components,
        "evidence": evidence,
        "delta": {"type": "mesh_edit", "recorded": False, "snapshot_reused": False},
        "warnings": _component_warnings(components),
    }


def _remove_temporary_mesh(mesh: Any) -> None:
    if mesh is not None and bpy.data.meshes.get(mesh.name) is mesh and int(mesh.users) == 0:
        bpy.data.meshes.remove(mesh)


def _restore_failed_edit(
    mesh: Any,
    call_snapshot: Any,
    before_fingerprint: str,
    failure: Exception,
) -> None:
    try:
        if mesh_fingerprint(mesh) != before_fingerprint:
            _restore_mesh_geometry(mesh, call_snapshot, before_fingerprint)
    except Exception as restore_error:
        raise MeshOperationError(
            "MESH_EDIT_RESTORE_FAILED",
            f"Mesh edit failed and the call state could not be restored: {mesh.name}",
            kind="blender_api",
            details={
                "failure_type": type(failure).__name__,
                "failure": str(failure),
                "restore_type": type(restore_error).__name__,
                "restore": str(restore_error),
            },
        ) from restore_error


def edit_mesh(
    transaction: Transaction,
    params: dict[str, Any],
    resources: Any | None = None,
) -> dict[str, Any]:
    from .mesh_topology_ops import (
        _created_selections,
        _finish_lineage,
        _map_evidence,
        _start_lineage,
    )

    obj, initial_mesh, data_scope, _refs = _validate_mesh_target(params)
    initial_mesh_reference = _mesh_reference(initial_mesh)
    before_map_evidence = _map_evidence(obj, initial_mesh)
    operation = _validate_operation(params.get("operation"))
    operation_type = operation.get("type")
    if _identity_transform(operation):
        return _identity_transform_result(
            transaction,
            obj,
            initial_mesh,
            initial_mesh_reference,
            data_scope,
            operation,
        )
    transaction.ensure_capacity()
    guard = transaction.mesh_snapshot_guard(
        initial_mesh.name, session_identity("mesh", initial_mesh)
    )
    new_guard = guard is None
    if guard is None:
        guard = _create_guard(transaction, obj, initial_mesh, data_scope)
    else:
        _validate_guard(guard)
        if guard.data_scope != data_scope:
            raise MeshOperationError(
                "MESH_OPERATION_INVALID",
                "data_scope must remain stable for repeated edits of the same Mesh",
            )
    mesh = bpy.data.meshes.get(guard.mesh_name)
    if mesh is None:
        raise MeshOperationError(
            "MESH_DATA_CONFLICT", "Guarded Mesh no longer exists", kind="conflict"
        )
    before_fingerprint = mesh_fingerprint(mesh)
    before_topology = topology_fingerprint(mesh)
    before_counts = mesh_counts(mesh)
    call_snapshot = mesh.copy()
    call_snapshot.name = f"{mesh.name}.MCP-Call-Snapshot"
    bm = bmesh.new()
    lineage = None
    component_map = None
    created_selections: dict[str, dict[str, Any]] = {}
    created_selection_ids: list[str] = []
    merged_target_source = None
    try:
        bm.from_mesh(mesh)
        component_baseline = _bmesh_baseline(bm)
        requested = _requested_components(operation, component_baseline)
        if resources is not None and operation_type in {
            "extrude_faces",
            "inset_faces",
            "bevel_edges",
            "delete",
            "dissolve",
            "merge_vertices",
        }:
            lineage = _start_lineage(bm)
        if operation_type == "face_settings":
            evidence = _operation_face_settings(bm, operation, len(mesh.materials))
        else:
            evidence = _OPERATION_HANDLERS[operation_type](bm, operation)
        if operation_type == "merge_vertices":
            merged_target_source = evidence.pop("_merged_target_source")
        bm.normal_update()
        components = _component_changes(bm, component_baseline)
        components["affected"] = requested
        if (
            len(bm.verts) > MAX_VERTICES
            or len(bm.edges) > MAX_EDGES
            or len(bm.faces) > MAX_FACES
            or sum(len(face.loops) for face in bm.faces) > MAX_LOOPS
        ):
            raise MeshOperationError(
                "MESH_BUDGET_EXCEEDED",
                "Mesh operation result exceeds the bounded topology budget",
            )
        relations = created = deleted = None
        if lineage is not None:
            relations, created, deleted = _finish_lineage(bm, lineage, str(operation_type))
            if operation_type == "merge_vertices":
                from .mesh_component_map_model import ComponentRelation

                merged_sources = _indices(operation.get("vertex_indices"), "vertex_indices")
                bm.verts.index_update()
                target_relation = next(
                    (
                        item
                        for item in relations["VERTEX"]
                        if item.source_index == merged_target_source
                    ),
                    None,
                )
                if target_relation is None or len(target_relation.target_indices) != 1:
                    raise MeshOperationError(
                        "MESH_LINEAGE_GENERATION_FAILED",
                        "Could not identify the exact merged vertex",
                    )
                target_index = target_relation.target_indices[0]
                unrelated = tuple(
                    item for item in relations["VERTEX"] if item.source_index not in merged_sources
                )
                relations["VERTEX"] = tuple(
                    sorted(
                        (
                            *unrelated,
                            *(
                                ComponentRelation(source, (target_index,), "MERGED")
                                for source in merged_sources
                            ),
                        ),
                        key=lambda item: item.source_index,
                    )
                )
                deleted["VERTEX"] = tuple(
                    index for index in deleted["VERTEX"] if index not in merged_sources
                )
            lineage = None
        bm.to_mesh(mesh)
        mesh.update(calc_edges=True, calc_edges_loose=True)
        after_topology_candidate = topology_fingerprint(mesh)
        if (
            resources is not None
            and relations is not None
            and created is not None
            and deleted is not None
            and after_topology_candidate != before_topology
        ):
            from .mesh_component_map_model import make_component_map

            component_map = make_component_map(
                transaction_id=transaction.transaction_id,
                operation=str(operation_type),
                before=before_map_evidence,
                after=_map_evidence(obj, mesh),
                after_users=int(mesh.users),
                after_user_objects=mesh_user_refs(mesh),
                relations=relations,
                created=created,
                deleted=deleted,
            )
            resources.add_component_map(component_map)
            created_selections, created_selection_ids = _created_selections(
                resources, component_map, obj, mesh
            )
    except (MeshOperationError, MeshResourceError) as exc:
        if component_map is not None and resources is not None:
            resources.release_component_map(component_map.component_map_id)
        if resources is not None:
            for selection_id in created_selection_ids:
                resources.release_selection(selection_id)
        _restore_failed_edit(mesh, call_snapshot, before_fingerprint, exc)
        if new_guard:
            _remove_new_guard(transaction, guard)
        raise
    except Exception as exc:
        if component_map is not None and resources is not None:
            resources.release_component_map(component_map.component_map_id)
        if resources is not None:
            for selection_id in created_selection_ids:
                resources.release_selection(selection_id)
        _restore_failed_edit(mesh, call_snapshot, before_fingerprint, exc)
        if new_guard:
            _remove_new_guard(transaction, guard)
        raise MeshOperationError(
            "MESH_EDIT_FAILED",
            f"Mesh operation failed: {type(exc).__name__}",
            kind="blender_api",
            details={"error_type": type(exc).__name__, "message": str(exc)},
        ) from exc
    finally:
        if lineage is not None:
            for state in lineage.values():
                with contextlib.suppress(Exception):
                    state.sequence.layers.int.remove(state.layer)
        bm.free()
        _remove_temporary_mesh(call_snapshot)
    after_fingerprint = mesh_fingerprint(mesh)
    after_topology = topology_fingerprint(mesh)
    if after_fingerprint == before_fingerprint:
        if new_guard:
            _remove_new_guard(transaction, guard)
        return {
            "transaction_id": transaction.transaction_id,
            "changed": False,
            "operation": operation_type,
            "data_scope": data_scope,
            "object": {
                "name": obj.name,
                "session_identity": session_identity("object", obj),
            },
            "mesh": {
                "name": obj.data.name,
                "session_identity": session_identity("mesh", obj.data),
                "users": int(obj.data.users),
            },
            "before_mesh": initial_mesh_reference,
            "after_mesh": _mesh_reference(obj.data),
            "before_mesh_fingerprint": before_fingerprint,
            "after_mesh_fingerprint": after_fingerprint,
            "before_topology_fingerprint": before_topology,
            "after_topology_fingerprint": after_topology,
            "before_counts": before_counts,
            "after_counts": mesh_counts(obj.data),
            "components": components,
            "evidence": evidence,
            "component_map": None,
            "created_selections": {},
            "delta": {
                "type": "mesh_edit",
                "recorded": False,
                "snapshot_reused": not new_guard,
            },
            "warnings": _component_warnings(components),
        }
    guard.expected_fingerprint = after_fingerprint
    guard.expected_users = int(mesh.users)
    guard.expected_user_objects = mesh_user_refs(mesh)
    transaction.record(
        MeshEditDelta(
            object_name=obj.name,
            object_identity=session_identity("object", obj),
            mesh_name=mesh.name,
            mesh_identity=session_identity("mesh", mesh),
            operation=str(operation_type),
            before_fingerprint=before_fingerprint,
            after_fingerprint=after_fingerprint,
            data_scope=data_scope,
        )
    )
    refresh_structure_guard_if_present(transaction, "object", obj)
    refresh_structure_guard_if_present(transaction, "mesh", mesh)
    return {
        "transaction_id": transaction.transaction_id,
        "changed": True,
        "operation": operation_type,
        "data_scope": data_scope,
        "object": {
            "name": obj.name,
            "session_identity": session_identity("object", obj),
        },
        "mesh": {
            "name": mesh.name,
            "session_identity": session_identity("mesh", mesh),
            "users": int(mesh.users),
            "user_objects": [
                {"object_name": name, "session_identity": identity}
                for name, identity in mesh_user_refs(mesh)
            ],
        },
        "before_mesh": initial_mesh_reference,
        "after_mesh": _mesh_reference(mesh),
        "before_mesh_fingerprint": before_fingerprint,
        "after_mesh_fingerprint": after_fingerprint,
        "before_topology_fingerprint": before_topology,
        "after_topology_fingerprint": after_topology,
        "before_counts": before_counts,
        "after_counts": mesh_counts(mesh),
        "components": components,
        "evidence": evidence,
        "component_map": component_map.summary() if component_map is not None else None,
        "created_selections": created_selections,
        "delta": {
            "type": "mesh_edit",
            "recorded": True,
            "snapshot_reused": not new_guard,
        },
        "warnings": _component_warnings(components),
    }


def touch_mesh_for_test(params: dict[str, Any]) -> dict[str, Any]:
    obj, mesh = _mesh_object(str(params.get("object_name", "")))
    action = params.get("action", "coordinate")
    if action == "coordinate":
        if not mesh.vertices:
            raise MeshOperationError("TEST_MESH_TOUCH_INVALID", "Mesh has no vertices")
        mesh.vertices[0].co.x += 0.125
        mesh.update()
    elif action == "topology":
        bm = bmesh.new()
        try:
            bm.from_mesh(mesh)
            bm.verts.new((0.0, 0.0, 0.0))
            bm.to_mesh(mesh)
            mesh.update()
        finally:
            bm.free()
    elif action == "shared_user":
        name = params.get("name")
        if not isinstance(name, str) or not name or bpy.data.objects.get(name) is not None:
            raise MeshOperationError(
                "TEST_MESH_TOUCH_INVALID", "shared_user requires a unique name"
            )
        duplicate = obj.copy()
        duplicate.data = mesh
        duplicate.name = name
        collection = (
            obj.users_collection[0] if obj.users_collection else bpy.context.scene.collection
        )
        collection.objects.link(duplicate)
    else:
        raise MeshOperationError("TEST_MESH_TOUCH_INVALID", f"Unsupported action: {action}")
    return {
        "test_hook": "mesh_touch",
        "action": action,
        "mesh_fingerprint": mesh_fingerprint(mesh),
        "mesh_users": int(mesh.users),
    }
