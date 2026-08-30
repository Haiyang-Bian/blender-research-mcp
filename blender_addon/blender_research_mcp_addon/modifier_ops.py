"""Typed, object-local Modifier inspection and transaction guards."""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any

import bpy

from .authoring_ops import AuthoringOperationError
from .lookdev_ops import require_object, session_identity
from .structural_ops import refresh_structure_guard_if_present
from .transaction_model import (
    ModifierCreateDelta,
    ModifierDeleteDelta,
    ModifierMoveDelta,
    ModifierSettingsDelta,
    ModifierStackGuard,
    ModifierStateDelta,
    Transaction,
    TransactionModelError,
)

SUPPORTED_MODIFIER_TYPES = {"BEVEL", "SUBSURF", "SOLIDIFY", "BOOLEAN"}
MODIFIER_LIMIT = 256
_PENDING_DELETE_TOKENS: dict[str, str] = {}

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

_COMMON_FIELDS = {"show_viewport", "show_render"}
_CREATE_DEFAULTS: dict[str, dict[str, Any]] = {
    "BEVEL": {
        "show_viewport": True,
        "show_render": True,
        "width": 0.1,
        "segments": 2,
        "limit_method": "ANGLE",
        "angle_limit_degrees": 30.0,
        "affect": "EDGES",
        "width_mode": "OFFSET",
        "profile": 0.5,
        "clamp_overlap": True,
        "harden_normals": False,
    },
    "SUBSURF": {
        "show_viewport": True,
        "show_render": True,
        "subdivision_type": "CATMULL_CLARK",
        "levels": 2,
        "render_levels": 2,
        "quality": 3,
        "show_only_control_edges": False,
        "use_limit_surface": True,
        "use_creases": True,
    },
    "SOLIDIFY": {
        "show_viewport": True,
        "show_render": True,
        "thickness": 0.01,
        "offset": -1.0,
        "use_even_offset": False,
        "use_quality_normals": False,
        "use_rim": True,
        "use_rim_only": False,
        "use_flip_normals": False,
    },
    "BOOLEAN": {
        "show_viewport": True,
        "show_render": True,
        "operation": "DIFFERENCE",
        "solver": "EXACT",
        "use_self": False,
        "use_hole_tolerant": False,
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
    return session_identity("modifier", modifier) in _PENDING_DELETE_TOKENS


def clear_modifier_pending_deletes() -> None:
    """Forget session-only pending markers after file load or add-on shutdown."""

    _PENDING_DELETE_TOKENS.clear()


def modifier_summary(obj: Any, modifier: Any, stack_index: int) -> dict[str, Any]:
    modifier_type = str(modifier.type)
    supported = modifier_type in SUPPORTED_MODIFIER_TYPES
    fields = _FIELD_SPECS.get(modifier_type, {})
    linked = obj.library is not None and obj.override_library is None
    driven = {
        public_name: modifier_is_driven(obj, modifier, rna_name)
        for public_name, (rna_name, _kind) in fields.items()
    }
    driven.update({field: modifier_is_driven(obj, modifier, field) for field in _COMMON_FIELDS})
    readonly = {
        public_name: _is_readonly(modifier, rna_name)
        for public_name, (rna_name, _kind) in fields.items()
    }
    readonly.update({field: _is_readonly(modifier, field) for field in _COMMON_FIELDS})
    writable = [
        field for field in fields if not linked and not driven[field] and not readonly[field]
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
        "writable_fields": (
            [
                field
                for field in ["show_viewport", "show_render", *writable]
                if not driven[field] and not readonly[field]
            ]
            if not linked
            else []
        ),
        "ranges": _RANGES.get(modifier_type, {}),
    }
    if supported:
        result["settings"] = modifier_settings(modifier)
    return result


def modifier_stack_summary(obj: Any) -> list[dict[str, Any]]:
    return [modifier_summary(obj, modifier, index) for index, modifier in enumerate(obj.modifiers)]


def modifier_stack_fingerprint(obj: Any) -> str:
    summary = modifier_stack_summary(obj)
    for item, modifier in zip(summary, obj.modifiers, strict=True):
        item["_pending_delete_token"] = _PENDING_DELETE_TOKENS.get(
            session_identity("modifier", modifier)
        )
    encoded = json.dumps(
        summary,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def touch_modifier_for_test(params: dict[str, Any]) -> dict[str, Any]:
    """Deterministically simulate one user Modifier edit for private live tests."""

    object_name = _require_text(params.get("object_name"), "object_name")
    modifier_name = _require_text(params.get("modifier_name"), "modifier_name")
    obj = bpy.data.objects.get(object_name)
    if obj is None or obj.type != "MESH":
        raise AuthoringOperationError(
            "OBJECT_NOT_FOUND",
            f"Mesh object does not exist: {object_name}",
            kind="not_found",
        )
    modifier = obj.modifiers.get(modifier_name)
    if modifier is None:
        raise AuthoringOperationError(
            "MODIFIER_NOT_FOUND",
            f"Modifier does not exist: {modifier_name}",
            kind="not_found",
        )
    action = params.get("action")
    if action == "setting":
        field = _require_text(params.get("property"), "property")
        if field not in _COMMON_FIELDS | set(_FIELD_SPECS.get(str(modifier.type), {})):
            raise AuthoringOperationError(
                "TEST_MODIFIER_TOUCH_INVALID",
                f"Unsupported Modifier test property: {field}",
                kind="validation",
            )
        value = _validate_public_value(str(modifier.type), field, params.get("value"))
        _write_public_setting(modifier, field, value)
        detail = {"property": field, "value": value}
    elif action == "move":
        target_index = _stack_index(
            params.get("target_stack_index"),
            maximum=len(obj.modifiers),
            allow_append=False,
        )
        current_index = list(obj.modifiers).index(modifier)
        obj.modifiers.move(current_index, target_index)
        detail = {"before_index": current_index, "target_stack_index": target_index}
    else:
        raise AuthoringOperationError(
            "TEST_MODIFIER_TOUCH_INVALID",
            "action must be setting or move",
            kind="validation",
        )
    bpy.context.view_layer.update()
    return {
        "test_hook": "modifier_touch",
        "action": action,
        "object_name": object_name,
        "modifier_name": modifier_name,
        "stack_fingerprint": modifier_stack_fingerprint(obj),
        **detail,
    }


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


def _require_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value:
        raise AuthoringOperationError(
            "MODIFIER_PARAMETER_INVALID",
            f"{field} must be a non-empty string",
            kind="validation",
            details={"field": field},
        )
    return value


def _strict_number(value: Any, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AuthoringOperationError(
            "MODIFIER_PARAMETER_INVALID",
            f"{field} must be a JSON number",
            kind="validation",
            details={"field": field},
        )
    result = float(value)
    if not math.isfinite(result):
        raise AuthoringOperationError(
            "MODIFIER_PARAMETER_INVALID",
            f"{field} must be finite",
            kind="validation",
            details={"field": field},
        )
    return result


def _validate_public_value(modifier_type: str, field: str, value: Any) -> Any:
    if field in _COMMON_FIELDS:
        if type(value) is not bool:
            raise AuthoringOperationError(
                "MODIFIER_PARAMETER_INVALID",
                f"{field} must be a boolean",
                kind="validation",
                details={"field": field},
            )
        return value
    _rna_name, kind = _FIELD_SPECS[modifier_type][field]
    allowed = _RANGES.get(modifier_type, {}).get(field)
    if kind == "bool":
        if type(value) is not bool:
            raise AuthoringOperationError(
                "MODIFIER_PARAMETER_INVALID",
                f"{field} must be a boolean",
                kind="validation",
                details={"field": field},
            )
        return value
    if kind == "int":
        if type(value) is not int:
            raise AuthoringOperationError(
                "MODIFIER_PARAMETER_INVALID",
                f"{field} must be an integer",
                kind="validation",
                details={"field": field},
            )
        result: Any = value
    elif kind in {"float", "degrees"}:
        result = _strict_number(value, field)
    elif kind == "enum":
        if not isinstance(value, str) or value not in allowed:
            raise AuthoringOperationError(
                "MODIFIER_PARAMETER_INVALID",
                f"{field} must be one of the supported enum values",
                kind="validation",
                details={"field": field, "allowed": allowed},
            )
        return value
    else:
        raise RuntimeError(f"Unsupported Modifier field kind: {kind}")
    if allowed is not None and not allowed[0] <= result <= allowed[1]:
        raise AuthoringOperationError(
            "MODIFIER_PARAMETER_INVALID",
            f"{field} is outside the supported range",
            kind="validation",
            details={"field": field, "minimum": allowed[0], "maximum": allowed[1]},
        )
    return result


def _base_face_count(obj: Any) -> int:
    return len(obj.data.polygons)


def _operand_summary(obj: Any) -> dict[str, str]:
    return {
        "object_name": str(obj.name),
        "object_identity": session_identity("object", obj),
    }


def _resolve_boolean_operand(source: Any, raw: Any) -> Any:
    if not isinstance(raw, dict) or set(raw) != {
        "object_name",
        "expected_object_identity",
    }:
        raise AuthoringOperationError(
            "BOOLEAN_OPERAND_INVALID",
            "Boolean operand must contain exact object_name and expected_object_identity",
            kind="validation",
        )
    name = _require_text(raw.get("object_name"), "operand.object_name")
    identity = _require_text(
        raw.get("expected_object_identity"), "operand.expected_object_identity"
    )
    operand = bpy.data.objects.get(name)
    if operand is None:
        raise AuthoringOperationError(
            "BOOLEAN_OPERAND_NOT_FOUND",
            f"Boolean operand does not exist: {name}",
            kind="not_found",
        )
    actual = session_identity("object", operand)
    if actual != identity:
        raise AuthoringOperationError(
            "BOOLEAN_OPERAND_IDENTITY_MISMATCH",
            f"Boolean operand identity changed: {name}",
            kind="conflict",
            details={"expected": identity, "actual": actual},
        )
    if session_identity("object", operand) == session_identity("object", source):
        raise AuthoringOperationError(
            "BOOLEAN_OPERAND_SELF",
            "A Boolean Modifier cannot use its own object as operand",
            kind="precondition",
        )
    if operand.type != "MESH":
        raise AuthoringOperationError(
            "BOOLEAN_OPERAND_TYPE_INVALID",
            f"Boolean operand must be a MESH object: {name}",
            kind="precondition",
        )
    return operand


def _boolean_reaches(start: Any, target: Any, visited: set[str]) -> bool:
    identity = session_identity("object", start)
    if identity in visited:
        return False
    visited.add(identity)
    if session_identity("object", start) == session_identity("object", target):
        return True
    for modifier in start.modifiers:
        if modifier.type != "BOOLEAN" or modifier_pending_delete(modifier):
            continue
        operand = modifier.object
        if operand is not None and _boolean_reaches(operand, target, visited):
            return True
    return False


def _validate_final_state(
    obj: Any,
    modifier_type: str,
    final: dict[str, Any],
    *,
    supplied_fields: set[str],
) -> None:
    if modifier_type == "SUBSURF":
        level = max(int(final["levels"]), int(final["render_levels"]))
        estimate = _base_face_count(obj) * (4**level)
        if estimate > 2_000_000:
            raise AuthoringOperationError(
                "SUBDIVISION_BUDGET_EXCEEDED",
                "Subdivision settings exceed the deterministic face budget",
                kind="precondition",
                details={"estimated_faces": estimate, "maximum": 2_000_000},
            )
    elif modifier_type == "SOLIDIFY":
        if final["use_rim_only"] and not final["use_rim"]:
            raise AuthoringOperationError(
                "MODIFIER_PARAMETER_INVALID",
                "use_rim_only=true requires use_rim=true",
                kind="validation",
                details={"field": "use_rim_only"},
            )
    elif modifier_type == "BOOLEAN":
        solver = final["solver"]
        if solver != "EXACT" and (final["use_self"] or final["use_hole_tolerant"]):
            raise AuthoringOperationError(
                "BOOLEAN_SOLVER_CONFLICT",
                "use_self and use_hole_tolerant require solver=EXACT",
                kind="validation",
            )
        if "double_threshold" in supplied_fields and solver != "FAST":
            raise AuthoringOperationError(
                "BOOLEAN_SOLVER_CONFLICT",
                "double_threshold may only be changed with solver=FAST",
                kind="validation",
            )
        operand = final.get("operand_object")
        if operand is None:
            raise AuthoringOperationError(
                "BOOLEAN_OPERAND_INVALID",
                "Boolean Modifier requires one exact Mesh operand",
                kind="validation",
            )
        if _boolean_reaches(operand, obj, set()):
            raise AuthoringOperationError(
                "BOOLEAN_CYCLE",
                "Boolean operand would create a direct or transitive cycle",
                kind="precondition",
            )
        combined_faces = _base_face_count(obj) + _base_face_count(operand)
        if combined_faces > 2_000_000:
            raise AuthoringOperationError(
                "BOOLEAN_BUDGET_EXCEEDED",
                "Boolean source and operand exceed the deterministic face budget",
                kind="precondition",
                details={"combined_faces": combined_faces, "maximum": 2_000_000},
            )


def _public_state(modifier: Any) -> dict[str, Any]:
    return {
        "show_viewport": bool(modifier.show_viewport),
        "show_render": bool(modifier.show_render),
        **modifier_settings(modifier),
    }


def _same(left: Any, right: Any) -> bool:
    if isinstance(left, float) or isinstance(right, float):
        try:
            return abs(float(left) - float(right)) <= 1e-7
        except (TypeError, ValueError):
            return False
    return type(left) is type(right) and left == right


def _write_public_setting(modifier: Any, field: str, value: Any) -> None:
    if field in _COMMON_FIELDS:
        setattr(modifier, field, value)
        return
    rna_name, kind = _FIELD_SPECS[str(modifier.type)][field]
    if kind == "degrees":
        setattr(modifier, rna_name, math.radians(float(value)))
    else:
        setattr(modifier, rna_name, value)


def _verify_public_settings(modifier: Any, expected: dict[str, Any]) -> bool:
    current = _public_state(modifier)
    return all(_same(current[field], value) for field, value in expected.items())


def _validate_modifier_patch(
    obj: Any,
    modifier: Any,
    modifier_type: str,
    raw_settings: Any,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    if not isinstance(raw_settings, dict) or not raw_settings:
        raise AuthoringOperationError(
            "MODIFIER_SETTINGS_EMPTY",
            "settings must contain at least one field",
            kind="validation",
        )
    supplied_type = raw_settings.get("type")
    if supplied_type is not None and supplied_type != modifier_type:
        raise AuthoringOperationError(
            "MODIFIER_TYPE_MISMATCH",
            "settings type does not match the inspected Modifier type",
            kind="conflict",
        )
    patch = {key: value for key, value in raw_settings.items() if key != "type"}
    allowed = _COMMON_FIELDS | set(_FIELD_SPECS[modifier_type])
    if modifier_type == "BOOLEAN":
        allowed.add("operand")
    if not patch or set(patch) - allowed:
        raise AuthoringOperationError(
            "MODIFIER_PARAMETER_INVALID",
            "settings contains no supported fields or includes unknown fields",
            kind="validation",
            details={"unknown": sorted(set(patch) - allowed)},
        )
    before = _public_state(modifier)
    final = dict(before)
    normalized: dict[str, Any] = {}
    for field, raw_value in patch.items():
        if field == "operand":
            operand = _resolve_boolean_operand(obj, raw_value)
            normalized[field] = _operand_summary(operand)
            final[field] = normalized[field]
            final["operand_object"] = operand
            continue
        normalized[field] = _validate_public_value(modifier_type, field, raw_value)
        final[field] = normalized[field]
    if modifier_type == "BOOLEAN" and "operand_object" not in final:
        final["operand_object"] = modifier.object
    _validate_final_state(obj, modifier_type, final, supplied_fields=set(patch))
    return before, normalized, final


def _require_exact_modifier(
    obj: Any,
    *,
    modifier_name: str,
    modifier_identity: str,
    modifier_type: str,
    stack_index: Any,
    supported_only: bool = True,
) -> Any:
    if supported_only and modifier_type not in SUPPORTED_MODIFIER_TYPES:
        raise AuthoringOperationError(
            "MODIFIER_TYPE_UNSUPPORTED",
            f"Unsupported Modifier type: {modifier_type}",
            kind="validation",
        )
    if type(stack_index) is not int or not 0 <= stack_index < len(obj.modifiers):
        raise AuthoringOperationError(
            "MODIFIER_STACK_INDEX_MISMATCH",
            "Modifier stack index is invalid or changed",
            kind="conflict",
        )
    modifier = obj.modifiers[stack_index]
    named_modifier = obj.modifiers.get(modifier_name)
    if (
        modifier.name != modifier_name
        or named_modifier is None
        or session_identity("modifier", named_modifier) != session_identity("modifier", modifier)
    ):
        raise AuthoringOperationError(
            "MODIFIER_STACK_INDEX_MISMATCH",
            f"Modifier is no longer at the inspected stack index: {modifier_name}",
            kind="conflict",
        )
    actual_identity = session_identity("modifier", modifier)
    if actual_identity != modifier_identity:
        raise AuthoringOperationError(
            "MODIFIER_IDENTITY_MISMATCH",
            f"Modifier identity changed: {modifier_name}",
            kind="conflict",
            details={"expected": modifier_identity, "actual": actual_identity},
        )
    if str(modifier.type) != modifier_type:
        raise AuthoringOperationError(
            "MODIFIER_TYPE_MISMATCH",
            f"Modifier type changed: {modifier_name}",
            kind="conflict",
            details={"expected": modifier_type, "actual": str(modifier.type)},
        )
    if modifier_pending_delete(modifier):
        raise AuthoringOperationError(
            "MODIFIER_PENDING_DELETE",
            f"Modifier is already pending deletion: {modifier_name}",
            kind="conflict",
        )
    return modifier


def _stack_index(value: Any, *, maximum: int, allow_append: bool) -> int:
    if value is None and allow_append:
        return maximum
    upper = maximum if allow_append else maximum - 1
    if type(value) is not int or not 0 <= value <= upper:
        raise AuthoringOperationError(
            "MODIFIER_STACK_INDEX_INVALID",
            f"stack index must be between 0 and {upper}",
            kind="validation",
        )
    return value


def _require_stack_fingerprint(params: dict[str, Any]) -> str:
    return _require_text(params.get("expected_stack_fingerprint"), "expected_stack_fingerprint")


def create_modifier(transaction: Transaction, params: dict[str, Any]) -> dict[str, Any]:
    object_name = _require_text(params.get("object_name"), "object_name")
    object_identity = _require_text(
        params.get("expected_object_identity"), "expected_object_identity"
    )
    obj = require_modifier_object(object_name, object_identity)
    before_fingerprint = _require_stack_fingerprint(params)
    ensure_modifier_stack_guard(transaction, obj, before_fingerprint)
    definition = params.get("definition")
    if not isinstance(definition, dict):
        raise AuthoringOperationError(
            "MODIFIER_DEFINITION_INVALID",
            "definition must be an object",
            kind="validation",
        )
    modifier_type = _require_text(definition.get("type"), "definition.type")
    if modifier_type not in SUPPORTED_MODIFIER_TYPES:
        raise AuthoringOperationError(
            "MODIFIER_TYPE_UNSUPPORTED",
            f"Unsupported Modifier type: {modifier_type}",
            kind="validation",
        )
    modifier_name = _require_text(definition.get("name"), "definition.name")
    if obj.modifiers.get(modifier_name) is not None:
        raise AuthoringOperationError(
            "MODIFIER_NAME_CONFLICT",
            f"Modifier name already exists on {object_name}: {modifier_name}",
            kind="conflict",
        )
    target_index = _stack_index(
        definition.get("stack_index"), maximum=len(obj.modifiers), allow_append=True
    )
    allowed = {
        "type",
        "name",
        "stack_index",
        *_COMMON_FIELDS,
        *_FIELD_SPECS[modifier_type],
    }
    if modifier_type == "BOOLEAN":
        allowed.add("operand")
        allowed.add("double_threshold")
    unknown = set(definition) - allowed
    if unknown:
        raise AuthoringOperationError(
            "MODIFIER_DEFINITION_INVALID",
            "definition contains unknown fields",
            kind="validation",
            details={"unknown": sorted(unknown)},
        )
    normalized = dict(_CREATE_DEFAULTS[modifier_type])
    supplied_fields = set(definition) - {"type", "name", "stack_index"}
    operand_object = None
    for field in supplied_fields:
        if field == "operand":
            operand_object = _resolve_boolean_operand(obj, definition[field])
            normalized[field] = _operand_summary(operand_object)
        elif definition[field] is not None:
            normalized[field] = _validate_public_value(modifier_type, field, definition[field])
    final = dict(normalized)
    if modifier_type == "BOOLEAN":
        if operand_object is None:
            raise AuthoringOperationError(
                "BOOLEAN_OPERAND_INVALID",
                "Boolean Modifier requires one exact Mesh operand",
                kind="validation",
            )
        final["operand_object"] = operand_object
    _validate_final_state(obj, modifier_type, final, supplied_fields=supplied_fields)
    transaction.ensure_capacity()

    modifier = None
    try:
        modifier = obj.modifiers.new(name=modifier_name, type=modifier_type)
        for field, value in normalized.items():
            if field == "operand":
                modifier.object = operand_object
            else:
                _write_public_setting(modifier, field, value)
        current_index = list(obj.modifiers).index(modifier)
        if current_index != target_index:
            obj.modifiers.move(current_index, target_index)
        bpy.context.view_layer.update()
    except Exception as exc:
        existing = obj.modifiers.get(modifier.name) if modifier is not None else None
        if (
            modifier is not None
            and existing is not None
            and session_identity("modifier", existing) == session_identity("modifier", modifier)
        ):
            obj.modifiers.remove(modifier)
        bpy.context.view_layer.update()
        remaining = obj.modifiers.get(modifier_name)
        if (
            modifier is not None
            and remaining is not None
            and session_identity("modifier", remaining) == session_identity("modifier", modifier)
        ):
            raise AuthoringOperationError(
                "MODIFIER_CREATE_RESTORE_FAILED",
                f"Failed to restore the stack after creating {modifier_name}",
                kind="internal",
            ) from exc
        raise AuthoringOperationError(
            "MODIFIER_CREATE_FAILED",
            f"Failed to create Modifier {modifier_name}: {exc}",
            kind="internal",
        ) from exc

    after_fingerprint = refresh_modifier_stack_guard(transaction, obj)
    refresh_structure_guard_if_present(transaction, "object", obj)
    transaction.record(
        ModifierCreateDelta(
            object_name,
            object_identity,
            modifier_name,
            session_identity("modifier", modifier),
            modifier_type,
            target_index,
            payload={"object": obj, "modifier": modifier},
        )
    )
    return {
        "transaction_id": transaction.transaction_id,
        "object": {"name": object_name, "identity": object_identity},
        "changed": True,
        "before_stack_fingerprint": before_fingerprint,
        "after_stack_fingerprint": after_fingerprint,
        "modifier": modifier_summary(obj, modifier, target_index),
        "stack": modifier_stack_summary(obj),
        "changes": [
            {"path": "stack", "before": None, "after": modifier_name},
            {"path": "stack_index", "before": None, "after": target_index},
        ],
        "delta_type": "modifier_create",
    }


def set_modifier(transaction: Transaction, params: dict[str, Any]) -> dict[str, Any]:
    object_name = _require_text(params.get("object_name"), "object_name")
    object_identity = _require_text(
        params.get("expected_object_identity"), "expected_object_identity"
    )
    obj = require_modifier_object(object_name, object_identity)
    before_fingerprint = _require_stack_fingerprint(params)
    ensure_modifier_stack_guard(transaction, obj, before_fingerprint)
    modifier_type = _require_text(params.get("expected_modifier_type"), "expected_modifier_type")
    modifier_name = _require_text(params.get("modifier_name"), "modifier_name")
    modifier_identity = _require_text(
        params.get("expected_modifier_identity"), "expected_modifier_identity"
    )
    modifier = _require_exact_modifier(
        obj,
        modifier_name=modifier_name,
        modifier_identity=modifier_identity,
        modifier_type=modifier_type,
        stack_index=params.get("expected_stack_index"),
    )
    before, normalized, _final = _validate_modifier_patch(
        obj, modifier, modifier_type, params.get("settings")
    )
    changed_fields = sorted(
        field for field, value in normalized.items() if not _same(before.get(field), value)
    )
    if not changed_fields:
        return {
            "transaction_id": transaction.transaction_id,
            "object": {"name": object_name, "identity": object_identity},
            "changed": False,
            "before_stack_fingerprint": before_fingerprint,
            "after_stack_fingerprint": before_fingerprint,
            "modifier": modifier_summary(obj, modifier, int(params["expected_stack_index"])),
            "stack": modifier_stack_summary(obj),
            "changes": [],
            "delta_type": None,
        }
    for field in changed_fields:
        rna_name = field if field in _COMMON_FIELDS else _FIELD_SPECS[modifier_type][field][0]
        if modifier_is_driven(obj, modifier, rna_name):
            raise AuthoringOperationError(
                "MODIFIER_PROPERTY_DRIVEN",
                f"Driven Modifier property cannot be changed: {modifier_name}.{field}",
                kind="precondition",
            )
        if _is_readonly(modifier, rna_name):
            raise AuthoringOperationError(
                "MODIFIER_PROPERTY_READONLY",
                f"Read-only Modifier property cannot be changed: {modifier_name}.{field}",
                kind="precondition",
            )
    transaction.ensure_capacity()
    try:
        for field in changed_fields:
            if field == "operand":
                modifier.object = _final["operand_object"]
            else:
                _write_public_setting(modifier, field, normalized[field])
        bpy.context.view_layer.update()
    except Exception as exc:
        restored = True
        try:
            for field in reversed(changed_fields):
                if field == "operand":
                    previous = before[field]
                    modifier.object = (
                        bpy.data.objects.get(previous["object_name"])
                        if previous is not None
                        else None
                    )
                else:
                    _write_public_setting(modifier, field, before[field])
            bpy.context.view_layer.update()
            restored = _verify_public_settings(
                modifier, {field: before[field] for field in changed_fields}
            )
        except Exception:
            restored = False
        if not restored:
            raise AuthoringOperationError(
                "MODIFIER_SETTINGS_RESTORE_FAILED",
                f"Failed to restore Modifier settings after an error: {modifier_name}",
                kind="internal",
            ) from exc
        raise AuthoringOperationError(
            "MODIFIER_SETTINGS_APPLY_FAILED",
            f"Failed to set Modifier {modifier_name}: {exc}",
            kind="internal",
        ) from exc

    after_fingerprint = refresh_modifier_stack_guard(transaction, obj)
    refresh_structure_guard_if_present(transaction, "object", obj)
    delta_before = {field: before[field] for field in changed_fields}
    delta_after = {field: normalized[field] for field in changed_fields}
    transaction.record(
        ModifierSettingsDelta(
            object_name,
            object_identity,
            modifier_name,
            modifier_identity,
            modifier_type,
            delta_before,
            delta_after,
        )
    )
    return {
        "transaction_id": transaction.transaction_id,
        "object": {"name": object_name, "identity": object_identity},
        "changed": True,
        "before_stack_fingerprint": before_fingerprint,
        "after_stack_fingerprint": after_fingerprint,
        "modifier": modifier_summary(obj, modifier, int(params["expected_stack_index"])),
        "stack": modifier_stack_summary(obj),
        "changes": [
            {"path": field, "before": delta_before[field], "after": delta_after[field]}
            for field in changed_fields
        ],
        "delta_type": "modifier_settings",
    }


def move_modifier(transaction: Transaction, params: dict[str, Any]) -> dict[str, Any]:
    object_name = _require_text(params.get("object_name"), "object_name")
    object_identity = _require_text(
        params.get("expected_object_identity"), "expected_object_identity"
    )
    obj = require_modifier_object(object_name, object_identity)
    before_fingerprint = _require_stack_fingerprint(params)
    ensure_modifier_stack_guard(transaction, obj, before_fingerprint)
    modifier_type = _require_text(params.get("expected_modifier_type"), "expected_modifier_type")
    modifier_name = _require_text(params.get("modifier_name"), "modifier_name")
    modifier_identity = _require_text(
        params.get("expected_modifier_identity"), "expected_modifier_identity"
    )
    before_index = params.get("expected_stack_index")
    modifier = _require_exact_modifier(
        obj,
        modifier_name=modifier_name,
        modifier_identity=modifier_identity,
        modifier_type=modifier_type,
        stack_index=before_index,
    )
    target_index = _stack_index(
        params.get("target_stack_index"), maximum=len(obj.modifiers), allow_append=False
    )
    if before_index == target_index:
        return {
            "transaction_id": transaction.transaction_id,
            "object": {"name": object_name, "identity": object_identity},
            "changed": False,
            "before_stack_fingerprint": before_fingerprint,
            "after_stack_fingerprint": before_fingerprint,
            "modifier": modifier_summary(obj, modifier, int(before_index)),
            "stack": modifier_stack_summary(obj),
            "changes": [],
            "delta_type": None,
        }
    transaction.ensure_capacity()
    try:
        obj.modifiers.move(int(before_index), target_index)
        bpy.context.view_layer.update()
    except Exception as exc:
        current_index = list(obj.modifiers).index(modifier)
        try:
            if current_index != before_index:
                obj.modifiers.move(current_index, int(before_index))
            bpy.context.view_layer.update()
        except Exception:
            raise AuthoringOperationError(
                "MODIFIER_MOVE_RESTORE_FAILED",
                f"Failed to restore Modifier order: {modifier_name}",
                kind="internal",
            ) from exc
        raise AuthoringOperationError(
            "MODIFIER_MOVE_FAILED",
            f"Failed to move Modifier {modifier_name}: {exc}",
            kind="internal",
        ) from exc
    after_fingerprint = refresh_modifier_stack_guard(transaction, obj)
    refresh_structure_guard_if_present(transaction, "object", obj)
    transaction.record(
        ModifierMoveDelta(
            object_name,
            object_identity,
            modifier_name,
            modifier_identity,
            int(before_index),
            target_index,
        )
    )
    return {
        "transaction_id": transaction.transaction_id,
        "object": {"name": object_name, "identity": object_identity},
        "changed": True,
        "before_stack_fingerprint": before_fingerprint,
        "after_stack_fingerprint": after_fingerprint,
        "modifier": modifier_summary(obj, modifier, target_index),
        "stack": modifier_stack_summary(obj),
        "changes": [{"path": "stack_index", "before": before_index, "after": target_index}],
        "delta_type": "modifier_move",
    }


def delete_modifier(transaction: Transaction, params: dict[str, Any]) -> dict[str, Any]:
    object_name = _require_text(params.get("object_name"), "object_name")
    object_identity = _require_text(
        params.get("expected_object_identity"), "expected_object_identity"
    )
    obj = require_modifier_object(object_name, object_identity)
    before_fingerprint = _require_stack_fingerprint(params)
    ensure_modifier_stack_guard(transaction, obj, before_fingerprint)
    modifier_type = _require_text(params.get("expected_modifier_type"), "expected_modifier_type")
    modifier_name = _require_text(params.get("modifier_name"), "modifier_name")
    modifier_identity = _require_text(
        params.get("expected_modifier_identity"), "expected_modifier_identity"
    )
    stack_index = params.get("expected_stack_index")
    modifier = _require_exact_modifier(
        obj,
        modifier_name=modifier_name,
        modifier_identity=modifier_identity,
        modifier_type=modifier_type,
        stack_index=stack_index,
    )
    transaction.ensure_capacity()
    before = {
        "show_viewport": bool(modifier.show_viewport),
        "show_render": bool(modifier.show_render),
    }
    try:
        modifier.show_viewport = False
        modifier.show_render = False
        _PENDING_DELETE_TOKENS[modifier_identity] = transaction.transaction_id
        bpy.context.view_layer.update()
    except Exception as exc:
        try:
            modifier.show_viewport = before["show_viewport"]
            modifier.show_render = before["show_render"]
            _PENDING_DELETE_TOKENS.pop(modifier_identity, None)
            bpy.context.view_layer.update()
        except Exception:
            raise AuthoringOperationError(
                "MODIFIER_DELETE_RESTORE_FAILED",
                f"Failed to restore pending deletion: {modifier_name}",
                kind="internal",
            ) from exc
        raise AuthoringOperationError(
            "MODIFIER_DELETE_FAILED",
            f"Failed to mark Modifier for deletion: {modifier_name}",
            kind="internal",
        ) from exc
    after_fingerprint = refresh_modifier_stack_guard(transaction, obj)
    refresh_structure_guard_if_present(transaction, "object", obj)
    transaction.record(
        ModifierDeleteDelta(
            object_name,
            object_identity,
            modifier_name,
            modifier_identity,
            modifier_type,
            int(stack_index),
            before,
            {"show_viewport": False, "show_render": False},
            payload={"object": obj, "modifier": modifier},
        )
    )
    return {
        "transaction_id": transaction.transaction_id,
        "object": {"name": object_name, "identity": object_identity},
        "changed": True,
        "before_stack_fingerprint": before_fingerprint,
        "after_stack_fingerprint": after_fingerprint,
        "modifier": modifier_summary(obj, modifier, int(stack_index)),
        "stack": modifier_stack_summary(obj),
        "changes": [
            {"path": "pending_delete", "before": False, "after": True},
            {
                "path": "show_render",
                "before": before["show_render"],
                "after": False,
            },
            {
                "path": "show_viewport",
                "before": before["show_viewport"],
                "after": False,
            },
        ],
        "delta_type": "modifier_delete",
    }


def set_modifier_state_compat(
    transaction: Transaction,
    obj: Any,
    modifier: Any,
    patch: dict[str, bool],
) -> tuple[dict[str, bool], dict[str, bool], list[str]]:
    fingerprint = modifier_stack_fingerprint(obj)
    guard = transaction.ensure_modifier_stack_guard(
        object_name=str(obj.name),
        object_identity=session_identity("object", obj),
        fingerprint=fingerprint,
    )
    if guard.expected_fingerprint != fingerprint:
        raise AuthoringOperationError(
            "MODIFIER_STACK_CONFLICT",
            f"Modifier stack changed outside the transaction: {obj.name}",
            kind="conflict",
        )
    before = {field: bool(getattr(modifier, field)) for field in patch}
    after = dict(patch)
    changed = sorted(field for field in after if before[field] != after[field])
    if not changed:
        return before, after, changed
    for field in changed:
        if modifier_is_driven(obj, modifier, field):
            raise AuthoringOperationError(
                "MODIFIER_PROPERTY_DRIVEN",
                f"Driven Modifier property cannot be changed: {modifier.name}.{field}",
                kind="precondition",
            )
        if _is_readonly(modifier, field):
            raise AuthoringOperationError(
                "MODIFIER_PROPERTY_READONLY",
                f"Read-only Modifier property cannot be changed: {modifier.name}.{field}",
                kind="precondition",
            )
    transaction.ensure_capacity()
    try:
        for field in changed:
            _write_public_setting(modifier, field, after[field])
        bpy.context.view_layer.update()
    except Exception as exc:
        restored = True
        try:
            for field in reversed(changed):
                _write_public_setting(modifier, field, before[field])
            bpy.context.view_layer.update()
            restored = _verify_public_settings(
                modifier, {field: before[field] for field in changed}
            )
        except Exception:
            restored = False
        if not restored:
            raise AuthoringOperationError(
                "MODIFIER_SETTINGS_RESTORE_FAILED",
                f"Failed to restore Modifier state: {modifier.name}",
                kind="internal",
            ) from exc
        raise AuthoringOperationError(
            "MODIFIER_SETTINGS_APPLY_FAILED",
            f"Failed to set Modifier state: {modifier.name}",
            kind="internal",
        ) from exc
    refresh_modifier_stack_guard(transaction, obj)
    refresh_structure_guard_if_present(transaction, "object", obj)
    transaction.record(
        ModifierStateDelta(
            str(obj.name),
            session_identity("object", obj),
            str(modifier.name),
            session_identity("modifier", modifier),
            {field: before[field] for field in changed},
            {field: after[field] for field in changed},
        )
    )
    return before, after, changed


def _require_delta_modifier(delta: Any) -> tuple[Any, Any]:
    obj = require_modifier_object(delta.object_name, delta.object_identity)
    modifier = obj.modifiers.get(delta.modifier_name)
    if modifier is None or session_identity("modifier", modifier) != delta.modifier_identity:
        raise TransactionModelError(
            "MODIFIER_STACK_CONFLICT",
            f"Modifier identity changed during restoration: {delta.modifier_name}",
        )
    return obj, modifier


def restore_modifier_delta(delta: Any) -> dict[str, Any]:
    if isinstance(delta, ModifierStateDelta):
        _obj, modifier = _require_delta_modifier(delta)
        for field, value in delta.before.items():
            _write_public_setting(modifier, field, value)
        return {"kind": "modifier_state", "modifier_name": delta.modifier_name}
    if isinstance(delta, ModifierSettingsDelta):
        _obj, modifier = _require_delta_modifier(delta)
        for field, value in reversed(list(delta.before.items())):
            if field == "operand":
                modifier.object = (
                    bpy.data.objects.get(value["object_name"]) if value is not None else None
                )
            else:
                _write_public_setting(modifier, field, value)
        return {"kind": "modifier_settings", "modifier_name": delta.modifier_name}
    if isinstance(delta, ModifierMoveDelta):
        obj, modifier = _require_delta_modifier(delta)
        current = list(obj.modifiers).index(modifier)
        obj.modifiers.move(current, delta.before_index)
        return {"kind": "modifier_move", "modifier_name": delta.modifier_name}
    if isinstance(delta, ModifierDeleteDelta):
        _obj, modifier = _require_delta_modifier(delta)
        _PENDING_DELETE_TOKENS.pop(delta.modifier_identity, None)
        modifier.show_viewport = delta.before["show_viewport"]
        modifier.show_render = delta.before["show_render"]
        return {"kind": "modifier_delete", "modifier_name": delta.modifier_name}
    if isinstance(delta, ModifierCreateDelta):
        obj, modifier = _require_delta_modifier(delta)
        obj.modifiers.remove(modifier)
        return {"kind": "modifier_create", "modifier_name": delta.modifier_name}
    raise TransactionModelError(
        "TRANSACTION_DELTA_INVALID",
        f"Unsupported Modifier delta: {type(delta).__name__}",
    )


def finalize_modifier_delta(delta: Any) -> dict[str, Any] | None:
    if not isinstance(delta, ModifierDeleteDelta):
        return None
    obj, modifier = _require_delta_modifier(delta)
    _PENDING_DELETE_TOKENS.pop(delta.modifier_identity, None)
    obj.modifiers.remove(modifier)
    return {"kind": "modifier_delete", "modifier_name": delta.modifier_name}


def adopt_modifier_delta_for_native_save(
    delta: Any,
    transaction_id: str,
) -> dict[str, Any] | None:
    """Finalize an untouched pending delete, otherwise preserve the user's state."""

    if not isinstance(delta, ModifierDeleteDelta):
        return None
    obj = delta.payload.get("object")
    modifier = delta.payload.get("modifier")
    token_matches = _PENDING_DELETE_TOKENS.get(delta.modifier_identity) == transaction_id
    identity_matches = (
        obj is not None
        and modifier is not None
        and bpy.data.objects.get(delta.object_name) is obj
        and obj.modifiers.get(delta.modifier_name) is modifier
        and session_identity("object", obj) == delta.object_identity
        and session_identity("modifier", modifier) == delta.modifier_identity
    )
    state_matches = identity_matches and all(
        bool(getattr(modifier, field)) == value for field, value in delta.after.items()
    )
    _PENDING_DELETE_TOKENS.pop(delta.modifier_identity, None)
    if token_matches and state_matches:
        obj.modifiers.remove(modifier)
        return {
            "kind": "modifier_delete",
            "modifier_name": delta.modifier_name,
            "action": "finalized_native_save",
        }
    return {
        "kind": "modifier_delete",
        "modifier_name": delta.modifier_name,
        "action": "preserved_user_state",
    }
