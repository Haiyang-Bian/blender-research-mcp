"""Exact Armature inspection and reversible Mesh-object binding."""

from __future__ import annotations

import hashlib
import json
from typing import Any

import bpy

from .authoring_ops import object_summary
from .lookdev_ops import session_identity
from .mesh_ops import MeshOperationError, mesh_revision_id
from .mesh_weight_ops import (
    _capture_weights,
    group_schema_fingerprint,
    weights_fingerprint,
)
from .modifier_ops import modifier_stack_fingerprint
from .structural_ops import make_structure_guard, structure_fingerprint
from .transaction_model import StructuralDelta, Transaction


class RigOperationError(MeshOperationError):
    pass


def bone_schema_fingerprint(armature: Any) -> str:
    payload = [
        {
            "name": bone.name,
            "identity": session_identity("bone", bone),
            "parent": (
                session_identity("bone", bone.parent) if bone.parent is not None else None
            ),
            "use_deform": bool(bone.use_deform),
            "head": [round(float(value), 9) for value in bone.head_local],
            "tail": [round(float(value), 9) for value in bone.tail_local],
        }
        for bone in armature.bones
    ]
    encoded = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _mesh_object(name: Any) -> Any:
    obj = bpy.data.objects.get(name) if isinstance(name, str) else None
    if obj is None:
        raise RigOperationError(
            "OBJECT_NOT_FOUND", f"Object does not exist: {name}", kind="not_found"
        )
    if obj.type != "MESH" or obj.data is None:
        raise RigOperationError(
            "RIG_MESH_TARGET_INVALID", f"Rig binding requires a MESH object: {name}"
        )
    return obj


def _armature_object(name: Any) -> Any:
    obj = bpy.data.objects.get(name) if isinstance(name, str) else None
    if obj is None:
        raise RigOperationError(
            "RIG_ARMATURE_NOT_FOUND", f"Armature object does not exist: {name}", kind="not_found"
        )
    if obj.type != "ARMATURE" or obj.data is None:
        raise RigOperationError(
            "RIG_ARMATURE_TARGET_INVALID", f"Object is not an Armature: {name}"
        )
    return obj


def _armature_modifier_summary(obj: Any, modifier: Any, index: int) -> dict[str, Any]:
    target = modifier.object
    return {
        "name": modifier.name,
        "session_identity": session_identity("modifier", modifier),
        "stack_index": index,
        "target": (
            {
                "object_name": target.name,
                "object_identity": session_identity("object", target),
                "data_identity": session_identity("armature", target.data),
            }
            if target is not None and target.data is not None
            else None
        ),
        "settings": {
            "use_vertex_groups": bool(modifier.use_vertex_groups),
            "use_bone_envelopes": bool(modifier.use_bone_envelopes),
            "preserve_volume": bool(modifier.use_deform_preserve_volume),
            "use_multi_modifier": bool(modifier.use_multi_modifier),
            "vertex_group": str(modifier.vertex_group),
        },
    }


