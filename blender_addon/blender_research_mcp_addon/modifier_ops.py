"""Typed, object-local Modifier inspection and transaction guards."""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any

import bpy

from .authoring_ops import AuthoringOperationError
from .lookdev_ops import require_object, session_identity
from .transaction_model import ModifierStackGuard, Transaction, TransactionModelError

SUPPORTED_MODIFIER_TYPES = {"BEVEL", "SUBSURF", "SOLIDIFY", "BOOLEAN"}
MODIFIER_LIMIT = 256
PENDING_DELETE_KEY = "_brmcp_pending_delete"

_FIELD_SPECS: dict[str, dict[str, tuple[str, str]]] = {
    "BEVEL": {
        "width": ("width", "float"),
        "segments": ("segments", "int"),
        "limit_method": ("limit_method", "enum"),
        "angle_limit_degrees": ("angle_limit", "degrees"),
        "affect": ("affect", "enum"),
        "width_mode": ("offset_type", "enum"),
        "profile": ("profile", "float"),
        "clamp_overlap": ("use_clamp_overlap", "bool"),
        "harden_normals": ("harden_normals", "bool"),
    },
    "SUBSURF": {
        "subdivision_type": ("subdivision_type", "enum"),
        "levels": ("levels", "int"),
        "render_levels": ("render_levels", "int"),
        "quality": ("quality", "int"),
        "show_only_control_edges": ("show_only_control_edges", "bool"),
        "use_limit_surface": ("use_limit_surface", "bool"),
        "use_creases": ("use_creases", "bool"),
    },
    "SOLIDIFY": {
        "thickness": ("thickness", "float"),
        "offset": ("offset", "float"),
        "use_even_offset": ("use_even_offset", "bool"),
        "use_quality_normals": ("use_quality_normals", "bool"),
        "use_rim": ("use_rim", "bool"),
        "use_rim_only": ("use_rim_only", "bool"),
        "use_flip_normals": ("use_flip_normals", "bool"),
    },
    "BOOLEAN": {
        "operation": ("operation", "enum"),
        "solver": ("solver", "enum"),
        "use_self": ("use_self", "bool"),
        "use_hole_tolerant": ("use_hole_tolerant", "bool"),
        "double_threshold": ("double_threshold", "float"),
    },
}

_RANGES: dict[str, dict[str, Any]] = {
    "BEVEL": {
        "width": [0.0, 100_000.0],
        "segments": [1, 64],
        "limit_method": ["NONE", "ANGLE"],
        "angle_limit_degrees": [0.0, 180.0],
        "affect": ["EDGES", "VERTICES"],
        "width_mode": ["OFFSET", "WIDTH", "DEPTH", "ABSOLUTE"],
        "profile": [0.0, 1.0],
    },
    "SUBSURF": {
        "subdivision_type": ["CATMULL_CLARK", "SIMPLE"],
        "levels": [0, 4],
        "render_levels": [0, 4],
        "quality": [1, 6],
        "evaluated_face_budget": 2_000_000,
    },
    "SOLIDIFY": {
        "thickness": [-100_000.0, 100_000.0],
        "offset": [-1.0, 1.0],
    },
    "BOOLEAN": {
        "operation": ["DIFFERENCE", "UNION", "INTERSECT"],
        "solver": ["FAST", "EXACT"],
        "double_threshold": [0.0, 1.0],
        "combined_face_budget": 2_000_000,
    },
}


def _round(value: Any) -> Any:
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return round(value, 9)
    return value


def modifier_is_driven(obj: Any, modifier: Any, rna_attribute: str) -> bool:
    animation_data = getattr(obj, "animation_data", None)
    if animation_data is None:
        return False
    try:
        data_path = modifier.path_from_id(rna_attribute)
    except (AttributeError, TypeError, ValueError):
        return False
    return any(driver.data_path == data_path for driver in animation_data.drivers)


