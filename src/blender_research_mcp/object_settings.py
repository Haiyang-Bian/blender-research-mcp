"""Closed typed schemas for unified object, Light, and Camera settings."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    model_validator,
)

from blender_research_mcp.authoring import (
    FiniteNumber,
    LocationAxisPatch,
    RotationAxisPatch,
    ScaleAxisPatch,
)

SessionIdentity = Annotated[str, Field(min_length=1, max_length=128)]
DataUsers = Annotated[StrictInt, Field(ge=1)]
HexSrgb = Annotated[str, Field(pattern=r"^#[0-9A-Fa-f]{6}$", strict=True)]


class TransformSettingPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["transform"]
    location: LocationAxisPatch | None = None
    rotation_euler_degrees: RotationAxisPatch | None = None
    scale: ScaleAxisPatch | None = None

    @model_validator(mode="after")
    def require_channel(self) -> TransformSettingPatch:
        if self.location is None and self.rotation_euler_degrees is None and self.scale is None:
            raise ValueError("transform requires location, rotation_euler_degrees, and/or scale")
        return self


class VisibilitySettingPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["visibility"]
    hide_viewport: StrictBool | None = None
    hide_render: StrictBool | None = None

    @model_validator(mode="after")
    def require_field(self) -> VisibilitySettingPatch:
        if self.hide_viewport is None and self.hide_render is None:
            raise ValueError("visibility requires hide_viewport and/or hide_render")
        return self


class LightSettingPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["light"]
    expected_data_identity: SessionIdentity
    expected_data_users: DataUsers
    expected_light_type: Literal["POINT", "SUN", "SPOT", "AREA"]
    allow_shared_data: StrictBool = False
    energy: FiniteNumber | None = Field(default=None, ge=0, le=10_000_000)
    color: HexSrgb | None = None
    radius: FiniteNumber | None = Field(default=None, ge=0, le=100_000)
    shape: Literal["SQUARE", "RECTANGLE", "DISK", "ELLIPSE"] | None = None
    size: FiniteNumber | None = Field(default=None, gt=0, le=100_000)
    size_y: FiniteNumber | None = Field(default=None, gt=0, le=100_000)
    spot_size_degrees: FiniteNumber | None = Field(default=None, gt=0, le=179)
    spot_blend: FiniteNumber | None = Field(default=None, ge=0, le=1)
    angle_degrees: FiniteNumber | None = Field(default=None, ge=0, le=180)

    @model_validator(mode="after")
    def validate_light_fields(self) -> LightSettingPatch:
        common = {"energy", "color"}
        supplied = {
            name
            for name in (
                "energy",
                "color",
                "radius",
                "shape",
                "size",
                "size_y",
                "spot_size_degrees",
                "spot_blend",
                "angle_degrees",
            )
            if getattr(self, name) is not None
        }
        if not supplied:
            raise ValueError("light requires at least one setting")
        allowed = {
            "POINT": common | {"radius"},
            "SPOT": common | {"radius", "spot_size_degrees", "spot_blend"},
            "SUN": common | {"angle_degrees"},
            "AREA": common | {"shape", "size", "size_y"},
        }[self.expected_light_type]
        unsupported = supplied - allowed
        if unsupported:
            field_names = ", ".join(sorted(unsupported))
            raise ValueError(
                f"{self.expected_light_type} light does not support: {field_names}"
            )
        if self.size_y is not None and self.shape in {"SQUARE", "DISK"}:
            raise ValueError("size_y requires RECTANGLE or ELLIPSE Area shape")
        return self


class CameraSettingPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["camera"]
    expected_data_identity: SessionIdentity
    expected_data_users: DataUsers
    expected_camera_type: Literal["PERSP", "ORTHO"]
    allow_shared_data: StrictBool = False
    lens: FiniteNumber | None = Field(default=None, ge=1, le=250)
    sensor_width: FiniteNumber | None = Field(default=None, ge=1, le=100)
    clip_start: FiniteNumber | None = Field(default=None, ge=0.00001, le=1_000_000)
    clip_end: FiniteNumber | None = Field(default=None, ge=0.0001, le=10_000_000)
    ortho_scale: FiniteNumber | None = Field(default=None, ge=0.000001, le=1_000_000)
    shift_x: FiniteNumber | None = Field(default=None, ge=-10, le=10)
    shift_y: FiniteNumber | None = Field(default=None, ge=-10, le=10)

    @model_validator(mode="after")
    def validate_camera_fields(self) -> CameraSettingPatch:
        supplied = {
            name
            for name in (
                "lens",
                "sensor_width",
                "clip_start",
                "clip_end",
                "ortho_scale",
                "shift_x",
                "shift_y",
            )
            if getattr(self, name) is not None
        }
        if not supplied:
            raise ValueError("camera requires at least one setting")
        if self.expected_camera_type == "PERSP" and self.ortho_scale is not None:
            raise ValueError("ortho_scale is only valid for ORTHO cameras")
        if self.expected_camera_type == "ORTHO" and (
            self.lens is not None or self.sensor_width is not None
        ):
            raise ValueError("lens and sensor_width are only valid for PERSP cameras")
        if (
            self.clip_start is not None
            and self.clip_end is not None
            and self.clip_end <= self.clip_start
        ):
            raise ValueError("clip_end must be greater than clip_start")
        return self


ObjectSettingPatch = Annotated[
    TransformSettingPatch | VisibilitySettingPatch | LightSettingPatch | CameraSettingPatch,
    Field(discriminator="type"),
]


def _unique_patch_types(
    patches: tuple[ObjectSettingPatch, ...],
) -> tuple[ObjectSettingPatch, ...]:
    patch_types = [patch.type for patch in patches]
    if len(set(patch_types)) != len(patch_types):
        raise ValueError("object setting patch types must be unique")
    return patches


ObjectSettingPatches = Annotated[
    tuple[ObjectSettingPatch, ...],
    Field(min_length=1, max_length=4),
    AfterValidator(_unique_patch_types),
]
