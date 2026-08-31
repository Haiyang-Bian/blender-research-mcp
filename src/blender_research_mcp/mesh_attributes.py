"""Closed public schemas for UV, deform-weight, and attribute-transfer authoring."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, model_validator

from blender_research_mcp.authoring import FiniteNumber
from blender_research_mcp.mesh_authoring import MeshDataScope, MeshUserObject
from blender_research_mcp.mesh_resources import SelectionId
from blender_research_mcp.mesh_topology import ComponentMapIds

Fingerprint = Annotated[str, Field(min_length=64, max_length=64)]
ResourceIdentity = Annotated[str, Field(min_length=1, max_length=128)]
LayerName = Annotated[str, Field(min_length=1, max_length=255)]
GroupName = Annotated[str, Field(min_length=1, max_length=255)]
MeshUVComponent = Literal["SUMMARY", "FACES", "LOOPS", "ISLANDS", "SEAMS"]
MeshWeightComponent = Literal["SUMMARY", "GROUPS", "VERTICES"]
UVCoordinate = Annotated[tuple[FiniteNumber, FiniteNumber], Field(min_length=2, max_length=2)]


class UVLayerRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    layer_name: LayerName
    expected_layer_identity: ResourceIdentity


class UVCornerRef(BaseModel):
    """Exact loop evidence; redundant face/corner/vertex indices reject stale addressing."""

    model_config = ConfigDict(extra="forbid")

    loop_index: Annotated[StrictInt, Field(ge=0)]
    face_index: Annotated[StrictInt, Field(ge=0)]
    corner_index: Annotated[StrictInt, Field(ge=0)]
    vertex_index: Annotated[StrictInt, Field(ge=0)]


class UVCoordinateWrite(UVCornerRef):
    uv: UVCoordinate


class UVLayerCreateOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["layer_create"]
    layer_name: LayerName
    source: Literal["EMPTY", "ACTIVE", "LAYER"] = "EMPTY"
    source_layer: UVLayerRef | None = None

    @model_validator(mode="after")
    def source_fields(self) -> UVLayerCreateOperation:
        if self.source == "LAYER" and self.source_layer is None:
            raise ValueError("LAYER source requires source_layer")
        if self.source != "LAYER" and self.source_layer is not None:
            raise ValueError("source_layer is only valid for LAYER source")
        return self


class UVLayerDeleteOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["layer_delete"]
    layer: UVLayerRef


class UVLayerRolesOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["layer_roles"]
    layer: UVLayerRef
    display: StrictBool | None = None
    render: StrictBool | None = None
    clone: StrictBool | None = None

    @model_validator(mode="after")
    def has_role(self) -> UVLayerRolesOperation:
        if self.display is None and self.render is None and self.clone is None:
            raise ValueError("at least one UV role is required")
        return self


class UVSeamSetOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["seam_set"]
    selection_id: SelectionId
    seam: StrictBool


class UVCoordinateSetOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["coordinate_set"]
    layer: UVLayerRef
    mode: Literal["ABSOLUTE", "OFFSET"] = "ABSOLUTE"
    corners: Annotated[tuple[UVCoordinateWrite, ...], Field(min_length=1, max_length=4096)]

    @model_validator(mode="after")
    def unique_loops(self) -> UVCoordinateSetOperation:
        indices = [item.loop_index for item in self.corners]
        if len(indices) != len(set(indices)):
            raise ValueError("UV corner loop indices must be unique")
        return self


class UVTransformOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["transform"]
    layer: UVLayerRef
    selection_id: SelectionId
    scope: Literal["FACES", "ISLANDS"] = "ISLANDS"
    translation: UVCoordinate = (0.0, 0.0)
    rotation_degrees: FiniteNumber = Field(default=0, ge=-360_000, le=360_000)
    scale: UVCoordinate = (1.0, 1.0)
    pivot: UVCoordinate | Literal["MEDIAN"] = "MEDIAN"

    @model_validator(mode="after")
    def bounded_transform(self) -> UVTransformOperation:
        if (
            all(value == 0 for value in self.translation)
            and self.rotation_degrees == 0
            and all(value == 1 for value in self.scale)
        ):
            raise ValueError("UV transform must change translation, rotation, or scale")
        if any(abs(value) > 1_000_000 for value in (*self.translation, *self.scale)):
            raise ValueError("UV transform components are out of range")
        return self


class UVPinSetOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["pin_set"]
    layer: UVLayerRef
    corners: Annotated[tuple[UVCornerRef, ...], Field(min_length=1, max_length=4096)]
    pinned: StrictBool

    @model_validator(mode="after")
    def unique_loops(self) -> UVPinSetOperation:
        indices = [item.loop_index for item in self.corners]
        if len(indices) != len(set(indices)):
            raise ValueError("UV corner loop indices must be unique")
        return self


class UVUnwrapOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["unwrap"]
    layer: UVLayerRef
    selection_id: SelectionId
    method: Literal["ANGLE_BASED", "CONFORMAL"] = "ANGLE_BASED"
    fill_holes: StrictBool = True
    correct_aspect: StrictBool = True
    use_subsurf_data: StrictBool = False
    margin: FiniteNumber = Field(default=0.001, ge=0, le=1)
    pin_policy: Literal["RESPECT", "IGNORE", "ERROR_IF_PRESENT"] = "RESPECT"


class UVPackOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["pack"]
    layer: UVLayerRef
    selection_id: SelectionId
    tile_u: Annotated[StrictInt, Field(ge=0, le=999)] = 0
    tile_v: Annotated[StrictInt, Field(ge=0, le=999)] = 0
    rotate: StrictBool = True
    scale: StrictBool = True
    margin: FiniteNumber = Field(default=0.001, ge=0, le=1)
    pinned_policy: Literal["KEEP", "MOVE", "ERROR_IF_PRESENT"] = "KEEP"


UVOperation = Annotated[
    UVLayerCreateOperation
    | UVLayerDeleteOperation
    | UVLayerRolesOperation
    | UVSeamSetOperation
    | UVCoordinateSetOperation
    | UVTransformOperation
    | UVPinSetOperation
    | UVUnwrapOperation
    | UVPackOperation,
    Field(discriminator="type"),
]


class VertexGroupRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    group_name: GroupName
    expected_group_identity: ResourceIdentity


class VertexWeightValue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    vertex_index: Annotated[StrictInt, Field(ge=0)]
    weight: FiniteNumber = Field(ge=0, le=1)


class WeightGroupCreateOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["group_create"]
    group_name: GroupName
    lock_weight: StrictBool = False


class WeightGroupRenameOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["group_rename"]
    group: VertexGroupRef
    new_name: GroupName
    allow_locked: StrictBool = False


class WeightGroupDeleteOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["group_delete"]
    group: VertexGroupRef
    allow_locked: StrictBool = False


class WeightSetOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["set"]
    group: VertexGroupRef
    selection_id: SelectionId
    mode: Literal["REPLACE", "ADD", "SUBTRACT"] = "REPLACE"
    value: FiniteNumber | None = Field(default=None, ge=0, le=1)
    values: (
        Annotated[tuple[VertexWeightValue, ...], Field(min_length=1, max_length=4096)] | None
    ) = None
    use_selection_weights: StrictBool = False
    allow_locked: StrictBool = False

    @model_validator(mode="after")
    def one_value_source(self) -> WeightSetOperation:
        choices = (
            int(self.value is not None)
            + int(self.values is not None)
            + int(self.use_selection_weights)
        )
        if choices != 1:
            raise ValueError("set requires exactly one of value, values, or use_selection_weights")
        if self.values is not None:
            indices = [item.vertex_index for item in self.values]
            if len(indices) != len(set(indices)):
                raise ValueError("weight vertex indices must be unique")
        return self


class WeightClearOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["clear"]
    selection_id: SelectionId
    groups: Annotated[tuple[VertexGroupRef, ...], Field(min_length=1, max_length=256)] | None = None
    all_groups: StrictBool = False
    allow_locked: StrictBool = False

    @model_validator(mode="after")
    def one_group_scope(self) -> WeightClearOperation:
        if (self.groups is None) == (not self.all_groups):
            raise ValueError("clear requires either groups or all_groups=true")
        return self


class WeightNormalizeOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["normalize"]
    selection_id: SelectionId
    groups: Annotated[tuple[VertexGroupRef, ...], Field(min_length=1, max_length=256)]
    keep_locked: StrictBool = True
    zero_policy: Literal["KEEP", "ERROR"] = "KEEP"
    target_total: FiniteNumber = Field(default=1, gt=0, le=1)


class WeightLimitTotalOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["limit_total"]
    selection_id: SelectionId
    maximum_influences: Annotated[StrictInt, Field(ge=1, le=32)] = 4
    normalize: StrictBool = True
    keep_locked: StrictBool = True


WeightOperation = Annotated[
    WeightGroupCreateOperation
    | WeightGroupRenameOperation
    | WeightGroupDeleteOperation
    | WeightSetOperation
    | WeightClearOperation
    | WeightNormalizeOperation
    | WeightLimitTotalOperation,
    Field(discriminator="type"),
]


class AttributeMeshTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    object_name: str = Field(min_length=1, max_length=255)
    expected_object_identity: ResourceIdentity
    expected_mesh_identity: ResourceIdentity
    expected_mesh_users: Annotated[StrictInt, Field(ge=1)]
    expected_mesh_user_objects: Annotated[
        tuple[MeshUserObject, ...], Field(min_length=1, max_length=256)
    ]
    expected_mesh_fingerprint: Fingerprint
    data_scope: MeshDataScope = "OBJECT"


class UVTransfer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["UV"]
    source_layer: UVLayerRef
    target_layer_name: LayerName
    expected_target_layer_identity: ResourceIdentity | None = None
    target_selection_id: SelectionId
    mapping: Literal["TOPOLOGY", "NEAREST_SURFACE"]
    component_map_ids: ComponentMapIds | None = None
    source_geometry: Literal["BASE", "EVALUATED_DEFORM_ONLY"] = "BASE"
    maximum_distance: FiniteNumber = Field(gt=0, le=1_000_000)
    on_miss: Literal["KEEP", "ERROR"] = "ERROR"


class GroupMapping(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: VertexGroupRef
    target_group_name: GroupName


class WeightTransfer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["WEIGHTS"]
    groups: Annotated[tuple[GroupMapping, ...], Field(min_length=1, max_length=256)]
    target_selection_id: SelectionId
    mapping: Literal["TOPOLOGY", "NEAREST_VERTEX", "NEAREST_SURFACE"]
    component_map_ids: ComponentMapIds | None = None
    source_geometry: Literal["BASE", "EVALUATED_DEFORM_ONLY"] = "BASE"
    maximum_distance: FiniteNumber = Field(gt=0, le=1_000_000)
    on_miss: Literal["KEEP", "ERROR"] = "ERROR"


AttributeTransfer = Annotated[UVTransfer | WeightTransfer, Field(discriminator="type")]