def _is_readonly(modifier: Any, rna_attribute: str) -> bool:
    checker = getattr(modifier, "is_property_readonly", None)
    if checker is None:
        return False
    try:
        return bool(checker(rna_attribute))
    except (TypeError, ValueError):
        return True


def _read_setting(modifier: Any, public_name: str, rna_name: str, kind: str) -> Any:
    value = getattr(modifier, rna_name)
    if kind == "degrees":
        return round(math.degrees(float(value)), 9)
    if kind == "float":
        return round(float(value), 9)
    if kind == "int":
        return int(value)
    if kind == "bool":
        return bool(value)
    if kind == "enum":
        return str(value)
    raise RuntimeError(f"Unsupported Modifier field kind: {public_name}:{kind}")


def modifier_settings(modifier: Any) -> dict[str, Any]:
    modifier_type = str(modifier.type)
    settings = {
        public_name: _read_setting(modifier, public_name, rna_name, kind)
        for public_name, (rna_name, kind) in _FIELD_SPECS.get(modifier_type, {}).items()
    }
    if modifier_type == "BOOLEAN":
        operand = modifier.object
        settings["operand"] = (
            {
                "object_name": operand.name,
                "object_identity": session_identity("object", operand),
            }
            if operand is not None
            else None
        )
    return settings


def modifier_pending_delete(modifier: Any) -> bool:
    getter = getattr(modifier, "get", None)
    return bool(getter(PENDING_DELETE_KEY)) if getter is not None else False


def modifier_summary(obj: Any, modifier: Any, stack_index: int) -> dict[str, Any]:
    modifier_type = str(modifier.type)
    supported = modifier_type in SUPPORTED_MODIFIER_TYPES
    fields = _FIELD_SPECS.get(modifier_type, {})
    linked = obj.library is not None and obj.override_library is None
    driven = {
        public_name: modifier_is_driven(obj, modifier, rna_name)
        for public_name, (rna_name, _kind) in fields.items()
    }
    readonly = {
        public_name: _is_readonly(modifier, rna_name)
        for public_name, (rna_name, _kind) in fields.items()
    }
    writable = [
        field
        for field in fields
        if not linked and not driven[field] and not readonly[field]
    ]
    result: dict[str, Any] = {
        "name": str(modifier.name),
        "session_identity": session_identity("modifier", modifier),
        "stack_index": stack_index,
        "type": modifier_type,
        "supported": supported,
        "pending_delete": modifier_pending_delete(modifier),
        "show_viewport": bool(modifier.show_viewport),
        "show_render": bool(modifier.show_render),
        "driven": driven,
        "readonly": readonly,
        "writable_fields": ["show_viewport", "show_render", *writable] if not linked else [],
        "ranges": _RANGES.get(modifier_type, {}),
    }
    if supported:
        result["settings"] = modifier_settings(modifier)
    return result


def modifier_stack_summary(obj: Any) -> list[dict[str, Any]]:
    return [
        modifier_summary(obj, modifier, index)
        for index, modifier in enumerate(obj.modifiers)
    ]


