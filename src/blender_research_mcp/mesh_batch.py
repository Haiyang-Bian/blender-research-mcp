"""Closed public schemas for declarative revision-aware Mesh batches."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, model_validator

from blender_research_mcp.authoring import FiniteNumber, Vector3
from blender_research_mcp.mesh_authoring import MeshDataScope, MeshUserObject
from blender_research_mcp.mesh_resources import (
    CoordinateSpace,
    MeshDomain,
    SelectionId,
    SelectionQuery,
    SurfaceId,
    ValidationCheck,
)
from blender_research_mcp.mesh_topology import SelectionRemapMode, WeightMergeMode

BatchAlias = Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[A-Za-z][A-Za-z0-9_-]*$")]
Fingerprint = Annotated[str, Field(min_length=64, max_length=64)]


class BatchTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    alias: BatchAlias
    object_name: str = Field(min_length=1, max_length=255)
    expected_object_identity: str = Field(min_length=1, max_length=128)
    expected_mesh_identity: str = Field(min_length=1, max_length=128)
    expected_mesh_users: Annotated[StrictInt, Field(ge=1)]
    expected_mesh_user_objects: Annotated[
        tuple[MeshUserObject, ...], Field(min_length=1, max_length=256)
    ]
    expected_mesh_fingerprint: Fingerprint

    @model_validator(mode="after")
    def exact_user_set(self) -> BatchTarget:
        values = {
            (item.object_name, item.expected_object_identity)
            for item in self.expected_mesh_user_objects
        }
        if len(values) != len(self.expected_mesh_user_objects):
            raise ValueError("expected_mesh_user_objects must be unique")
        if self.expected_mesh_users != len(self.expected_mesh_user_objects):
            raise ValueError("expected_mesh_users must equal expected_mesh_user_objects length")
        return self


class BatchSelectionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["selection"]
    alias: BatchAlias
    selection_id: SelectionId
    target_alias: BatchAlias
    remap_mode: SelectionRemapMode = "ALL_MAPPED"
    weight_merge: WeightMergeMode = "MAX"


class BatchSurfaceInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["surface"]
    alias: BatchAlias
    surface_id: SurfaceId


BatchInput = Annotated[BatchSelectionInput | BatchSurfaceInput, Field(discriminator="type")]


class BatchSelectionQueryStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["selection_query"]
    target_alias: BatchAlias
    output_alias: BatchAlias
    domain: MeshDomain
    query: SelectionQuery
    remap_mode: SelectionRemapMode = "ALL_MAPPED"
    weight_merge: WeightMergeMode = "MAX"


class BatchCombineDerivation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["combine"]
    mode: Literal["UNION", "INTERSECTION", "DIFFERENCE"]
    selection_aliases: Annotated[tuple[BatchAlias, ...], Field(min_length=2, max_length=16)]


class BatchGrowDerivation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["expand", "contract"]
    selection_alias: BatchAlias
    steps: Annotated[StrictInt, Field(ge=1, le=64)] = 1


class BatchUnaryDerivation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["boundary", "connected"]
    selection_alias: BatchAlias


class BatchConvertDerivation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["convert"]
    selection_alias: BatchAlias
    domain: MeshDomain
    mode: Literal["ANY", "ALL"] = "ANY"


class BatchFalloffDerivation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["falloff"]
    selection_alias: BatchAlias
    radius: FiniteNumber = Field(gt=0, le=1_000_000)
    profile: Literal["LINEAR", "SMOOTH", "SHARP"] = "SMOOTH"
    space: CoordinateSpace = "LOCAL"


BatchSelectionDerivation = Annotated[
    BatchCombineDerivation
    | BatchGrowDerivation
    | BatchUnaryDerivation
    | BatchConvertDerivation
    | BatchFalloffDerivation,
    Field(discriminator="type"),
]


class BatchSelectionDeriveStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["selection_derive"]
    output_alias: BatchAlias
    operation: BatchSelectionDerivation
    remap_mode: SelectionRemapMode = "ALL_MAPPED"
    weight_merge: WeightMergeMode = "MAX"


class BatchSetPositions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["set_positions"]
    selection_alias: BatchAlias
    mode: Literal["ABSOLUTE", "OFFSET"] = "ABSOLUTE"
    space: CoordinateSpace = "LOCAL"
    positions: Annotated[tuple[Vector3, ...], Field(min_length=1, max_length=4096)]


class BatchSmooth(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["smooth", "relax"]
    selection_alias: BatchAlias
    iterations: Annotated[StrictInt, Field(ge=1, le=64)] = 1
    factor: FiniteNumber = Field(default=0.5, ge=0, le=1)
    preserve_boundary: StrictBool = True


class BatchProject(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["project"]
    selection_alias: BatchAlias
    surface_alias: BatchAlias
    direction: Literal["CLOSEST_POINT", "NORMAL", "AXIS", "VECTOR", "VIEW_RAY"] = "CLOSEST_POINT"
    axis: Literal["X", "Y", "Z", "-X", "-Y", "-Z"] | None = None
    vector: Vector3 | None = None
    capture_id: Annotated[str, Field(min_length=1, max_length=128)] | None = None
    maximum_distance: FiniteNumber = Field(gt=0, le=1_000_000)
    offset: FiniteNumber = Field(default=0, ge=-100_000, le=100_000)
    side: Literal["ANY", "FRONT", "BACK"] = "ANY"
    on_miss: Literal["KEEP", "ERROR"] = "KEEP"

    @model_validator(mode="after")
    def direction_fields(self) -> BatchProject:
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


class BatchShrinkwrap(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["shrinkwrap"]
    selection_alias: BatchAlias
    surface_alias: BatchAlias
    iterations: Annotated[StrictInt, Field(ge=1, le=16)] = 1
    factor: FiniteNumber = Field(default=1, gt=0, le=1)
    maximum_distance: FiniteNumber = Field(gt=0, le=1_000_000)
    offset: FiniteNumber = Field(default=0, ge=-100_000, le=100_000)
    side: Literal["ANY", "FRONT", "BACK"] = "ANY"
    on_miss: Literal["KEEP", "ERROR"] = "KEEP"


class BatchInflate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["inflate"]
    selection_alias: BatchAlias
    amount: FiniteNumber = Field(ge=-100_000, le=100_000)


class BatchExplicitPlane(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["EXPLICIT"]
    origin: Vector3
    normal: Vector3

    @model_validator(mode="after")
    def nonzero_normal(self) -> BatchExplicitPlane:
        if self.normal.x == self.normal.y == self.normal.z == 0:
            raise ValueError("normal must be non-zero")
        return self


class BatchBestFitPlane(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["BEST_FIT"] = "BEST_FIT"


class BatchFlatten(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["flatten"]
    selection_alias: BatchAlias
    plane: Annotated[BatchExplicitPlane | BatchBestFitPlane, Field(discriminator="type")] = Field(
        default_factory=BatchBestFitPlane
    )
    factor: FiniteNumber = Field(default=1, ge=0, le=1)
    space: CoordinateSpace = "LOCAL"


class BatchSubdivide(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["subdivide"]
    selection_alias: BatchAlias
    cuts: Annotated[StrictInt, Field(ge=1, le=32)] = 1
    smooth: FiniteNumber = Field(default=0, ge=0, le=1)
    smooth_falloff: Literal["LINEAR", "SMOOTH"] = "SMOOTH"
    quad_corner: Literal["STRAIGHT_CUT", "INNER_VERT", "PATH", "FAN"] = "STRAIGHT_CUT"
    use_grid_fill: StrictBool = False


class BatchLoopCut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["loop_cut"]
    selection_alias: BatchAlias
    cuts: Annotated[StrictInt, Field(ge=1, le=32)] = 1
    interpolation: Literal["LINEAR", "PATH", "SURFACE"] = "LINEAR"
    smooth: FiniteNumber = Field(default=0, ge=0, le=1)


class BatchBisect(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["bisect"]
    selection_alias: BatchAlias
    plane_origin: Vector3
    plane_normal: Vector3
    space: CoordinateSpace = "LOCAL"
    tolerance: FiniteNumber = Field(default=1e-6, ge=0, le=1)
    snap_to_plane: StrictBool = False
    clear_side: Literal["NONE", "POSITIVE", "NEGATIVE"] = "NONE"

    @model_validator(mode="after")
    def nonzero_normal(self) -> BatchBisect:
        if self.plane_normal.x == self.plane_normal.y == self.plane_normal.z == 0:
            raise ValueError("plane_normal must be non-zero")
        return self


class BatchSplit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["split"]
    selection_alias: BatchAlias


class BatchBridge(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["bridge"]
    selection_alias: BatchAlias
    twist_offset: Annotated[StrictInt, Field(ge=-4096, le=4096)] = 0
    material_slot_index: Annotated[StrictInt, Field(ge=0, le=63)] | None = None
    smooth: StrictBool = False


class BatchFill(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["fill"]
    selection_alias: BatchAlias
    method: Literal["NGON", "TRIANGLES"] = "NGON"
    max_sides: Annotated[StrictInt, Field(ge=0, le=1024)] = 0
    material_slot_index: Annotated[StrictInt, Field(ge=0, le=63)] | None = None
    smooth: StrictBool = False


class BatchGridFill(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["grid_fill"]
    selection_alias: BatchAlias
    use_interp_simple: StrictBool = False
    material_slot_index: Annotated[StrictInt, Field(ge=0, le=63)] | None = None
    smooth: StrictBool = False


BatchMeshOperation = Annotated[
    BatchSetPositions
    | BatchSmooth
    | BatchProject
    | BatchShrinkwrap
    | BatchInflate
    | BatchFlatten
    | BatchSubdivide
    | BatchLoopCut
    | BatchBisect
    | BatchSplit
    | BatchBridge
    | BatchFill
    | BatchGridFill,
    Field(discriminator="type"),
]


class CreatedSelectionAliases(BaseModel):
    model_config = ConfigDict(extra="forbid")
    vertex: BatchAlias | None = None
    edge: BatchAlias | None = None
    face: BatchAlias | None = None

    @model_validator(mode="after")
    def nonempty(self) -> CreatedSelectionAliases:
        if self.vertex is None and self.edge is None and self.face is None:
            raise ValueError("at least one created selection alias is required")
        return self


class BatchMeshEditStep(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["mesh_edit"]
    target_alias: BatchAlias
    data_scope: MeshDataScope
    operation: BatchMeshOperation
    map_alias: BatchAlias | None = None
    created_selection_aliases: CreatedSelectionAliases | None = None


class BatchMeshSeparateStep(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["mesh_separate"]
    target_alias: BatchAlias
    selection_alias: BatchAlias
    new_target_alias: BatchAlias
    new_selection_alias: BatchAlias
    source_map_alias: BatchAlias
    separated_map_alias: BatchAlias
    new_object_name: str = Field(min_length=1, max_length=255)
    collection_name: str | None = Field(default=None, min_length=1, max_length=255)
    expected_collection_identity: str | None = Field(default=None, min_length=1, max_length=128)

    @model_validator(mode="after")
    def collection_pair(self) -> BatchMeshSeparateStep:
        if (self.collection_name is None) != (self.expected_collection_identity is None):
            raise ValueError("collection name and identity must be supplied together")
        return self


class CountAtMost(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["count_at_most"]
    value: Annotated[StrictInt, Field(ge=0)]


class NumericAtMost(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["p95_at_most", "maximum_at_most", "penetration_at_most"]
    value: FiniteNumber


class RequireSignReliable(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["require_sign_reliable"]
    value: StrictBool = True


BatchAssertion = Annotated[
    CountAtMost | NumericAtMost | RequireSignReliable, Field(discriminator="type")
]


class BatchMeshValidateStep(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["mesh_validate"]
    selection_alias: BatchAlias
    check: ValidationCheck
    output_alias: BatchAlias
    surface_alias: BatchAlias | None = None
    tolerance: FiniteNumber = Field(default=1e-6, ge=0, le=1)
    maximum_distance: FiniteNumber = Field(default=1_000_000, gt=0, le=1_000_000)
    threshold: FiniteNumber | None = None
    sample_limit: Annotated[StrictInt, Field(ge=0, le=256)] = 64
    assertions: Annotated[tuple[BatchAssertion, ...], Field(max_length=8)] = ()

    @model_validator(mode="after")
    def surface_requirement(self) -> BatchMeshValidateStep:
        requires_surface = self.check in {"TARGET_INTERSECTION", "DISTANCE", "PENETRATION"}
        if requires_surface and self.surface_alias is None:
            raise ValueError(f"{self.check} requires surface_alias")
        if not requires_surface and self.surface_alias is not None:
            raise ValueError("surface_alias is only valid for surface validation checks")
        if len({item.type for item in self.assertions}) != len(self.assertions):
            raise ValueError("assertion types must be unique")
        return self


BatchStep = Annotated[
    BatchSelectionQueryStep
    | BatchSelectionDeriveStep
    | BatchMeshEditStep
    | BatchMeshSeparateStep
    | BatchMeshValidateStep,
    Field(discriminator="type"),
]


BatchTargets = Annotated[tuple[BatchTarget, ...], Field(min_length=1, max_length=8)]
BatchInputs = Annotated[tuple[BatchInput, ...], Field(max_length=64)]
BatchSteps = Annotated[tuple[BatchStep, ...], Field(min_length=1, max_length=32)]
