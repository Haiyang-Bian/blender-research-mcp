"""Typed reversible LookDev comparison models and deterministic image evidence."""

from __future__ import annotations

import asyncio
import io
import math
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Annotated, Any, Literal, Protocol, cast
from uuid import uuid4

from PIL import Image, ImageChops, ImageFilter, ImageStat, UnidentifiedImageError
from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    WithJsonSchema,
    model_validator,
)

from blender_research_mcp.errors import BridgeError, ErrorInfo, ErrorKind
from blender_research_mcp.observation import (
    capture_image,
    settle_capture_generation,
    settle_scene_generation,
)

ObjectName = Annotated[str, Field(min_length=1, max_length=255)]
SessionIdentity = Annotated[str, Field(min_length=1, max_length=128)]
CandidateLabel = Annotated[str, Field(min_length=1, max_length=64)]
MaterialSlotIndex = Annotated[StrictInt, Field(ge=0, le=63)]
MaterialUsers = Annotated[StrictInt, Field(ge=1)]
SemanticView = Literal["FRONT", "RIGHT", "TOP", "BACK", "LEFT", "BOTTOM", "CURRENT"]
DisplayMode = Literal["CURRENT", "WIREFRAME", "SOLID", "MATERIAL", "RENDERED"]
OverlaysMode = Literal["CURRENT", "ON", "OFF"]


def _validate_candidate_value(value: Any) -> Any:
    if type(value) in {bool, int}:
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("candidate floating-point values must be finite")
        return value
    if type(value) is str:
        return value
    if (
        isinstance(value, list)
        and len(value) in {3, 4}
        and all(type(component) is float and math.isfinite(component) for component in value)
    ):
        return value
    raise ValueError(
        "candidate value must be a boolean, integer, finite float, string, or 3/4 finite floats"
    )


CandidateValue = Annotated[
    Any,
    BeforeValidator(_validate_candidate_value),
    WithJsonSchema(
        {
            "oneOf": [
                {"type": "boolean"},
                {"type": "integer"},
                {"type": "number"},
                {"type": "string"},
                {
                    "type": "array",
                    "items": {"type": "number"},
                    "minItems": 3,
                    "maxItems": 3,
                },
                {
                    "type": "array",
                    "items": {"type": "number"},
                    "minItems": 4,
                    "maxItems": 4,
                },
            ]
        }
    ),
]


class ObjectScaleAxisTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["object_scale_axis"]
    object_name: ObjectName
    expected_object_identity: SessionIdentity
    axis: Literal["x", "y", "z"]


class ObjectVisibilityTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["object_visibility"]
    object_name: ObjectName
    expected_object_identity: SessionIdentity
    property: Literal["hide_viewport", "hide_render"]


class ModifierStateTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["modifier_state"]
    object_name: ObjectName
    expected_object_identity: SessionIdentity
    modifier_name: Annotated[str, Field(min_length=1, max_length=255)]
    expected_modifier_identity: SessionIdentity
    property: Literal["show_viewport", "show_render"]


class ShapeKeyValueTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["shape_key_value"]
    object_name: ObjectName
    expected_object_identity: SessionIdentity
    shape_key_name: Annotated[str, Field(min_length=1, max_length=255)]
    expected_shape_key_identity: SessionIdentity


class MaterialInputTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["material_input"]
    object_name: ObjectName
    expected_object_identity: SessionIdentity
    material_slot_index: MaterialSlotIndex
    material_name: Annotated[str, Field(min_length=1, max_length=255)]
    expected_material_identity: SessionIdentity
    expected_material_users: MaterialUsers
    node_name: Annotated[str, Field(min_length=1, max_length=255)]
    expected_node_identity: SessionIdentity
    socket_identifier: Annotated[str, Field(min_length=1, max_length=255)]
    expected_socket_identity: SessionIdentity
    allow_shared: StrictBool = False


