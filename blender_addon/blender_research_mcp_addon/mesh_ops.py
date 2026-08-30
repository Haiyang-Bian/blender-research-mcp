"""Exact base-Mesh inspection and bounded semantic BMesh editing."""

from __future__ import annotations

import hashlib
import math
import struct
from array import array
from typing import Any

import bmesh
import bpy
from mathutils import Euler, Vector

from .lookdev_ops import session_identity
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
}
_ATTRIBUTE_LAYOUTS = {
    "FLOAT": ("value", "f", 1),
    "INT": ("value", "i", 1),
    "BOOLEAN": ("value", "b", 1),
    "FLOAT_VECTOR": ("vector", "f", 3),
    "FLOAT_COLOR": ("color", "f", 4),
    "BYTE_COLOR": ("color", "f", 4),
    "FLOAT2": ("vector", "f", 2),
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
    _hash_foreach(hasher, mesh.edges, "vertices", "i", 2)
    _hash_foreach(hasher, mesh.loops, "vertex_index", "i", 1)
    _hash_foreach(hasher, mesh.loops, "edge_index", "i", 1)
    _hash_foreach(hasher, mesh.polygons, "loop_start", "i", 1)
    _hash_foreach(hasher, mesh.polygons, "loop_total", "i", 1)
    return hasher.hexdigest()


def _hash_attributes(hasher: Any, mesh: Any) -> tuple[str, ...]:
    unsupported: list[str] = []
    for attribute in sorted(mesh.attributes, key=lambda item: item.name):
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
    _hash_foreach(hasher, mesh.polygons, "material_index", "i", 1)
    _hash_foreach(hasher, mesh.polygons, "use_smooth", "b", 1)
    _hash_foreach(hasher, mesh.polygons, "select", "b", 1)
    _hash_foreach(hasher, mesh.polygons, "hide", "b", 1)
    _hash_attributes(hasher, mesh)
    return hasher.hexdigest()


def unsupported_attributes(mesh: Any) -> tuple[str, ...]:
    return tuple(
        f"{attribute.name}:{attribute.data_type}"
        for attribute in mesh.attributes
        if str(attribute.data_type) not in SUPPORTED_ATTRIBUTE_TYPES
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
        reasons.append("OBJECT_LINKED")
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
            "supported": str(attribute.data_type) in SUPPORTED_ATTRIBUTE_TYPES,
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
        "component": component,
        "writable": not _writable_reasons(obj, mesh),
        "write_blockers": _writable_reasons(obj, mesh),
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
            items.append(
                {
                    "index": index,
                    "vertices": list(item.vertices),
                    "edges": list(item.edge_keys),
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


def _restore_mesh_geometry(mesh: Any, snapshot: Any, expected: str) -> None:
    bm = bmesh.new()
    try:
        bm.from_mesh(snapshot)
        mesh.clear_geometry()
        bm.to_mesh(mesh)
        mesh.update(calc_edges=True, calc_edges_loose=True)
    finally:
        bm.free()
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
        merge_co = bm.verts[target_index].co.copy()
    else:
        merge_co = sum((vert.co for vert in verts), Vector()) / len(verts)
    before = (len(bm.verts), len(bm.edges), len(bm.faces))
    bmesh.ops.pointmerge(bm, verts=verts, merge_co=merge_co)
    return {
        "deleted_vertices": before[0] - len(bm.verts),
        "deleted_edges": before[1] - len(bm.edges),
        "deleted_faces": before[2] - len(bm.faces),
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


def edit_mesh(transaction: Transaction, params: dict[str, Any]) -> dict[str, Any]:
    obj, initial_mesh, data_scope, _refs = _validate_mesh_target(params)
    operation = params.get("operation")
    if not isinstance(operation, dict):
        raise MeshOperationError("MESH_OPERATION_INVALID", "operation must be an object")
    operation_type = operation.get("type")
    if operation_type not in {*_OPERATION_HANDLERS, "face_settings"}:
        raise MeshOperationError(
            "MESH_OPERATION_INVALID", f"Unsupported operation: {operation_type}"
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
    bm = bmesh.new()
    try:
        bm.from_mesh(mesh)
        bm.verts.ensure_lookup_table()
        bm.edges.ensure_lookup_table()
        bm.faces.ensure_lookup_table()
        if operation_type == "face_settings":
            evidence = _operation_face_settings(bm, operation, len(mesh.materials))
        else:
            evidence = _OPERATION_HANDLERS[operation_type](bm, operation)
        bm.normal_update()
        bm.verts.index_update()
        bm.edges.index_update()
        bm.faces.index_update()
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
        bm.to_mesh(mesh)
        mesh.update(calc_edges=True, calc_edges_loose=True)
    except MeshOperationError:
        if new_guard:
            _remove_new_guard(transaction, guard)
        raise
    except Exception as exc:
        if new_guard:
            _remove_new_guard(transaction, guard)
        raise MeshOperationError(
            "MESH_EDIT_FAILED",
            f"Mesh operation failed: {type(exc).__name__}",
            kind="blender_api",
            details={"error_type": type(exc).__name__, "message": str(exc)},
        ) from exc
    finally:
        bm.free()
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
            "before_mesh_fingerprint": before_fingerprint,
            "after_mesh_fingerprint": after_fingerprint,
            "before_topology_fingerprint": before_topology,
            "after_topology_fingerprint": after_topology,
            "before_counts": before_counts,
            "after_counts": mesh_counts(obj.data),
            "evidence": evidence,
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
        "before_mesh_fingerprint": before_fingerprint,
        "after_mesh_fingerprint": after_fingerprint,
        "before_topology_fingerprint": before_topology,
        "after_topology_fingerprint": after_topology,
        "before_counts": before_counts,
        "after_counts": mesh_counts(mesh),
        "evidence": evidence,
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
