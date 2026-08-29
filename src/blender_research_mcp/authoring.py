"""Closed-world schemas and capability helpers for semantic scene authoring."""

from __future__ import annotations

import math
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, model_validator

from blender_research_mcp.client import BridgeClient


def _finite_number(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("value must be a JSON number")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("value must be finite")
    return result


FiniteNumber = Annotated[float, BeforeValidator(_finite_number)]


class Vector3(BaseModel):
    model_config = ConfigDict(extra="forbid")

    x: FiniteNumber = 0.0
    y: FiniteNumber = 0.0
    z: FiniteNumber = 0.0


class InitialTransform(BaseModel):
    model_config = ConfigDict(extra="forbid")

    location: Vector3 = Field(default_factory=Vector3)
    rotation_euler_degrees: Vector3 = Field(default_factory=Vector3)
    scale: Vector3 = Field(default_factory=lambda: Vector3(x=1.0, y=1.0, z=1.0))

    @model_validator(mode="after")
    def validate_ranges(self) -> InitialTransform:
        if any(abs(value) > 1_000_000 for value in self.location.model_dump().values()):
            raise ValueError("location components must be between -1000000 and 1000000")
        if any(
            abs(value) > 360_000
            for value in self.rotation_euler_degrees.model_dump().values()
        ):
            raise ValueError("rotation components must be between -360000 and 360000")
        if any(
            not 0.000001 <= value <= 1000 for value in self.scale.model_dump().values()
        ):
            raise ValueError("scale components must be between 0.000001 and 1000")
        return self


class AxisPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    x: FiniteNumber | None = None
    y: FiniteNumber | None = None
    z: FiniteNumber | None = None

    @model_validator(mode="after")
    def require_axis(self) -> AxisPatch:
        if self.x is None and self.y is None and self.z is None:
            raise ValueError("at least one axis is required")
        return self


class ScaleAxisPatch(AxisPatch):
    @model_validator(mode="after")
    def validate_scale(self) -> ScaleAxisPatch:
        values = [value for value in (self.x, self.y, self.z) if value is not None]
        if any(not 0.000001 <= value <= 1000 for value in values):
            raise ValueError("scale components must be between 0.000001 and 1000")
        return self


class LocationAxisPatch(AxisPatch):
    @model_validator(mode="after")
    def validate_location(self) -> LocationAxisPatch:
        values = [value for value in (self.x, self.y, self.z) if value is not None]
        if any(abs(value) > 1_000_000 for value in values):
            raise ValueError("location components must be between -1000000 and 1000000")
        return self


class RotationAxisPatch(AxisPatch):
    @model_validator(mode="after")
    def validate_rotation(self) -> RotationAxisPatch:
        values = [value for value in (self.x, self.y, self.z) if value is not None]
        if any(abs(value) > 360_000 for value in values):
            raise ValueError("rotation components must be between -360000 and 360000")
        return self


class _ObjectDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=255)
    collection_name: str | None = Field(default=None, min_length=1, max_length=255)
    expected_collection_identity: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
    )
    transform: InitialTransform = Field(default_factory=InitialTransform)

    @model_validator(mode="after")
    def collection_identity_pair(self) -> _ObjectDefinition:
        if (self.collection_name is None) != (self.expected_collection_identity is None):
            raise ValueError(
                "collection_name and expected_collection_identity must be supplied together"
            )
        return self


class PlaneDefinition(_ObjectDefinition):
    type: Literal["plane"]
    size: FiniteNumber = Field(default=2.0, gt=0, le=100_000)


class GridDefinition(_ObjectDefinition):
    type: Literal["grid"]
    size: FiniteNumber = Field(default=2.0, gt=0, le=100_000)
    x_subdivisions: int = Field(default=10, ge=2, le=512)
    y_subdivisions: int = Field(default=10, ge=2, le=512)


class CubeDefinition(_ObjectDefinition):
    type: Literal["cube"]
    size: FiniteNumber = Field(default=2.0, gt=0, le=100_000)


class UVSphereDefinition(_ObjectDefinition):
    type: Literal["uv_sphere"]
    radius: FiniteNumber = Field(default=1.0, gt=0, le=100_000)
    segments: int = Field(default=32, ge=3, le=512)
    ring_count: int = Field(default=16, ge=3, le=256)


class IcoSphereDefinition(_ObjectDefinition):
    type: Literal["ico_sphere"]
    radius: FiniteNumber = Field(default=1.0, gt=0, le=100_000)
    subdivisions: int = Field(default=2, ge=1, le=6)


class CylinderDefinition(_ObjectDefinition):
    type: Literal["cylinder"]
    radius: FiniteNumber = Field(default=1.0, gt=0, le=100_000)
    depth: FiniteNumber = Field(default=2.0, gt=0, le=100_000)
    vertices: int = Field(default=32, ge=3, le=512)


class ConeDefinition(_ObjectDefinition):
    type: Literal["cone"]
    radius1: FiniteNumber = Field(default=1.0, ge=0, le=100_000)
    radius2: FiniteNumber = Field(default=0.0, ge=0, le=100_000)
    depth: FiniteNumber = Field(default=2.0, gt=0, le=100_000)
    vertices: int = Field(default=32, ge=3, le=512)

    @model_validator(mode="after")
    def require_radius(self) -> ConeDefinition:
        if self.radius1 == 0 and self.radius2 == 0:
            raise ValueError("at least one cone radius must be positive")
        return self


class EmptyDefinition(_ObjectDefinition):
    type: Literal["empty"]
    display_type: Literal["PLAIN_AXES", "ARROWS", "SINGLE_ARROW", "CIRCLE", "CUBE", "SPHERE"] = (
        "PLAIN_AXES"
    )
    display_size: FiniteNumber = Field(default=1.0, gt=0, le=100_000)


class CameraDefinition(_ObjectDefinition):
    type: Literal["camera"]
    lens: FiniteNumber = Field(default=50.0, ge=1, le=250)
    sensor_width: FiniteNumber = Field(default=36.0, ge=1, le=100)


class LightDefinition(_ObjectDefinition):
    type: Literal["point_light", "sun_light", "spot_light", "area_light"]
    energy: FiniteNumber = Field(default=1000.0, ge=0, le=10_000_000)
    color: str = Field(default="#FFFFFF", pattern=r"^#[0-9A-Fa-f]{6}$")
    size: FiniteNumber = Field(default=1.0, gt=0, le=100_000)
    spot_size_degrees: FiniteNumber = Field(default=45.0, gt=0, le=179)


ObjectDefinition = Annotated[
    PlaneDefinition
    | GridDefinition
    | CubeDefinition
    | UVSphereDefinition
    | IcoSphereDefinition
    | CylinderDefinition
    | ConeDefinition
    | EmptyDefinition
    | CameraDefinition
    | LightDefinition,
    Field(discriminator="type"),
]

SceneKind = Literal["objects", "collections", "materials", "images", "world", "camera", "render"]
SceneKinds = Annotated[tuple[SceneKind, ...], Field(min_length=1, max_length=7)]


async def require_capability(client: BridgeClient, name: str, version: int = 1) -> None:
    await client.connect()
    client.require_capability(name, version)