class ObjectTransformSettingLocator(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["transform"]
    channel: Literal["location", "rotation_euler_degrees", "scale"]
    axis: Literal["x", "y", "z"]


class ObjectVisibilitySettingLocator(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["visibility"]
    property: Literal["hide_viewport", "hide_render"]


class ObjectLightSettingLocator(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["light"]
    expected_data_identity: SessionIdentity
    expected_data_users: MaterialUsers
    expected_light_type: Literal["POINT", "SUN", "SPOT", "AREA"]
    allow_shared_data: StrictBool = False
    property: Literal[
        "energy",
        "color",
        "radius",
        "shape",
        "size",
        "size_y",
        "spot_size_degrees",
        "spot_blend",
        "angle_degrees",
    ]

    @model_validator(mode="after")
    def validate_property_for_light_type(self) -> ObjectLightSettingLocator:
        common = {"energy", "color"}
        allowed = {
            "POINT": common | {"radius"},
            "SPOT": common | {"radius", "spot_size_degrees", "spot_blend"},
            "SUN": common | {"angle_degrees"},
            "AREA": common | {"shape", "size", "size_y"},
        }[self.expected_light_type]
        if self.property not in allowed:
            raise ValueError(
                f"{self.expected_light_type} light does not support {self.property}"
            )
        return self


class ObjectCameraSettingLocator(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["camera"]
    expected_data_identity: SessionIdentity
    expected_data_users: MaterialUsers
    expected_camera_type: Literal["PERSP", "ORTHO"]
    allow_shared_data: StrictBool = False
    property: Literal[
        "lens",
        "sensor_width",
        "clip_start",
        "clip_end",
        "ortho_scale",
        "shift_x",
        "shift_y",
    ]

    @model_validator(mode="after")
    def validate_property_for_camera_type(self) -> ObjectCameraSettingLocator:
        if self.expected_camera_type == "PERSP" and self.property == "ortho_scale":
            raise ValueError("ortho_scale is only valid for ORTHO cameras")
        if self.expected_camera_type == "ORTHO" and self.property in {"lens", "sensor_width"}:
            raise ValueError("lens and sensor_width are only valid for PERSP cameras")
        return self


ObjectSettingLocator = Annotated[
    ObjectTransformSettingLocator
    | ObjectVisibilitySettingLocator
    | ObjectLightSettingLocator
    | ObjectCameraSettingLocator,
    Field(discriminator="type"),
]


class ObjectSettingTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["object_setting"]
    object_name: ObjectName
    expected_object_identity: SessionIdentity
    locator: ObjectSettingLocator


ComparisonTarget = Annotated[
    ObjectScaleAxisTarget
    | ObjectVisibilityTarget
    | ModifierStateTarget
    | ShapeKeyValueTarget
    | MaterialInputTarget
    | ObjectSettingTarget,
    Field(discriminator="type"),
]


class ComparisonCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: CandidateLabel
    value: CandidateValue


ComparisonCandidates = Annotated[
    tuple[ComparisonCandidate, ...], Field(min_length=1, max_length=3)
]


class ComparisonOrbit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    yaw_degrees: float = Field(default=0.0, ge=-180.0, le=180.0, allow_inf_nan=False)
    pitch_degrees: float = Field(default=0.0, ge=-89.0, le=89.0, allow_inf_nan=False)


class ComparisonCapture(BaseModel):
    model_config = ConfigDict(extra="forbid")

    object_name: ObjectName
    view: SemanticView = "CURRENT"
    max_size: int = Field(default=800, ge=256, le=1000)
    viewport_id: Annotated[str, Field(min_length=1, max_length=128)] | None = None
    display_mode: DisplayMode = "CURRENT"
    overlays: OverlaysMode = "CURRENT"
    orbit: ComparisonOrbit | None = None

    @model_validator(mode="after")
    def require_semantic_orbit_base(self) -> ComparisonCapture:
        if self.view == "CURRENT" and self.orbit is not None:
            raise ValueError("orbit requires a semantic base view rather than CURRENT")
        return self


def property_values_equal(left: Any, right: Any) -> bool:
    """Match Blender property semantics without coercing JSON value types."""
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if isinstance(left, list) or isinstance(right, list):
        if not isinstance(left, list) or not isinstance(right, list) or len(left) != len(right):
            return False
        return all(
            property_values_equal(left_item, right_item)
            for left_item, right_item in zip(left, right, strict=True)
        )
    if isinstance(left, int) or isinstance(right, int):
        return type(left) is type(right) and left == right
    if isinstance(left, float) and isinstance(right, float):
        return abs(left - right) <= 1e-7
    if isinstance(left, str) or isinstance(right, str):
        return type(left) is type(right) and left == right
    return False


class ComparisonRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: ComparisonTarget
    candidates: ComparisonCandidates
    capture: ComparisonCapture

    @model_validator(mode="after")
    def validate_candidates_for_target(self) -> ComparisonRequest:
        labels = [candidate.label for candidate in self.candidates]
        if len(set(labels)) != len(labels):
            raise ValueError("candidate labels must be unique")
        for index, candidate in enumerate(self.candidates):
            if any(
                _target_values_equal(self.target, candidate.value, previous.value)
                for previous in self.candidates[:index]
            ):
                raise ValueError("candidate values must be unique")

        if isinstance(self.target, (ObjectVisibilityTarget, ModifierStateTarget)) or (
            isinstance(self.target, ObjectSettingTarget)
            and isinstance(self.target.locator, ObjectVisibilitySettingLocator)
        ):
            if len(self.candidates) != 1 or type(self.candidates[0].value) is not bool:
                raise ValueError("boolean targets require exactly one boolean candidate")
        elif isinstance(self.target, ObjectSettingTarget):
            locator = self.target.locator
            if isinstance(locator, ObjectLightSettingLocator) and locator.property == "color":
                if any(
                    type(candidate.value) is not str
                    or re.fullmatch(r"#[0-9A-Fa-f]{6}", candidate.value) is None
                    for candidate in self.candidates
                ):
                    raise ValueError("Light color candidates must use #RRGGBB sRGB")
            elif isinstance(locator, ObjectLightSettingLocator) and locator.property == "shape":
                if any(
                    candidate.value not in {"SQUARE", "RECTANGLE", "DISK", "ELLIPSE"}
                    for candidate in self.candidates
                ):
                    raise ValueError("Area shape candidates must use the supported enum")
            elif any(type(candidate.value) is not float for candidate in self.candidates):
                raise ValueError("numeric object-setting candidates must be floating-point values")
        elif isinstance(self.target, (ObjectScaleAxisTarget, ShapeKeyValueTarget)) and any(
            type(candidate.value) is not float for candidate in self.candidates
        ):
            raise ValueError("scale and shape-key candidates must be floating-point values")
        return self


def _target_values_equal(target: ComparisonTarget, left: Any, right: Any) -> bool:
    if (
        isinstance(target, ObjectSettingTarget)
        and isinstance(target.locator, ObjectLightSettingLocator)
        and target.locator.property == "color"
        and isinstance(left, str)
        and isinstance(right, str)
    ):
        return left.upper() == right.upper()
    return property_values_equal(left, right)


class ImageDifferenceStatistics(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_channel_difference: int = Field(ge=0, le=255)
    mean_absolute_difference: float = Field(ge=0.0)
    rms_difference: float = Field(ge=0.0)
    structure_mean_absolute_difference: float = Field(ge=0.0)


def _decode_nonblank_rgb(image_bytes: bytes, *, label: str) -> Image.Image:
    try:
        with Image.open(io.BytesIO(image_bytes)) as opened:
            opened.load()
            grayscale_extrema = opened.convert("L").getextrema()
            if grayscale_extrema is None or grayscale_extrema[0] == grayscale_extrema[1]:
                raise ValueError(f"{label} image is blank")
            return opened.convert("RGB")
    except (OSError, UnidentifiedImageError) as exc:
        raise ValueError(f"{label} image is not a valid PNG") from exc


def validate_evidence_image(image_bytes: bytes, *, label: str) -> None:
    _decode_nonblank_rgb(image_bytes, label=label)


def image_difference_statistics(
    baseline_bytes: bytes,
    candidate_bytes: bytes,
) -> ImageDifferenceStatistics:
    """Measure aligned RGB evidence while suppressing stochastic rendered-view noise."""
    baseline = _decode_nonblank_rgb(baseline_bytes, label="baseline")
    candidate = _decode_nonblank_rgb(candidate_bytes, label="candidate")
    if baseline.size != candidate.size:
        raise ValueError("comparison images have different dimensions")

    difference = ImageChops.difference(baseline, candidate)
    statistics = ImageStat.Stat(difference)
    max_channel_difference = 0
    for channel in difference.split():
        channel_extrema = cast(tuple[int, int] | None, channel.getextrema())
        if channel_extrema is not None:
            max_channel_difference = max(max_channel_difference, int(channel_extrema[1]))

    baseline_structure = baseline.convert("L").filter(ImageFilter.GaussianBlur(2.0))
    candidate_structure = candidate.convert("L").filter(ImageFilter.GaussianBlur(2.0))
    baseline_structure.thumbnail((256, 256), Image.Resampling.LANCZOS)
    candidate_structure.thumbnail((256, 256), Image.Resampling.LANCZOS)
    structure_difference = ImageChops.difference(baseline_structure, candidate_structure)
    structure_mean = ImageStat.Stat(structure_difference).mean[0]

    return ImageDifferenceStatistics(
        max_channel_difference=max_channel_difference,
        mean_absolute_difference=sum(statistics.mean) / 3.0,
        rms_difference=math.sqrt(sum(value * value for value in statistics.rms) / 3.0),
        structure_mean_absolute_difference=structure_mean,
    )


def images_are_visually_indistinguishable(statistics: ImageDifferenceStatistics) -> bool:
    return (
        statistics.mean_absolute_difference <= 1.0
        and statistics.structure_mean_absolute_difference <= 0.5
    )


class ComparisonClient(Protocol):
    async def call(
        self,
        command: str,
        params: dict[str, Any] | None = None,
        *,
        deadline_ms: int = 5000,
        expected_scene_generation: int | None = None,
        idempotency_key: str | None = None,
        read_only: bool,
    ) -> dict[str, Any]: ...


ComparisonPhaseHook = Callable[[str, str | None, dict[str, Any]], Awaitable[None]]


@dataclass(frozen=True)
class ResolvedTarget:
    value: Any
    guard: dict[str, Any]
    evidence: dict[str, Any]
    scene_generation: int
    value_kind: str
    minimum: float | int | None = None
    maximum: float | int | None = None


def comparison_error(
    kind: ErrorKind,
    code: str,
    message: str,
    *,
    retryable: bool = False,
    details: dict[str, Any] | None = None,
) -> BridgeError:
    return BridgeError(
        ErrorInfo(
            kind=kind,
            code=code,
            message=message,
            retryable=retryable,
            details=details or {},
        )
    )


def _annotate_error(error: BridgeError, *, phase: str, label: str | None) -> BridgeError:
    details = dict(error.error.details)
    details["comparison_phase"] = phase
    if label is not None:
        details["candidate_label"] = label
    return BridgeError(error.error.model_copy(update={"details": details}))


def _identity(value: dict[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if key != "scene_generation"}


def _changed_fields(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    return sorted(key for key in before.keys() | after.keys() if before.get(key) != after.get(key))


def _require_identity(actual: Any, expected: str, *, kind: str) -> None:
    if actual != expected:
        raise comparison_error(
            ErrorKind.CONFLICT,
            "TARGET_IDENTITY_CONFLICT",
            f"The inspected {kind} identity no longer matches the comparison target",
            retryable=True,
            details={"expected": expected, "actual": actual},
        )


def _find_named(items: Any, name: str, *, kind: str) -> dict[str, Any]:
    if isinstance(items, list):
        for item in items:
            if isinstance(item, dict) and item.get("name") == name:
                return item
    raise comparison_error(
        ErrorKind.CONFLICT,
        f"{kind.upper()}_NOT_FOUND",
        f"The inspected {kind} no longer exists: {name}",
        retryable=True,
    )


def _json_property_value(value: Any) -> Any:
    if isinstance(value, tuple):
        return [_json_property_value(item) for item in value]
    return value


async def _resolve_target(
    client: ComparisonClient,
    target: ComparisonTarget,
) -> ResolvedTarget:
    value: Any
    if isinstance(target, ObjectScaleAxisTarget):
        inspected = await client.call(
            "object.inspect", {"object_name": target.object_name}, read_only=True
        )
        _require_identity(
            inspected.get("session_identity"), target.expected_object_identity, kind="object"
        )
        scale = inspected.get("scale")
        if not isinstance(scale, list) or len(scale) != 3:
            raise comparison_error(
                ErrorKind.BLENDER_API,
                "TARGET_INSPECTION_INVALID",
                "Object inspection did not return a three-axis scale",
            )
        axis_index = {"x": 0, "y": 1, "z": 2}[target.axis]
        value = float(scale[axis_index])
        guard: dict[str, Any] = {
            "type": target.type,
            "object_name": target.object_name,
            "object_identity": target.expected_object_identity,
            "axis": target.axis,
        }
        return ResolvedTarget(
            value=value,
            guard=guard,
            evidence={**guard, "value": value, "minimum": 0.000001, "maximum": 1000.0},
            scene_generation=int(inspected["scene_generation"]),
            value_kind="FLOAT",
            minimum=0.000001,
            maximum=1000.0,
        )

    if isinstance(target, ObjectSettingTarget):
        inspected = await client.call(
            "object.inspect", {"object_name": target.object_name}, read_only=True
        )
        _require_identity(
            inspected.get("session_identity"), target.expected_object_identity, kind="object"
        )
        locator = target.locator
        locator_guard = locator.model_dump()
        object_setting_guard = {
            "type": target.type,
            "object_name": target.object_name,
            "object_identity": target.expected_object_identity,
            "locator": locator_guard,
        }
        if isinstance(locator, ObjectTransformSettingLocator):
            values = inspected.get(locator.channel)
            if not isinstance(values, list) or len(values) != 3:
                raise comparison_error(
                    ErrorKind.BLENDER_API,
                    "TARGET_INSPECTION_INVALID",
                    "Object inspection did not return the requested transform channel",
                )
            value = float(values[{"x": 0, "y": 1, "z": 2}[locator.axis]])
            minimum, maximum = {
                "location": (-1_000_000.0, 1_000_000.0),
                "rotation_euler_degrees": (-360_000.0, 360_000.0),
                "scale": (0.000001, 1000.0),
            }[locator.channel]
            return ResolvedTarget(
                value=value,
                guard=object_setting_guard,
                evidence={
                    **object_setting_guard,
                    "value": value,
                    "minimum": minimum,
                    "maximum": maximum,
                },
                scene_generation=int(inspected["scene_generation"]),
                value_kind="FLOAT",
                minimum=minimum,
                maximum=maximum,
            )
        if isinstance(locator, ObjectVisibilitySettingLocator):
            visibility = inspected.get("visibility")
            value = visibility.get(locator.property) if isinstance(visibility, dict) else None
            if type(value) is not bool:
                raise comparison_error(
                    ErrorKind.BLENDER_API,
                    "TARGET_INSPECTION_INVALID",
                    "Object inspection did not return the requested visibility property",
                )
            return ResolvedTarget(
                value=value,
                guard=object_setting_guard,
                evidence={**object_setting_guard, "value": value},
                scene_generation=int(inspected["scene_generation"]),
                value_kind="BOOLEAN",
            )

        data = inspected.get("data")
        if not isinstance(data, dict) or data.get("type") != locator.type:
            raise comparison_error(
                ErrorKind.CONFLICT,
                "OBJECT_TYPE_MISMATCH",
                f"The inspected object no longer has {locator.type} data",
                retryable=True,
            )
        _require_identity(
            data.get("session_identity"), locator.expected_data_identity, kind="object data"
        )
        actual_users = data.get("users")
        if actual_users != locator.expected_data_users:
            raise comparison_error(
                ErrorKind.CONFLICT,
                "OBJECT_DATA_USERS_MISMATCH",
                "The object data user count changed after inspection",
                retryable=True,
                details={"expected": locator.expected_data_users, "actual": actual_users},
            )
        if locator.expected_data_users > 1 and not locator.allow_shared_data:
            raise comparison_error(
                ErrorKind.PRECONDITION,
                "SHARED_OBJECT_DATA_CONFIRMATION_REQUIRED",
                "Shared object data requires allow_shared_data=true for comparison",
                details={"users": locator.expected_data_users},
            )
        if not data.get("writable"):
            raise comparison_error(
                ErrorKind.PRECONDITION,
                "OBJECT_DATA_NOT_WRITABLE",
                "The inspected object data is not writable",
                details={"library": data.get("library")},
            )
        settings = data.get("settings")
        writable_fields = data.get("writable_fields")
        if not isinstance(settings, dict) or not isinstance(writable_fields, dict):
            raise comparison_error(
                ErrorKind.BLENDER_API,
                "TARGET_INSPECTION_INVALID",
                "Object inspection did not return typed object data settings",
            )
        expected_type = (
            locator.expected_light_type
            if isinstance(locator, ObjectLightSettingLocator)
            else locator.expected_camera_type
        )
        type_field = "light_type" if locator.type == "light" else "camera_type"
        if settings.get(type_field) != expected_type:
            raise comparison_error(
                ErrorKind.CONFLICT,
                "OBJECT_TYPE_MISMATCH",
                "The object data type changed after inspection",
                retryable=True,
                details={"expected": expected_type, "actual": settings.get(type_field)},
            )
        field = locator.property
        metadata = writable_fields.get(field)
        if field not in settings or not isinstance(metadata, dict):
            raise comparison_error(
                ErrorKind.PRECONDITION,
                "OBJECT_SETTING_NOT_WRITABLE",
                f"The inspected {locator.type}.{field} setting is not writable",
            )
        value = settings[field]
        setting_minimum: float | int | None = metadata.get("minimum")
        setting_maximum: float | int | None = metadata.get("maximum")
        if locator.type == "camera" and field == "clip_start":
            if setting_maximum is None:
                raise comparison_error(
                    ErrorKind.BLENDER_API,
                    "TARGET_INSPECTION_INVALID",
                    "Camera clip_start inspection did not return a maximum",
                )
            setting_maximum = min(
                float(setting_maximum), float(settings["clip_end"]) - 0.0000001
            )
        elif locator.type == "camera" and field == "clip_end":
            if setting_minimum is None:
                raise comparison_error(
                    ErrorKind.BLENDER_API,
                    "TARGET_INSPECTION_INVALID",
                    "Camera clip_end inspection did not return a minimum",
                )
            setting_minimum = max(
                float(setting_minimum), float(settings["clip_start"]) + 0.0000001
            )
        value_kind = "FLOAT"
        if locator.type == "light" and field == "color":
            value = str(value).upper()
            value_kind = "HEX_COLOR"
        elif locator.type == "light" and field == "shape":
            value = str(value)
            value_kind = "ENUM"
        else:
            value = float(value)
        guard = {
            **object_setting_guard,
            "data_name": data.get("name"),
            "data_identity": locator.expected_data_identity,
            "data_users": locator.expected_data_users,
            "data_type": expected_type,
            "writable": True,
        }
        return ResolvedTarget(
            value=value,
            guard=guard,
            evidence={
                **guard,
                "value": value,
                "minimum": setting_minimum,
                "maximum": setting_maximum,
            },
            scene_generation=int(inspected["scene_generation"]),
            value_kind=value_kind,
            minimum=setting_minimum,
            maximum=setting_maximum,
        )

    if isinstance(target, MaterialInputTarget):
        inspected = await client.call(
            "material.inspect",
            {
                "object_name": target.object_name,
                "material_slot_index": target.material_slot_index,
            },
            read_only=True,
        )
        _require_identity(
            inspected.get("object_identity"), target.expected_object_identity, kind="object"
        )
        _require_identity(
            inspected.get("material_identity"),
            target.expected_material_identity,
            kind="material",
        )
        if inspected.get("material_name") != target.material_name:
            raise comparison_error(
                ErrorKind.CONFLICT,
                "MATERIAL_NAME_CONFLICT",
                "The inspected material name changed",
                retryable=True,
            )
        actual_users = inspected.get("material_users")
        if actual_users != target.expected_material_users:
            raise comparison_error(
                ErrorKind.CONFLICT,
                "MATERIAL_USERS_CONFLICT",
                "The material user count changed after inspection",
                retryable=True,
                details={"expected": target.expected_material_users, "actual": actual_users},
            )
        if target.expected_material_users > 1 and not target.allow_shared:
            raise comparison_error(
                ErrorKind.PRECONDITION,
                "SHARED_MATERIAL_CONFIRMATION_REQUIRED",
                "Shared materials require allow_shared=true for comparison",
                details={
                    "material_users": target.expected_material_users,
                    "affected_objects": inspected.get("affected_objects", []),
                },
            )
        socket = None
        for item in inspected.get("sockets", []):
            if (
                isinstance(item, dict)
                and item.get("node_name") == target.node_name
                and item.get("socket_identifier") == target.socket_identifier
            ):
                socket = item
                break
        if socket is None:
            raise comparison_error(
                ErrorKind.CONFLICT,
                "MATERIAL_SOCKET_NOT_FOUND",
                "The inspected material socket no longer exists",
                retryable=True,
            )
        _require_identity(
            socket.get("node_identity"), target.expected_node_identity, kind="node"
        )
        _require_identity(
            socket.get("socket_identity"), target.expected_socket_identity, kind="socket"
        )
        if not socket.get("writable"):
            raise comparison_error(
                ErrorKind.PRECONDITION,
                "MATERIAL_SOCKET_NOT_WRITABLE",
                "The inspected material socket is not writable",
                details={"blocked_reasons": socket.get("blocked_reasons", [])},
            )
        value_kind = str(socket.get("socket_kind"))
        value = _json_property_value(socket.get("value"))
        guard = {
            "type": target.type,
            "object_name": target.object_name,
            "object_identity": target.expected_object_identity,
            "material_slot_index": target.material_slot_index,
            "material_name": target.material_name,
            "material_identity": target.expected_material_identity,
            "material_users": target.expected_material_users,
            "node_name": target.node_name,
            "node_identity": target.expected_node_identity,
            "socket_identifier": target.socket_identifier,
            "socket_identity": target.expected_socket_identity,
            "socket_kind": value_kind,
            "minimum": socket.get("minimum"),
            "maximum": socket.get("maximum"),
            "writable": True,
        }
        return ResolvedTarget(
            value=value,
            guard=guard,
            evidence={**guard, "value": value},
            scene_generation=int(inspected["scene_generation"]),
            value_kind=value_kind,
            minimum=socket.get("minimum"),
            maximum=socket.get("maximum"),
        )

    inspected = await client.call(
        "object.lookdev.inspect", {"object_name": target.object_name}, read_only=True
    )
    _require_identity(
        inspected.get("session_identity"), target.expected_object_identity, kind="object"
    )
    common_guard: dict[str, Any] = {
        "type": target.type,
        "object_name": target.object_name,
        "object_identity": target.expected_object_identity,
    }
    if isinstance(target, ObjectVisibilityTarget):
        visibility = inspected.get("visibility")
        if not isinstance(visibility, dict) or type(visibility.get(target.property)) is not bool:
            raise comparison_error(
                ErrorKind.BLENDER_API,
                "TARGET_INSPECTION_INVALID",
                "Object LookDev inspection did not return the requested visibility property",
            )
        value = visibility[target.property]
        guard = {**common_guard, "property": target.property}
        return ResolvedTarget(
            value=value,
            guard=guard,
            evidence={**guard, "value": value},
            scene_generation=int(inspected["scene_generation"]),
            value_kind="BOOLEAN",
        )
    if isinstance(target, ModifierStateTarget):
        modifier = _find_named(inspected.get("modifiers"), target.modifier_name, kind="modifier")
        _require_identity(
            modifier.get("session_identity"), target.expected_modifier_identity, kind="modifier"
        )
        value = modifier.get(target.property)
        if type(value) is not bool:
            raise comparison_error(
                ErrorKind.BLENDER_API,
                "TARGET_INSPECTION_INVALID",
                "Modifier inspection did not return the requested Boolean state",
            )
        guard = {
            **common_guard,
            "modifier_name": target.modifier_name,
            "modifier_identity": target.expected_modifier_identity,
            "property": target.property,
        }
        return ResolvedTarget(
            value=value,
            guard=guard,
            evidence={**guard, "value": value},
            scene_generation=int(inspected["scene_generation"]),
            value_kind="BOOLEAN",
        )
    assert isinstance(target, ShapeKeyValueTarget)
    shape_key = _find_named(inspected.get("shape_keys"), target.shape_key_name, kind="shape_key")
    _require_identity(
        shape_key.get("session_identity"),
        target.expected_shape_key_identity,
        kind="shape_key",
    )
    if shape_key.get("driven"):
        raise comparison_error(
            ErrorKind.PRECONDITION,
            "SHAPE_KEY_DRIVEN",
            "Driven shape keys cannot be compared",
        )
    value = float(shape_key["value"])
    minimum = float(shape_key["slider_min"])
    maximum = float(shape_key["slider_max"])
    guard = {
        **common_guard,
        "shape_key_name": target.shape_key_name,
        "shape_key_identity": target.expected_shape_key_identity,
        "slider_min": minimum,
        "slider_max": maximum,
        "driven": False,
    }
    return ResolvedTarget(
        value=value,
        guard=guard,
        evidence={**guard, "value": value},
        scene_generation=int(inspected["scene_generation"]),
        value_kind="FLOAT",
        minimum=minimum,
        maximum=maximum,
    )


def _numeric_value_in_range(value: Any, minimum: Any, maximum: Any) -> bool:
    values = value if isinstance(value, list) else [value]
    for component in values:
        if minimum is not None and component < minimum:
            return False
        if maximum is not None and component > maximum:
            return False
    return True


def _validate_live_candidates(
    request: ComparisonRequest,
    baseline: ResolvedTarget,
) -> None:
    expected_types: dict[str, tuple[type[Any], int | None]] = {
        "BOOLEAN": (bool, None),
        "INT": (int, None),
        "FLOAT": (float, None),
        "VECTOR": (list, 3),
        "COLOR": (list, 4),
        "HEX_COLOR": (str, None),
        "ENUM": (str, None),
    }
    expected = expected_types.get(baseline.value_kind)
    if expected is None:
        raise comparison_error(
            ErrorKind.PRECONDITION,
            "TARGET_TYPE_UNSUPPORTED",
            f"Comparison does not support target value kind {baseline.value_kind}",
        )
    expected_type, expected_length = expected
    for candidate in request.candidates:
        value = candidate.value
        if type(value) is not expected_type:
            raise comparison_error(
                ErrorKind.VALIDATION,
                "CANDIDATE_TYPE_MISMATCH",
                f"Candidate {candidate.label} does not match {baseline.value_kind}",
                details={"candidate_label": candidate.label},
            )
        if expected_length is not None and len(value) != expected_length:
            raise comparison_error(
                ErrorKind.VALIDATION,
                "CANDIDATE_TYPE_MISMATCH",
                f"Candidate {candidate.label} has the wrong component count",
                details={"candidate_label": candidate.label},
            )
        if _target_values_equal(request.target, value, baseline.value):
            raise comparison_error(
                ErrorKind.VALIDATION,
                "CANDIDATE_EQUALS_BASELINE",
                f"Candidate {candidate.label} equals the inspected baseline",
                details={"candidate_label": candidate.label},
            )
        if not _numeric_value_in_range(value, baseline.minimum, baseline.maximum):
            raise comparison_error(
                ErrorKind.VALIDATION,
                "CANDIDATE_OUT_OF_RANGE",
                f"Candidate {candidate.label} is outside the inspected range",
                details={
                    "candidate_label": candidate.label,
                    "minimum": baseline.minimum,
                    "maximum": baseline.maximum,
                },
            )


def _assert_target_matches(baseline: ResolvedTarget, current: ResolvedTarget) -> None:
    if baseline.guard != current.guard:
        raise comparison_error(
            ErrorKind.CONFLICT,
            "COMPARISON_TARGET_DRIFT",
            "The comparison target identity or write scope changed",
            retryable=True,
            details={"changed_fields": _changed_fields(baseline.guard, current.guard)},
        )
    if not property_values_equal(baseline.value, current.value):
        raise comparison_error(
            ErrorKind.CONFLICT,
            "COMPARISON_TARGET_DRIFT",
            "The comparison target value changed from the baseline",
            retryable=True,
            details={"baseline": baseline.value, "actual": current.value},
        )


def _assert_context_matches(baseline: dict[str, Any], current: dict[str, Any]) -> None:
    if _identity(baseline) != _identity(current):
        raise comparison_error(
            ErrorKind.CONFLICT,
            "COMPARISON_CONTEXT_DRIFT",
            "Blender user context changed during comparison",
            retryable=True,
            details={"changed_fields": _changed_fields(_identity(baseline), _identity(current))},
        )


def _assert_object_matches(baseline: dict[str, Any], current: dict[str, Any]) -> None:
    if _identity(baseline) != _identity(current):
        raise comparison_error(
            ErrorKind.CONFLICT,
            "COMPARISON_OBJECT_DRIFT",
            "The comparison evidence object changed",
            retryable=True,
            details={"changed_fields": _changed_fields(_identity(baseline), _identity(current))},
        )


def _require_same_generation(*values: int) -> int:
    if len(set(values)) != 1:
        raise comparison_error(
            ErrorKind.CONFLICT,
            "OBSERVATION_SCENE_CHANGED",
            "Blender scene data changed while comparison state was inspected",
            retryable=True,
            details={"scene_generations": list(values)},
        )
    return values[0]


async def _read_guarded_state(
    client: ComparisonClient,
    request: ComparisonRequest,
) -> tuple[dict[str, Any], dict[str, Any], ResolvedTarget, dict[str, Any]]:
    ping = await settle_scene_generation(client)  # type: ignore[arg-type]
    context = await client.call("context.get", read_only=True)
    evidence_object = await client.call(
        "object.inspect", {"object_name": request.capture.object_name}, read_only=True
    )
    target = await _resolve_target(client, request.target)
    _require_same_generation(
        int(ping["scene_generation"]),
        int(context["scene_generation"]),
        int(evidence_object["scene_generation"]),
        target.scene_generation,
    )
    return context, evidence_object, target, ping


async def _verify_restored(
    client: ComparisonClient,
    request: ComparisonRequest,
    baseline_context: dict[str, Any],
    baseline_object: dict[str, Any],
    baseline_target: ResolvedTarget,
) -> dict[str, Any]:
    context, evidence_object, target, ping = await _read_guarded_state(client, request)
    _assert_context_matches(baseline_context, context)
    _assert_object_matches(baseline_object, evidence_object)
    _assert_target_matches(baseline_target, target)
    return ping


async def _call_writer(
    client: ComparisonClient,
    target: ComparisonTarget,
    transaction_id: str,
    value: Any,
    scene_generation: int,
) -> dict[str, Any]:
    params: dict[str, Any]
    command: str
    if isinstance(target, ObjectSettingTarget):
        command = "object.set"
        locator = target.locator
        if isinstance(locator, ObjectTransformSettingLocator):
            patch: dict[str, Any] = {
                "type": "transform",
                locator.channel: {locator.axis: value},
            }
        elif isinstance(locator, ObjectVisibilitySettingLocator):
            patch = {"type": "visibility", locator.property: value}
        elif isinstance(locator, ObjectLightSettingLocator):
            patch = {
                "type": "light",
                "expected_data_identity": locator.expected_data_identity,
                "expected_data_users": locator.expected_data_users,
                "expected_light_type": locator.expected_light_type,
                "allow_shared_data": locator.allow_shared_data,
                locator.property: value,
            }
        else:
            patch = {
                "type": "camera",
                "expected_data_identity": locator.expected_data_identity,
                "expected_data_users": locator.expected_data_users,
                "expected_camera_type": locator.expected_camera_type,
                "allow_shared_data": locator.allow_shared_data,
                locator.property: value,
            }
        params = {
            "transaction_id": transaction_id,
            "object_name": target.object_name,
            "expected_object_identity": target.expected_object_identity,
            "patches": [patch],
        }
    elif isinstance(target, ObjectScaleAxisTarget):
        command = "object.transform"
        params = {
            "transaction_id": transaction_id,
            "object_name": target.object_name,
            "scale": {target.axis: value},
        }
    elif isinstance(target, ObjectVisibilityTarget):
        command = "object.visibility.set"
        params = {
            "transaction_id": transaction_id,
            "object_name": target.object_name,
            "expected_object_identity": target.expected_object_identity,
            "visibility": {target.property: value},
        }
    elif isinstance(target, ModifierStateTarget):
        command = "modifier.set_state"
        params = {
            "transaction_id": transaction_id,
            "object_name": target.object_name,
            "expected_object_identity": target.expected_object_identity,
            "modifier_name": target.modifier_name,
            "expected_modifier_identity": target.expected_modifier_identity,
            "state": {target.property: value},
        }
    elif isinstance(target, ShapeKeyValueTarget):
        command = "shape_key.set_value"
        params = {
            "transaction_id": transaction_id,
            "object_name": target.object_name,
            "expected_object_identity": target.expected_object_identity,
            "shape_key_name": target.shape_key_name,
            "expected_shape_key_identity": target.expected_shape_key_identity,
            "value": value,
        }
    else:
        command = "material.set_input"
        params = {
            "transaction_id": transaction_id,
            "object_name": target.object_name,
            "expected_object_identity": target.expected_object_identity,
            "material_slot_index": target.material_slot_index,
            "material_name": target.material_name,
            "expected_material_identity": target.expected_material_identity,
            "expected_material_users": target.expected_material_users,
            "node_name": target.node_name,
            "expected_node_identity": target.expected_node_identity,
            "socket_identifier": target.socket_identifier,
            "expected_socket_identity": target.expected_socket_identity,
            "value": value,
            "allow_shared": target.allow_shared,
        }
    return await client.call(
        command,
        params,
        expected_scene_generation=scene_generation,
        idempotency_key=str(uuid4()),
        read_only=False,
    )


async def _rollback(
    client: ComparisonClient,
    transaction_id: str,
    scene_generation: int,
) -> dict[str, Any]:
    return await client.call(
        "transaction.rollback",
        {"transaction_id": transaction_id},
        expected_scene_generation=scene_generation,
        idempotency_key=str(uuid4()),
        read_only=False,
    )


async def _attempt_cleanup_rollback(
    client: ComparisonClient,
    transaction_id: str,
) -> dict[str, Any]:
    settled = await settle_scene_generation(client)  # type: ignore[arg-type]
    return await _rollback(client, transaction_id, int(settled["scene_generation"]))


async def run_lookdev_comparison(
    client: ComparisonClient,
    request: ComparisonRequest,
    *,
    _phase_hook: ComparisonPhaseHook | None = None,
) -> tuple[list[bytes], dict[str, Any]]:
    """Compare absolute candidates and return evidence only after full restoration."""
    started = time.perf_counter()
    baseline_context, baseline_object, baseline_target, ping_before = await _read_guarded_state(
        client, request
    )
    _validate_live_candidates(request, baseline_target)
    capture = request.capture
    baseline_started = time.perf_counter()
    baseline_image, baseline_capture = await capture_image(
        client,  # type: ignore[arg-type]
        object_name=capture.object_name,
        view=capture.view,
        max_size=capture.max_size,
        viewport_id=capture.viewport_id,
        display_mode=capture.display_mode,
        overlays=capture.overlays,
        orbit=capture.orbit.model_dump() if capture.orbit is not None else None,
    )
    await settle_capture_generation(client, baseline_capture)  # type: ignore[arg-type]
    try:
        validate_evidence_image(baseline_image, label="baseline")
    except ValueError as exc:
        raise comparison_error(
            ErrorKind.BLENDER_API,
            "CAPTURE_INVALID",
            str(exc),
            retryable=True,
        ) from exc
    baseline_capture["content_index"] = 0
    _require_same_generation(
        int(ping_before["scene_generation"]), int(baseline_capture["scene_generation"])
    )
    await _verify_restored(
        client, request, baseline_context, baseline_object, baseline_target
    )
    baseline_result = {
        "label": "baseline",
        "requested_value": baseline_target.value,
        "writer": None,
        "capture": baseline_capture,
        "rollback": None,
        "difference": ImageDifferenceStatistics(
            max_channel_difference=0,
            mean_absolute_difference=0.0,
            rms_difference=0.0,
            structure_mean_absolute_difference=0.0,
        ).model_dump(),
        "content_index": 0,
        "scene_generation_before": int(ping_before["scene_generation"]),
        "scene_generation_after": int(baseline_capture["scene_generation"]),
        "elapsed_ms": round((time.perf_counter() - baseline_started) * 1000, 3),
    }

    images = [baseline_image]
    candidate_results: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    for index, candidate in enumerate(request.candidates, start=1):
        label = candidate.label
        phase = "preflight"
        transaction_id: str | None = None
        rollback_attempted = False
        candidate_started = time.perf_counter()
        try:
            context, evidence_object, current_target, ping = await _read_guarded_state(
                client, request
            )
            _assert_context_matches(baseline_context, context)
            _assert_object_matches(baseline_object, evidence_object)
            _assert_target_matches(baseline_target, current_target)

            phase = "begin"
            transaction = await client.call(
                "transaction.begin",
                {"label": f"compare:{label}", "viewport_id": capture.viewport_id},
                expected_scene_generation=int(ping["scene_generation"]),
                idempotency_key=str(uuid4()),
                read_only=False,
            )
            transaction_id = str(transaction["transaction_id"])
            await asyncio.sleep(0)

            phase = "write"
            writer = await _call_writer(
                client,
                request.target,
                transaction_id,
                candidate.value,
                int(transaction["scene_generation"]),
            )
            await asyncio.sleep(0)
            if _phase_hook is not None:
                await _phase_hook("after_write", label, {"writer": writer})

            phase = "capture"
            image, capture_metadata = await capture_image(
                client,  # type: ignore[arg-type]
                object_name=capture.object_name,
                view=capture.view,
                max_size=capture.max_size,
                viewport_id=capture.viewport_id,
                display_mode=capture.display_mode,
                overlays=capture.overlays,
                orbit=capture.orbit.model_dump() if capture.orbit is not None else None,
            )
            await settle_capture_generation(client, capture_metadata)  # type: ignore[arg-type]
            try:
                validate_evidence_image(image, label=f"candidate {label}")
                statistics = image_difference_statistics(baseline_image, image)
            except ValueError as exc:
                raise comparison_error(
                    ErrorKind.BLENDER_API,
                    "CAPTURE_INVALID",
                    str(exc),
                    retryable=True,
                ) from exc
            capture_metadata["content_index"] = index
            if _phase_hook is not None:
                await _phase_hook(
                    "after_capture", label, {"capture": capture_metadata, "writer": writer}
                )

            phase = "rollback"
            rollback_attempted = True
            rollback = await _rollback(
                client,
                transaction_id,
                int(capture_metadata["scene_generation"]),
            )
            transaction_id = None
            await asyncio.sleep(0)
            if _phase_hook is not None:
                await _phase_hook("after_rollback", label, {"rollback": rollback})

            phase = "verify_restored"
            try:
                restored_ping = await _verify_restored(
                    client, request, baseline_context, baseline_object, baseline_target
                )
            except BridgeError as exc:
                raise comparison_error(
                    ErrorKind.CONFLICT,
                    "COMPARISON_RESTORE_FAILED",
                    "Comparison rollback could not prove baseline restoration",
                    details={"cause": exc.error.model_dump(mode="json")},
                ) from exc

            images.append(image)
            candidate_result = {
                "label": label,
                "requested_value": candidate.value,
                "writer": writer,
                "capture": capture_metadata,
                "rollback": rollback,
                "difference": statistics.model_dump(),
                "content_index": index,
                "scene_generation_before": int(ping["scene_generation"]),
                "scene_generation_after": int(restored_ping["scene_generation"]),
                "elapsed_ms": round((time.perf_counter() - candidate_started) * 1000, 3),
            }
            candidate_results.append(candidate_result)
            if images_are_visually_indistinguishable(statistics):
                warnings.append(
                    {"code": "CANDIDATE_VISUALLY_INDISTINGUISHABLE", "label": label}
                )
        except BridgeError as exc:
            if transaction_id is not None and not rollback_attempted:
                try:
                    await _attempt_cleanup_rollback(client, transaction_id)
                    transaction_id = None
                    await _verify_restored(
                        client, request, baseline_context, baseline_object, baseline_target
                    )
                except BridgeError as cleanup_exc:
                    if cleanup_exc.error.kind == ErrorKind.CONFLICT:
                        raise _annotate_error(
                            cleanup_exc, phase="cleanup_rollback", label=label
                        ) from cleanup_exc
                    raise comparison_error(
                        ErrorKind.CONFLICT,
                        "COMPARISON_RESTORE_FAILED",
                        "Comparison failure cleanup could not prove baseline restoration",
                        details={
                            "candidate_label": label,
                            "comparison_phase": phase,
                            "cause": exc.error.model_dump(mode="json"),
                            "cleanup_cause": cleanup_exc.error.model_dump(mode="json"),
                        },
                    ) from cleanup_exc
            raise _annotate_error(exc, phase=phase, label=label) from exc

    final_ping = await _verify_restored(
        client, request, baseline_context, baseline_object, baseline_target
    )
    return images, {
        "target": request.target.model_dump(mode="json"),
        "baseline_value": baseline_target.value,
        "baseline_target": baseline_target.evidence,
        "baseline_context": baseline_context,
        "baseline_object": baseline_object,
        "baseline_capture": baseline_capture,
        "items": [baseline_result, *candidate_results],
        "candidates": candidate_results,
        "context_unchanged": True,
        "target_restored": True,
        "object_unchanged": True,
        "scene_generation_start": int(ping_before["scene_generation"]),
        "scene_generation_end": int(final_ping["scene_generation"]),
        "scene_generation": int(final_ping["scene_generation"]),
        "heartbeat_before": int(ping_before["heartbeat"]),
        "heartbeat_after": int(final_ping["heartbeat"]),
        "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
        "warnings": warnings,
    }
