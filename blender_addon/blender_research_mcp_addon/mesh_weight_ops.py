"""Exact Vertex Group inspection and transactional deform-weight authoring."""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any

import bpy

from .lookdev_ops import session_identity
from .mesh_ops import (
    MeshOperationError,
    _create_guard,
    _remove_new_guard,
    _validate_guard,
    mesh_fingerprint,
    mesh_revision_id,
    mesh_user_objects,
    validate_mesh_attribute_target,
)
from .mesh_query_ops import validate_selection
from .mesh_resource_model import MeshResourceBook, MeshResourceError, SelectionRecord
from .structural_ops import refresh_structure_guard_if_present
from .transaction_model import Transaction, WeightEditDelta, WeightSnapshotGuard

MAX_GROUPS = 256
MAX_VERTEX_VALUES = 4096
MAX_WEIGHT_INFLUENCES = 16_000_000


class MeshWeightOperationError(MeshOperationError):
    pass


def _json_fingerprint(value: Any) -> str:
    encoded = json.dumps(value, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _group_identity(group: Any) -> str:
    return session_identity("vertex_group", group)


def _group_schema(obj: Any, *, identities: bool = True) -> tuple[tuple[Any, ...], ...]:
    return tuple(
        (
            group.name,
            bool(group.lock_weight),
            *([_group_identity(group)] if identities else []),
        )
        for group in obj.vertex_groups
    )


def group_schema_fingerprint(obj: Any) -> str:
    return _json_fingerprint(_group_schema(obj))


def _capture_weights(mesh: Any) -> tuple[tuple[tuple[int, float], ...], ...]:
    result = tuple(
        tuple(sorted((int(item.group), float(item.weight)) for item in vertex.groups))
        for vertex in mesh.vertices
    )
    if sum(len(items) for items in result) > MAX_WEIGHT_INFLUENCES:
        raise MeshWeightOperationError(
            "MESH_WEIGHT_BUDGET_EXCEEDED",
            f"Mesh exceeds the bounded {MAX_WEIGHT_INFLUENCES} deform-weight influence budget",
        )
    return result


def weights_fingerprint(mesh: Any) -> str:
    return _json_fingerprint(_capture_weights(mesh))


def _group_bone_matches(obj: Any, name: str) -> list[dict[str, Any]]:
    matches = []
    for modifier in obj.modifiers:
        if modifier.type != "ARMATURE" or modifier.object is None:
            continue
        armature = modifier.object
        bone = getattr(armature.data, "bones", {}).get(name)
        if bone is not None:
            matches.append(
                {
                    "armature_object_name": armature.name,
                    "armature_object_identity": session_identity("object", armature),
                    "bone_name": bone.name,
                    "bone_identity": session_identity("bone", bone),
                }
            )
    return matches


def _group_summary(obj: Any, group: Any) -> dict[str, Any]:
    return {
        "name": group.name,
        "session_identity": _group_identity(group),
        "index": int(group.index),
        "lock_weight": bool(group.lock_weight),
        "bone_matches": _group_bone_matches(obj, group.name),
    }


def inspect_weights(
    object_name: str,
    group_name: str | None,
    component: str,
    offset: int,
    limit: int,
) -> dict[str, Any]:
    obj = bpy.data.objects.get(object_name)
    if obj is None:
        raise MeshWeightOperationError(
            "OBJECT_NOT_FOUND", f"Object does not exist: {object_name}", kind="not_found"
        )
    if obj.type != "MESH" or obj.data is None:
        raise MeshWeightOperationError(
            "MESH_OBJECT_UNSUPPORTED", f"Weight inspection requires a MESH object: {object_name}"
        )
    mesh = obj.data
    if component not in {"SUMMARY", "GROUPS", "VERTICES"}:
        raise MeshWeightOperationError(
            "MESH_WEIGHT_COMPONENT_INVALID", f"Unsupported weight component: {component}"
        )
    if offset < 0 or not 1 <= limit <= 512:
        raise MeshWeightOperationError(
            "MESH_PAGINATION_INVALID", "offset must be non-negative and limit must be 1-512"
        )
    group = obj.vertex_groups.get(group_name) if group_name is not None else None
    if group_name is not None and group is None:
        raise MeshWeightOperationError(
            "MESH_WEIGHT_GROUP_NOT_FOUND",
            f"Vertex Group does not exist: {group_name}",
            kind="not_found",
        )
    all_weights = _capture_weights(mesh)
    totals = [sum(weight for _index, weight in weights) for weights in all_weights]
    influence_counts = [sum(weight > 0 for _index, weight in weights) for weights in all_weights]
    items: list[dict[str, Any]] = []
    total = 0
    if component == "GROUPS":
        groups = list(obj.vertex_groups)
        total = len(groups)
        items = [_group_summary(obj, item) for item in groups[offset : min(total, offset + limit)]]
    elif component == "VERTICES":
        total = len(mesh.vertices)
        for vertex_index in range(offset, min(total, offset + limit)):
            sparse = [
                {
                    "group_index": group_index,
                    "group_name": (
                        obj.vertex_groups[group_index].name
                        if group_index < len(obj.vertex_groups)
                        else None
                    ),
                    "weight": weight,
                }
                for group_index, weight in all_weights[vertex_index]
                if group is None or group_index == int(group.index)
            ]
            items.append(
                {
                    "vertex_index": vertex_index,
                    "weights": sparse,
                    "total": totals[vertex_index],
                    "influence_count": influence_counts[vertex_index],
                }
            )
    if offset > total:
        raise MeshWeightOperationError(
            "MESH_PAGINATION_INVALID", f"offset {offset} exceeds weight item count {total}"
        )
    stop = min(total, offset + limit)
    warnings = (
        [{"code": "MESH_WEIGHT_ITEMS_TRUNCATED", "next_offset": stop}] if stop < total else []
    )
    return {
        "object": {
            "name": obj.name,
            "session_identity": session_identity("object", obj),
        },
        "mesh": {
            "name": mesh.name,
            "session_identity": session_identity("mesh", mesh),
            "users": int(mesh.users),
        },
        "group": _group_summary(obj, group) if group is not None else None,
        "groups": [_group_summary(obj, item) for item in obj.vertex_groups],
        "group_schema_fingerprint": group_schema_fingerprint(obj),
        "weights_fingerprint": weights_fingerprint(mesh),
        "mesh_fingerprint": mesh_fingerprint(mesh),
        "mesh_revision_id": mesh_revision_id(mesh),
        "counts": {
            "groups": len(obj.vertex_groups),
            "vertices": len(mesh.vertices),
            "influences": sum(influence_counts),
            "unassigned_vertices": sum(count == 0 for count in influence_counts),
            "unnormalized_vertices": sum(
                count > 0 and not math.isclose(total_weight, 1.0, abs_tol=1e-6)
                for count, total_weight in zip(influence_counts, totals, strict=True)
            ),
            "over_four_influences": sum(count > 4 for count in influence_counts),
        },
        "component": component,
        "items": items,
        "pagination": {
            "offset": offset,
            "limit": limit,
            "total": total,
            "returned": len(items),
            "truncated": stop < total,
            "next_offset": stop if stop < total else None,
        },
        "warnings": warnings,
    }


def _objects_for_scope(obj: Any, mesh: Any, data_scope: str) -> tuple[Any, ...]:
    if data_scope == "OBJECT":
        return (obj,)
    users = mesh_user_objects(mesh)
    shapes = {_group_schema(item, identities=False) for item in users}
    if len(shapes) != 1:
        raise MeshWeightOperationError(
            "MESH_WEIGHT_SHARED_SCHEMA_MISMATCH",
            "SHARED_DATA requires identical ordered Group names and lock states on every user",
            kind="conflict",
        )
    return users


def _group(obj: Any, raw: Any, *, allow_locked: bool = False) -> Any:
    if not isinstance(raw, dict):
        raise MeshWeightOperationError("MESH_WEIGHT_OPERATION_INVALID", "group must be an object")
    name = raw.get("group_name")
    identity = raw.get("expected_group_identity")
    if not isinstance(name, str) or not name or not isinstance(identity, str) or not identity:
        raise MeshWeightOperationError(
            "MESH_WEIGHT_OPERATION_INVALID", "group requires exact name and identity"
        )
    group = obj.vertex_groups.get(name)
    if group is None:
        raise MeshWeightOperationError(
            "MESH_WEIGHT_GROUP_NOT_FOUND", f"Vertex Group does not exist: {name}", kind="not_found"
        )
    if _group_identity(group) != identity:
        raise MeshWeightOperationError(
            "MESH_WEIGHT_GROUP_IDENTITY_MISMATCH",
            f"Vertex Group identity changed: {name}",
            kind="conflict",
        )
    if group.lock_weight and not allow_locked:
        raise MeshWeightOperationError(
            "MESH_WEIGHT_LOCKED", f"Vertex Group is locked: {name}", kind="conflict"
        )
    return group


def _selection(
    resources: MeshResourceBook,
    selection_id: Any,
    obj: Any,
    mesh: Any,
) -> SelectionRecord:
    if not isinstance(selection_id, str) or not selection_id:
        raise MeshWeightOperationError(
            "MESH_WEIGHT_OPERATION_INVALID", "selection_id must be non-empty"
        )
    record = resources.selection(selection_id)
    selected_obj, selected_mesh = validate_selection(record)
    if selected_obj is not obj or selected_mesh is not mesh or record.domain != "VERTEX":
        raise MeshWeightOperationError(
            "MESH_WEIGHT_SELECTION_INVALID",
            "Weight operation requires a VERTEX SelectionSet on the exact target revision",
        )
    return record


def _clear_all_weights(obj: Any, vertex_count: int) -> None:
    indices = list(range(vertex_count))
    if not indices:
        return
    for group in obj.vertex_groups:
        group.remove(indices)


def _write_weights(obj: Any, weights: tuple[tuple[tuple[int, float], ...], ...]) -> None:
    _clear_all_weights(obj, len(weights))
    by_group: dict[int, list[tuple[int, float]]] = {}
    for vertex_index, assignments in enumerate(weights):
        for group_index, weight in assignments:
            by_group.setdefault(group_index, []).append((vertex_index, weight))
    for group_index, assignments in by_group.items():
        if group_index >= len(obj.vertex_groups):
            raise MeshWeightOperationError(
                "MESH_WEIGHT_RESTORE_FAILED",
                f"Weight snapshot references missing Group index {group_index}",
                kind="blender_api",
            )
        group = obj.vertex_groups[group_index]
        for vertex_index, weight in assignments:
            group.add([vertex_index], float(weight), "REPLACE")


def _restore_schemas(
    object_identities: dict[str, str],
    schemas: dict[str, tuple[tuple[str, bool], ...]],
) -> tuple[Any, ...]:
    objects = []
    for name, identity in object_identities.items():
        obj = bpy.data.objects.get(name)
        if obj is None or session_identity("object", obj) != identity:
            raise MeshWeightOperationError(
                "MESH_WEIGHT_DATA_CONFLICT",
                f"Weight user identity changed: {name}",
                kind="conflict",
            )
        while obj.vertex_groups:
            obj.vertex_groups.remove(obj.vertex_groups[-1])
        for group_name, locked in schemas[name]:
            group = obj.vertex_groups.new(name=group_name)
            group.lock_weight = locked
        objects.append(obj)
    return tuple(objects)


def _schema_fingerprints(objects: tuple[Any, ...]) -> dict[str, str]:
    return {obj.name: group_schema_fingerprint(obj) for obj in objects}


def _validate_weight_guard(guard: WeightSnapshotGuard) -> tuple[Any, tuple[Any, ...]]:
    mesh = bpy.data.meshes.get(guard.mesh_name)
    if mesh is None or session_identity("mesh", mesh) != guard.mesh_identity:
        raise MeshWeightOperationError(
            "MESH_WEIGHT_DATA_CONFLICT", "Guarded weight Mesh identity changed", kind="conflict"
        )
    objects = []
    for name, identity in guard.object_identities.items():
        obj = bpy.data.objects.get(name)
        if obj is None or session_identity("object", obj) != identity or obj.data is not mesh:
            raise MeshWeightOperationError(
                "MESH_WEIGHT_DATA_CONFLICT", f"Weight user changed: {name}", kind="conflict"
            )
        actual = group_schema_fingerprint(obj)
        expected = guard.expected_schema_fingerprints[name]
        if actual != expected:
            raise MeshWeightOperationError(
                "MESH_WEIGHT_DATA_CONFLICT",
                f"Vertex Group schema changed outside the transaction: {name}",
                kind="conflict",
                details={"expected": expected, "actual": actual},
            )
        objects.append(obj)
    actual_weights = weights_fingerprint(mesh)
    if actual_weights != guard.expected_weights_fingerprint:
        raise MeshWeightOperationError(
            "MESH_WEIGHT_DATA_CONFLICT",
            "Deform weights changed outside the transaction",
            kind="conflict",
            details={"expected": guard.expected_weights_fingerprint, "actual": actual_weights},
        )
    return mesh, tuple(objects)


def validate_weight_snapshot_guards(transaction: Transaction) -> None:
    for guard in transaction.weight_snapshot_guards.values():
        _validate_weight_guard(guard)


def restore_weight_snapshots(transaction: Transaction) -> list[dict[str, Any]]:
    restored = []
    for guard in reversed(tuple(transaction.weight_snapshot_guards.values())):
        mesh = bpy.data.meshes.get(guard.mesh_name)
        if mesh is None and guard.data_scope == "OBJECT":
            objects = _restore_schemas(guard.object_identities, guard.baseline_schemas)
            restored.append(
                {
                    "kind": "mesh_weights",
                    "action": "restore_group_schema_after_shared_link",
                    "mesh_name": objects[0].data.name,
                    "objects": sorted(guard.object_identities),
                }
            )
            continue
        objects = _restore_schemas(guard.object_identities, guard.baseline_schemas)
        _write_weights(objects[0], guard.baseline_weights)
        restored.append(
            {
                "kind": "mesh_weights",
                "action": "restore_group_schema_and_weights",
                "mesh_name": mesh.name,
                "objects": sorted(guard.object_identities),
            }
        )
    return restored


def finalize_weight_snapshots(transaction: Transaction) -> list[dict[str, Any]]:
    finalized = []
    for guard in transaction.weight_snapshot_guards.values():
        _validate_weight_guard(guard)
        finalized.append(
            {
                "kind": "mesh_weights",
                "action": "discard_weight_snapshot",
                "mesh_name": guard.mesh_name,
            }
        )
    return finalized


def adopt_weight_snapshots_for_native_save(transaction: Transaction) -> list[dict[str, Any]]:
    return [
        {
            "kind": "mesh_weights",
            "action": "accept_current_weights_native_save",
            "mesh_name": guard.mesh_name,
        }
        for guard in transaction.weight_snapshot_guards.values()
    ]


def _create_weight_guard(
    transaction: Transaction,
    obj: Any,
    mesh: Any,
    data_scope: str,
    *,
    baseline_weights: tuple[tuple[tuple[int, float], ...], ...] | None = None,
    expected_weights_fingerprint: str | None = None,
) -> WeightSnapshotGuard:
    objects = _objects_for_scope(obj, mesh, data_scope)
    captured = baseline_weights if baseline_weights is not None else _capture_weights(mesh)
    fingerprint = (
        expected_weights_fingerprint
        if expected_weights_fingerprint is not None
        else _json_fingerprint(captured)
    )
    guard = WeightSnapshotGuard(
        object_name=obj.name,
        object_identity=session_identity("object", obj),
        mesh_name=mesh.name,
        mesh_identity=session_identity("mesh", mesh),
        data_scope=data_scope,
        object_identities={item.name: session_identity("object", item) for item in objects},
        baseline_schemas={item.name: _group_schema(item, identities=False) for item in objects},
        expected_schema_fingerprints=_schema_fingerprints(objects),
        baseline_weights=captured,
        expected_weights_fingerprint=fingerprint,
    )
    transaction.add_weight_snapshot_guard(guard)
    return guard


def _restore_call_state(
    mesh: Any,
    object_identities: dict[str, str],
    schemas: dict[str, tuple[tuple[str, bool], ...]],
    weights: tuple[tuple[tuple[int, float], ...], ...],
    failure: Exception,
) -> None:
    try:
        objects = _restore_schemas(object_identities, schemas)
        _write_weights(objects[0], weights)
    except Exception as restore_error:
        raise MeshWeightOperationError(
            "MESH_WEIGHT_RESTORE_FAILED",
            f"Weight edit failed and call state could not be restored: {mesh.name}",
            kind="blender_api",
            details={
                "failure": str(failure),
                "restore_type": type(restore_error).__name__,
                "restore": str(restore_error),
            },
        ) from restore_error


def _weight_at(mesh: Any, vertex_index: int, group_index: int) -> float:
    return next(
        (
            float(item.weight)
            for item in mesh.vertices[vertex_index].groups
            if item.group == group_index
        ),
        0.0,
    )


def _refs(obj: Any, raw: Any, allow_locked: bool) -> tuple[Any, ...]:
    if not isinstance(raw, list) or not raw:
        raise MeshWeightOperationError(
            "MESH_WEIGHT_OPERATION_INVALID", "groups must be a non-empty list"
        )
    groups = tuple(_group(obj, item, allow_locked=allow_locked) for item in raw)
    if len({int(group.index) for group in groups}) != len(groups):
        raise MeshWeightOperationError("MESH_WEIGHT_OPERATION_INVALID", "groups must be unique")
    return groups


def _apply_schema_operation(
    objects: tuple[Any, ...],
    mesh: Any,
    operation: dict[str, Any],
    before_weights: tuple[tuple[tuple[int, float], ...], ...],
) -> dict[str, Any]:
    operation_type = operation["type"]
    if operation_type == "group_create":
        name = operation.get("group_name")
        if (
            not isinstance(name, str)
            or not name
            or any(item.vertex_groups.get(name) for item in objects)
        ):
            raise MeshWeightOperationError(
                "MESH_WEIGHT_GROUP_NAME_CONFLICT", f"Vertex Group name is invalid or used: {name}"
            )
        if len(objects[0].vertex_groups) >= MAX_GROUPS:
            raise MeshWeightOperationError(
                "MESH_WEIGHT_GROUP_LIMIT", f"An object may have at most {MAX_GROUPS} Vertex Groups"
            )
        locked = operation.get("lock_weight", False)
        if type(locked) is not bool:
            raise MeshWeightOperationError(
                "MESH_WEIGHT_OPERATION_INVALID", "lock_weight must be a boolean"
            )
        for item in objects:
            group = item.vertex_groups.new(name=name)
            group.lock_weight = locked
        return {"group_name": name, "group_index": len(objects[0].vertex_groups) - 1}
    group = _group(
        objects[0],
        operation.get("group"),
        allow_locked=operation.get("allow_locked") is True,
    )
    index = int(group.index)
    for item in objects[1:]:
        peer = item.vertex_groups[index]
        if peer.name != group.name or bool(peer.lock_weight) != bool(group.lock_weight):
            raise MeshWeightOperationError(
                "MESH_WEIGHT_SHARED_SCHEMA_MISMATCH", "Shared Group schema changed", kind="conflict"
            )
    if operation_type == "group_rename":
        new_name = operation.get("new_name")
        if not isinstance(new_name, str) or not new_name:
            raise MeshWeightOperationError(
                "MESH_WEIGHT_OPERATION_INVALID", "new_name must be non-empty"
            )
        if any(
            item.vertex_groups.get(new_name) not in {None, item.vertex_groups[index]}
            for item in objects
        ):
            raise MeshWeightOperationError(
                "MESH_WEIGHT_GROUP_NAME_CONFLICT", f"Vertex Group name is already used: {new_name}"
            )
        old_name = group.name
        for item in objects:
            item.vertex_groups[index].name = new_name
        return {"before_name": old_name, "after_name": new_name, "group_index": index}
    if operation_type == "group_delete":
        name = group.name
        for item in objects:
            item.vertex_groups.remove(item.vertex_groups[index])
        desired = tuple(
            tuple(
                (current - 1 if current > index else current, weight)
                for current, weight in assignments
                if current != index
            )
            for assignments in before_weights
        )
        _write_weights(objects[0], desired)
        return {"deleted_group": name, "group_index": index}
    raise MeshWeightOperationError(
        "MESH_WEIGHT_OPERATION_INVALID", f"Unsupported Group operation: {operation_type}"
    )


def _apply_weight_operation(
    obj: Any,
    mesh: Any,
    record: SelectionRecord,
    operation: dict[str, Any],
) -> dict[str, Any]:
    operation_type = operation["type"]
    indices = tuple(record.indices)
    if operation_type == "set":
        group = _group(
            obj, operation.get("group"), allow_locked=operation.get("allow_locked") is True
        )
        value = operation.get("value")
        values = operation.get("values")
        use_selection_weights = operation.get("use_selection_weights", False)
        choices = (
            int(value is not None) + int(values is not None) + int(use_selection_weights is True)
        )
        if choices != 1:
            raise MeshWeightOperationError(
                "MESH_WEIGHT_OPERATION_INVALID", "set requires exactly one weight source"
            )
        desired: dict[int, float]
        if values is not None:
            if not isinstance(values, list) or not 1 <= len(values) <= MAX_VERTEX_VALUES:
                raise MeshWeightOperationError(
                    "MESH_WEIGHT_OPERATION_INVALID", "values must contain 1-4096 entries"
                )
            desired = {}
            for item in values:
                if not isinstance(item, dict):
                    raise MeshWeightOperationError(
                        "MESH_WEIGHT_OPERATION_INVALID", "weight values must be objects"
                    )
                vertex_index = item.get("vertex_index")
                weight = item.get("weight")
                if (
                    isinstance(vertex_index, bool)
                    or not isinstance(vertex_index, int)
                    or isinstance(weight, bool)
                    or not isinstance(weight, (int, float))
                    or not math.isfinite(float(weight))
                    or not 0 <= float(weight) <= 1
                ):
                    raise MeshWeightOperationError(
                        "MESH_WEIGHT_OPERATION_INVALID", "weight value is invalid"
                    )
                if vertex_index in desired:
                    raise MeshWeightOperationError(
                        "MESH_WEIGHT_OPERATION_INVALID", "weight vertex indices must be unique"
                    )
                desired[vertex_index] = float(weight)
            if set(desired) != set(indices):
                raise MeshWeightOperationError(
                    "MESH_WEIGHT_OPERATION_INVALID",
                    "per-vertex values must exactly cover SelectionSet",
                )
        elif use_selection_weights is True:
            if record.weights is None:
                raise MeshWeightOperationError(
                    "MESH_WEIGHT_OPERATION_INVALID", "SelectionSet has no falloff weights"
                )
            desired = dict(zip(indices, record.weights, strict=True))
        else:
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not 0 <= float(value) <= 1
            ):
                raise MeshWeightOperationError(
                    "MESH_WEIGHT_OPERATION_INVALID", "value must be between 0 and 1"
                )
            desired = {index: float(value) for index in indices}
        mode = operation.get("mode", "REPLACE")
        if mode not in {"REPLACE", "ADD", "SUBTRACT"}:
            raise MeshWeightOperationError(
                "MESH_WEIGHT_OPERATION_INVALID", "mode must be REPLACE, ADD, or SUBTRACT"
            )
        for vertex_index, submitted in desired.items():
            current = _weight_at(mesh, vertex_index, int(group.index))
            result = (
                submitted
                if mode == "REPLACE"
                else current + submitted * (1 if mode == "ADD" else -1)
            )
            result = max(0.0, min(1.0, result))
            if result == 0:
                group.remove([vertex_index])
            else:
                group.add([vertex_index], result, "REPLACE")
        return {"affected_vertices": len(indices), "group_name": group.name, "mode": mode}
    if operation_type == "clear":
        all_groups = operation.get("all_groups", False)
        groups = (
            tuple(obj.vertex_groups)
            if all_groups is True
            else _refs(obj, operation.get("groups"), operation.get("allow_locked") is True)
        )
        if operation.get("allow_locked") is not True and any(group.lock_weight for group in groups):
            raise MeshWeightOperationError(
                "MESH_WEIGHT_LOCKED", "clear includes a locked Vertex Group", kind="conflict"
            )
        for group in groups:
            group.remove(list(indices))
        return {"affected_vertices": len(indices), "affected_groups": len(groups)}
    if operation_type == "normalize":
        groups = _refs(obj, operation.get("groups"), allow_locked=True)
        target = float(operation.get("target_total", 1.0))
        if not 0 < target <= 1 or not math.isfinite(target):
            raise MeshWeightOperationError(
                "MESH_WEIGHT_OPERATION_INVALID", "target_total must be finite and in (0,1]"
            )
        keep_locked = operation.get("keep_locked", True)
        zero_policy = operation.get("zero_policy", "KEEP")
        for vertex_index in indices:
            locked_total = sum(
                _weight_at(mesh, vertex_index, int(group.index))
                for group in groups
                if keep_locked and group.lock_weight
            )
            editable = [group for group in groups if not (keep_locked and group.lock_weight)]
            current = [_weight_at(mesh, vertex_index, int(group.index)) for group in editable]
            desired_total = target - locked_total
            if desired_total < -1e-7:
                raise MeshWeightOperationError(
                    "MESH_WEIGHT_NORMALIZE_CONFLICT", "Locked weights exceed target_total"
                )
            current_total = sum(current)
            if current_total <= 0:
                if zero_policy == "ERROR" and desired_total > 0:
                    raise MeshWeightOperationError(
                        "MESH_WEIGHT_NORMALIZE_CONFLICT", "Cannot normalize zero editable weights"
                    )
                continue
            factor = max(0.0, desired_total) / current_total
            for group, weight in zip(editable, current, strict=True):
                group.add([vertex_index], min(1.0, weight * factor), "REPLACE")
        return {"affected_vertices": len(indices), "affected_groups": len(groups)}
    if operation_type == "limit_total":
        maximum = operation.get("maximum_influences", 4)
        if isinstance(maximum, bool) or not isinstance(maximum, int) or not 1 <= maximum <= 32:
            raise MeshWeightOperationError(
                "MESH_WEIGHT_OPERATION_INVALID", "maximum_influences must be 1-32"
            )
        keep_locked = operation.get("keep_locked", True)
        normalize = operation.get("normalize", True)
        for vertex_index in indices:
            assignments = [
                (obj.vertex_groups[group_index], weight)
                for group_index, weight in _capture_weights(mesh)[vertex_index]
                if group_index < len(obj.vertex_groups) and weight > 0
            ]
            locked = [item for item in assignments if keep_locked and item[0].lock_weight]
            if len(locked) > maximum:
                raise MeshWeightOperationError(
                    "MESH_WEIGHT_LIMIT_CONFLICT", "Locked influences exceed maximum_influences"
                )
            editable = sorted(
                (item for item in assignments if item not in locked),
                key=lambda item: item[1],
                reverse=True,
            )[: maximum - len(locked)]
            kept = locked + editable
            kept_groups = {int(group.index) for group, _weight in kept}
            for group, _weight in assignments:
                if int(group.index) not in kept_groups:
                    group.remove([vertex_index])
            if normalize and kept:
                total = sum(weight for _group_item, weight in kept)
                if total > 0:
                    for group, weight in kept:
                        group.add([vertex_index], weight / total, "REPLACE")
        return {"affected_vertices": len(indices), "maximum_influences": maximum}
    raise MeshWeightOperationError(
        "MESH_WEIGHT_OPERATION_INVALID", f"Unsupported weight operation: {operation_type}"
    )