def modifier_stack_fingerprint(obj: Any) -> str:
    encoded = json.dumps(
        modifier_stack_summary(obj),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def inspect_modifiers(object_name: str, scene_generation: int) -> dict[str, Any]:
    obj = bpy.data.objects.get(object_name)
    if obj is None:
        raise AuthoringOperationError(
            "OBJECT_NOT_FOUND",
            f"Object does not exist: {object_name}",
            kind="not_found",
        )
    if obj.type != "MESH":
        raise AuthoringOperationError(
            "MODIFIER_OBJECT_TYPE_INVALID",
            f"Modifier authoring requires a MESH object: {object_name}",
            kind="precondition",
            details={"object_type": str(obj.type)},
        )
    full_stack = modifier_stack_summary(obj)
    warnings = []
    if len(full_stack) > MODIFIER_LIMIT:
        warnings.append(
            {
                "code": "MODIFIER_STACK_TRUNCATED",
                "count": len(full_stack),
                "limit": MODIFIER_LIMIT,
            }
        )
    return {
        "object_name": str(obj.name),
        "object_identity": session_identity("object", obj),
        "object_library": obj.library.filepath if obj.library is not None else None,
        "mesh_identity": session_identity("mesh", obj.data),
        "mesh_users": int(obj.data.users),
        "base_faces": len(obj.data.polygons),
        "scene_generation": scene_generation,
        "stack_fingerprint": modifier_stack_fingerprint(obj),
        "modifiers": full_stack[:MODIFIER_LIMIT],
        "count": len(full_stack),
        "warnings": warnings,
    }


def require_modifier_object(object_name: str, object_identity: str) -> Any:
    obj = require_object(object_name, object_identity)
    if obj.type != "MESH":
        raise AuthoringOperationError(
            "MODIFIER_OBJECT_TYPE_INVALID",
            f"Modifier authoring requires a MESH object: {object_name}",
            kind="precondition",
            details={"object_type": str(obj.type)},
        )
    if obj.library is not None and obj.override_library is None:
        raise AuthoringOperationError(
            "MODIFIER_OBJECT_LINKED",
            f"Linked object Modifier stacks are read-only: {object_name}",
            kind="precondition",
        )
    return obj


def ensure_modifier_stack_guard(
    transaction: Transaction,
    obj: Any,
    expected_fingerprint: str,
) -> ModifierStackGuard:
    actual = modifier_stack_fingerprint(obj)
    if actual != expected_fingerprint:
        raise AuthoringOperationError(
            "MODIFIER_STACK_CONFLICT",
            f"Modifier stack changed after inspection: {obj.name}",
            kind="conflict",
            details={"expected": expected_fingerprint, "actual": actual},
        )
    object_identity = session_identity("object", obj)
    guard = transaction.ensure_modifier_stack_guard(
        object_name=str(obj.name),
        object_identity=object_identity,
        fingerprint=actual,
    )
    if guard.expected_fingerprint != actual:
        raise AuthoringOperationError(
            "MODIFIER_STACK_CONFLICT",
            f"Modifier stack changed outside the transaction: {obj.name}",
            kind="conflict",
            details={"expected": guard.expected_fingerprint, "actual": actual},
        )
    return guard


def refresh_modifier_stack_guard(transaction: Transaction, obj: Any) -> str:
    fingerprint = modifier_stack_fingerprint(obj)
    transaction.refresh_modifier_stack_guard(
        object_name=str(obj.name),
        object_identity=session_identity("object", obj),
        fingerprint=fingerprint,
    )
    return fingerprint


def _resolve_guard_object(guard: ModifierStackGuard) -> Any:
    obj = bpy.data.objects.get(guard.object_name)
    if obj is None or session_identity("object", obj) != guard.object_identity:
        raise TransactionModelError(
            "MODIFIER_STACK_CONFLICT",
            f"Modifier stack object identity changed: {guard.object_name}",
        )
    return obj


def validate_modifier_stack_guards(transaction: Transaction) -> None:
    for guard in transaction.modifier_stack_guards.values():
        obj = _resolve_guard_object(guard)
        actual = modifier_stack_fingerprint(obj)
        if actual != guard.expected_fingerprint:
            raise TransactionModelError(
                "MODIFIER_STACK_CONFLICT",
                f"Modifier stack changed outside the transaction: {guard.object_name}",
            )


def validate_restored_modifier_stacks(transaction: Transaction) -> None:
    for guard in transaction.modifier_stack_guards.values():
        obj = _resolve_guard_object(guard)
        actual = modifier_stack_fingerprint(obj)
        if actual != guard.baseline_fingerprint:
            raise TransactionModelError(
                "MODIFIER_STACK_RESTORE_FAILED",
                f"Modifier stack was not fully restored: {guard.object_name}",
            )
