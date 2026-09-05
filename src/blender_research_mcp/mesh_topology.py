"""Closed schemas for topology lineage resources and bounded topology operations."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, model_validator

from blender_research_mcp.authoring import FiniteNumber, Vector3
from blender_research_mcp.mesh_resources import CoordinateSpace, SelectionId

ComponentMapId = Annotated[str, Field(min_length=1, max_length=128)]
ComponentMapIds = Annotated[tuple[ComponentMapId, ...], Field(min_length=2, max_length=8)]
ComponentMapDomain = Literal["SUMMARY", "VERTEX", "EDGE", "FACE"]
ComponentMapDirection = Literal["FORWARD", "REVERSE", "CREATED", "DELETED", "CREATION_EVIDENCE"]
SelectionRemapMode = Literal["ALL_MAPPED", "EXACT_SURVIVORS", "STRICT"]
WeightMergeMode = Literal["MAX", "AVERAGE"]
AttributeMigrationMode = Literal["PRESERVE_INTERPOLATE", "ERROR_IF_PRESENT", "DISCARD"]


class MeshAttributePolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    uv: AttributeMigrationMode = "PRESERVE_INTERPOLATE"
    weights: AttributeMigrationMode = "PRESERVE_INTERPOLATE"


class SubdivideOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["subdivide"]
    selection_id: SelectionId
    cuts: Annotated[StrictInt, Field(ge=1, le=32)] = 1
    smooth: FiniteNumber = Field(default=0, ge=0, le=1)
    smooth_falloff: Literal["LINEAR", "SMOOTH"] = "SMOOTH"
    quad_corner: Literal["STRAIGHT_CUT", "INNER_VERT", "PATH", "FAN"] = "STRAIGHT_CUT"
    use_grid_fill: StrictBool = False
    attribute_policy: MeshAttributePolicy = Field(default_factory=MeshAttributePolicy)


class LoopCutOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["loop_cut"]
    selection_id: SelectionId
    cuts: Annotated[StrictInt, Field(ge=1, le=32)] = 1
    interpolation: Literal["LINEAR", "PATH", "SURFACE"] = "LINEAR"
    smooth: FiniteNumber = Field(default=0, ge=0, le=1)
    attribute_policy: MeshAttributePolicy = Field(default_factory=MeshAttributePolicy)


class BisectOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["bisect"]
    selection_id: SelectionId
    plane_origin: Vector3
    plane_normal: Vector3
    space: CoordinateSpace = "LOCAL"
    tolerance: FiniteNumber = Field(default=1e-6, ge=0, le=1)
    snap_to_plane: StrictBool = False
    clear_side: Literal["NONE", "POSITIVE", "NEGATIVE"] = "NONE"
    attribute_policy: MeshAttributePolicy = Field(default_factory=MeshAttributePolicy)

    @model_validator(mode="after")
    def nonzero_normal(self) -> BisectOperation:
        if self.plane_normal.x == self.plane_normal.y == self.plane_normal.z == 0:
            raise ValueError("plane_normal must be non-zero")
        return self


class SplitOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["split"]
    selection_id: SelectionId
    attribute_policy: MeshAttributePolicy = Field(default_factory=MeshAttributePolicy)


class DirectedPath(BaseModel):
    model_config = ConfigDict(extra="forbid")

    selection_id: SelectionId
    start_vertex: SelectionId


class FourPathsBoundary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["FOUR_PATHS"]
    paths: tuple[DirectedPath, DirectedPath, DirectedPath, DirectedPath]


class ClosedLoopBoundary(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["CLOSED_LOOP"]
    selection_id: SelectionId
    corners: tuple[SelectionId, SelectionId, SelectionId, SelectionId]


PatchBoundary = Annotated[FourPathsBoundary | ClosedLoopBoundary, Field(discriminator="type")]


class BoundaryAnnotations(BaseModel):
    model_config = ConfigDict(extra="forbid")
    paths: Annotated[tuple[DirectedPath, ...], Field(max_length=4)] = ()
    problem_vertices: Annotated[tuple[SelectionId, ...], Field(max_length=64)] = ()


class BridgeOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["bridge"]
    selection_id: SelectionId | None = None
    paths: tuple[DirectedPath, DirectedPath] | None = None
    cuts: Annotated[StrictInt, Field(ge=0, le=32)] = 0
    allow_hidden: StrictBool = False
    uv_creation: dict[str, Literal["BOUNDARY_INTERPOLATE", "INDEPENDENT_ISLAND"]] = Field(
        default_factory=dict
    )
    twist_offset: Annotated[StrictInt, Field(ge=-4096, le=4096)] = 0
    material_slot_index: Annotated[StrictInt, Field(ge=0, le=63)] | None = None
    smooth: StrictBool = False
    attribute_policy: MeshAttributePolicy = Field(default_factory=MeshAttributePolicy)

    @model_validator(mode="after")
    def exclusive_input(self) -> BridgeOperation:
        if (self.selection_id is None) == (self.paths is None):
            raise ValueError("Provide exactly one of selection_id or paths")
        if self.paths is None and (self.cuts or self.allow_hidden or self.uv_creation):
            raise ValueError("cuts, allow_hidden and uv_creation require directed paths")
        if self.paths is not None and self.twist_offset:
            raise ValueError(
                "Directed path starts define correspondence; twist_offset must be zero"
            )
        return self


class FillOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["fill"]
    selection_id: SelectionId
    method: Literal["NGON", "TRIANGLES"] = "NGON"
    max_sides: Annotated[StrictInt, Field(ge=0, le=1024)] = 0
    material_slot_index: Annotated[StrictInt, Field(ge=0, le=63)] | None = None
    smooth: StrictBool = False
    attribute_policy: MeshAttributePolicy = Field(default_factory=MeshAttributePolicy)


class GridFillOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["grid_fill"]
    selection_id: SelectionId | None = None
    boundary: PatchBoundary | None = None
    allow_hidden: StrictBool = False
    uv_creation: dict[str, Literal["BOUNDARY_INTERPOLATE", "INDEPENDENT_ISLAND"]] = Field(
        default_factory=dict
    )
    use_interp_simple: StrictBool = False
    material_slot_index: Annotated[StrictInt, Field(ge=0, le=63)] | None = None
    smooth: StrictBool = False
    attribute_policy: MeshAttributePolicy = Field(default_factory=MeshAttributePolicy)

    @model_validator(mode="after")
    def exclusive_input(self) -> GridFillOperation:
        if (self.selection_id is None) == (self.boundary is None):
            raise ValueError("Provide exactly one of selection_id or boundary")
        if self.boundary is None and self.allow_hidden:
            raise ValueError("allow_hidden requires an explicit boundary")
        return self


class CreateEdgeOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["create_edge"]
    vertices: tuple[SelectionId, SelectionId]
    allow_hidden: StrictBool = False
    attribute_policy: MeshAttributePolicy = Field(default_factory=MeshAttributePolicy)


class CreateFaceOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["create_face"]
    vertices: Annotated[tuple[SelectionId, ...], Field(min_length=3, max_length=4)]
    allow_hidden: StrictBool = False
    material_slot_index: Annotated[StrictInt, Field(ge=0, le=63)] | None = None
    smooth: StrictBool = False
    uv_creation: dict[str, Literal["BOUNDARY_INTERPOLATE", "INDEPENDENT_ISLAND"]] = Field(
        default_factory=dict
    )
    attribute_policy: MeshAttributePolicy = Field(default_factory=MeshAttributePolicy)


TopologyOperation = (
    SubdivideOperation
    | LoopCutOperation
    | BisectOperation
    | SplitOperation
    | BridgeOperation
    | FillOperation
    | GridFillOperation
    | CreateEdgeOperation
    | CreateFaceOperation
)