def edit_weights(
    transaction: Transaction,
    resources: MeshResourceBook,
    params: dict[str, Any],
) -> dict[str, Any]:
    obj, initial_mesh, data_scope, _refs_value = validate_mesh_attribute_target(params)
    expected_schema = params.get("expected_group_schema_fingerprint")
    expected_weights = params.get("expected_weights_fingerprint")
    if expected_schema != group_schema_fingerprint(obj):
        raise MeshWeightOperationError(
            "MESH_WEIGHT_SCHEMA_FINGERPRINT_MISMATCH",
            "Vertex Group schema evidence changed",
            kind="conflict",
        )
    initial_weight_values = _capture_weights(initial_mesh)
    initial_weights_fingerprint = _json_fingerprint(initial_weight_values)
    if expected_weights != initial_weights_fingerprint:
        raise MeshWeightOperationError(
            "MESH_WEIGHT_FINGERPRINT_MISMATCH", "Deform-weight evidence changed", kind="conflict"
        )
    operation = params.get("operation")
    if not isinstance(operation, dict) or operation.get("type") not in {
        "group_create",
        "group_rename",
        "group_delete",
        "set",
        "clear",
        "normalize",
        "limit_total",
    }:
        raise MeshWeightOperationError(
            "MESH_WEIGHT_OPERATION_INVALID", "operation must be a supported typed object"
        )
    operation_type = str(operation["type"])
    selection_record = None
    if operation_type in {"set", "clear", "normalize", "limit_total"}:
        selection_record = _selection(resources, operation.get("selection_id"), obj, initial_mesh)
    if operation_type in {"group_rename", "group_delete"}:
        _group(obj, operation.get("group"), allow_locked=operation.get("allow_locked") is True)
    transaction.ensure_capacity()
    mesh_guard = transaction.mesh_snapshot_guard(
        initial_mesh.name, session_identity("mesh", initial_mesh)
    )
    new_mesh_guard = mesh_guard is None
    if mesh_guard is None:
        mesh_guard = _create_guard(transaction, obj, initial_mesh, data_scope)
    else:
        _validate_guard(mesh_guard)
        if mesh_guard.data_scope != data_scope:
            raise MeshWeightOperationError(
                "MESH_WEIGHT_OPERATION_INVALID",
                "data_scope must remain stable within a transaction",
            )
    mesh = bpy.data.meshes.get(mesh_guard.mesh_name)
    if mesh is None:
        raise MeshWeightOperationError(
            "MESH_WEIGHT_DATA_CONFLICT", "Guarded Mesh no longer exists", kind="conflict"
        )
    weight_guard = transaction.weight_snapshot_guard(mesh.name, session_identity("mesh", mesh))
    new_weight_guard = weight_guard is None
    if weight_guard is None:
        weight_guard = _create_weight_guard(
            transaction,
            obj,
            mesh,
            data_scope,
            baseline_weights=initial_weight_values,
            expected_weights_fingerprint=initial_weights_fingerprint,
        )
    else:
        _validate_weight_guard(weight_guard)
        if weight_guard.data_scope != data_scope:
            raise MeshWeightOperationError(
                "MESH_WEIGHT_OPERATION_INVALID",
                "data_scope must remain stable within a transaction",
            )
    objects = tuple(bpy.data.objects[name] for name in weight_guard.object_identities)
    before_schema = group_schema_fingerprint(obj)
    before_weights = initial_weights_fingerprint
    before_mesh = mesh_fingerprint(mesh)
    call_schemas = {item.name: _group_schema(item, identities=False) for item in objects}
    call_identities = {item.name: session_identity("object", item) for item in objects}
    call_weights = initial_weight_values
    try:
        if operation_type.startswith("group_"):
            evidence = _apply_schema_operation(objects, mesh, operation, call_weights)
        else:
            if selection_record is None:
                raise MeshWeightOperationError(
                    "MESH_WEIGHT_OPERATION_INVALID", "Weight operation is missing a SelectionSet"
                )
            evidence = _apply_weight_operation(obj, mesh, selection_record, operation)
    except (MeshOperationError, MeshResourceError) as exc:
        _restore_call_state(mesh, call_identities, call_schemas, call_weights, exc)
        if new_weight_guard:
            transaction.remove_weight_snapshot_guard(weight_guard)
        if new_mesh_guard:
            _remove_new_guard(transaction, mesh_guard)
        raise
    except Exception as exc:
        _restore_call_state(mesh, call_identities, call_schemas, call_weights, exc)
        if new_weight_guard:
            transaction.remove_weight_snapshot_guard(weight_guard)
        if new_mesh_guard:
            _remove_new_guard(transaction, mesh_guard)
        raise MeshWeightOperationError(
            "MESH_WEIGHT_EDIT_FAILED",
            f"Weight edit failed: {type(exc).__name__}",
            kind="blender_api",
            details={"message": str(exc)},
        ) from exc
    after_schema = group_schema_fingerprint(obj)
    after_weights = (
        before_weights
        if operation_type in {"group_create", "group_rename"}
        else weights_fingerprint(mesh)
    )
    after_mesh = mesh_fingerprint(mesh)
    changed = before_schema != after_schema or before_weights != after_weights
    if not changed:
        if new_weight_guard:
            transaction.remove_weight_snapshot_guard(weight_guard)
        if new_mesh_guard:
            _remove_new_guard(transaction, mesh_guard)
    else:
        mesh_guard.expected_fingerprint = after_mesh
        weight_guard.expected_schema_fingerprints = _schema_fingerprints(objects)
        weight_guard.expected_weights_fingerprint = after_weights
        transaction.record(
            WeightEditDelta(
                object_name=obj.name,
                object_identity=session_identity("object", obj),
                mesh_name=mesh.name,
                mesh_identity=session_identity("mesh", mesh),
                operation=operation_type,
                before_schema_fingerprint=before_schema,
                after_schema_fingerprint=after_schema,
                before_weights_fingerprint=before_weights,
                after_weights_fingerprint=after_weights,
                data_scope=data_scope,
            )
        )
        for item in objects:
            refresh_structure_guard_if_present(transaction, "object", item)
        refresh_structure_guard_if_present(transaction, "mesh", mesh)
    return {
        "transaction_id": transaction.transaction_id,
        "changed": changed,
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
        },
        "before_mesh_fingerprint": before_mesh,
        "after_mesh_fingerprint": after_mesh,
        "before_group_schema_fingerprint": before_schema,
        "after_group_schema_fingerprint": after_schema,
        "before_weights_fingerprint": before_weights,
        "after_weights_fingerprint": after_weights,
        "mesh_revision_id": mesh_revision_id(mesh),
        "evidence": evidence,
        "delta": {
            "type": "mesh_weights",
            "recorded": changed,
            "snapshot_reused": not new_weight_guard,
        },
        "warnings": [],
    }