def inspect_rig(
    object_name: str,
    armature_object_name: str | None,
    offset: int,
    limit: int,
) -> dict[str, Any]:
    obj = _mesh_object(object_name)
    if offset < 0 or not 1 <= limit <= 512:
        raise RigOperationError(
            "RIG_PAGINATION_INVALID", "offset must be non-negative and limit must be 1-512"
        )
    armature_filter = (
        _armature_object(armature_object_name) if armature_object_name is not None else None
    )
    modifiers = [
        _armature_modifier_summary(obj, modifier, index)
        for index, modifier in enumerate(obj.modifiers)
        if modifier.type == "ARMATURE"
        and (armature_filter is None or modifier.object is armature_filter)
    ]
    armatures = {
        modifier.object
        for modifier in obj.modifiers
        if modifier.type == "ARMATURE" and modifier.object is not None
    }
    if armature_filter is not None:
        armatures.add(armature_filter)
    bone_names = {
        bone.name
        for armature in armatures
        for bone in armature.data.bones
        if bool(bone.use_deform)
    }
    groups = list(obj.vertex_groups)
    group_items = [
        {
            "name": group.name,
            "session_identity": session_identity("vertex_group", group),
            "index": int(group.index),
            "lock_weight": bool(group.lock_weight),
            "matched_bone": group.name if group.name in bone_names else None,
        }
        for group in groups
    ]
    total = len(group_items)
    if offset > total:
        raise RigOperationError(
            "RIG_PAGINATION_INVALID", f"offset {offset} exceeds group count {total}"
        )
    weights = _capture_weights(obj.data)
    return {
        "object": object_summary(obj),
        "parent": (
            {
                "name": obj.parent.name,
                "session_identity": session_identity("object", obj.parent),
                "type": str(obj.parent_type),
                "bone": str(obj.parent_bone),
            }
            if obj.parent is not None
            else None
        ),
        "modifier_stack_fingerprint": modifier_stack_fingerprint(obj),
        "armature_modifiers": modifiers,
        "armatures": [
            {
                "object_name": armature.name,
                "object_identity": session_identity("object", armature),
                "data_name": armature.data.name,
                "data_identity": session_identity("armature", armature.data),
                "bone_schema_fingerprint": bone_schema_fingerprint(armature.data),
                "bone_count": len(armature.data.bones),
            }
            for armature in sorted(armatures, key=lambda item: item.name)
        ],
        "group_schema_fingerprint": group_schema_fingerprint(obj),
        "weights_fingerprint": weights_fingerprint(obj.data),
        "matched_groups": [item["name"] for item in group_items if item["matched_bone"]],
        "unmatched_groups": [item["name"] for item in group_items if not item["matched_bone"]],
        "unassigned_vertices": sum(not sparse for sparse in weights),
        "items": group_items[offset : min(total, offset + limit)],
        "pagination": {
            "offset": offset,
            "limit": limit,
            "total": total,
            "returned": len(group_items[offset : min(total, offset + limit)]),
            "truncated": offset + limit < total,
            "next_offset": min(total, offset + limit) if offset + limit < total else None,
        },
    }


def _validate_targets(params: dict[str, Any]) -> tuple[Any, Any]:
    mesh_raw = params.get("mesh_target")
    armature_raw = params.get("armature_target")
    if not isinstance(mesh_raw, dict) or not isinstance(armature_raw, dict):
        raise RigOperationError(
            "RIG_BIND_REQUEST_INVALID", "mesh_target and armature_target must be objects"
        )
    obj = _mesh_object(mesh_raw.get("object_name"))
    mesh_actual = {
        "object_identity": session_identity("object", obj),
        "mesh_identity": session_identity("mesh", obj.data),
        "mesh_revision_id": mesh_revision_id(obj.data),
        "group_schema_fingerprint": group_schema_fingerprint(obj),
        "weights_fingerprint": weights_fingerprint(obj.data),
    }
    mesh_expected = {
        "object_identity": mesh_raw.get("expected_object_identity"),
        "mesh_identity": mesh_raw.get("expected_mesh_identity"),
        "mesh_revision_id": mesh_raw.get("expected_mesh_revision_id"),
        "group_schema_fingerprint": mesh_raw.get("expected_group_schema_fingerprint"),
        "weights_fingerprint": mesh_raw.get("expected_weights_fingerprint"),
    }
    if mesh_actual != mesh_expected:
        raise RigOperationError(
            "RIG_MESH_EVIDENCE_MISMATCH",
            "Mesh, group, or weight evidence changed before binding",
            kind="conflict",
            details={"expected": mesh_expected, "actual": mesh_actual},
        )
    armature = _armature_object(armature_raw.get("object_name"))
    armature_actual = {
        "object_identity": session_identity("object", armature),
        "data_identity": session_identity("armature", armature.data),
        "bone_schema_fingerprint": bone_schema_fingerprint(armature.data),
    }
    armature_expected = {
        "object_identity": armature_raw.get("expected_object_identity"),
        "data_identity": armature_raw.get("expected_data_identity"),
        "bone_schema_fingerprint": armature_raw.get("expected_bone_schema_fingerprint"),
    }
    if armature_actual != armature_expected:
        raise RigOperationError(
            "RIG_ARMATURE_EVIDENCE_MISMATCH",
            "Armature object, data, or bone schema changed before binding",
            kind="conflict",
            details={"expected": armature_expected, "actual": armature_actual},
        )
    return obj, armature


