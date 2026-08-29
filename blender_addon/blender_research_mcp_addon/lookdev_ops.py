"""Resolve, read, and restore allow-listed LookDev transaction properties."""

from __future__ import annotations

from typing import Any

import bpy

from .context_ops import ContextOperationError
from .transaction_model import (
    MaterialInputDelta,
    ModifierStateDelta,
    PropertyRef,
    PropertyValue,
    ScaleDelta,
    ShapeKeyDelta,
    TransactionDelta,
    VisibilityDelta,
)

AXIS_INDEX = {"x": 0, "y": 1, "z": 2}
MODIFIER_LIMIT = 256
SHAPE_KEY_LIMIT = 256
MATERIAL_SLOT_LIMIT = 64


def session_identity(kind: str, value: Any) -> str:
    return f"{kind}:{value.as_pointer():x}"


def _library_path(value: Any) -> str | None:
    library = getattr(value, "library", None)
    return library.filepath if library is not None else None


def shape_key_is_driven(shape_keys: Any, key_block: Any) -> bool:
    animation_data = shape_keys.animation_data
    if animation_data is None:
        return False
    data_path = key_block.path_from_id("value")
    return any(driver.data_path == data_path for driver in animation_data.drivers)


def inspect_object_lookdev(object_name: str) -> dict[str, Any]:
    obj = bpy.data.objects.get(object_name)
    if obj is None:
        raise ContextOperationError(
            "OBJECT_NOT_FOUND",
            f"Object does not exist: {object_name}",
            kind="not_found",
        )
    warnings: list[dict[str, Any]] = []
    modifiers = list(obj.modifiers)
    if len(modifiers) > MODIFIER_LIMIT:
        warnings.append(
            {
                "code": "LOOKDEV_DIAGNOSTICS_TRUNCATED",
                "section": "modifiers",
                "limit": MODIFIER_LIMIT,
                "count": len(modifiers),
            }
        )
    modifier_results = [
        {
            "name": modifier.name,
            "type": modifier.type,
            "session_identity": session_identity("modifier", modifier),
            "show_viewport": bool(modifier.show_viewport),
            "show_render": bool(modifier.show_render),
        }
        for modifier in modifiers[:MODIFIER_LIMIT]
    ]

    shape_key_results: list[dict[str, Any]] = []
    shape_keys = getattr(obj.data, "shape_keys", None)
    key_blocks = list(shape_keys.key_blocks)[1:] if shape_keys is not None else []
    if len(key_blocks) > SHAPE_KEY_LIMIT:
        warnings.append(
            {
                "code": "LOOKDEV_DIAGNOSTICS_TRUNCATED",
                "section": "shape_keys",
                "limit": SHAPE_KEY_LIMIT,
                "count": len(key_blocks),
            }
        )
    for key_block in key_blocks[:SHAPE_KEY_LIMIT]:
        shape_key_results.append(
            {
                "name": key_block.name,
                "session_identity": session_identity("shape_key", key_block),
                "value": float(key_block.value),
                "slider_min": float(key_block.slider_min),
                "slider_max": float(key_block.slider_max),
                "mute": bool(key_block.mute),
                "driven": shape_key_is_driven(shape_keys, key_block),
                "relative_key": key_block.relative_key.name,
            }
        )

    slots = list(obj.material_slots)
    if len(slots) > MATERIAL_SLOT_LIMIT:
        warnings.append(
            {
                "code": "LOOKDEV_DIAGNOSTICS_TRUNCATED",
                "section": "material_slots",
                "limit": MATERIAL_SLOT_LIMIT,
                "count": len(slots),
            }
        )
    material_slots = []
    for index, slot in enumerate(slots[:MATERIAL_SLOT_LIMIT]):
        material = slot.material
        material_slots.append(
            {
                "index": index,
                "name": material.name if material is not None else None,
                "session_identity": (
                    session_identity("material", material) if material is not None else None
                ),
                "users": int(material.users) if material is not None else 0,
                "library": _library_path(material) if material is not None else None,
                "use_nodes": bool(material.use_nodes) if material is not None else False,
            }
        )

    return {
        "name": obj.name,
        "type": obj.type,
        "session_identity": session_identity("object", obj),
        "library": _library_path(obj),
        "data_library": _library_path(obj.data),
        "visibility": {
            "hide_viewport": bool(obj.hide_viewport),
            "hide_render": bool(obj.hide_render),
            "visible": bool(obj.visible_get()),
        },
        "modifiers": modifier_results,
        "shape_keys": shape_key_results,
        "shape_keys_library": _library_path(shape_keys) if shape_keys is not None else None,
        "material_slots": material_slots,
        "counts": {
            "modifiers": len(modifiers),
            "shape_keys": len(key_blocks),
            "material_slots": len(slots),
        },
        "warnings": warnings,
    }


