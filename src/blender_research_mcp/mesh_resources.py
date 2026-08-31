"""Closed-world schemas for revision-bound Mesh resources and surface fitting."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, model_validator

from blender_research_mcp.authoring import FiniteNumber, Vector3

MeshDomain = Literal["VERTEX", "EDGE", "FACE"]
CoordinateSpace = Literal["LOCAL", "WORLD"]
SelectionId = Annotated[str, Field(min_length=1, max_length=128)]
SurfaceId = Annotated[str, Field(min_length=1, max_length=128)]
MeshRevisionId = Annotated[str, Field(min_length=64, max_length=64)]
ComponentIndex = Annotated[StrictInt, Field(ge=0)]


class Point2(BaseModel):
    model_config = ConfigDict(extra="forbid")

    x: FiniteNumber
    y: FiniteNumber


class IndicesQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["indices"]
    indices: Annotated[tuple[ComponentIndex, ...], Field(min_length=1, max_length=4096)]

    @model_validator(mode="after")
    def unique_indices(self) -> IndicesQuery:
        if len(set(self.indices)) != len(self.indices):
            raise ValueError("indices must be unique")
        return self


class AllQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["all"]


class SphereQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["sphere"]
    center: Vector3
    radius: FiniteNumber = Field(gt=0, le=1_000_000)
    space: CoordinateSpace = "LOCAL"


class BoxQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["box"]
    minimum: Vector3
    maximum: Vector3
    space: CoordinateSpace = "LOCAL"

    @model_validator(mode="after")
    def ordered_bounds(self) -> BoxQuery:
        if not (
            self.minimum.x <= self.maximum.x
            and self.minimum.y <= self.maximum.y
            and self.minimum.z <= self.maximum.z
        ):
            raise ValueError("minimum components must not exceed maximum components")
        return self


class PlaneQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["plane"]
    origin: Vector3
    normal: Vector3
    side: Literal["POSITIVE", "NEGATIVE", "ON"] = "POSITIVE"
    tolerance: FiniteNumber = Field(default=1e-5, ge=0, le=100_000)
    space: CoordinateSpace = "LOCAL"

    @model_validator(mode="after")
    def nonzero_normal(self) -> PlaneQuery:
        if self.normal.x == 0 and self.normal.y == 0 and self.normal.z == 0:
            raise ValueError("normal must be non-zero")
        return self


class MaterialQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["material"]
    slot_indices: Annotated[
        tuple[Annotated[StrictInt, Field(ge=0, le=63)], ...],
        Field(min_length=1, max_length=64),
    ]

    @model_validator(mode="after")
    def unique_slots(self) -> MaterialQuery:
        if len(set(self.slot_indices)) != len(self.slot_indices):
            raise ValueError("slot_indices must be unique")
        return self


class NormalQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["normal"]
    direction: Vector3
    minimum_dot: FiniteNumber = Field(ge=-1, le=1)
    space: CoordinateSpace = "LOCAL"

    @model_validator(mode="after")
    def nonzero_direction(self) -> NormalQuery:
        if self.direction.x == 0 and self.direction.y == 0 and self.direction.z == 0:
            raise ValueError("direction must be non-zero")
        return self


class MeasureQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["measure"]
    field: Literal["FACE_AREA", "EDGE_LENGTH"]
    minimum: FiniteNumber | None = Field(default=None, ge=0)
    maximum: FiniteNumber | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def bounded_measure(self) -> MeasureQuery:
        if self.minimum is None and self.maximum is None:
            raise ValueError("minimum and/or maximum is required")
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError("minimum must not exceed maximum")
        return self


class TopologyQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["topology"]
    kind: Literal["BOUNDARY", "NON_MANIFOLD", "CONNECTED"]
    seed_indices: (
        Annotated[tuple[ComponentIndex, ...], Field(min_length=1, max_length=4096)] | None
    ) = None

    @model_validator(mode="after")
    def connected_requires_seed(self) -> TopologyQuery:
        if self.kind == "CONNECTED" and not self.seed_indices:
            raise ValueError("CONNECTED requires seed_indices")
        if self.kind != "CONNECTED" and self.seed_indices is not None:
            raise ValueError("seed_indices is only valid for CONNECTED")
        if self.seed_indices is not None and len(set(self.seed_indices)) != len(self.seed_indices):
            raise ValueError("seed_indices must be unique")
        return self


class ScreenQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["screen"]
    capture_id: Annotated[str, Field(min_length=1, max_length=128)]
    shape: Literal["POINT", "BOX", "LASSO"]
    points: Annotated[tuple[Point2, ...], Field(min_length=1, max_length=128)]
    visibility: Literal["VISIBLE_ONLY", "THROUGH"] = "VISIBLE_ONLY"
    include_backface: StrictBool = False

    @model_validator(mode="after")
    def shape_cardinality(self) -> ScreenQuery:
        expected = {"POINT": 1, "BOX": 2}
        if self.shape in expected and len(self.points) != expected[self.shape]:
            raise ValueError(f"{self.shape} requires {expected[self.shape]} points")
        if self.shape == "LASSO" and len(self.points) < 3:
            raise ValueError("LASSO requires at least three points")
        if any(not 0 <= value <= 1 for point in self.points for value in (point.x, point.y)):
            raise ValueError("screen points must use normalized 0-1 coordinates")
        return self


SelectionQuery = Annotated[
    IndicesQuery
    | AllQuery
    | SphereQuery
    | BoxQuery
    | PlaneQuery
    | MaterialQuery
    | NormalQuery
    | MeasureQuery
    | TopologyQuery
    | ScreenQuery,
    Field(discriminator="type"),
]


class CombineSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["combine"]
    mode: Literal["UNION", "INTERSECTION", "DIFFERENCE"]
    selection_ids: Annotated[tuple[SelectionId, ...], Field(min_length=2, max_length=16)]


class GrowSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["expand", "contract"]
    selection_id: SelectionId
    steps: Annotated[StrictInt, Field(ge=1, le=64)] = 1


class BoundarySelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["boundary"]
    selection_id: SelectionId


class ConnectedSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["connected"]
    selection_id: SelectionId


class ConvertSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["convert"]
    selection_id: SelectionId
    domain: MeshDomain
    mode: Literal["ANY", "ALL"] = "ANY"


class FalloffSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["falloff"]
    selection_id: SelectionId
    radius: FiniteNumber = Field(gt=0, le=1_000_000)
    profile: Literal["LINEAR", "SMOOTH", "SHARP"] = "SMOOTH"
    space: CoordinateSpace = "LOCAL"


SelectionDerivation = Annotated[
    CombineSelection
    | GrowSelection
    | BoundarySelection
    | ConnectedSelection
    | ConvertSelection
    | FalloffSelection,
    Field(discriminator="type"),
]


class SetPositionsOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["set_positions"]
    selection_id: SelectionId
    mode: Literal["ABSOLUTE", "OFFSET"] = "ABSOLUTE"
    space: CoordinateSpace = "LOCAL"
    positions: Annotated[tuple[Vector3, ...], Field(min_length=1, max_length=4096)]


class SmoothOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["smooth"]
    selection_id: SelectionId
    iterations: Annotated[StrictInt, Field(ge=1, le=64)] = 1
    factor: FiniteNumber = Field(default=0.5, ge=0, le=1)
    preserve_boundary: StrictBool = True


class RelaxOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["relax"]
    selection_id: SelectionId
    iterations: Annotated[StrictInt, Field(ge=1, le=64)] = 1
    factor: FiniteNumber = Field(default=0.5, ge=0, le=1)
    preserve_boundary: StrictBool = True


class ProjectOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["project"]
    selection_id: SelectionId
    surface_id: SurfaceId
    direction: Literal["CLOSEST_POINT", "NORMAL", "AXIS", "VECTOR", "VIEW_RAY"] = "CLOSEST_POINT"
    axis: Literal["X", "Y", "Z", "-X", "-Y", "-Z"] | None = None
    vector: Vector3 | None = None
    capture_id: Annotated[str, Field(min_length=1, max_length=128)] | None = None
    maximum_distance: FiniteNumber = Field(gt=0, le=1_000_000)
    offset: FiniteNumber = Field(default=0, ge=-100_000, le=100_000)
    side: Literal["ANY", "FRONT", "BACK"] = "ANY"
    on_miss: Literal["KEEP", "ERROR"] = "KEEP"

    @model_validator(mode="after")
    def direction_fields(self) -> ProjectOperation:
        required = {
            "AXIS": self.axis is not None,
            "VECTOR": self.vector is not None,
            "VIEW_RAY": self.capture_id is not None,
        }
        if self.direction in required and not required[self.direction]:
            raise ValueError(f"{self.direction} requires its direction field")
        if self.direction != "AXIS" and self.axis is not None:
            raise ValueError("axis is only valid for AXIS")
        if self.direction != "VECTOR" and self.vector is not None:
            raise ValueError("vector is only valid for VECTOR")
        if self.direction != "VIEW_RAY" and self.capture_id is not None:
            raise ValueError("capture_id is only valid for VIEW_RAY")
        if self.vector is not None and self.vector.x == self.vector.y == self.vector.z == 0:
            raise ValueError("vector must be non-zero")
        return self


class ShrinkwrapOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["shrinkwrap"]
    selection_id: SelectionId
    surface_id: SurfaceId
    iterations: Annotated[StrictInt, Field(ge=1, le=16)] = 1
    factor: FiniteNumber = Field(default=1, gt=0, le=1)
    maximum_distance: FiniteNumber = Field(gt=0, le=1_000_000)
    offset: FiniteNumber = Field(default=0, ge=-100_000, le=100_000)
    side: Literal["ANY", "FRONT", "BACK"] = "ANY"
    on_miss: Literal["KEEP", "ERROR"] = "KEEP"


class InflateOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["inflate"]
    selection_id: SelectionId
    amount: FiniteNumber = Field(ge=-100_000, le=100_000)


class ExplicitPlane(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["EXPLICIT"]
    origin: Vector3
    normal: Vector3

    @model_validator(mode="after")
    def nonzero_normal(self) -> ExplicitPlane:
        if self.normal.x == self.normal.y == self.normal.z == 0:
            raise ValueError("normal must be non-zero")
        return self


class BestFitPlane(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["BEST_FIT"] = "BEST_FIT"


FlattenPlane = Annotated[ExplicitPlane | BestFitPlane, Field(discriminator="type")]


class FlattenOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["flatten"]
    selection_id: SelectionId
    plane: FlattenPlane = Field(default_factory=BestFitPlane)
    factor: FiniteNumber = Field(default=1, ge=0, le=1)
    space: CoordinateSpace = "LOCAL"


SemanticDeformOperation = (
    SetPositionsOperation
    | SmoothOperation
    | RelaxOperation
    | ProjectOperation
    | ShrinkwrapOperation
    | InflateOperation
    | FlattenOperation
)


SurfaceGeometry = Literal["BASE", "EVALUATED"]
SurfaceQueryMode = Literal["CLOSEST_POINT", "RAYCAST"]
ValidationCheck = Literal[
    "NON_MANIFOLD",
    "DEGENERATE",
    "ORIENTATION",
    "SELF_INTERSECTION",
    "TARGET_INTERSECTION",
    "DISTANCE",
    "PENETRATION",
    "UV_BOUNDS",
    "UV_DEGENERATE",
    "UV_OVERLAP",
    "UV_STRETCH",
]
