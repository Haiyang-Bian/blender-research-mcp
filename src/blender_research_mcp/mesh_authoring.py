"""Closed-world schemas for exact base-Mesh inspection and semantic editing."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, model_validator

from blender_research_mcp.authoring import FiniteNumber, Vector3
from blender_research_mcp.mesh_resources import (
    FlattenOperation,
    InflateOperation,
    ProjectOperation,
    RelaxOperation,
    SetPositionsOperation,
    ShrinkwrapOperation,
    SmoothOperation,
)
from blender_research_mcp.mesh_topology import (
    BisectOperation,
    BridgeOperation,
    CreateEdgeOperation,
    CreateFaceOperation,
    FillOperation,
    GridFillOperation,
    LoopCutOperation,
    MeshAttributePolicy,
    SplitOperation,
    SubdivideOperation,
)

MeshComponent = Literal["summary", "vertices", "edges", "faces"]
MeshDataScope = Literal["OBJECT", "SHARED_DATA"]
MeshElementKind = Literal["vertices", "edges", "faces"]
MeshIndex = Annotated[StrictInt, Field(ge=0)]
MeshIndices = Annotated[tuple[MeshIndex, ...], Field(min_length=1, max_length=4096)]


class MeshUserObject(BaseModel):
    model_config = ConfigDict(extra="forbid")

    object_name: str = Field(min_length=1, max_length=255)
    expected_object_identity: str = Field(min_length=1, max_length=128)


class ElementTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: MeshElementKind
    indices: MeshIndices

    @model_validator(mode="after")
    def unique_indices(self) -> ElementTarget:
        if len(set(self.indices)) != len(self.indices):
            raise ValueError("component indices must be unique")
        return self


class MedianPivot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["MEDIAN"] = "MEDIAN"


class PointPivot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["POINT"] = "POINT"
    value: Vector3


MeshPivot = Annotated[MedianPivot | PointPivot, Field(discriminator="type")]


def _vector_values(value: Vector3) -> tuple[float, float, float]:
    return value.x, value.y, value.z


class TransformOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["transform"]
    target: ElementTarget
    translation: Vector3 | None = None
    rotation_euler_degrees: Vector3 | None = None
    scale: Vector3 | None = None
    pivot: MeshPivot = Field(default_factory=MedianPivot)

    @model_validator(mode="after")
    def validate_transform(self) -> TransformOperation:
        if self.translation is None and self.rotation_euler_degrees is None and self.scale is None:
            raise ValueError("translation, rotation_euler_degrees, and/or scale is required")
        if self.translation is not None and any(
            abs(value) > 1_000_000 for value in _vector_values(self.translation)
        ):
            raise ValueError("translation components are out of range")
        if self.rotation_euler_degrees is not None and any(
            abs(value) > 360_000 for value in _vector_values(self.rotation_euler_degrees)
        ):
            raise ValueError("rotation components are out of range")
        if self.scale is not None and any(
            abs(value) > 1000 for value in _vector_values(self.scale)
        ):
            raise ValueError("scale components are out of range")
        return self


class ExtrudeFacesOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["extrude_faces"]
    face_indices: MeshIndices
    offset: Vector3
    attribute_policy: MeshAttributePolicy = Field(default_factory=MeshAttributePolicy)

    @model_validator(mode="after")
    def validate_extrusion(self) -> ExtrudeFacesOperation:
        if len(set(self.face_indices)) != len(self.face_indices):
            raise ValueError("face_indices must be unique")
        values = _vector_values(self.offset)
        if all(value == 0 for value in values):
            raise ValueError("extrude offset must be non-zero")
        if any(abs(value) > 1_000_000 for value in values):
            raise ValueError("extrude offset components are out of range")
        return self


class InsetFacesOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["inset_faces"]
    face_indices: MeshIndices
    thickness: FiniteNumber = Field(ge=0, le=100_000)
    depth: FiniteNumber = Field(default=0.0, ge=-100_000, le=100_000)
    individual: StrictBool = False
    even_offset: StrictBool = True
    attribute_policy: MeshAttributePolicy = Field(default_factory=MeshAttributePolicy)

    @model_validator(mode="after")
    def validate_inset(self) -> InsetFacesOperation:
        if len(set(self.face_indices)) != len(self.face_indices):
            raise ValueError("face_indices must be unique")
        if self.thickness == 0 and self.depth == 0:
            raise ValueError("inset thickness and depth cannot both be zero")
        return self


class BevelEdgesOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["bevel_edges"]
    edge_indices: MeshIndices
    width: FiniteNumber = Field(gt=0, le=100_000)
    segments: Annotated[StrictInt, Field(ge=1, le=32)] = 1
    profile: FiniteNumber = Field(default=0.5, ge=0, le=1)
    clamp_overlap: StrictBool = True
    attribute_policy: MeshAttributePolicy = Field(default_factory=MeshAttributePolicy)

    @model_validator(mode="after")
    def unique_edges(self) -> BevelEdgesOperation:
        if len(set(self.edge_indices)) != len(self.edge_indices):
            raise ValueError("edge_indices must be unique")
        return self


class DeleteOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["delete"]
    target: ElementTarget
    attribute_policy: MeshAttributePolicy = Field(default_factory=MeshAttributePolicy)


class DissolveOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["dissolve"]
    target: ElementTarget
    use_face_split: StrictBool = False
    use_boundary_tear: StrictBool = False
    use_verts: StrictBool = False
    attribute_policy: MeshAttributePolicy = Field(default_factory=MeshAttributePolicy)

    @model_validator(mode="after")
    def validate_target_options(self) -> DissolveOperation:
        if self.target.type == "vertices" and self.use_verts:
            raise ValueError("use_verts is not valid when dissolving vertices")
        if self.target.type == "edges" and self.use_boundary_tear:
            raise ValueError("use_boundary_tear is only valid when dissolving vertices")
        if self.target.type == "faces" and (self.use_face_split or self.use_boundary_tear):
            raise ValueError("face_split and boundary_tear are not valid for faces")
        return self


class MergeVerticesOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["merge_vertices"]
    vertex_indices: Annotated[tuple[MeshIndex, ...], Field(min_length=2, max_length=4096)]
    destination: Literal["CENTER", "TARGET"] = "CENTER"
    target_index: MeshIndex | None = None
    attribute_policy: MeshAttributePolicy = Field(default_factory=MeshAttributePolicy)

    @model_validator(mode="after")
    def validate_merge(self) -> MergeVerticesOperation:
        if len(set(self.vertex_indices)) != len(self.vertex_indices):
            raise ValueError("vertex_indices must be unique")
        if self.destination == "TARGET":
            if self.target_index is None or self.target_index not in self.vertex_indices:
                raise ValueError("TARGET destination requires target_index in vertex_indices")
        elif self.target_index is not None:
            raise ValueError("target_index is only valid for TARGET destination")
        return self


class WeldVerticesOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["weld_vertices"]
    selection_ids: Annotated[tuple[str, ...], Field(min_length=1, max_length=8)]
    mode: Literal["ALL_SELECTED", "CROSS_SELECTIONS"] = "CROSS_SELECTIONS"
    maximum_distance: FiniteNumber = Field(gt=0, le=1_000_000)
    destination: Literal["LOWEST_INDEX", "CENTER"] = "LOWEST_INDEX"
    weight_merge: Literal["MAX", "AVERAGE", "SUM_NORMALIZE"] = "MAX"
    attribute_policy: MeshAttributePolicy = Field(default_factory=MeshAttributePolicy)

    @model_validator(mode="after")
    def validate_selections(self) -> WeldVerticesOperation:
        if len(set(self.selection_ids)) != len(self.selection_ids):
            raise ValueError("selection_ids must be unique")
        if self.mode == "CROSS_SELECTIONS" and len(self.selection_ids) < 2:
            raise ValueError("CROSS_SELECTIONS requires at least two SelectionSets")
        return self


class FaceSettingsOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["face_settings"]
    face_indices: MeshIndices
    material_slot_index: Annotated[StrictInt, Field(ge=0, le=63)] | None = None
    smooth: StrictBool | None = None

    @model_validator(mode="after")
    def validate_settings(self) -> FaceSettingsOperation:
        if len(set(self.face_indices)) != len(self.face_indices):
            raise ValueError("face_indices must be unique")
        if self.material_slot_index is None and self.smooth is None:
            raise ValueError("material_slot_index and/or smooth is required")
        return self


class NormalsOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["normals"]
    mode: Literal["FLIP", "RECALCULATE_OUTSIDE"]
    face_indices: MeshIndices | None = None

    @model_validator(mode="after")
    def validate_normals(self) -> NormalsOperation:
        if self.mode == "FLIP":
            if self.face_indices is None:
                raise ValueError("FLIP requires face_indices")
            if len(set(self.face_indices)) != len(self.face_indices):
                raise ValueError("face_indices must be unique")
        elif self.face_indices is not None:
            raise ValueError("RECALCULATE_OUTSIDE always targets the complete mesh")
        return self


MeshOperation = Annotated[
    TransformOperation
    | ExtrudeFacesOperation
    | InsetFacesOperation
    | BevelEdgesOperation
    | DeleteOperation
    | DissolveOperation
    | MergeVerticesOperation
    | WeldVerticesOperation
    | FaceSettingsOperation
    | NormalsOperation
    | SetPositionsOperation
    | SmoothOperation
    | RelaxOperation
    | ProjectOperation
    | ShrinkwrapOperation
    | InflateOperation
    | FlattenOperation
    | SubdivideOperation
    | LoopCutOperation
    | BisectOperation
    | SplitOperation
    | BridgeOperation
    | FillOperation
    | GridFillOperation
    | CreateEdgeOperation
    | CreateFaceOperation,
    Field(discriminator="type"),
]