def _require_identity(kind: str, value: Any, expected: str) -> None:
    if session_identity(kind, value) != expected:
        raise ContextOperationError(
            "TARGET_IDENTITY_CONFLICT",
            f"The {kind} target was replaced after it was inspected",
            kind="conflict",
            details={"expected": expected, "actual": session_identity(kind, value)},
        )


def require_object(name: str, expected_identity: str) -> Any:
    obj = bpy.data.objects.get(name)
    if obj is None:
        raise ContextOperationError(
            "OBJECT_NOT_FOUND",
            f"Object does not exist: {name}",
            kind="conflict",
        )
    _require_identity("object", obj, expected_identity)
    return obj


def require_modifier(
    object_name: str,
    object_identity: str,
    modifier_name: str,
    modifier_identity: str,
) -> tuple[Any, Any]:
    obj = require_object(object_name, object_identity)
    modifier = obj.modifiers.get(modifier_name)
    if modifier is None:
        raise ContextOperationError(
            "MODIFIER_NOT_FOUND",
            f"Modifier does not exist: {object_name}.{modifier_name}",
            kind="conflict",
        )
    _require_identity("modifier", modifier, modifier_identity)
    return obj, modifier


def require_shape_key(
    object_name: str,
    object_identity: str,
    shape_key_name: str,
    shape_key_identity: str,
) -> tuple[Any, Any]:
    obj = require_object(object_name, object_identity)
    shape_keys = getattr(obj.data, "shape_keys", None)
    key_block = shape_keys.key_blocks.get(shape_key_name) if shape_keys is not None else None
    if key_block is None:
        raise ContextOperationError(
            "SHAPE_KEY_NOT_FOUND",
            f"Shape key does not exist: {object_name}.{shape_key_name}",
            kind="conflict",
        )
    _require_identity("shape_key", key_block, shape_key_identity)
    return obj, key_block


def require_material_socket(target: tuple[str, ...]) -> tuple[Any, Any, Any, Any]:
    (
        object_name,
        object_identity,
        raw_slot_index,
        material_name,
        material_identity,
        node_name,
        node_identity,
        socket_identifier,
        socket_identity,
        _socket_kind,
    ) = target
    obj = require_object(object_name, object_identity)
    slot_index = int(raw_slot_index)
    if not 0 <= slot_index < len(obj.material_slots):
        raise ContextOperationError(
            "MATERIAL_SLOT_NOT_FOUND",
            f"Material slot does not exist: {object_name}[{slot_index}]",
            kind="conflict",
        )
    material = obj.material_slots[slot_index].material
    if material is None or material.name != material_name:
        raise ContextOperationError(
            "MATERIAL_IDENTITY_CONFLICT",
            f"Material slot {slot_index} no longer contains {material_name}",
            kind="conflict",
        )
    _require_identity("material", material, material_identity)
    node_tree = material.node_tree
    node = node_tree.nodes.get(node_name) if node_tree is not None else None
    if node is None:
        raise ContextOperationError(
            "MATERIAL_NODE_NOT_FOUND",
            f"Material node does not exist: {material_name}.{node_name}",
            kind="conflict",
        )
    _require_identity("node", node, node_identity)
    socket = next(
        (candidate for candidate in node.inputs if candidate.identifier == socket_identifier),
        None,
    )
    if socket is None:
        raise ContextOperationError(
            "MATERIAL_SOCKET_NOT_FOUND",
            f"Material socket does not exist: {material_name}.{node_name}.{socket_identifier}",
            kind="conflict",
        )
    _require_identity("socket", socket, socket_identity)
    return obj, material, node, socket


