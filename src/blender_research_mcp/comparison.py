"""Typed reversible LookDev comparison models and deterministic image evidence."""

from __future__ import annotations

import io
import math
from typing import Annotated, Any, Literal, cast

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
    if (
        isinstance(value, list)
        and len(value) in {3, 4}
        and all(type(component) is float and math.isfinite(component) for component in value)
    ):
        return value
    raise ValueError(
        "candidate value must be a boolean, integer, finite float, or 3/4 finite floats"
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


ComparisonTarget = Annotated[
    ObjectScaleAxisTarget
    | ObjectVisibilityTarget
    | ModifierStateTarget
    | ShapeKeyValueTarget
    | MaterialInputTarget,
    Field(discriminator="type"),
]


class ComparisonCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    label: CandidateLabel
    value: CandidateValue


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
    return False


class ComparisonRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target: ComparisonTarget
    candidates: Annotated[tuple[ComparisonCandidate, ...], Field(min_length=1, max_length=3)]
    capture: ComparisonCapture

    @model_validator(mode="after")
    def validate_candidates_for_target(self) -> ComparisonRequest:
        labels = [candidate.label for candidate in self.candidates]
        if len(set(labels)) != len(labels):
            raise ValueError("candidate labels must be unique")
        for index, candidate in enumerate(self.candidates):
            if any(
                property_values_equal(candidate.value, previous.value)
                for previous in self.candidates[:index]
            ):
                raise ValueError("candidate values must be unique")

        if isinstance(self.target, (ObjectVisibilityTarget, ModifierStateTarget)):
            if len(self.candidates) != 1 or type(self.candidates[0].value) is not bool:
                raise ValueError("boolean targets require exactly one boolean candidate")
        elif isinstance(self.target, (ObjectScaleAxisTarget, ShapeKeyValueTarget)) and any(
            type(candidate.value) is not float for candidate in self.candidates
        ):
            raise ValueError("scale and shape-key candidates must be floating-point values")
        return self


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
