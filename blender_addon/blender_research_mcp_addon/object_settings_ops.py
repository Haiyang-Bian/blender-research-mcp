"""Closed typed object, Light, and Camera property operations."""

from __future__ import annotations

import math
from typing import Any

import bpy

from .authoring_ops import AuthoringOperationError
from .lookdev_ops import require_object, session_identity
from .structural_ops import refresh_structure_guard_if_present
from .transaction_model import (
    ObjectDataDelta,
    ObjectTransformDelta,
    PropertyValue,
    Transaction,
    VisibilityDelta,
    values_equal,
)

AXES = {"x": 0, "y": 1, "z": 2}
PATCH_ORDER = {"transform": 0, "visibility": 1, "light": 2, "camera": 2}
LIGHT_FIELDS = {
    "energy",
    "color",
    "radius",
    "shape",
    "size",
    "size_y",
    "spot_size_degrees",
    "spot_blend",
    "angle_degrees",
}
CAMERA_FIELDS = {
    "lens",
    "sensor_width",
    "clip_start",
    "clip_end",
    "ortho_scale",
    "shift_x",
    "shift_y",
}


def _linear_channel(channel: float) -> float:
    return channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4


def _srgb_channel(channel: float) -> float:
    channel = min(1.0, max(0.0, channel))
    return 12.92 * channel if channel <= 0.0031308 else 1.055 * channel ** (1 / 2.4) - 0.055


def hex_to_linear_rgb(value: str) -> tuple[float, float, float]:
    if len(value) != 7 or not value.startswith("#"):
        raise AuthoringOperationError(
            "OBJECT_SETTING_INVALID",
            "Light colors must use #RRGGBB sRGB",
            kind="validation",
        )
    try:
        channels = [int(value[index : index + 2], 16) / 255.0 for index in (1, 3, 5)]
    except ValueError as exc:
        raise AuthoringOperationError(
            "OBJECT_SETTING_INVALID",
            "Light colors must use #RRGGBB sRGB",
            kind="validation",
        ) from exc
    return tuple(_linear_channel(channel) for channel in channels)  # type: ignore[return-value]


def linear_rgb_to_hex(values: Any) -> str:
    channels = [round(_srgb_channel(float(channel)) * 255) for channel in values[:3]]
    return "#" + "".join(f"{channel:02X}" for channel in channels)


