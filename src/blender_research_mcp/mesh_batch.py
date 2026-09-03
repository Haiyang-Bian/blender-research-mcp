"""Closed public schemas for declarative revision-aware Mesh batches."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, model_validator

from blender_research_mcp.authoring import FiniteNumber, Vector3
from blender_research_mcp.library_assets import LibraryEntry
from blender_research_mcp.mesh_attributes import (
    AttributeComponentMapIds,
    GroupMapping,
    GroupName,
    LayerName,
    UVCoordinate,
    UVCoordinateSetOperation,
    UVLayerCreateOperation,
    UVLayerDeleteOperation,
    UVLayerRef,
    UVLayerRolesOperation,
    UVPinSetOperation,
    VertexGroupRef,
    VertexWeightValue,
    WeightGroupCreateOperation,
    WeightGroupDeleteOperation,
    WeightGroupRenameOperation,
)
from blender_research_mcp.mesh_authoring import MeshDataScope, MeshUserObject
from blender_research_mcp.mesh_component_catalog import (
    DEFAULT_COMPONENT_CATALOG_METRICS,
    ComponentCatalogMetrics,
    ComponentIdentities,
)
from blender_research_mcp.mesh_join import MeshJoinAttributes, MeshJoinDependencies
from blender_research_mcp.mesh_modular import (
    ArmatureTarget,
    ExtractOutputPolicy,
    MaterializeCopyPolicy,
    MaterializeEvaluation,
    RigGroupScope,
    RigModifierPolicy,
)
from blender_research_mcp.mesh_resources import (
    CoordinateSpace,
    MeshDomain,
    SelectionId,
    SelectionQuery,
    SurfaceGeometry,
    SurfaceId,
    ValidationCheck,
)
from blender_research_mcp.mesh_topology import (
    MeshAttributePolicy,
    SelectionRemapMode,
    WeightMergeMode,
)
from blender_research_mcp.object_settings import ObjectSettingPatches
from blender_research_mcp.scene_organization import (
    ParentTransformMode,
    SceneRootCollectionParent,
)

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


class BatchObjectInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["object"]
    alias: BatchAlias
    object_name: str = Field(min_length=1, max_length=255)
    expected_object_identity: str = Field(min_length=1, max_length=128)
    expected_object_structure_fingerprint: Fingerprint


class BatchArmatureInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["armature"]
    alias: BatchAlias
    target: ArmatureTarget


class BatchCollectionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["collection"]
    alias: BatchAlias
    collection_name: str = Field(min_length=1, max_length=255)
    expected_collection_identity: str = Field(min_length=1, max_length=128)
    expected_collection_structure_fingerprint: Fingerprint


class BatchComponentCatalogInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["component_catalog"]
    alias: BatchAlias
    component_catalog_id: str = Field(min_length=1, max_length=128)
    target_alias: BatchAlias


class BatchLibraryInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["library"]
    alias: BatchAlias
    path: str = Field(min_length=1, max_length=32767)
    expected_file_sha256: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    expected_size_bytes: Annotated[StrictInt, Field(ge=12)]


BatchInput = Annotated[
    BatchSelectionInput
    | BatchSurfaceInput
    | BatchObjectInput
    | BatchArmatureInput
    | BatchCollectionInput
    | BatchComponentCatalogInput
    | BatchLibraryInput,
    Field(discriminator="type"),
]


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
    maximum_displacement: FiniteNumber | None = Field(default=None, ge=0, le=1_000_000)
    selection_alias: BatchAlias
    mode: Literal["ABSOLUTE", "OFFSET"] = "ABSOLUTE"
    space: CoordinateSpace = "LOCAL"
    positions: Annotated[tuple[Vector3, ...], Field(min_length=1, max_length=4096)]


class BatchSmooth(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["smooth", "relax"]
    maximum_displacement: FiniteNumber | None = Field(default=None, ge=0, le=1_000_000)
    selection_alias: BatchAlias
    iterations: Annotated[StrictInt, Field(ge=1, le=64)] = 1
    factor: FiniteNumber = Field(default=0.5, ge=0, le=1)
    preserve_boundary: StrictBool = True


class BatchProject(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["project"]
    maximum_displacement: FiniteNumber | None = Field(default=None, ge=0, le=1_000_000)
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
    maximum_displacement: FiniteNumber | None = Field(default=None, ge=0, le=1_000_000)
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
    maximum_displacement: FiniteNumber | None = Field(default=None, ge=0, le=1_000_000)
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
    maximum_displacement: FiniteNumber | None = Field(default=None, ge=0, le=1_000_000)
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
    attribute_policy: MeshAttributePolicy = Field(default_factory=MeshAttributePolicy)


class BatchLoopCut(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["loop_cut"]
    selection_alias: BatchAlias
    cuts: Annotated[StrictInt, Field(ge=1, le=32)] = 1
    interpolation: Literal["LINEAR", "PATH", "SURFACE"] = "LINEAR"
    smooth: FiniteNumber = Field(default=0, ge=0, le=1)
    attribute_policy: MeshAttributePolicy = Field(default_factory=MeshAttributePolicy)


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
    attribute_policy: MeshAttributePolicy = Field(default_factory=MeshAttributePolicy)

    @model_validator(mode="after")
    def nonzero_normal(self) -> BatchBisect:
        if self.plane_normal.x == self.plane_normal.y == self.plane_normal.z == 0:
            raise ValueError("plane_normal must be non-zero")
        return self


class BatchSplit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["split"]
    selection_alias: BatchAlias
    attribute_policy: MeshAttributePolicy = Field(default_factory=MeshAttributePolicy)


class BatchDirectedPath(BaseModel):
    model_config = ConfigDict(extra="forbid")
    selection_alias: BatchAlias
    start_vertex_alias: BatchAlias


class BatchFourPaths(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["FOUR_PATHS"]
    paths: tuple[BatchDirectedPath, BatchDirectedPath, BatchDirectedPath, BatchDirectedPath]


class BatchClosedLoop(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["CLOSED_LOOP"]
    selection_alias: BatchAlias
    corner_aliases: tuple[BatchAlias, BatchAlias, BatchAlias, BatchAlias]


class BatchBridge(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["bridge"]
    selection_alias: BatchAlias | None = None
    paths: tuple[BatchDirectedPath, BatchDirectedPath] | None = None
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
    def exclusive_input(self) -> BatchBridge:
        if (self.selection_alias is None) == (self.paths is None):
            raise ValueError("Provide exactly one of selection_alias and paths")
        if self.paths is None and (self.cuts or self.allow_hidden or self.uv_creation):
            raise ValueError("Open-bridge options require paths")
        if self.paths is not None and self.twist_offset:
            raise ValueError("Path starts define correspondence")
        return self


class BatchFill(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["fill"]
    selection_alias: BatchAlias
    method: Literal["NGON", "TRIANGLES"] = "NGON"
    max_sides: Annotated[StrictInt, Field(ge=0, le=1024)] = 0
    material_slot_index: Annotated[StrictInt, Field(ge=0, le=63)] | None = None
    smooth: StrictBool = False
    attribute_policy: MeshAttributePolicy = Field(default_factory=MeshAttributePolicy)


class BatchGridFill(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["grid_fill"]
    selection_alias: BatchAlias | None = None
    boundary: Annotated[BatchFourPaths | BatchClosedLoop, Field(discriminator="type")] | None = None
    allow_hidden: StrictBool = False
    uv_creation: dict[str, Literal["BOUNDARY_INTERPOLATE", "INDEPENDENT_ISLAND"]] = Field(
        default_factory=dict
    )
    use_interp_simple: StrictBool = False
    material_slot_index: Annotated[StrictInt, Field(ge=0, le=63)] | None = None
    smooth: StrictBool = False
    attribute_policy: MeshAttributePolicy = Field(default_factory=MeshAttributePolicy)

    @model_validator(mode="after")
    def exclusive_input(self) -> BatchGridFill:
        if (self.selection_alias is None) == (self.boundary is None):
            raise ValueError("Provide exactly one of selection_alias and boundary")
        return self


class BatchCreateEdge(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["create_edge"]
    vertex_aliases: tuple[BatchAlias, BatchAlias]
    allow_hidden: StrictBool = False
    attribute_policy: MeshAttributePolicy = Field(default_factory=MeshAttributePolicy)


class BatchCreateFace(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["create_face"]
    vertex_aliases: Annotated[tuple[BatchAlias, ...], Field(min_length=3, max_length=4)]
    allow_hidden: StrictBool = False
    material_slot_index: Annotated[StrictInt, Field(ge=0, le=63)] | None = None
    smooth: StrictBool = False
    uv_creation: dict[str, Literal["BOUNDARY_INTERPOLATE", "INDEPENDENT_ISLAND"]] = Field(
        default_factory=dict
    )
    attribute_policy: MeshAttributePolicy = Field(default_factory=MeshAttributePolicy)


class BatchWeldVertices(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["weld_vertices"]
    selection_aliases: Annotated[tuple[BatchAlias, ...], Field(min_length=1, max_length=8)]
    mode: Literal["ALL_SELECTED", "CROSS_SELECTIONS"] = "CROSS_SELECTIONS"
    maximum_distance: FiniteNumber = Field(gt=0, le=1_000_000)
    destination: Literal["LOWEST_INDEX", "CENTER"] = "LOWEST_INDEX"
    weight_merge: Literal["MAX", "AVERAGE", "SUM_NORMALIZE"] = "MAX"
    attribute_policy: MeshAttributePolicy = Field(default_factory=MeshAttributePolicy)

    @model_validator(mode="after")
    def exact_selection_groups(self) -> BatchWeldVertices:
        if len(set(self.selection_aliases)) != len(self.selection_aliases):
            raise ValueError("selection_aliases must be unique")
        if self.mode == "CROSS_SELECTIONS" and len(self.selection_aliases) < 2:
            raise ValueError("CROSS_SELECTIONS requires at least two selections")
        return self


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
    | BatchGridFill
    | BatchWeldVertices
    | BatchCreateEdge
    | BatchCreateFace,
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
    source_attribute_policy: MeshAttributePolicy = Field(default_factory=MeshAttributePolicy)
    separated_attribute_policy: MeshAttributePolicy = Field(default_factory=MeshAttributePolicy)

    @model_validator(mode="after")
    def collection_pair(self) -> BatchMeshSeparateStep:
        if (self.collection_name is None) != (self.expected_collection_identity is None):
            raise ValueError("collection name and identity must be supplied together")
        return self


class BatchComponentCatalogPrepareStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["component_catalog_prepare"]
    selection_alias: BatchAlias
    output_catalog_alias: BatchAlias
    include: ComponentCatalogMetrics = DEFAULT_COMPONENT_CATALOG_METRICS


class BatchComponentCatalogSelectStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["component_catalog_select"]
    catalog_alias: BatchAlias
    component_identities: ComponentIdentities
    output_selection_alias: BatchAlias
    remap_mode: SelectionRemapMode = "ALL_MAPPED"
    weight_merge: WeightMergeMode = "MAX"


class BatchMeshMaterializeStep(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    type: Literal["mesh_materialize"]
    source_target_alias: BatchAlias
    evaluation: MaterializeEvaluation
    new_object_name: str = Field(min_length=1, max_length=255)
    copy_policy: MaterializeCopyPolicy = Field(alias="copy")
    output_target_alias: BatchAlias
    collection_alias: BatchAlias | None = None
    map_alias: BatchAlias | None = None


class BatchMeshExtractStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["mesh_extract"]
    target_alias: BatchAlias
    selection_alias: BatchAlias
    new_target_alias: BatchAlias
    new_selection_alias: BatchAlias
    source_map_alias: BatchAlias
    extracted_map_alias: BatchAlias
    new_object_name: str = Field(min_length=1, max_length=255)
    output_policy: ExtractOutputPolicy
    source_attribute_policy: MeshAttributePolicy
    extracted_attribute_policy: MeshAttributePolicy
    collection_alias: BatchAlias | None = None


class BatchCollectionAliasParent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["COLLECTION_ALIAS"]
    collection_alias: BatchAlias


BatchCollectionParent = Annotated[
    SceneRootCollectionParent | BatchCollectionAliasParent,
    Field(discriminator="type"),
]


class BatchCollectionCreateStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["collection_create"]
    name: str = Field(min_length=1, max_length=255)
    parent: BatchCollectionParent
    output_collection_alias: BatchAlias


class BatchCollectionLinkStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["collection_link_object", "collection_unlink_object"]
    collection_alias: BatchAlias
    object_alias: BatchAlias


class BatchObjectParentSetStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["object_parent_set"]
    child_alias: BatchAlias
    parent_alias: BatchAlias
    transform_mode: ParentTransformMode


class BatchObjectParentClearStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["object_parent_clear"]
    child_alias: BatchAlias
    expected_parent_alias: BatchAlias
    transform_mode: ParentTransformMode


class BatchRigBindStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["rig_bind"]
    mesh_target_alias: BatchAlias
    armature_alias: BatchAlias
    modifier: RigModifierPolicy
    parenting: Literal["NONE", "KEEP_WORLD", "KEEP_LOCAL"]
    group_scope: RigGroupScope
    output_binding_alias: BatchAlias


class BatchLibraryObjectOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["OBJECT"]
    new_object_name: str = Field(min_length=1, max_length=255)
    collection_alias: BatchAlias


class BatchLibraryCollectionOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["COLLECTION"]
    new_collection_name: str = Field(min_length=1, max_length=255)
    parent: BatchCollectionParent


class BatchLibraryMeshOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["MESH"]
    new_mesh_name: str = Field(min_length=1, max_length=255)
    new_object_name: str = Field(min_length=1, max_length=255)
    collection_alias: BatchAlias

    @model_validator(mode="after")
    def distinct_names(self) -> BatchLibraryMeshOutput:
        if self.new_mesh_name == self.new_object_name:
            raise ValueError("new_mesh_name and new_object_name must be distinct")
        return self


BatchLibraryOutput = Annotated[
    BatchLibraryObjectOutput | BatchLibraryCollectionOutput | BatchLibraryMeshOutput,
    Field(discriminator="type"),
]
BatchLibraryRootKind = Literal["OBJECT", "MESH_TARGET", "ARMATURE", "COLLECTION"]


class BatchLibraryExport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_object_name: str = Field(min_length=1, max_length=255)
    expected_entry_identity: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
    output_alias: BatchAlias
    alias_kind: Literal["OBJECT", "MESH_TARGET", "ARMATURE"]


class BatchLibraryAppendStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["library_append"]
    library_alias: BatchAlias
    entry: LibraryEntry
    output: BatchLibraryOutput
    output_root_alias: BatchAlias
    root_alias_kind: BatchLibraryRootKind
    exports: Annotated[tuple[BatchLibraryExport, ...], Field(max_length=64)] = ()

    @model_validator(mode="after")
    def output_contract(self) -> BatchLibraryAppendStep:
        if self.entry.type != self.output.type:
            raise ValueError("entry.type and output.type must match")
        expected_kind = {
            "COLLECTION": "COLLECTION",
            "MESH": "MESH_TARGET",
        }.get(self.entry.type)
        if expected_kind is not None and self.root_alias_kind != expected_kind:
            raise ValueError(f"{self.entry.type} root requires {expected_kind} alias kind")
        if self.entry.type != "COLLECTION" and self.exports:
            raise ValueError("exports are only valid for a COLLECTION root")
        names = [item.source_object_name for item in self.exports]
        aliases = [item.output_alias for item in self.exports]
        if len(set(names)) != len(names) or len(set(aliases)) != len(aliases):
            raise ValueError("Library export names and aliases must be unique")
        return self


class BatchObjectSetStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["object_set"]
    object_alias: BatchAlias
    patches: ObjectSettingPatches


class BatchMeshSurfacePrepareStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["mesh_surface_prepare"]
    target_alias: BatchAlias
    geometry: SurfaceGeometry = "EVALUATED"
    output_surface_alias: BatchAlias


class BatchMeshJoinSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_alias: BatchAlias
    map_alias: BatchAlias
    boundary_selection_alias: BatchAlias
    selection_aliases: Annotated[tuple[BatchAlias, ...], Field(max_length=8)] = ()
    rebound_selection_aliases: Annotated[tuple[BatchAlias, ...], Field(max_length=8)] = ()

    @model_validator(mode="after")
    def paired_rebound_aliases(self) -> BatchMeshJoinSource:
        if len(self.selection_aliases) != len(self.rebound_selection_aliases):
            raise ValueError(
                "selection_aliases and rebound_selection_aliases must have equal length"
            )
        if len(set(self.selection_aliases)) != len(self.selection_aliases):
            raise ValueError("selection_aliases must be unique per source")
        return self


class BatchJoinWorldCoordinates(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["WORLD"]


class BatchJoinSourceCoordinates(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["SOURCE_OBJECT"]
    source_target_alias: BatchAlias


BatchJoinCoordinateFrame = Annotated[
    BatchJoinWorldCoordinates | BatchJoinSourceCoordinates,
    Field(discriminator="type"),
]


class BatchMeshJoinStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["mesh_join"]
    sources: Annotated[tuple[BatchMeshJoinSource, ...], Field(min_length=2, max_length=32)]
    output_target_alias: BatchAlias
    new_object_name: str = Field(min_length=1, max_length=255)
    new_mesh_name: str = Field(min_length=1, max_length=255)
    collection_alias: BatchAlias
    coordinate_frame: BatchJoinCoordinateFrame
    source_disposition: Literal["KEEP", "DELETE_ON_COMMIT"] = "KEEP"
    attributes: MeshJoinAttributes
    dependencies: MeshJoinDependencies

    @model_validator(mode="after")
    def exact_sources_and_output(self) -> BatchMeshJoinStep:
        target_aliases = [source.target_alias for source in self.sources]
        aliases = [
            alias
            for source in self.sources
            for alias in (
                source.map_alias,
                source.boundary_selection_alias,
                *source.rebound_selection_aliases,
            )
        ]
        if len(set(target_aliases)) != len(target_aliases):
            raise ValueError("mesh_join source target aliases must be unique")
        if len(set(aliases)) != len(aliases):
            raise ValueError("mesh_join output aliases must be unique")
        if self.new_object_name == self.new_mesh_name:
            raise ValueError("new_object_name and new_mesh_name must be distinct")
        if (
            isinstance(self.coordinate_frame, BatchJoinSourceCoordinates)
            and self.coordinate_frame.source_target_alias not in target_aliases
        ):
            raise ValueError("SOURCE_OBJECT frame must reference one of the join sources")
        return self


class BatchUVSeamSetOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["seam_set"]
    selection_alias: BatchAlias
    seam: StrictBool


class BatchUVTransformOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["transform"]
    layer: UVLayerRef
    selection_alias: BatchAlias
    scope: Literal["FACES", "ISLANDS"] = "ISLANDS"
    translation: UVCoordinate = (0.0, 0.0)
    rotation_degrees: FiniteNumber = Field(default=0, ge=-360_000, le=360_000)
    scale: UVCoordinate = (1.0, 1.0)
    pivot: UVCoordinate | Literal["MEDIAN"] = "MEDIAN"

    @model_validator(mode="after")
    def bounded_transform(self) -> BatchUVTransformOperation:
        if (
            all(value == 0 for value in self.translation)
            and self.rotation_degrees == 0
            and all(value == 1 for value in self.scale)
        ):
            raise ValueError("UV transform must change translation, rotation, or scale")
        if any(abs(value) > 1_000_000 for value in (*self.translation, *self.scale)):
            raise ValueError("UV transform components are out of range")
        return self


class BatchUVUnwrapOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["unwrap"]
    layer: UVLayerRef
    selection_alias: BatchAlias
    method: Literal["ANGLE_BASED", "CONFORMAL"] = "ANGLE_BASED"
    fill_holes: StrictBool = True
    correct_aspect: StrictBool = True
    use_subsurf_data: StrictBool = False
    margin: FiniteNumber = Field(default=0.001, ge=0, le=1)
    pin_policy: Literal["RESPECT", "IGNORE", "ERROR_IF_PRESENT"] = "RESPECT"


class BatchUVPackOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["pack"]
    layer: UVLayerRef
    selection_alias: BatchAlias
    tile_u: Annotated[StrictInt, Field(ge=0, le=999)] = 0
    tile_v: Annotated[StrictInt, Field(ge=0, le=999)] = 0
    rotate: StrictBool = True
    scale: StrictBool = True
    margin: FiniteNumber = Field(default=0.001, ge=0, le=1)
    pinned_policy: Literal["KEEP", "MOVE", "ERROR_IF_PRESENT"] = "KEEP"


BatchUVOperation = Annotated[
    UVLayerCreateOperation
    | UVLayerDeleteOperation
    | UVLayerRolesOperation
    | BatchUVSeamSetOperation
    | UVCoordinateSetOperation
    | BatchUVTransformOperation
    | UVPinSetOperation
    | BatchUVUnwrapOperation
    | BatchUVPackOperation,
    Field(discriminator="type"),
]


class BatchUVEditStep(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["uv_edit"]
    target_alias: BatchAlias
    data_scope: MeshDataScope
    operation: BatchUVOperation


class BatchWeightSetOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["set"]
    group: VertexGroupRef
    selection_alias: BatchAlias
    mode: Literal["REPLACE", "ADD", "SUBTRACT"] = "REPLACE"
    value: FiniteNumber | None = Field(default=None, ge=0, le=1)
    values: (
        Annotated[tuple[VertexWeightValue, ...], Field(min_length=1, max_length=4096)] | None
    ) = None
    use_selection_weights: StrictBool = False
    allow_locked: StrictBool = False

    @model_validator(mode="after")
    def one_value_source(self) -> BatchWeightSetOperation:
        choices = (
            int(self.value is not None)
            + int(self.values is not None)
            + int(self.use_selection_weights)
        )
        if choices != 1:
            raise ValueError("set requires exactly one value source")
        return self


class BatchWeightClearOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["clear"]
    selection_alias: BatchAlias
    groups: Annotated[tuple[VertexGroupRef, ...], Field(min_length=1, max_length=256)] | None = None
    all_groups: StrictBool = False
    allow_locked: StrictBool = False

    @model_validator(mode="after")
    def one_group_scope(self) -> BatchWeightClearOperation:
        if (self.groups is None) == (not self.all_groups):
            raise ValueError("clear requires either groups or all_groups=true")
        return self


class BatchWeightNormalizeOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["normalize"]
    selection_alias: BatchAlias
    groups: Annotated[tuple[VertexGroupRef, ...], Field(min_length=1, max_length=256)]
    keep_locked: StrictBool = True
    zero_policy: Literal["KEEP", "ERROR"] = "KEEP"
    target_total: FiniteNumber = Field(default=1, gt=0, le=1)


class BatchWeightLimitTotalOperation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["limit_total"]
    selection_alias: BatchAlias
    maximum_influences: Annotated[StrictInt, Field(ge=1, le=32)] = 4
    normalize: StrictBool = True
    keep_locked: StrictBool = True


BatchWeightOperation = Annotated[
    WeightGroupCreateOperation
    | WeightGroupRenameOperation
    | WeightGroupDeleteOperation
    | BatchWeightSetOperation
    | BatchWeightClearOperation
    | BatchWeightNormalizeOperation
    | BatchWeightLimitTotalOperation,
    Field(discriminator="type"),
]


class BatchWeightsEditStep(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["weights_edit"]
    target_alias: BatchAlias
    data_scope: MeshDataScope
    operation: BatchWeightOperation


class BatchUVTransfer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["UV"]
    source_layer: UVLayerRef
    target_layer_name: LayerName
    expected_target_layer_identity: str | None = Field(default=None, min_length=1, max_length=128)
    target_selection_alias: BatchAlias
    mapping: Literal["TOPOLOGY", "NEAREST_SURFACE"]
    component_map_ids: AttributeComponentMapIds | None = None
    source_geometry: Literal["BASE", "EVALUATED_DEFORM_ONLY"] = "BASE"
    maximum_distance: FiniteNumber = Field(gt=0, le=1_000_000)
    on_miss: Literal["KEEP", "ERROR"] = "ERROR"


class BatchWeightTransfer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["WEIGHTS"]
    groups: Annotated[tuple[GroupMapping, ...], Field(min_length=1, max_length=256)]
    target_selection_alias: BatchAlias
    mapping: Literal["TOPOLOGY", "NEAREST_VERTEX", "NEAREST_SURFACE"]
    component_map_ids: AttributeComponentMapIds | None = None
    source_geometry: Literal["BASE", "EVALUATED_DEFORM_ONLY"] = "BASE"
    maximum_distance: FiniteNumber = Field(gt=0, le=1_000_000)
    on_miss: Literal["KEEP", "ERROR"] = "ERROR"


BatchAttributeTransfer = Annotated[
    BatchUVTransfer | BatchWeightTransfer, Field(discriminator="type")
]


class BatchAttributeTransferStep(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["attribute_transfer"]
    source_target_alias: BatchAlias
    target_alias: BatchAlias
    target_data_scope: MeshDataScope = "OBJECT"
    transfer: BatchAttributeTransfer


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
    scope: Literal["SELECTION", "SELECTION_AND_NEIGHBORS"] | None = None
    output_alias: BatchAlias
    surface_alias: BatchAlias | None = None
    tolerance: FiniteNumber = Field(default=1e-6, ge=0, le=1)
    maximum_distance: FiniteNumber = Field(default=1_000_000, gt=0, le=1_000_000)
    threshold: FiniteNumber | None = None
    sample_limit: Annotated[StrictInt, Field(ge=0, le=256)] = 64
    group_names: Annotated[tuple[GroupName, ...], Field(min_length=1, max_length=256)] | None = None
    target_total: FiniteNumber = Field(default=1, gt=0, le=1)
    maximum_influences: Annotated[StrictInt, Field(ge=1, le=32)] = 4
    assertions: Annotated[tuple[BatchAssertion, ...], Field(max_length=8)] = ()

    @model_validator(mode="after")
    def surface_requirement(self) -> BatchMeshValidateStep:
        requires_surface = self.check in {"TARGET_INTERSECTION", "DISTANCE", "PENETRATION"}
        if requires_surface and self.surface_alias is None:
            raise ValueError(f"{self.check} requires surface_alias")
        if not requires_surface and self.surface_alias is not None:
            raise ValueError("surface_alias is only valid for surface validation checks")
        if (
            self.check
            not in {
                "WEIGHT_SUM",
                "WEIGHT_INFLUENCE_LIMIT",
                "WEIGHT_UNASSIGNED",
                "DEFORM_GROUP_MISMATCH",
            }
            and self.group_names is not None
        ):
            raise ValueError("weight evidence is only valid for weight validation checks")
        if len({item.type for item in self.assertions}) != len(self.assertions):
            raise ValueError("assertion types must be unique")
        return self


BatchStep = Annotated[
    BatchSelectionQueryStep
    | BatchSelectionDeriveStep
    | BatchMeshEditStep
    | BatchMeshSeparateStep
    | BatchUVEditStep
    | BatchWeightsEditStep
    | BatchAttributeTransferStep
    | BatchMeshValidateStep
    | BatchComponentCatalogPrepareStep
    | BatchComponentCatalogSelectStep
    | BatchMeshMaterializeStep
    | BatchMeshExtractStep
    | BatchCollectionCreateStep
    | BatchCollectionLinkStep
    | BatchObjectParentSetStep
    | BatchObjectParentClearStep
    | BatchRigBindStep
    | BatchLibraryAppendStep
    | BatchObjectSetStep
    | BatchMeshSurfacePrepareStep
    | BatchMeshJoinStep,
    Field(discriminator="type"),
]


BatchTargets = Annotated[tuple[BatchTarget, ...], Field(min_length=1, max_length=8)]
BatchInputs = Annotated[tuple[BatchInput, ...], Field(max_length=64)]
BatchSteps = Annotated[tuple[BatchStep, ...], Field(min_length=1, max_length=32)]