def _modifier_state(modifier: Any) -> dict[str, Any]:
    return {
        "object": modifier.object,
        "use_vertex_groups": bool(modifier.use_vertex_groups),
        "use_bone_envelopes": bool(modifier.use_bone_envelopes),
        "preserve_volume": bool(modifier.use_deform_preserve_volume),
        "use_multi_modifier": bool(modifier.use_multi_modifier),
        "vertex_group": str(modifier.vertex_group),
        "show_viewport": bool(modifier.show_viewport),
        "show_render": bool(modifier.show_render),
    }


def _apply_modifier(modifier: Any, armature: Any, policy: dict[str, Any]) -> None:
    modifier.object = armature
    modifier.use_vertex_groups = bool(policy.get("use_vertex_groups", True))
    modifier.use_bone_envelopes = bool(policy.get("use_bone_envelopes", False))
    modifier.use_deform_preserve_volume = bool(policy.get("preserve_volume", False))
    modifier.use_multi_modifier = bool(policy.get("use_multi_modifier", False))
    modifier.vertex_group = str(policy.get("vertex_group") or "")


def _validate_group_scope(obj: Any, armature: Any, scope: dict[str, Any]) -> list[str]:
    bone_names = {bone.name for bone in armature.data.bones if bool(bone.use_deform)}
    group_names = {group.name for group in obj.vertex_groups}
    if scope.get("type") == "ALL_MATCHED":
        selected = sorted(group_names & bone_names)
    elif scope.get("type") == "EXPLICIT":
        raw = scope.get("group_names")
        if not isinstance(raw, list) or not raw or len(raw) > 256 or len(set(raw)) != len(raw):
            raise RigOperationError(
                "RIG_GROUP_SCOPE_INVALID", "EXPLICIT group_names must be 1-256 unique names"
            )
        missing_groups = sorted(set(raw) - group_names)
        missing_bones = sorted(set(raw) - bone_names)
        if missing_groups or missing_bones:
            raise RigOperationError(
                "RIG_GROUP_SCOPE_INVALID",
                "Explicit groups must exist and match deform bones",
                details={"missing_groups": missing_groups, "missing_bones": missing_bones},
            )
        selected = list(raw)
    else:
        raise RigOperationError(
            "RIG_GROUP_SCOPE_INVALID", "group_scope must be ALL_MATCHED or EXPLICIT"
        )
    if not selected:
        raise RigOperationError(
            "RIG_GROUP_SCOPE_INVALID", "No Vertex Group matches a deform bone"
        )
    return selected


def _restore_binding_call(
    obj: Any,
    modifier: Any,
    *,
    created: bool,
    modifier_state: dict[str, Any] | None,
    modifier_index: int | None,
    parent: Any,
    parent_type: str,
    parent_bone: str,
    parent_inverse: Any,
    matrix_world: Any,
    matrix_basis: Any,
) -> None:
    if created:
        if obj.modifiers.get(modifier.name) is modifier:
            obj.modifiers.remove(modifier)
    elif modifier_state is not None:
        _apply_modifier(modifier, modifier_state["object"], modifier_state)
        modifier.show_viewport = modifier_state["show_viewport"]
        modifier.show_render = modifier_state["show_render"]
        current_index = list(obj.modifiers).index(modifier)
        if modifier_index is not None and current_index != modifier_index:
            obj.modifiers.move(current_index, modifier_index)
    obj.parent = parent
    obj.parent_type = parent_type
    obj.parent_bone = parent_bone
    obj.matrix_parent_inverse = parent_inverse
    obj.matrix_world = matrix_world
    if parent is not None:
        obj.matrix_basis = matrix_basis