def read_property(reference: PropertyRef) -> PropertyValue:
    if reference.kind in {"object_scale", "object_visibility"}:
        obj = require_object(*reference.target)
        if reference.kind == "object_scale":
            return float(obj.scale[AXIS_INDEX[reference.attribute]])
        return bool(getattr(obj, reference.attribute))
    if reference.kind == "modifier_state":
        _obj, modifier = require_modifier(*reference.target)
        return bool(getattr(modifier, reference.attribute))
    if reference.kind == "shape_key_value":
        _obj, key_block = require_shape_key(*reference.target)
        return float(key_block.value)
    if reference.kind == "material_input":
        _obj, _material, _node, socket = require_material_socket(reference.target)
        value = socket.default_value
        if reference.target[-1] in {"VECTOR", "COLOR"}:
            return tuple(float(component) for component in value)
        if reference.target[-1] == "BOOLEAN":
            return bool(value)
        if reference.target[-1] == "INT":
            return int(value)
        return float(value)
    raise ContextOperationError(
        "TRANSACTION_DELTA_INVALID",
        f"Unsupported transaction property kind: {reference.kind}",
        kind="internal",
    )


def restore_delta(delta: TransactionDelta) -> dict[str, Any]:
    if isinstance(delta, ScaleDelta):
        obj = require_object(delta.object_name, delta.object_identity)
        for axis, value in delta.before.items():
            obj.scale[AXIS_INDEX[axis]] = value
        return {"kind": "object_scale", "object_name": delta.object_name, "scale": delta.before}
    if isinstance(delta, VisibilityDelta):
        obj = require_object(delta.object_name, delta.object_identity)
        for attribute, value in delta.before.items():
            setattr(obj, attribute, value)
        return {
            "kind": "object_visibility",
            "object_name": delta.object_name,
            "values": delta.before,
        }
    if isinstance(delta, ModifierStateDelta):
        _obj, modifier = require_modifier(
            delta.object_name,
            delta.object_identity,
            delta.modifier_name,
            delta.modifier_identity,
        )
        for attribute, value in delta.before.items():
            setattr(modifier, attribute, value)
        return {
            "kind": "modifier_state",
            "object_name": delta.object_name,
            "modifier_name": delta.modifier_name,
            "values": delta.before,
        }
    if isinstance(delta, ShapeKeyDelta):
        _obj, key_block = require_shape_key(
            delta.object_name,
            delta.object_identity,
            delta.shape_key_name,
            delta.shape_key_identity,
        )
        key_block.value = delta.before
        return {
            "kind": "shape_key_value",
            "object_name": delta.object_name,
            "shape_key_name": delta.shape_key_name,
            "value": delta.before,
        }
    if isinstance(delta, MaterialInputDelta):
        _obj, _material, _node, socket = require_material_socket(
            (
                delta.object_name,
                delta.object_identity,
                str(delta.material_slot_index),
                delta.material_name,
                delta.material_identity,
                delta.node_name,
                delta.node_identity,
                delta.socket_identifier,
                delta.socket_identity,
                delta.socket_kind,
            )
        )
        socket.default_value = delta.before
        return {
            "kind": "material_input",
            "object_name": delta.object_name,
            "material_name": delta.material_name,
            "node_name": delta.node_name,
            "socket_identifier": delta.socket_identifier,
            "value": delta.before,
        }
    raise ContextOperationError(
        "TRANSACTION_DELTA_INVALID",
        f"Unsupported transaction delta: {type(delta).__name__}",
        kind="internal",
    )