def _finite_number(value: Any, path: str, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise AuthoringOperationError(
            "OBJECT_SETTING_INVALID",
            f"{path} must be a JSON number",
            kind="validation",
        )
    result = float(value)
    if not math.isfinite(result) or not minimum <= result <= maximum:
        raise AuthoringOperationError(
            "OBJECT_SETTING_INVALID",
            f"{path} must be between {minimum} and {maximum}",
            kind="validation",
        )
    return result


def _data_kind(data: Any) -> str:
    return data.__class__.__name__.lower()


def object_data_summary(obj: Any) -> dict[str, Any] | None:
    data = obj.data
    if data is None:
        return None
    result: dict[str, Any] = {
        "name": data.name,
        "kind": _data_kind(data),
        "session_identity": session_identity(_data_kind(data), data),
        "users": int(data.users),
        "shared": int(data.users) > 1,
        "library": data.library.filepath if data.library else None,
        "writable": data.library is None,
    }
    if obj.type == "LIGHT":
        settings: dict[str, Any] = {
            "light_type": data.type,
            "energy": float(data.energy),
            "color": linear_rgb_to_hex(data.color),
            "color_linear": [float(value) for value in data.color],
        }
        writable: dict[str, Any] = {
            "energy": {"minimum": 0.0, "maximum": 10_000_000.0},
            "color": {"format": "#RRGGBB_sRGB"},
        }
        if data.type in {"POINT", "SPOT"}:
            settings["radius"] = float(data.shadow_soft_size)
            writable["radius"] = {"minimum": 0.0, "maximum": 100_000.0}
        if data.type == "AREA":
            settings.update(
                {"shape": data.shape, "size": float(data.size), "size_y": float(data.size_y)}
            )
            writable.update(
                {
                    "shape": {"enum": ["SQUARE", "RECTANGLE", "DISK", "ELLIPSE"]},
                    "size": {"exclusive_minimum": 0.0, "maximum": 100_000.0},
                    "size_y": {"exclusive_minimum": 0.0, "maximum": 100_000.0},
                }
            )
        if data.type == "SPOT":
            settings.update(
                {
                    "spot_size_degrees": math.degrees(float(data.spot_size)),
                    "spot_blend": float(data.spot_blend),
                }
            )
            writable.update(
                {
                    "spot_size_degrees": {"exclusive_minimum": 0.0, "maximum": 179.0},
                    "spot_blend": {"minimum": 0.0, "maximum": 1.0},
                }
            )
        if data.type == "SUN":
            settings["angle_degrees"] = math.degrees(float(data.angle))
            writable["angle_degrees"] = {"minimum": 0.0, "maximum": 180.0}
        result.update({"type": "light", "settings": settings, "writable_fields": writable})
    elif obj.type == "CAMERA":
        settings = {
            "camera_type": data.type,
            "clip_start": float(data.clip_start),
            "clip_end": float(data.clip_end),
            "shift_x": float(data.shift_x),
            "shift_y": float(data.shift_y),
        }
        writable = {
            "clip_start": {"minimum": 0.00001, "maximum": 1_000_000.0},
            "clip_end": {"minimum": 0.0001, "maximum": 10_000_000.0},
            "shift_x": {"minimum": -10.0, "maximum": 10.0},
            "shift_y": {"minimum": -10.0, "maximum": 10.0},
        }
        if data.type == "PERSP":
            settings.update({"lens": float(data.lens), "sensor_width": float(data.sensor_width)})
            writable.update(
                {
                    "lens": {"minimum": 1.0, "maximum": 250.0},
                    "sensor_width": {"minimum": 1.0, "maximum": 100.0},
                }
            )
        elif data.type == "ORTHO":
            settings["ortho_scale"] = float(data.ortho_scale)
            writable["ortho_scale"] = {"minimum": 0.000001, "maximum": 1_000_000.0}
        else:
            result["writable"] = False
            result["writable_reason"] = "camera_type_unsupported"
            writable = {}
        result.update({"type": "camera", "settings": settings, "writable_fields": writable})
    return result


def _require_object(params: dict[str, Any]) -> Any:
    object_name = params.get("object_name")
    identity = params.get("expected_object_identity")
    if not isinstance(object_name, str) or not object_name:
        raise AuthoringOperationError(
            "OBJECT_NAME_INVALID", "object_name must be non-empty", kind="validation"
        )
    if not isinstance(identity, str) or not identity:
        raise AuthoringOperationError(
            "TARGET_IDENTITY_REQUIRED",
            "expected_object_identity must be non-empty",
            kind="validation",
        )
    obj = require_object(object_name, identity)
    if obj.library is not None and obj.override_library is None:
        raise AuthoringOperationError("OBJECT_LINKED", f"Linked object is not writable: {obj.name}")
    return obj


def _ordered_patches(params: dict[str, Any]) -> dict[str, dict[str, Any]]:
    raw = params.get("patches")
    if not isinstance(raw, list) or not 1 <= len(raw) <= 4:
        raise AuthoringOperationError(
            "OBJECT_SETTINGS_EMPTY",
            "patches must contain between one and four settings",
            kind="validation",
        )
    patches: dict[str, dict[str, Any]] = {}
    for patch in raw:
        if not isinstance(patch, dict) or patch.get("type") not in PATCH_ORDER:
            raise AuthoringOperationError(
                "OBJECT_SETTING_INVALID", "every patch requires a supported type", kind="validation"
            )
        patch_type = str(patch["type"])
        if patch_type in patches:
            raise AuthoringOperationError(
                "OBJECT_SETTING_DUPLICATE",
                f"duplicate object setting patch: {patch_type}",
                kind="validation",
            )
        patches[patch_type] = patch
    if "light" in patches and "camera" in patches:
        raise AuthoringOperationError(
            "OBJECT_TYPE_MISMATCH",
            "one object cannot accept both Light and Camera settings",
            kind="validation",
        )
    return dict(sorted(patches.items(), key=lambda item: PATCH_ORDER[item[0]]))


def _prepare_transform(
    obj: Any, patch: dict[str, Any]
) -> tuple[ObjectTransformDelta | None, list[dict[str, Any]]]:
    allowed = {"type", "location", "rotation_euler_degrees", "scale"}
    supplied = set(patch) - {"type"}
    if not supplied or supplied - (allowed - {"type"}):
        raise AuthoringOperationError(
            "OBJECT_SETTING_INVALID",
            "transform requires supported setting fields",
            kind="validation",
        )
    before: dict[str, dict[str, float]] = {}
    after: dict[str, dict[str, float]] = {}
    changes: list[dict[str, Any]] = []
    for public_channel, internal_channel, minimum, maximum in (
        ("location", "location", -1_000_000.0, 1_000_000.0),
        ("rotation_euler_degrees", "rotation_euler", -360_000.0, 360_000.0),
        ("scale", "scale", 0.000001, 1000.0),
    ):
        raw = patch.get(public_channel)
        if raw is None:
            continue
        if not isinstance(raw, dict) or not raw or set(raw) - set(AXES):
            raise AuthoringOperationError(
                "OBJECT_SETTING_INVALID",
                f"{public_channel} requires one or more x/y/z fields",
                kind="validation",
            )
        target = getattr(obj, internal_channel)
        for axis, raw_value in raw.items():
            public_after = _finite_number(raw_value, f"{public_channel}.{axis}", minimum, maximum)
            internal_after = (
                math.radians(public_after)
                if public_channel == "rotation_euler_degrees"
                else public_after
            )
            internal_before = float(target[AXES[axis]])
            if values_equal(internal_before, internal_after):
                continue
            before.setdefault(internal_channel, {})[axis] = internal_before
            after.setdefault(internal_channel, {})[axis] = internal_after
            public_before = (
                math.degrees(internal_before)
                if public_channel == "rotation_euler_degrees"
                else internal_before
            )
            changes.append(
                {
                    "path": f"transform.{public_channel}.{axis}",
                    "before": public_before,
                    "after": public_after,
                }
            )
    if not before:
        return None, changes
    return (
        ObjectTransformDelta(
            object_name=obj.name,
            object_identity=session_identity("object", obj),
            before=before,
            after=after,
        ),
        changes,
    )


def _prepare_visibility(
    obj: Any, patch: dict[str, Any]
) -> tuple[VisibilityDelta | None, list[dict[str, Any]]]:
    allowed = {"type", "hide_viewport", "hide_render"}
    supplied = set(patch) - {"type"}
    if not supplied or supplied - (allowed - {"type"}):
        raise AuthoringOperationError(
            "OBJECT_SETTING_INVALID", "visibility requires supported fields", kind="validation"
        )
    if any(type(patch[field]) is not bool for field in supplied):
        raise AuthoringOperationError(
            "OBJECT_SETTING_INVALID", "visibility values must be booleans", kind="validation"
        )
    before: dict[str, bool] = {}
    after: dict[str, bool] = {}
    changes: list[dict[str, Any]] = []
    for field in sorted(supplied):
        old = bool(getattr(obj, field))
        new = bool(patch[field])
        if old == new:
            continue
        before[field] = old
        after[field] = new
        changes.append({"path": f"visibility.{field}", "before": old, "after": new})
    if after.get("hide_viewport") is True and (
        obj.select_get() or bpy.context.view_layer.objects.active is obj
    ):
        raise AuthoringOperationError(
            "VISIBILITY_CONTEXT_CONFLICT",
            "Cannot hide the active or selected object without changing user context",
        )
    if not before:
        return None, changes
    return (
        VisibilityDelta(
            object_name=obj.name,
            object_identity=session_identity("object", obj),
            before=before,
            after=after,
        ),
        changes,
    )


def _require_data(obj: Any, patch: dict[str, Any], expected_object_type: str) -> tuple[Any, int]:
    if obj.type != expected_object_type or obj.data is None:
        raise AuthoringOperationError(
            "OBJECT_TYPE_MISMATCH",
            f"{patch['type']} settings require a {expected_object_type} object",
            kind="validation",
        )
    data = obj.data
    expected_identity = patch.get("expected_data_identity")
    actual_identity = session_identity(_data_kind(data), data)
    if not isinstance(expected_identity, str) or actual_identity != expected_identity:
        raise AuthoringOperationError(
            "OBJECT_DATA_IDENTITY_MISMATCH",
            f"Object data identity changed: {obj.name}",
            kind="conflict",
            details={"expected": expected_identity, "actual": actual_identity},
        )
    expected_users = patch.get("expected_data_users")
    actual_users = int(data.users)
    if (
        isinstance(expected_users, bool)
        or not isinstance(expected_users, int)
        or expected_users < 1
    ):
        raise AuthoringOperationError(
            "OBJECT_SETTING_INVALID",
            "expected_data_users must be a positive integer",
            kind="validation",
        )
    if actual_users != expected_users:
        raise AuthoringOperationError(
            "OBJECT_DATA_USERS_MISMATCH",
            "Object data user count changed after inspection",
            kind="conflict",
            details={"expected": expected_users, "actual": actual_users},
        )
    if actual_users > 1 and patch.get("allow_shared_data") is not True:
        raise AuthoringOperationError(
            "SHARED_OBJECT_DATA_CONFIRMATION_REQUIRED",
            "Object data settings affect every object sharing this data-block",
            details={"users": actual_users},
        )
    if data.library is not None:
        raise AuthoringOperationError(
            "OBJECT_DATA_LINKED", f"Linked object data is not writable: {data.name}"
        )
    return data, actual_users


def _prepare_light(
    obj: Any, patch: dict[str, Any]
) -> tuple[ObjectDataDelta | None, list[dict[str, Any]]]:
    allowed_meta = {
        "type",
        "expected_data_identity",
        "expected_data_users",
        "expected_light_type",
        "allow_shared_data",
    }
    supplied = set(patch) - allowed_meta
    if not supplied or supplied - LIGHT_FIELDS:
        raise AuthoringOperationError(
            "OBJECT_SETTING_INVALID", "light requires supported setting fields", kind="validation"
        )
    data, users = _require_data(obj, patch, "LIGHT")
    expected_type = patch.get("expected_light_type")
    if expected_type not in {"POINT", "SUN", "SPOT", "AREA"} or data.type != expected_type:
        raise AuthoringOperationError(
            "OBJECT_TYPE_MISMATCH",
            "Light type changed after inspection",
            kind="conflict",
            details={"expected": expected_type, "actual": data.type},
        )
    allowed = {
        "POINT": {"energy", "color", "radius"},
        "SPOT": {"energy", "color", "radius", "spot_size_degrees", "spot_blend"},
        "SUN": {"energy", "color", "angle_degrees"},
        "AREA": {"energy", "color", "shape", "size", "size_y"},
    }[data.type]
    unsupported = supplied - allowed
    if unsupported:
        raise AuthoringOperationError(
            "OBJECT_SETTING_INVALID",
            f"{data.type} light does not support: {', '.join(sorted(unsupported))}",
            kind="validation",
        )
    before: dict[str, PropertyValue] = {}
    after: dict[str, PropertyValue] = {}
    changes: list[dict[str, Any]] = []
    for field in sorted(supplied):
        if field == "energy":
            old, new = (
                float(data.energy),
                _finite_number(patch[field], "light.energy", 0, 10_000_000),
            )
            public_old, public_new = old, new
        elif field == "color":
            if not isinstance(patch[field], str):
                raise AuthoringOperationError(
                    "OBJECT_SETTING_INVALID", "light.color must use #RRGGBB", kind="validation"
                )
            old, new = tuple(float(value) for value in data.color), hex_to_linear_rgb(patch[field])
            public_old, public_new = linear_rgb_to_hex(old), patch[field].upper()
        elif field == "radius":
            old = float(data.shadow_soft_size)
            new = _finite_number(patch[field], "light.radius", 0, 100_000)
            public_old, public_new = old, new
        elif field == "shape":
            if patch[field] not in {"SQUARE", "RECTANGLE", "DISK", "ELLIPSE"}:
                raise AuthoringOperationError(
                    "OBJECT_SETTING_INVALID", "unsupported Area shape", kind="validation"
                )
            old, new = str(data.shape), str(patch[field])
            public_old, public_new = old, new
        elif field in {"size", "size_y"}:
            old = float(getattr(data, field))
            new = _finite_number(patch[field], f"light.{field}", 0.000001, 100_000)
            public_old, public_new = old, new
        elif field == "spot_size_degrees":
            old = float(data.spot_size)
            public_new = _finite_number(patch[field], "light.spot_size_degrees", 0.000001, 179)
            new = math.radians(public_new)
            public_old = math.degrees(old)
        elif field == "spot_blend":
            old = float(data.spot_blend)
            new = _finite_number(patch[field], "light.spot_blend", 0, 1)
            public_old, public_new = old, new
        else:
            old = float(data.angle)
            public_new = _finite_number(patch[field], "light.angle_degrees", 0, 180)
            new = math.radians(public_new)
            public_old = math.degrees(old)
        if values_equal(old, new):
            continue
        before[field] = old
        after[field] = new
        changes.append({"path": f"light.{field}", "before": public_old, "after": public_new})
    resulting_shape = str(after.get("shape", data.shape))
    if "size_y" in supplied and resulting_shape not in {"RECTANGLE", "ELLIPSE"}:
        raise AuthoringOperationError(
            "OBJECT_SETTING_INVALID",
            "light.size_y requires RECTANGLE or ELLIPSE Area shape",
            kind="validation",
        )
    if not before:
        return None, changes
    return (
        ObjectDataDelta(
            object_name=obj.name,
            object_identity=session_identity("object", obj),
            data_name=data.name,
            data_identity=session_identity(_data_kind(data), data),
            data_kind="light",
            expected_users=users,
            before=before,
            after=after,
        ),
        changes,
    )


def _prepare_camera(
    obj: Any, patch: dict[str, Any]
) -> tuple[ObjectDataDelta | None, list[dict[str, Any]]]:
    allowed_meta = {
        "type",
        "expected_data_identity",
        "expected_data_users",
        "expected_camera_type",
        "allow_shared_data",
    }
    supplied = set(patch) - allowed_meta
    if not supplied or supplied - CAMERA_FIELDS:
        raise AuthoringOperationError(
            "OBJECT_SETTING_INVALID", "camera requires supported setting fields", kind="validation"
        )
    data, users = _require_data(obj, patch, "CAMERA")
    expected_type = patch.get("expected_camera_type")
    if expected_type not in {"PERSP", "ORTHO"} or data.type != expected_type:
        raise AuthoringOperationError(
            "OBJECT_TYPE_MISMATCH",
            "Camera type changed or is unsupported",
            kind="conflict",
            details={"expected": expected_type, "actual": data.type},
        )
    if data.type == "PERSP" and "ortho_scale" in supplied:
        raise AuthoringOperationError(
            "OBJECT_SETTING_INVALID", "ortho_scale requires an ORTHO camera", kind="validation"
        )
    if data.type == "ORTHO" and supplied & {"lens", "sensor_width"}:
        raise AuthoringOperationError(
            "OBJECT_SETTING_INVALID",
            "lens and sensor_width require a PERSP camera",
            kind="validation",
        )
    ranges = {
        "lens": (1.0, 250.0),
        "sensor_width": (1.0, 100.0),
        "clip_start": (0.00001, 1_000_000.0),
        "clip_end": (0.0001, 10_000_000.0),
        "ortho_scale": (0.000001, 1_000_000.0),
        "shift_x": (-10.0, 10.0),
        "shift_y": (-10.0, 10.0),
    }
    before: dict[str, PropertyValue] = {}
    after: dict[str, PropertyValue] = {}
    changes: list[dict[str, Any]] = []
    for field in sorted(supplied):
        old = float(getattr(data, field))
        new = _finite_number(patch[field], f"camera.{field}", *ranges[field])
        if values_equal(old, new):
            continue
        before[field] = old
        after[field] = new
        changes.append({"path": f"camera.{field}", "before": old, "after": new})
    clip_start = float(after.get("clip_start", data.clip_start))
    clip_end = float(after.get("clip_end", data.clip_end))
    if clip_end <= clip_start:
        raise AuthoringOperationError(
            "OBJECT_SETTING_INVALID", "camera.clip_end must exceed clip_start", kind="validation"
        )
    if not before:
        return None, changes
    return (
        ObjectDataDelta(
            object_name=obj.name,
            object_identity=session_identity("object", obj),
            data_name=data.name,
            data_identity=session_identity(_data_kind(data), data),
            data_kind="camera",
            expected_users=users,
            before=before,
            after=after,
        ),
        changes,
    )


def _apply_data(data: Any, values: dict[str, PropertyValue]) -> None:
    for field, value in values.items():
        attribute = {
            "radius": "shadow_soft_size",
            "spot_size_degrees": "spot_size",
            "angle_degrees": "angle",
        }.get(field, field)
        setattr(data, attribute, value)


def _resolve_delta_data(delta: ObjectDataDelta, *, check_users: bool) -> Any:
    obj = require_object(delta.object_name, delta.object_identity)
    data = obj.data
    if data is None or data.name != delta.data_name:
        raise AuthoringOperationError(
            "OBJECT_DATA_IDENTITY_MISMATCH", "Object data no longer matches", kind="conflict"
        )
    if session_identity(_data_kind(data), data) != delta.data_identity:
        raise AuthoringOperationError(
            "OBJECT_DATA_IDENTITY_MISMATCH", "Object data identity changed", kind="conflict"
        )
    if check_users and int(data.users) != delta.expected_users:
        raise AuthoringOperationError(
            "OBJECT_DATA_USERS_MISMATCH",
            "Object data user count changed during the transaction",
            kind="conflict",
            details={"expected": delta.expected_users, "actual": int(data.users)},
        )
    return data


def read_object_data_property(kind: str, target: tuple[str, ...], attribute: str) -> PropertyValue:
    object_name, object_identity, data_name, data_identity, users = target
    delta = ObjectDataDelta(
        object_name=object_name,
        object_identity=object_identity,
        data_name=data_name,
        data_identity=data_identity,
        data_kind=kind,
        expected_users=int(users),
        before={},
        after={},
    )
    data = _resolve_delta_data(delta, check_users=True)
    attribute_name = {
        "radius": "shadow_soft_size",
        "spot_size_degrees": "spot_size",
        "angle_degrees": "angle",
    }.get(attribute, attribute)
    value = getattr(data, attribute_name)
    if attribute == "color":
        return tuple(float(item) for item in value)
    if isinstance(value, str):
        return value
    return float(value)


def restore_object_data_delta(delta: ObjectDataDelta) -> dict[str, Any]:
    data = _resolve_delta_data(delta, check_users=False)
    _apply_data(data, delta.before)
    return {
        "kind": f"{delta.data_kind}_setting",
        "object_name": delta.object_name,
        "data_name": delta.data_name,
        "restored_fields": sorted(delta.before),
    }


def apply_object_settings(
    transaction: Transaction,
    params: dict[str, Any],
) -> dict[str, Any]:
    obj = _require_object(params)
    patches = _ordered_patches(params)
    prepared: list[ObjectTransformDelta | VisibilityDelta | ObjectDataDelta] = []
    changes: list[dict[str, Any]] = []
    for patch_type, patch in patches.items():
        if patch_type == "transform":
            delta, patch_changes = _prepare_transform(obj, patch)
        elif patch_type == "visibility":
            delta, patch_changes = _prepare_visibility(obj, patch)
        elif patch_type == "light":
            delta, patch_changes = _prepare_light(obj, patch)
        else:
            delta, patch_changes = _prepare_camera(obj, patch)
        if delta is not None:
            prepared.append(delta)
        changes.extend(patch_changes)
    transaction.ensure_capacity(len(prepared))
    applied: list[ObjectTransformDelta | VisibilityDelta | ObjectDataDelta] = []
    try:
        for delta in prepared:
            applied.append(delta)
            if isinstance(delta, ObjectTransformDelta):
                for channel, values in delta.after.items():
                    target = getattr(obj, channel)
                    for axis, value in values.items():
                        target[AXES[axis]] = value
            elif isinstance(delta, VisibilityDelta):
                for field, value in delta.after.items():
                    setattr(obj, field, value)
            else:
                data = _resolve_delta_data(delta, check_users=True)
                _apply_data(data, delta.after)
        refresh_structure_guard_if_present(transaction, "object", obj)
        bpy.context.view_layer.update()
    except Exception as exc:
        try:
            for delta in reversed(applied):
                if isinstance(delta, ObjectTransformDelta):
                    for channel, values in delta.before.items():
                        target = getattr(obj, channel)
                        for axis, value in values.items():
                            target[AXES[axis]] = value
                elif isinstance(delta, VisibilityDelta):
                    for field, value in delta.before.items():
                        setattr(obj, field, value)
                else:
                    _apply_data(_resolve_delta_data(delta, check_users=False), delta.before)
            bpy.context.view_layer.update()
            for delta in applied:
                if isinstance(delta, ObjectTransformDelta):
                    for channel, values in delta.before.items():
                        target = getattr(obj, channel)
                        if any(
                            not values_equal(float(target[AXES[axis]]), value)
                            for axis, value in values.items()
                        ):
                            raise RuntimeError("transform restore verification failed")
                elif isinstance(delta, VisibilityDelta):
                    if any(
                        bool(getattr(obj, field)) != value for field, value in delta.before.items()
                    ):
                        raise RuntimeError("visibility restore verification failed")
                else:
                    data = _resolve_delta_data(delta, check_users=False)
                    for field, value in delta.before.items():
                        current = read_object_data_property(
                            delta.data_kind,
                            (
                                delta.object_name,
                                delta.object_identity,
                                delta.data_name,
                                delta.data_identity,
                                str(delta.expected_users),
                            ),
                            field,
                        )
                        if not values_equal(current, value):
                            raise RuntimeError(f"{field} restore verification failed")
            refresh_structure_guard_if_present(transaction, "object", obj)
        except Exception as restore_exc:
            raise AuthoringOperationError(
                "OBJECT_SETTINGS_RESTORE_FAILED",
                "Object settings failed and the partial write could not be restored",
                kind="conflict",
                details={
                    "apply_error": type(exc).__name__,
                    "restore_error": type(restore_exc).__name__,
                },
            ) from restore_exc
        raise AuthoringOperationError(
            "OBJECT_SETTINGS_APPLY_FAILED",
            f"Object settings could not be applied: {type(exc).__name__}",
            kind="blender_api",
        ) from exc
    for delta in prepared:
        transaction.record(delta)
    changes.sort(key=lambda item: item["path"])
    return {
        "transaction_id": transaction.transaction_id,
        "object_name": obj.name,
        "object_identity": session_identity("object", obj),
        "changed": bool(changes),
        "changes": changes,
        "object": {
            "location": [float(value) for value in obj.location],
            "rotation_euler_degrees": [math.degrees(float(value)) for value in obj.rotation_euler],
            "scale": [float(value) for value in obj.scale],
            "visibility": {
                "hide_viewport": bool(obj.hide_viewport),
                "hide_render": bool(obj.hide_render),
            },
            "data": object_data_summary(obj),
        },
    }