def bind_rig(transaction: Transaction, params: dict[str, Any]) -> dict[str, Any]:
    transaction.ensure_capacity()
    obj, armature = _validate_targets(params)
    policy = params.get("modifier")
    scope = params.get("group_scope")
    parenting = params.get("parenting")
    if not isinstance(policy, dict) or not isinstance(scope, dict):
        raise RigOperationError(
            "RIG_BIND_REQUEST_INVALID", "modifier and group_scope must be objects"
        )
    if parenting not in {"NONE", "KEEP_WORLD", "KEEP_LOCAL"}:
        raise RigOperationError(
            "RIG_PARENTING_INVALID", "parenting must be NONE, KEEP_WORLD, or KEEP_LOCAL"
        )
    matched_groups = _validate_group_scope(obj, armature, scope)
    limit_group = policy.get("vertex_group")
    if limit_group is not None and obj.vertex_groups.get(limit_group) is None:
        raise RigOperationError(
            "RIG_GROUP_SCOPE_INVALID", f"Modifier limit group does not exist: {limit_group}"
        )

    name = policy.get("name")
    if not isinstance(name, str) or not name:
        raise RigOperationError("RIG_MODIFIER_INVALID", "Modifier name must be non-empty")
    existing_raw = policy.get("expected_existing")
    modifier = obj.modifiers.get(name)
    created = False
    if existing_raw is None:
        if modifier is not None:
            raise RigOperationError(
                "RIG_MODIFIER_CONFLICT",
                f"Modifier already exists without matching evidence: {name}",
                kind="conflict",
            )
        modifier = obj.modifiers.new(name=name, type="ARMATURE")
        created = True
    else:
        if not isinstance(existing_raw, dict) or modifier is None or modifier.type != "ARMATURE":
            raise RigOperationError(
                "RIG_MODIFIER_MISMATCH", "Expected Armature Modifier does not exist"
            )
        current_index = list(obj.modifiers).index(modifier)
        actual = {
            "identity": session_identity("modifier", modifier),
            "stack_index": current_index,
            "stack_fingerprint": modifier_stack_fingerprint(obj),
        }
        expected = {
            "identity": existing_raw.get("expected_identity"),
            "stack_index": existing_raw.get("expected_stack_index"),
            "stack_fingerprint": existing_raw.get("expected_stack_fingerprint"),
        }
        if actual != expected:
            raise RigOperationError(
                "RIG_MODIFIER_MISMATCH",
                "Armature Modifier identity, index, or stack changed",
                kind="conflict",
                details={"expected": expected, "actual": actual},
            )

    before_modifier = None if created else _modifier_state(modifier)
    before_modifier_index = None if created else list(obj.modifiers).index(modifier)
    before_parent = obj.parent
    before_parent_type = str(obj.parent_type)
    before_parent_bone = str(obj.parent_bone)
    before_parent_inverse = obj.matrix_parent_inverse.copy()
    before_world = obj.matrix_world.copy()
    before_basis = obj.matrix_basis.copy()
    before_object_fingerprint = structure_fingerprint("object", obj)
    try:
        _apply_modifier(modifier, armature, policy)
        if parenting == "KEEP_WORLD":
            world = obj.matrix_world.copy()
            obj.parent = armature
            obj.parent_type = "OBJECT"
            obj.parent_bone = ""
            obj.matrix_parent_inverse = armature.matrix_world.inverted_safe()
            obj.matrix_world = world
        elif parenting == "KEEP_LOCAL":
            basis = obj.matrix_basis.copy()
            obj.parent = armature
            obj.parent_type = "OBJECT"
            obj.parent_bone = ""
            obj.matrix_parent_inverse.identity()
            obj.matrix_basis = basis
        bpy.context.view_layer.update()
        object_identity = session_identity("object", obj)
        if transaction.tracks_object_transform(obj.name, object_identity):
            transaction.refresh_object_transform(
                obj.name,
                object_identity,
                {
                    "location": dict(zip("xyz", map(float, obj.location), strict=True)),
                    "rotation_euler": dict(
                        zip("xyz", map(float, obj.rotation_euler), strict=True)
                    ),
                    "scale": dict(zip("xyz", map(float, obj.scale), strict=True)),
                },
            )
        after_object_fingerprint = structure_fingerprint("object", obj)
        changed = before_object_fingerprint != after_object_fingerprint
        if not changed:
            if created:
                obj.modifiers.remove(modifier)
            return {
                "transaction_id": transaction.transaction_id,
                "changed": False,
                "object": object_summary(obj),
                "matched_groups": matched_groups,
                "delta": {"types": [], "recorded": False},
            }
        delta = StructuralDelta(
            kind="rig_binding",
            action="rig_binding",
            before=(),
            after=(
                make_structure_guard("object", obj),
                make_structure_guard("object", armature),
                make_structure_guard("armature", armature.data),
            ),
            payload={
                "object": obj,
                "modifier": modifier,
                "created_modifier": created,
                "before_modifier": before_modifier,
                "before_modifier_index": before_modifier_index,
                "before_parent": before_parent,
                "before_parent_type": before_parent_type,
                "before_parent_bone": before_parent_bone,
                "before_parent_inverse": before_parent_inverse,
                "before_matrix_world": before_world,
                "before_matrix_basis": before_basis,
            },
        )
        transaction.record(delta)
    except Exception as exc:
        try:
            _restore_binding_call(
                obj,
                modifier,
                created=created,
                modifier_state=before_modifier,
                modifier_index=before_modifier_index,
                parent=before_parent,
                parent_type=before_parent_type,
                parent_bone=before_parent_bone,
                parent_inverse=before_parent_inverse,
                matrix_world=before_world,
                matrix_basis=before_basis,
            )
            bpy.context.view_layer.update()
        except Exception as restore_error:
            raise RigOperationError(
                "RIG_BIND_RESTORE_FAILED",
                "Rig binding failed and the call state could not be restored",
                kind="conflict",
                details={"failure": str(exc), "restore_error": str(restore_error)},
            ) from restore_error
        if isinstance(exc, RigOperationError):
            raise
        raise RigOperationError(
            "RIG_BIND_FAILED",
            f"Rig binding failed: {type(exc).__name__}",
            kind="blender_api",
            details={"error_type": type(exc).__name__, "message": str(exc)},
        ) from exc

    return {
        "transaction_id": transaction.transaction_id,
        "changed": True,
        "object": object_summary(obj),
        "armature": object_summary(armature),
        "modifier": _armature_modifier_summary(obj, modifier, list(obj.modifiers).index(modifier)),
        "parenting": parenting,
        "matched_groups": matched_groups,
        "modifier_stack_fingerprint": modifier_stack_fingerprint(obj),
        "delta": {"types": ["rig_binding"], "recorded": True},
    }


def restore_rig_binding(delta: StructuralDelta) -> dict[str, Any]:
    obj = delta.payload["object"]
    modifier = delta.payload["modifier"]
    _restore_binding_call(
        obj,
        modifier,
        created=bool(delta.payload["created_modifier"]),
        modifier_state=delta.payload["before_modifier"],
        modifier_index=delta.payload["before_modifier_index"],
        parent=delta.payload["before_parent"],
        parent_type=str(delta.payload["before_parent_type"]),
        parent_bone=str(delta.payload["before_parent_bone"]),
        parent_inverse=delta.payload["before_parent_inverse"],
        matrix_world=delta.payload["before_matrix_world"],
        matrix_basis=delta.payload["before_matrix_basis"],
    )
    return {"kind": delta.kind, "action": delta.action, "restored": True}
