"""FastMCP stdio surface for the Blender research bridge."""

from __future__ import annotations

import base64
import json
import math
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Any, Literal

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult, ContentBlock, ImageContent, TextContent, ToolAnnotations
from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    WithJsonSchema,
)

from blender_research_mcp.authoring import (
    ColorSpec,
    FiniteNumber,
    ImageColorSpace,
    InitialTransform,
    LinkIdentities,
    LocationAxisPatch,
    MaterialAssignMode,
    MaterialDefinition,
    MaterialTextureChannel,
    ObjectDefinition,
    RenderSamples,
    RenderSize,
    RotationAxisPatch,
    ScaleAxisPatch,
    SceneKinds,
    TextureCoordinate,
    TextureMapping,
    Vector3,
    require_capability,
)
from blender_research_mcp.client import BridgeClient
from blender_research_mcp.comparison import (
    ComparisonCandidates,
    ComparisonCapture,
    ComparisonRequest,
    ComparisonTarget,
    ModifierSettingTarget,
    ObjectSettingTarget,
    run_lookdev_comparison,
)
from blender_research_mcp.constants import DEFAULT_PORT, PACKAGE_VERSION
from blender_research_mcp.lifecycle import (
    DEFAULT_LAUNCH_TIMEOUT_SECONDS,
    ApplicationManager,
)
from blender_research_mcp.mesh_attributes import (
    AttributeMeshTarget,
    AttributeTransfer,
    MeshUVComponent,
    MeshWeightComponent,
    UVOperation,
    WeightOperation,
)
from blender_research_mcp.mesh_authoring import (
    MeshComponent,
    MeshDataScope,
    MeshOperation,
    MeshUserObject,
)
from blender_research_mcp.mesh_batch import BatchInputs, BatchSteps, BatchTargets
from blender_research_mcp.mesh_component_catalog import (
    DEFAULT_COMPONENT_CATALOG_METRICS,
    ComponentCatalogId,
    ComponentCatalogMetrics,
    ComponentIdentities,
)
from blender_research_mcp.mesh_modular import (
    ArmatureTarget,
    ExtractMeshTarget,
    ExtractOutputPolicy,
    MaterializeCopyPolicy,
    MaterializeEvaluation,
    MaterializeSource,
    RigGroupScope,
    RigMeshTarget,
    RigModifierPolicy,
)
from blender_research_mcp.mesh_resources import (
    MeshDomain,
    MeshRevisionId,
    SelectionDerivation,
    SelectionId,
    SelectionQuery,
    SurfaceGeometry,
    SurfaceId,
    SurfaceQueryMode,
    ValidationCheck,
)
from blender_research_mcp.mesh_separation import MeshObjectName
from blender_research_mcp.mesh_topology import (
    ComponentMapDirection,
    ComponentMapDomain,
    ComponentMapId,
    ComponentMapIds,
    MeshAttributePolicy,
    SelectionRemapMode,
    WeightMergeMode,
)
from blender_research_mcp.modifier_authoring import (
    ModifierDefinition,
    ModifierSettings,
    ModifierStackIndex,
    ModifierType,
)
from blender_research_mcp.object_settings import ObjectSettingPatches
from blender_research_mcp.observation import (
    capture_image,
    collect_observation_bundle,
    settle_capture_generation,
)
from blender_research_mcp.rendering import request_render_preview, request_render_save
from blender_research_mcp.scene_organization import (
    CollectionParent,
    ParentTransformMode,
    SceneOrganizationFingerprint,
)

READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)
CONTEXT_MUTATION = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)
PREVIEW_MUTATION = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)
SCENE_MUTATION = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=True,
    idempotentHint=True,
    openWorldHint=False,
)
ObjectName = Annotated[str, Field(min_length=1, max_length=255)]
SnapshotId = Annotated[str, Field(min_length=1, max_length=128)]
CaptureSize = Annotated[int, Field(ge=256, le=1600)]
BundleCaptureSize = Annotated[int, Field(ge=256, le=1200)]
TransactionId = Annotated[str, Field(min_length=1, max_length=128)]
IdempotencyKey = Annotated[str, Field(min_length=1, max_length=128)]
SceneGeneration = Annotated[int, Field(ge=0)]
TransactionLabel = Annotated[str, Field(max_length=200)]
SemanticView = Literal["FRONT", "RIGHT", "TOP", "BACK", "LEFT", "BOTTOM", "CURRENT"]
DisplayMode = Literal["CURRENT", "WIREFRAME", "SOLID", "MATERIAL", "RENDERED"]
OverlaysMode = Literal["CURRENT", "ON", "OFF"]
BundleViews = Annotated[tuple[SemanticView, ...], Field(min_length=1, max_length=3)]
CaptureId = Annotated[str, Field(min_length=1, max_length=128)]
NormalizedCoordinate = Annotated[float, Field(ge=0.0, le=1.0)]
SessionIdentity = Annotated[str, Field(min_length=1, max_length=128)]
ModifierName = Annotated[str, Field(min_length=1, max_length=255)]
ShapeKeyName = Annotated[str, Field(min_length=1, max_length=255)]
FiniteFloat = Annotated[float, Field(allow_inf_nan=False)]
MaterialSlotIndex = Annotated[StrictInt, Field(ge=0, le=63)]
MaterialName = Annotated[str, Field(min_length=1, max_length=255)]
NodeName = Annotated[str, Field(min_length=1, max_length=255)]
SocketIdentifier = Annotated[str, Field(min_length=1, max_length=255)]
MaterialUsers = Annotated[StrictInt, Field(ge=1)]
ProjectPath = Annotated[str, Field(min_length=1, max_length=32767)]
ImageName = Annotated[str, Field(min_length=1, max_length=255)]
AssetPath = Annotated[str, Field(min_length=1, max_length=32767)]
DataUsers = Annotated[StrictInt, Field(ge=1)]


def _validate_material_input_value(value: Any) -> Any:
    if type(value) in {bool, int}:
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("material scalar values must be finite")
        return value
    if (
        isinstance(value, (list, tuple))
        and len(value) in {3, 4}
        and all(type(component) is float and math.isfinite(component) for component in value)
    ):
        return value
    raise ValueError(
        "value must be a boolean, integer, finite float, or 3/4 finite-float components"
    )


MaterialInputValue = Annotated[
    Any,
    BeforeValidator(_validate_material_input_value),
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


class OrbitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    yaw_degrees: float = Field(default=0.0, ge=-180.0, le=180.0)
    pitch_degrees: float = Field(default=0.0, ge=-89.0, le=89.0)


def create_server(
    *,
    port: int = DEFAULT_PORT,
    blender_executable: str | None = None,
    launch_timeout: float = DEFAULT_LAUNCH_TIMEOUT_SECONDS,
) -> FastMCP[Any]:
    client = BridgeClient(port=port)
    application = ApplicationManager(
        client,
        blender_executable=blender_executable,
        launch_timeout=launch_timeout,
    )

    @asynccontextmanager
    async def lifespan(_server: FastMCP[Any]) -> AsyncIterator[ApplicationManager]:
        try:
            yield application
        finally:
            await application.close()

    server = FastMCP(
        name="blender-research-mcp",
        instructions=(
            "Local semantic Blender research tools. Application launch is independent from "
            "project opening. Tools do not expose arbitrary Python execution."
        ),
        lifespan=lifespan,
    )
    # FastMCP 1.x does not expose the low-level Server version in its constructor.
    server._mcp_server.version = PACKAGE_VERSION

    @server.tool(
        name="application.status",
        description=(
            "Report whether a compatible Blender MCP session is running and summarize it."
        ),
        annotations=READ_ONLY,
        structured_output=True,
    )
    async def application_status() -> dict[str, Any]:
        return await application.status()

    @server.tool(
        name="application.launch",
        description=(
            "Reuse a compatible Blender MCP session or launch the configured Blender with "
            "the version-matched session add-on. This tool never opens a project."
        ),
        annotations=PREVIEW_MUTATION,
        structured_output=True,
    )
    async def application_launch() -> dict[str, Any]:
        return await application.launch()

    @server.tool(
        name="application.quit",
        description=(
            "Commit an active transaction, optionally save the current project, and quit "
            "Blender on the next main-thread tick."
        ),
        annotations=SCENE_MUTATION,
        structured_output=True,
    )
    async def application_quit(
        save_current: StrictBool = True,
        save_current_as: ProjectPath | None = None,
    ) -> dict[str, Any]:
        return await application.quit(
            save_current=save_current,
            save_current_as=save_current_as,
        )

    @server.tool(
        name="project.status",
        description=(
            "Read the current Blender project path, dirty state, generation, transaction, "
            "and most recent lifecycle operation without requiring a 3D viewport."
        ),
        annotations=READ_ONLY,
        structured_output=True,
    )
    async def project_status() -> dict[str, Any]:
        return await application.project_status()

    @server.tool(
        name="project.save",
        description=(
            "Commit the active transaction and save the current project, optionally using "
            "an absolute Save As path. Existing targets are overwritten."
        ),
        annotations=SCENE_MUTATION,
        structured_output=True,
    )
    async def project_save(path: ProjectPath | None = None) -> dict[str, Any]:
        return await application.project_save(path)

    @server.tool(
        name="project.open",
        description=(
            "Open an existing absolute .blend path on the next main-thread tick. By default "
            "the current transaction is committed and dirty current project is saved."
        ),
        annotations=SCENE_MUTATION,
        structured_output=True,
    )
    async def project_open(
        path: ProjectPath,
        save_current: StrictBool = True,
        save_current_as: ProjectPath | None = None,
        use_scripts: StrictBool = True,
        load_ui: StrictBool = True,
    ) -> dict[str, Any]:
        return await application.project_open(
            path,
            save_current=save_current,
            save_current_as=save_current_as,
            use_scripts=use_scripts,
            load_ui=load_ui,
        )

    @server.tool(
        name="project.reload",
        description=(
            "Reload the current saved .blend file on the next main-thread tick. Unsaved "
            "changes are discarded unless save_current is true."
        ),
        annotations=SCENE_MUTATION,
        structured_output=True,
    )
    async def project_reload(
        save_current: StrictBool = False,
        use_scripts: StrictBool = True,
        load_ui: StrictBool = True,
    ) -> dict[str, Any]:
        return await application.project_reload(
            save_current=save_current,
            use_scripts=use_scripts,
            load_ui=load_ui,
        )

    @server.tool(
        name="connection.ping",
        description="Check the authenticated Blender connection, versions, and UI heartbeat.",
        annotations=READ_ONLY,
        structured_output=True,
    )
    async def connection_ping() -> dict[str, Any]:
        return await client.call("connection.ping", read_only=True)

    @server.tool(
        name="context.get",
        description="Read the active Blender mode, selection, scene, and available viewports.",
        annotations=READ_ONLY,
        structured_output=True,
    )
    async def context_get() -> dict[str, Any]:
        return await client.call("context.get", read_only=True)

    @server.tool(
        name="context.snapshot",
        description="Store the current user context in Blender and return a session-local token.",
        annotations=READ_ONLY,
        structured_output=True,
    )
    async def context_snapshot(viewport_id: str | None = None) -> dict[str, Any]:
        return await client.call(
            "context.snapshot",
            {"viewport_id": viewport_id},
            read_only=True,
        )

    @server.tool(
        name="context.restore",
        description="Restore a previously captured session-local Blender context snapshot.",
        annotations=CONTEXT_MUTATION,
        structured_output=True,
    )
    async def context_restore(snapshot_id: SnapshotId) -> dict[str, Any]:
        return await client.call(
            "context.restore",
            {"snapshot_id": snapshot_id},
            read_only=False,
        )

    @server.tool(
        name="scene.inspect",
        description=(
            "Return bounded scene objects, collections, materials, images, world, active "
            "camera, and render summaries with session-local identities."
        ),
        annotations=READ_ONLY,
        structured_output=True,
    )
    async def scene_inspect(
        kinds: SceneKinds,
        name_filter: Annotated[str, Field(min_length=1, max_length=255)] | None = None,
        limit: Annotated[StrictInt, Field(ge=1, le=256)] = 100,
    ) -> dict[str, Any]:
        if len(set(kinds)) != len(kinds):
            raise ValueError("kinds must be unique")
        await require_capability(client, "scene_inspection")
        return await client.call(
            "scene.inspect",
            {"kinds": list(kinds), "name_filter": name_filter, "limit": limit},
            read_only=True,
        )

    @server.tool(
        name="collection.inspect",
        description=(
            "Inspect one exact Collection, its parents, children, direct object links, "
            "library state, and structural fingerprint."
        ),
        annotations=READ_ONLY,
        structured_output=True,
    )
    async def collection_inspect(
        collection_name: ObjectName,
        offset: Annotated[StrictInt, Field(ge=0)] = 0,
        limit: Annotated[StrictInt, Field(ge=1, le=256)] = 256,
    ) -> dict[str, Any]:
        await require_capability(client, "collection_authoring")
        return await client.call(
            "collection.inspect",
            {"collection_name": collection_name, "offset": offset, "limit": limit},
            read_only=True,
        )

    @server.tool(
        name="object.inspect",
        description="Inspect one exact Blender object without changing user context.",
        annotations=READ_ONLY,
        structured_output=True,
    )
    async def object_inspect(object_name: ObjectName) -> dict[str, Any]:
        return await client.call(
            "object.inspect",
            {"object_name": object_name},
            read_only=True,
        )

    @server.tool(
        name="object.geometry.inspect",
        description=(
            "Return a bounded evaluated mesh summary without exposing raw vertex or face arrays."
        ),
        annotations=READ_ONLY,
        structured_output=True,
    )
    async def object_geometry_inspect(object_name: ObjectName) -> dict[str, Any]:
        return await client.call(
            "object.geometry.inspect",
            {"object_name": object_name},
            read_only=True,
        )

    @server.tool(
        name="mesh.inspect",
        description=(
            "Inspect one exact base Mesh data-block with guarded topology/state "
            "fingerprints and one bounded page of vertices, edges, or faces."
        ),
        annotations=READ_ONLY,
        structured_output=True,
    )
    async def mesh_inspect(
        object_name: ObjectName,
        component: MeshComponent = "summary",
        offset: Annotated[StrictInt, Field(ge=0)] = 0,
        limit: Annotated[StrictInt, Field(ge=1, le=512)] = 256,
    ) -> dict[str, Any]:
        await require_capability(client, "mesh_topology")
        return await client.call(
            "mesh.inspect",
            {
                "object_name": object_name,
                "component": component,
                "offset": offset,
                "limit": limit,
            },
            read_only=True,
        )

    @server.tool(
        name="mesh.uv.inspect",
        description=(
            "Inspect exact UV layers, roles, seams, pins, loops, faces, and islands "
            "without changing Blender or UV Editor selection."
        ),
        annotations=READ_ONLY,
        structured_output=True,
    )
    async def mesh_uv_inspect(
        object_name: ObjectName,
        layer_name: Annotated[str, Field(min_length=1, max_length=255)] | None = None,
        component: MeshUVComponent = "SUMMARY",
        offset: Annotated[StrictInt, Field(ge=0)] = 0,
        limit: Annotated[StrictInt, Field(ge=1, le=512)] = 256,
    ) -> dict[str, Any]:
        await require_capability(client, "mesh_uv")
        return await client.call(
            "mesh.uv.inspect",
            {
                "object_name": object_name,
                "layer_name": layer_name,
                "component": component,
                "offset": offset,
                "limit": limit,
            },
            read_only=True,
        )

    @server.tool(
        name="mesh.weights.inspect",
        description=(
            "Inspect exact ordered Vertex Groups, Armature/Bone matches, sparse per-vertex "
            "weights, and schema/value fingerprints without entering Weight Paint mode."
        ),
        annotations=READ_ONLY,
        structured_output=True,
    )
    async def mesh_weights_inspect(
        object_name: ObjectName,
        group_name: Annotated[str, Field(min_length=1, max_length=255)] | None = None,
        component: MeshWeightComponent = "SUMMARY",
        offset: Annotated[StrictInt, Field(ge=0)] = 0,
        limit: Annotated[StrictInt, Field(ge=1, le=512)] = 256,
    ) -> dict[str, Any]:
        await require_capability(client, "mesh_weights")
        return await client.call(
            "mesh.weights.inspect",
            {
                "object_name": object_name,
                "group_name": group_name,
                "component": component,
                "offset": offset,
                "limit": limit,
            },
            read_only=True,
        )

    @server.tool(
        name="mesh.selection.query",
        description=(
            "Create one immutable revision-bound SelectionSet from an exact semantic "
            "Mesh component query without changing Blender UI selection."
        ),
        annotations=READ_ONLY,
        structured_output=True,
    )
    async def mesh_selection_query(
        object_name: ObjectName,
        expected_object_identity: SessionIdentity,
        expected_mesh_identity: SessionIdentity,
        expected_mesh_revision_id: MeshRevisionId,
        domain: MeshDomain,
        query: SelectionQuery,
    ) -> dict[str, Any]:
        await require_capability(client, "mesh_selection")
        return await client.call(
            "mesh.selection.query",
            {
                "object_name": object_name,
                "expected_object_identity": expected_object_identity,
                "expected_mesh_identity": expected_mesh_identity,
                "expected_mesh_revision_id": expected_mesh_revision_id,
                "domain": domain,
                "query": query.model_dump(),
            },
            read_only=True,
        )

    @server.tool(
        name="mesh.selection.derive",
        description=(
            "Derive one immutable SelectionSet through bounded set, topology, domain, "
            "or geodesic-falloff operations."
        ),
        annotations=READ_ONLY,
        structured_output=True,
    )
    async def mesh_selection_derive(operation: SelectionDerivation) -> dict[str, Any]:
        await require_capability(client, "mesh_selection")
        return await client.call(
            "mesh.selection.derive",
            {"operation": operation.model_dump()},
            read_only=True,
        )

    @server.tool(
        name="mesh.selection.inspect",
        description="Inspect a bounded page from one exact revision-bound SelectionSet.",
        annotations=READ_ONLY,
        structured_output=True,
    )
    async def mesh_selection_inspect(
        selection_id: SelectionId,
        offset: Annotated[StrictInt, Field(ge=0)] = 0,
        limit: Annotated[StrictInt, Field(ge=1, le=4096)] = 256,
    ) -> dict[str, Any]:
        await require_capability(client, "mesh_selection")
        return await client.call(
            "mesh.selection.inspect",
            {"selection_id": selection_id, "offset": offset, "limit": limit},
            read_only=True,
        )

    @server.tool(
        name="mesh.selection.release",
        description="Release one session-local SelectionSet resource; repeated release is safe.",
        annotations=READ_ONLY,
        structured_output=True,
    )
    async def mesh_selection_release(selection_id: SelectionId) -> dict[str, Any]:
        await require_capability(client, "mesh_selection")
        return await client.call(
            "mesh.selection.release",
            {"selection_id": selection_id},
            read_only=True,
        )

    @server.tool(
        name="mesh.component_catalog.prepare",
        description=(
            "Partition one live non-empty FACE SelectionSet into deterministic "
            "shared-edge connected components without creating Blender selections."
        ),
        annotations=READ_ONLY,
        structured_output=True,
    )
    async def mesh_component_catalog_prepare(
        selection_id: SelectionId,
        include: ComponentCatalogMetrics = DEFAULT_COMPONENT_CATALOG_METRICS,
    ) -> dict[str, Any]:
        await require_capability(client, "mesh_component_catalog")
        return await client.call(
            "mesh.component_catalog.prepare",
            {"selection_id": selection_id, "include": list(include)},
            read_only=True,
        )

    @server.tool(
        name="mesh.component_catalog.inspect",
        description="Inspect a bounded page of one revision-bound ComponentCatalog.",
        annotations=READ_ONLY,
        structured_output=True,
    )
    async def mesh_component_catalog_inspect(
        component_catalog_id: ComponentCatalogId,
        offset: Annotated[StrictInt, Field(ge=0)] = 0,
        limit: Annotated[StrictInt, Field(ge=1, le=256)] = 128,
    ) -> dict[str, Any]:
        await require_capability(client, "mesh_component_catalog")
        return await client.call(
            "mesh.component_catalog.inspect",
            {
                "component_catalog_id": component_catalog_id,
                "offset": offset,
                "limit": limit,
            },
            read_only=True,
        )

    @server.tool(
        name="mesh.component_catalog.select",
        description=(
            "Materialize one to 4096 exact catalog components as a new weighted "
            "FACE SelectionSet on the same Mesh revision."
        ),
        annotations=READ_ONLY,
        structured_output=True,
    )
    async def mesh_component_catalog_select(
        component_catalog_id: ComponentCatalogId,
        component_identities: ComponentIdentities,
    ) -> dict[str, Any]:
        await require_capability(client, "mesh_component_catalog")
        return await client.call(
            "mesh.component_catalog.select",
            {
                "component_catalog_id": component_catalog_id,
                "component_identities": list(component_identities),
            },
            read_only=True,
        )

    @server.tool(
        name="mesh.component_catalog.release",
        description="Release one session-local ComponentCatalog; repeated release is safe.",
        annotations=READ_ONLY,
        structured_output=True,
    )
    async def mesh_component_catalog_release(
        component_catalog_id: ComponentCatalogId,
    ) -> dict[str, Any]:
        await require_capability(client, "mesh_component_catalog")
        return await client.call(
            "mesh.component_catalog.release",
            {"component_catalog_id": component_catalog_id},
            read_only=True,
        )

    @server.tool(
        name="mesh.component_map.inspect",
        description=(
            "Inspect a bounded page from one exact one-revision ComponentMap without "
            "guessing post-topology component indices."
        ),
        annotations=READ_ONLY,
        structured_output=True,
    )
    async def mesh_component_map_inspect(
        component_map_id: ComponentMapId,
        domain: ComponentMapDomain = "SUMMARY",
        direction: ComponentMapDirection = "FORWARD",
        offset: Annotated[StrictInt, Field(ge=0)] = 0,
        limit: Annotated[StrictInt, Field(ge=1, le=4096)] = 256,
    ) -> dict[str, Any]:
        await require_capability(client, "mesh_component_map")
        return await client.call(
            "mesh.component_map.inspect",
            {
                "component_map_id": component_map_id,
                "domain": domain,
                "direction": direction,
                "offset": offset,
                "limit": limit,
            },
            read_only=True,
        )

    @server.tool(
        name="mesh.component_map.release",
        description="Release one session-local ComponentMap; repeated release is safe.",
        annotations=READ_ONLY,
        structured_output=True,
    )
    async def mesh_component_map_release(component_map_id: ComponentMapId) -> dict[str, Any]:
        await require_capability(client, "mesh_component_map")
        return await client.call(
            "mesh.component_map.release",
            {"component_map_id": component_map_id},
            read_only=True,
        )

    @server.tool(
        name="mesh.component_map.compose",
        description=(
            "Compose two to eight exact continuous ComponentMaps into one ordinary "
            "lineage resource without guessing spatial correspondence."
        ),
        annotations=READ_ONLY,
        structured_output=True,
    )
    async def mesh_component_map_compose(
        component_map_ids: ComponentMapIds,
    ) -> dict[str, Any]:
        await require_capability(client, "mesh_component_map", 2)
        return await client.call(
            "mesh.component_map.compose",
            {"component_map_ids": list(component_map_ids)},
            read_only=True,
        )

    @server.tool(
        name="mesh.selection.remap",
        description=(
            "Remap one before-revision SelectionSet through an exact ComponentMap into "
            "a new immutable after-revision SelectionSet."
        ),
        annotations=READ_ONLY,
        structured_output=True,
    )
    async def mesh_selection_remap(
        selection_id: SelectionId,
        component_map_id: ComponentMapId,
        mode: SelectionRemapMode = "ALL_MAPPED",
        weight_merge: WeightMergeMode = "MAX",
    ) -> dict[str, Any]:
        await require_capability(client, "mesh_component_map")
        return await client.call(
            "mesh.selection.remap",
            {
                "selection_id": selection_id,
                "component_map_id": component_map_id,
                "mode": mode,
                "weight_merge": weight_merge,
            },
            read_only=True,
        )

    @server.tool(
        name="mesh.surface.prepare",
        description=(
            "Prepare one bounded BASE or EVALUATED world-space SurfaceRef with fixed "
            "scene, frame, transform, revision, triangle evidence, and BVH."
        ),
        annotations=READ_ONLY,
        structured_output=True,
    )
    async def mesh_surface_prepare(
        object_name: ObjectName,
        expected_object_identity: SessionIdentity,
        expected_mesh_revision_id: MeshRevisionId,
        geometry: SurfaceGeometry = "EVALUATED",
    ) -> dict[str, Any]:
        await require_capability(client, "mesh_surface_query")
        return await client.call(
            "mesh.surface.prepare",
            {
                "object_name": object_name,
                "expected_object_identity": expected_object_identity,
                "expected_mesh_revision_id": expected_mesh_revision_id,
                "geometry": geometry,
            },
            read_only=True,
        )

    @server.tool(
        name="mesh.surface.query",
        description=(
            "Measure a revision-bound vertex SelectionSet against a fixed SurfaceRef "
            "using closest points or one world-space ray direction."
        ),
        annotations=READ_ONLY,
        structured_output=True,
    )
    async def mesh_surface_query(
        selection_id: SelectionId,
        surface_id: SurfaceId,
        mode: SurfaceQueryMode = "CLOSEST_POINT",
        direction: Vector3 | None = None,
        maximum_distance: FiniteNumber = 1_000_000,
        threshold: FiniteNumber | None = None,
        sample_limit: Annotated[StrictInt, Field(ge=0, le=256)] = 64,
    ) -> dict[str, Any]:
        await require_capability(client, "mesh_surface_query")
        return await client.call(
            "mesh.surface.query",
            {
                "selection_id": selection_id,
                "surface_id": surface_id,
                "mode": mode,
                "direction": direction.model_dump() if direction is not None else None,
                "maximum_distance": maximum_distance,
                "threshold": threshold,
                "sample_limit": sample_limit,
            },
            read_only=True,
        )

    @server.tool(
        name="mesh.validate",
        description=(
            "Validate bounded topology, orientation, intersections, distance, or "
            "penetration and return quantitative evidence plus SelectionSets."
        ),
        annotations=READ_ONLY,
        structured_output=True,
    )
    async def mesh_validate(
        selection_id: SelectionId,
        check: ValidationCheck,
        surface_id: SurfaceId | None = None,
        layer_name: Annotated[str, Field(min_length=1, max_length=255)] | None = None,
        expected_uv_fingerprint: Annotated[str, Field(min_length=64, max_length=64)] | None = None,
        group_names: Annotated[
            tuple[Annotated[str, Field(min_length=1, max_length=255)], ...],
            Field(min_length=1, max_length=256),
        ]
        | None = None,
        expected_group_schema_fingerprint: Annotated[str, Field(min_length=64, max_length=64)]
        | None = None,
        expected_weights_fingerprint: Annotated[str, Field(min_length=64, max_length=64)]
        | None = None,
        target_weight_total: FiniteNumber = 1.0,
        maximum_influences: Annotated[StrictInt, Field(ge=1, le=32)] = 4,
        tolerance: FiniteNumber = 1e-6,
        maximum_distance: FiniteNumber = 1_000_000,
        threshold: FiniteNumber | None = None,
        sample_limit: Annotated[StrictInt, Field(ge=0, le=256)] = 64,
    ) -> dict[str, Any]:
        await require_capability(client, "mesh_validation")
        return await client.call(
            "mesh.validate",
            {
                "selection_id": selection_id,
                "check": check,
                "surface_id": surface_id,
                "layer_name": layer_name,
                "expected_uv_fingerprint": expected_uv_fingerprint,
                "group_names": list(group_names) if group_names is not None else None,
                "expected_group_schema_fingerprint": expected_group_schema_fingerprint,
                "expected_weights_fingerprint": expected_weights_fingerprint,
                "target_weight_total": target_weight_total,
                "maximum_influences": maximum_influences,
                "tolerance": tolerance,
                "maximum_distance": maximum_distance,
                "threshold": threshold,
                "sample_limit": sample_limit,
            },
            read_only=True,
        )

    @server.tool(
        name="object.lookdev.inspect",
        description=(
            "List bounded object-local visibility, modifier, shape-key, and material-slot "
            "targets with session identities for safe preview writes."
        ),
        annotations=READ_ONLY,
        structured_output=True,
    )
    async def object_lookdev_inspect(object_name: ObjectName) -> dict[str, Any]:
        return await client.call(
            "object.lookdev.inspect",
            {"object_name": object_name},
            read_only=True,
        )

    @server.tool(
        name="modifier.inspect",
        description=(
            "Inspect the exact ordered Modifier stack for one mesh object, including typed "
            "settings, session identities, drivers, write ranges, and a guarded fingerprint."
        ),
        annotations=READ_ONLY,
        structured_output=True,
    )
    async def modifier_inspect(object_name: ObjectName) -> dict[str, Any]:
        await require_capability(client, "modifier_authoring")
        return await client.call(
            "modifier.inspect",
            {"object_name": object_name},
            read_only=True,
        )

    @server.tool(
        name="material.inspect",
        description=(
            "Inspect one exact material slot and list bounded node input identities, values, "
            "ranges, links, drivers, and write eligibility."
        ),
        annotations=READ_ONLY,
        structured_output=True,
    )
    async def material_inspect(
        object_name: ObjectName,
        material_slot_index: MaterialSlotIndex,
    ) -> dict[str, Any]:
        return await client.call(
            "material.inspect",
            {
                "object_name": object_name,
                "material_slot_index": material_slot_index,
            },
            read_only=True,
        )

    @server.tool(
        name="image.inspect",
        description=(
            "Inspect one exact Blender image data-block, including absolute path, identity, "
            "dimensions, color space, users, and packed state."
        ),
        annotations=READ_ONLY,
        structured_output=True,
    )
    async def image_inspect(image_name: ImageName) -> dict[str, Any]:
        await require_capability(client, "image_assets")
        return await client.call(
            "image.inspect",
            {"image_name": image_name},
            read_only=True,
        )

    @server.tool(
        name="viewport.capture",
        description=(
            "Temporarily frame an object from a semantic view, capture the 3D editor, "
            "and restore the user's context."
        ),
        annotations=READ_ONLY,
        structured_output=False,
    )
    async def viewport_capture(
        object_name: ObjectName,
        view: SemanticView = "CURRENT",
        max_size: CaptureSize = 800,
        viewport_id: str | None = None,
        display_mode: DisplayMode = "CURRENT",
        overlays: OverlaysMode = "CURRENT",
        orbit: OrbitRequest | None = None,
    ) -> CallToolResult:
        if view == "CURRENT" and orbit is not None:
            raise ValueError("orbit requires a semantic base view rather than CURRENT")
        image_bytes, result = await capture_image(
            client,
            object_name=object_name,
            view=view,
            max_size=max_size,
            viewport_id=viewport_id,
            display_mode=display_mode,
            overlays=overlays,
            orbit=orbit.model_dump() if orbit is not None else None,
        )
        await settle_capture_generation(client, result)
        return CallToolResult(
            content=[
                ImageContent(
                    type="image",
                    data=base64.b64encode(image_bytes).decode("ascii"),
                    mimeType="image/png",
                ),
                TextContent(type="text", text=json.dumps(result, ensure_ascii=False, indent=2)),
            ],
            structuredContent=result,
        )

    @server.tool(
        name="viewport.raycast",
        description=(
            "Resolve a normalized image coordinate against the evaluated Blender geometry "
            "represented by a prior viewport capture."
        ),
        annotations=READ_ONLY,
        structured_output=True,
    )
    async def viewport_raycast(
        capture_id: CaptureId,
        x: NormalizedCoordinate,
        y: NormalizedCoordinate,
    ) -> dict[str, Any]:
        return await client.call(
            "viewport.raycast",
            {"capture_id": capture_id, "x": x, "y": y},
            read_only=True,
        )

    @server.tool(
        name="observation.bundle",
        description=(
            "Capture one to three consistent semantic views with before/after context and "
            "object evidence."
        ),
        annotations=READ_ONLY,
        structured_output=False,
    )
    async def observation_bundle(
        object_name: ObjectName,
        views: BundleViews = ("FRONT", "RIGHT", "TOP"),
        max_size: BundleCaptureSize = 800,
        viewport_id: str | None = None,
        display_mode: DisplayMode = "CURRENT",
        overlays: OverlaysMode = "CURRENT",
    ) -> CallToolResult:
        images, result = await collect_observation_bundle(
            client,
            object_name=object_name,
            views=views,
            max_size=max_size,
            viewport_id=viewport_id,
            display_mode=display_mode,
            overlays=overlays,
        )
        content: list[ContentBlock] = [
            ImageContent(
                type="image",
                data=base64.b64encode(image).decode("ascii"),
                mimeType="image/png",
            )
            for image in images
        ]
        content.append(
            TextContent(type="text", text=json.dumps(result, ensure_ascii=False, indent=2))
        )
        return CallToolResult(
            content=content,
            structuredContent=result,
        )

    @server.tool(
        name="lookdev.compare",
        description=(
            "Capture a baseline and one to three absolute candidates for one inspected "
            "LookDev property, rolling every candidate back before returning evidence."
        ),
        annotations=PREVIEW_MUTATION,
        structured_output=False,
    )
    async def lookdev_compare(
        target: ComparisonTarget,
        candidates: ComparisonCandidates,
        capture: ComparisonCapture,
    ) -> CallToolResult:
        if isinstance(target, ObjectSettingTarget):
            await require_capability(client, "object_settings")
        if isinstance(target, ModifierSettingTarget):
            await require_capability(client, "modifier_authoring")
        request = ComparisonRequest(target=target, candidates=candidates, capture=capture)
        images, result = await run_lookdev_comparison(client, request)
        content: list[ContentBlock] = [
            ImageContent(
                type="image",
                data=base64.b64encode(image).decode("ascii"),
                mimeType="image/png",
            )
            for image in images
        ]
        content.append(
            TextContent(type="text", text=json.dumps(result, ensure_ascii=False, indent=2))
        )
        return CallToolResult(content=content, structuredContent=result)

    @server.tool(
        name="transaction.begin",
        description="Begin the single reversible preview transaction for this Blender instance.",
        annotations=SCENE_MUTATION,
        structured_output=True,
    )
    async def transaction_begin(
        expected_scene_generation: SceneGeneration,
        idempotency_key: IdempotencyKey,
        label: TransactionLabel | None = None,
        viewport_id: str | None = None,
    ) -> dict[str, Any]:
        return await client.call(
            "transaction.begin",
            {"label": label, "viewport_id": viewport_id},
            expected_scene_generation=expected_scene_generation,
            idempotency_key=idempotency_key,
            read_only=False,
        )

    @server.tool(
        name="object.create",
        description=(
            "Create one uniquely named bounded primitive, empty, camera, or light in an "
            "exact collection inside the active structural transaction."
        ),
        annotations=SCENE_MUTATION,
        structured_output=True,
    )
    async def object_create(
        transaction_id: TransactionId,
        definition: ObjectDefinition,
        expected_scene_generation: SceneGeneration,
        idempotency_key: IdempotencyKey,
    ) -> dict[str, Any]:
        await require_capability(client, "object_authoring")
        client.require_capability("transactions", 3)
        return await client.call(
            "object.create",
            {"transaction_id": transaction_id, "definition": definition.model_dump()},
            expected_scene_generation=expected_scene_generation,
            idempotency_key=idempotency_key,
            read_only=False,
        )

    @server.tool(
        name="object.duplicate",
        description=(
            "Duplicate one exact object with linked or independent object data and a unique "
            "new name inside the active structural transaction."
        ),
        annotations=SCENE_MUTATION,
        structured_output=True,
    )
    async def object_duplicate(
        transaction_id: TransactionId,
        source_name: ObjectName,
        expected_source_identity: SessionIdentity,
        name: ObjectName,
        expected_scene_generation: SceneGeneration,
        idempotency_key: IdempotencyKey,
        linked_data: StrictBool = False,
        collection_name: ObjectName | None = None,
        expected_collection_identity: SessionIdentity | None = None,
        transform: InitialTransform | None = None,
    ) -> dict[str, Any]:
        if (collection_name is None) != (expected_collection_identity is None):
            raise ValueError(
                "collection_name and expected_collection_identity must be supplied together"
            )
        await require_capability(client, "object_authoring")
        client.require_capability("transactions", 3)
        return await client.call(
            "object.duplicate",
            {
                "transaction_id": transaction_id,
                "source_name": source_name,
                "expected_source_identity": expected_source_identity,
                "name": name,
                "linked_data": linked_data,
                "collection_name": collection_name,
                "expected_collection_identity": expected_collection_identity,
                "transform": transform.model_dump() if transform is not None else None,
            },
            expected_scene_generation=expected_scene_generation,
            idempotency_key=idempotency_key,
            read_only=False,
        )

    @server.tool(
        name="object.delete",
        description=(
            "Unlink one exact unselected object now, restore it on rollback, or remove the "
            "object data-block when the structural transaction commits."
        ),
        annotations=SCENE_MUTATION,
        structured_output=True,
    )
    async def object_delete(
        transaction_id: TransactionId,
        object_name: ObjectName,
        expected_object_identity: SessionIdentity,
        expected_scene_generation: SceneGeneration,
        idempotency_key: IdempotencyKey,
    ) -> dict[str, Any]:
        await require_capability(client, "object_authoring")
        client.require_capability("transactions", 3)
        return await client.call(
            "object.delete",
            {
                "transaction_id": transaction_id,
                "object_name": object_name,
                "expected_object_identity": expected_object_identity,
            },
            expected_scene_generation=expected_scene_generation,
            idempotency_key=idempotency_key,
            read_only=False,
        )

    @server.tool(
        name="collection.create",
        description=(
            "Create one globally unique Collection under an exact Scene root or parent "
            "Collection inside the active transaction."
        ),
        annotations=SCENE_MUTATION,
        structured_output=True,
    )
    async def collection_create(
        transaction_id: TransactionId,
        name: ObjectName,
        parent: CollectionParent,
        expected_scene_generation: SceneGeneration,
        idempotency_key: IdempotencyKey,
    ) -> dict[str, Any]:
        await require_capability(client, "collection_authoring")
        client.require_capability("transactions", 11)
        return await client.call(
            "collection.create",
            {
                "transaction_id": transaction_id,
                "name": name,
                "parent": parent.model_dump(),
            },
            expected_scene_generation=expected_scene_generation,
            idempotency_key=idempotency_key,
            read_only=False,
        )

    async def _collection_link_call(
        command: str,
        *,
        transaction_id: TransactionId,
        collection_name: ObjectName,
        expected_collection_identity: SessionIdentity,
        expected_collection_structure_fingerprint: SceneOrganizationFingerprint,
        object_name: ObjectName,
        expected_object_identity: SessionIdentity,
        expected_object_collections_fingerprint: SceneOrganizationFingerprint,
        expected_scene_generation: SceneGeneration,
        idempotency_key: IdempotencyKey,
    ) -> dict[str, Any]:
        await require_capability(client, "collection_authoring")
        client.require_capability("transactions", 11)
        return await client.call(
            command,
            {
                "transaction_id": transaction_id,
                "collection_name": collection_name,
                "expected_collection_identity": expected_collection_identity,
                "expected_collection_structure_fingerprint": (
                    expected_collection_structure_fingerprint
                ),
                "object_name": object_name,
                "expected_object_identity": expected_object_identity,
                "expected_object_collections_fingerprint": (
                    expected_object_collections_fingerprint
                ),
            },
            expected_scene_generation=expected_scene_generation,
            idempotency_key=idempotency_key,
            read_only=False,
        )

    @server.tool(
        name="collection.link_object",
        description="Link one exact object into one exact Collection transactionally.",
        annotations=SCENE_MUTATION,
        structured_output=True,
    )
    async def collection_link_object(
        transaction_id: TransactionId,
        collection_name: ObjectName,
        expected_collection_identity: SessionIdentity,
        expected_collection_structure_fingerprint: SceneOrganizationFingerprint,
        object_name: ObjectName,
        expected_object_identity: SessionIdentity,
        expected_object_collections_fingerprint: SceneOrganizationFingerprint,
        expected_scene_generation: SceneGeneration,
        idempotency_key: IdempotencyKey,
    ) -> dict[str, Any]:
        return await _collection_link_call(
            "collection.link_object",
            transaction_id=transaction_id,
            collection_name=collection_name,
            expected_collection_identity=expected_collection_identity,
            expected_collection_structure_fingerprint=(
                expected_collection_structure_fingerprint
            ),
            object_name=object_name,
            expected_object_identity=expected_object_identity,
            expected_object_collections_fingerprint=expected_object_collections_fingerprint,
            expected_scene_generation=expected_scene_generation,
            idempotency_key=idempotency_key,
        )

    @server.tool(
        name="collection.unlink_object",
        description=(
            "Unlink one exact object from one exact Collection while refusing its final link."
        ),
        annotations=SCENE_MUTATION,
        structured_output=True,
    )
    async def collection_unlink_object(
        transaction_id: TransactionId,
        collection_name: ObjectName,
        expected_collection_identity: SessionIdentity,
        expected_collection_structure_fingerprint: SceneOrganizationFingerprint,
        object_name: ObjectName,
        expected_object_identity: SessionIdentity,
        expected_object_collections_fingerprint: SceneOrganizationFingerprint,
        expected_scene_generation: SceneGeneration,
        idempotency_key: IdempotencyKey,
    ) -> dict[str, Any]:
        return await _collection_link_call(
            "collection.unlink_object",
            transaction_id=transaction_id,
            collection_name=collection_name,
            expected_collection_identity=expected_collection_identity,
            expected_collection_structure_fingerprint=(
                expected_collection_structure_fingerprint
            ),
            object_name=object_name,
            expected_object_identity=expected_object_identity,
            expected_object_collections_fingerprint=expected_object_collections_fingerprint,
            expected_scene_generation=expected_scene_generation,
            idempotency_key=idempotency_key,
        )

    @server.tool(
        name="object.parent.set",
        description=(
            "Create one exact OBJECT parent relation while preserving world or local transform."
        ),
        annotations=SCENE_MUTATION,
        structured_output=True,
    )
    async def object_parent_set(
        transaction_id: TransactionId,
        child_name: ObjectName,
        expected_child_identity: SessionIdentity,
        expected_child_structure_fingerprint: SceneOrganizationFingerprint,
        parent_name: ObjectName,
        expected_parent_identity: SessionIdentity,
        expected_parent_structure_fingerprint: SceneOrganizationFingerprint,
        transform_mode: ParentTransformMode,
        expected_scene_generation: SceneGeneration,
        idempotency_key: IdempotencyKey,
    ) -> dict[str, Any]:
        await require_capability(client, "object_parenting")
        client.require_capability("transactions", 11)
        return await client.call(
            "object.parent.set",
            {
                "transaction_id": transaction_id,
                "child_name": child_name,
                "expected_child_identity": expected_child_identity,
                "expected_child_structure_fingerprint": (
                    expected_child_structure_fingerprint
                ),
                "parent_name": parent_name,
                "expected_parent_identity": expected_parent_identity,
                "expected_parent_structure_fingerprint": (
                    expected_parent_structure_fingerprint
                ),
                "transform_mode": transform_mode,
            },
            expected_scene_generation=expected_scene_generation,
            idempotency_key=idempotency_key,
            read_only=False,
        )

    @server.tool(
        name="object.parent.clear",
        description=(
            "Clear one exact existing OBJECT or BONE parent while preserving world or "
            "local transform."
        ),
        annotations=SCENE_MUTATION,
        structured_output=True,
    )
    async def object_parent_clear(
        transaction_id: TransactionId,
        child_name: ObjectName,
        expected_child_identity: SessionIdentity,
        expected_child_structure_fingerprint: SceneOrganizationFingerprint,
        expected_parent_name: ObjectName,
        expected_parent_identity: SessionIdentity,
        expected_parent_structure_fingerprint: SceneOrganizationFingerprint,
        transform_mode: ParentTransformMode,
        expected_scene_generation: SceneGeneration,
        idempotency_key: IdempotencyKey,
    ) -> dict[str, Any]:
        await require_capability(client, "object_parenting")
        client.require_capability("transactions", 11)
        return await client.call(
            "object.parent.clear",
            {
                "transaction_id": transaction_id,
                "child_name": child_name,
                "expected_child_identity": expected_child_identity,
                "expected_child_structure_fingerprint": (
                    expected_child_structure_fingerprint
                ),
                "expected_parent_name": expected_parent_name,
                "expected_parent_identity": expected_parent_identity,
                "expected_parent_structure_fingerprint": (
                    expected_parent_structure_fingerprint
                ),
                "transform_mode": transform_mode,
            },
            expected_scene_generation=expected_scene_generation,
            idempotency_key=idempotency_key,
            read_only=False,
        )

    @server.tool(
        name="object.set",
        description=(
            "Atomically apply typed transform, visibility, Light, and Camera settings to "
            "one exact object inside the active transaction."
        ),
        annotations=SCENE_MUTATION,
        structured_output=True,
    )
    async def object_set(
        transaction_id: TransactionId,
        object_name: ObjectName,
        expected_object_identity: SessionIdentity,
        expected_scene_generation: SceneGeneration,
        idempotency_key: IdempotencyKey,
        patches: ObjectSettingPatches,
    ) -> dict[str, Any]:
        await require_capability(client, "object_settings")
        client.require_capability("transactions", 3)
        return await client.call(
            "object.set",
            {
                "transaction_id": transaction_id,
                "object_name": object_name,
                "expected_object_identity": expected_object_identity,
                "patches": [patch.model_dump(exclude_none=True) for patch in patches],
            },
            expected_scene_generation=expected_scene_generation,
            idempotency_key=idempotency_key,
            read_only=False,
        )

    @server.tool(
        name="object.transform",
        description=(
            "Set one or more local object scale axes to absolute values inside "
            "the active transaction."
        ),
        annotations=SCENE_MUTATION,
        structured_output=True,
    )
    async def object_transform(
        transaction_id: TransactionId,
        object_name: ObjectName,
        expected_scene_generation: SceneGeneration,
        idempotency_key: IdempotencyKey,
        expected_object_identity: SessionIdentity | None = None,
        location: LocationAxisPatch | None = None,
        rotation_euler_degrees: RotationAxisPatch | None = None,
        scale: ScaleAxisPatch | None = None,
    ) -> dict[str, Any]:
        if location is None and rotation_euler_degrees is None and scale is None:
            raise ValueError("location, rotation_euler_degrees, and/or scale is required")
        if (location is not None or rotation_euler_degrees is not None) and (
            expected_object_identity is None
        ):
            raise ValueError("location and rotation require expected_object_identity")
        if location is not None or rotation_euler_degrees is not None:
            await require_capability(client, "object_transform")
            client.require_capability("transactions", 3)
        return await client.call(
            "object.transform",
            {
                "transaction_id": transaction_id,
                "object_name": object_name,
                "expected_object_identity": expected_object_identity,
                "location": location.model_dump(exclude_none=True) if location else None,
                "rotation_euler_degrees": (
                    rotation_euler_degrees.model_dump(exclude_none=True)
                    if rotation_euler_degrees
                    else None
                ),
                "scale": scale.model_dump(exclude_none=True) if scale else None,
            },
            expected_scene_generation=expected_scene_generation,
            idempotency_key=idempotency_key,
            read_only=False,
        )

    @server.tool(
        name="object.visibility.set",
        description=(
            "Set absolute object viewport and/or render visibility flags inside the active "
            "transaction."
        ),
        annotations=SCENE_MUTATION,
        structured_output=True,
    )
    async def object_visibility_set(
        transaction_id: TransactionId,
        object_name: ObjectName,
        expected_object_identity: SessionIdentity,
        expected_scene_generation: SceneGeneration,
        idempotency_key: IdempotencyKey,
        hide_viewport: StrictBool | None = None,
        hide_render: StrictBool | None = None,
    ) -> dict[str, Any]:
        visibility = {
            name: value
            for name, value in {
                "hide_viewport": hide_viewport,
                "hide_render": hide_render,
            }.items()
            if value is not None
        }
        if not visibility:
            raise ValueError("hide_viewport and/or hide_render is required")
        return await client.call(
            "object.visibility.set",
            {
                "transaction_id": transaction_id,
                "object_name": object_name,
                "expected_object_identity": expected_object_identity,
                "visibility": visibility,
            },
            expected_scene_generation=expected_scene_generation,
            idempotency_key=idempotency_key,
            read_only=False,
        )

    @server.tool(
        name="modifier.set_state",
        description=(
            "Set absolute viewport and/or render enable flags for one exact modifier inside "
            "the active transaction."
        ),
        annotations=SCENE_MUTATION,
        structured_output=True,
    )
    async def modifier_set_state(
        transaction_id: TransactionId,
        object_name: ObjectName,
        expected_object_identity: SessionIdentity,
        modifier_name: ModifierName,
        expected_modifier_identity: SessionIdentity,
        expected_scene_generation: SceneGeneration,
        idempotency_key: IdempotencyKey,
        show_viewport: StrictBool | None = None,
        show_render: StrictBool | None = None,
    ) -> dict[str, Any]:
        state = {
            name: value
            for name, value in {
                "show_viewport": show_viewport,
                "show_render": show_render,
            }.items()
            if value is not None
        }
        if not state:
            raise ValueError("show_viewport and/or show_render is required")
        return await client.call(
            "modifier.set_state",
            {
                "transaction_id": transaction_id,
                "object_name": object_name,
                "expected_object_identity": expected_object_identity,
                "modifier_name": modifier_name,
                "expected_modifier_identity": expected_modifier_identity,
                "state": state,
            },
            expected_scene_generation=expected_scene_generation,
            idempotency_key=idempotency_key,
            read_only=False,
        )

    @server.tool(
        name="modifier.create",
        description=(
            "Create one typed Bevel, Subdivision, Solidify, or Boolean Modifier at an "
            "exact guarded stack position inside the active transaction."
        ),
        annotations=SCENE_MUTATION,
        structured_output=True,
    )
    async def modifier_create(
        transaction_id: TransactionId,
        object_name: ObjectName,
        expected_object_identity: SessionIdentity,
        expected_stack_fingerprint: SessionIdentity,
        definition: ModifierDefinition,
        expected_scene_generation: SceneGeneration,
        idempotency_key: IdempotencyKey,
    ) -> dict[str, Any]:
        await require_capability(client, "modifier_authoring")
        client.require_capability("transactions", 3)
        return await client.call(
            "modifier.create",
            {
                "transaction_id": transaction_id,
                "object_name": object_name,
                "expected_object_identity": expected_object_identity,
                "expected_stack_fingerprint": expected_stack_fingerprint,
                "definition": definition.model_dump(exclude_none=True),
            },
            expected_scene_generation=expected_scene_generation,
            idempotency_key=idempotency_key,
            read_only=False,
        )

    @server.tool(
        name="modifier.set",
        description=(
            "Atomically patch typed settings on one exact supported Modifier after "
            "validating its identity, type, stack index, drivers, and stack fingerprint."
        ),
        annotations=SCENE_MUTATION,
        structured_output=True,
    )
    async def modifier_set(
        transaction_id: TransactionId,
        object_name: ObjectName,
        expected_object_identity: SessionIdentity,
        modifier_name: ModifierName,
        expected_modifier_identity: SessionIdentity,
        expected_modifier_type: ModifierType,
        expected_stack_index: ModifierStackIndex,
        expected_stack_fingerprint: SessionIdentity,
        settings: ModifierSettings,
        expected_scene_generation: SceneGeneration,
        idempotency_key: IdempotencyKey,
    ) -> dict[str, Any]:
        await require_capability(client, "modifier_authoring")
        client.require_capability("transactions", 3)
        return await client.call(
            "modifier.set",
            {
                "transaction_id": transaction_id,
                "object_name": object_name,
                "expected_object_identity": expected_object_identity,
                "modifier_name": modifier_name,
                "expected_modifier_identity": expected_modifier_identity,
                "expected_modifier_type": expected_modifier_type,
                "expected_stack_index": expected_stack_index,
                "expected_stack_fingerprint": expected_stack_fingerprint,
                "settings": settings.model_dump(exclude_none=True),
            },
            expected_scene_generation=expected_scene_generation,
            idempotency_key=idempotency_key,
            read_only=False,
        )

    @server.tool(
        name="modifier.move",
        description=(
            "Move one exact supported Modifier to an absolute stack index without changing "
            "the properties of intervening supported or unsupported Modifiers."
        ),
        annotations=SCENE_MUTATION,
        structured_output=True,
    )
    async def modifier_move(
        transaction_id: TransactionId,
        object_name: ObjectName,
        expected_object_identity: SessionIdentity,
        modifier_name: ModifierName,
        expected_modifier_identity: SessionIdentity,
        expected_modifier_type: ModifierType,
        expected_stack_index: ModifierStackIndex,
        expected_stack_fingerprint: SessionIdentity,
        target_stack_index: ModifierStackIndex,
        expected_scene_generation: SceneGeneration,
        idempotency_key: IdempotencyKey,
    ) -> dict[str, Any]:
        await require_capability(client, "modifier_authoring")
        client.require_capability("transactions", 3)
        return await client.call(
            "modifier.move",
            {
                "transaction_id": transaction_id,
                "object_name": object_name,
                "expected_object_identity": expected_object_identity,
                "modifier_name": modifier_name,
                "expected_modifier_identity": expected_modifier_identity,
                "expected_modifier_type": expected_modifier_type,
                "expected_stack_index": expected_stack_index,
                "expected_stack_fingerprint": expected_stack_fingerprint,
                "target_stack_index": target_stack_index,
            },
            expected_scene_generation=expected_scene_generation,
            idempotency_key=idempotency_key,
            read_only=False,
        )

    @server.tool(
        name="modifier.delete",
        description=(
            "Disable and mark one exact supported Modifier for deletion; rollback restores "
            "the same identity and commit performs the final removal."
        ),
        annotations=SCENE_MUTATION,
        structured_output=True,
    )
    async def modifier_delete(
        transaction_id: TransactionId,
        object_name: ObjectName,
        expected_object_identity: SessionIdentity,
        modifier_name: ModifierName,
        expected_modifier_identity: SessionIdentity,
        expected_modifier_type: ModifierType,
        expected_stack_index: ModifierStackIndex,
        expected_stack_fingerprint: SessionIdentity,
        expected_scene_generation: SceneGeneration,
        idempotency_key: IdempotencyKey,
    ) -> dict[str, Any]:
        await require_capability(client, "modifier_authoring")
        client.require_capability("transactions", 3)
        return await client.call(
            "modifier.delete",
            {
                "transaction_id": transaction_id,
                "object_name": object_name,
                "expected_object_identity": expected_object_identity,
                "modifier_name": modifier_name,
                "expected_modifier_identity": expected_modifier_identity,
                "expected_modifier_type": expected_modifier_type,
                "expected_stack_index": expected_stack_index,
                "expected_stack_fingerprint": expected_stack_fingerprint,
            },
            expected_scene_generation=expected_scene_generation,
            idempotency_key=idempotency_key,
            read_only=False,
        )

    @server.tool(
        name="mesh.edit",
        description=(
            "Apply one bounded semantic edit to exact base-Mesh components inside the "
            "active transaction, with explicit object-only or shared-data scope."
        ),
        annotations=SCENE_MUTATION,
        structured_output=True,
    )
    async def mesh_edit(
        transaction_id: TransactionId,
        object_name: ObjectName,
        expected_object_identity: SessionIdentity,
        expected_mesh_identity: SessionIdentity,
        expected_mesh_users: DataUsers,
        expected_mesh_user_objects: Annotated[
            tuple[MeshUserObject, ...], Field(min_length=1, max_length=256)
        ],
        expected_mesh_fingerprint: Annotated[str, Field(min_length=64, max_length=64)],
        data_scope: MeshDataScope,
        operation: MeshOperation,
        expected_scene_generation: SceneGeneration,
        idempotency_key: IdempotencyKey,
    ) -> dict[str, Any]:
        await require_capability(client, "mesh_topology")
        operation_payload = operation.model_dump(exclude_none=True)
        attribute_policy = operation_payload.get("attribute_policy")
        if attribute_policy == MeshAttributePolicy().model_dump():
            operation_payload.pop("attribute_policy", None)
        elif attribute_policy is not None:
            client.require_capability("mesh_topology", 4)
            client.require_capability("transactions", 9)
        topology_v2_operations = {
            "subdivide",
            "loop_cut",
            "bisect",
            "split",
            "bridge",
            "fill",
            "grid_fill",
        }
        if operation.type in topology_v2_operations:
            await require_capability(client, "mesh_component_map")
            client.require_capability("mesh_topology", 2)
            client.require_capability("transactions", 7)
        elif operation.type in {
            "set_positions",
            "smooth",
            "relax",
            "project",
            "shrinkwrap",
            "inflate",
            "flatten",
        }:
            await require_capability(client, "mesh_deformation")
            client.require_capability("transactions", 6)
        else:
            client.require_capability("transactions", 4)
        if len(
            {
                (item.object_name, item.expected_object_identity)
                for item in expected_mesh_user_objects
            }
        ) != len(expected_mesh_user_objects):
            raise ValueError("expected_mesh_user_objects must be unique")
        if expected_mesh_users != len(expected_mesh_user_objects):
            raise ValueError(
                "expected_mesh_users must equal the number of expected_mesh_user_objects"
            )
        return await client.call(
            "mesh.edit",
            {
                "transaction_id": transaction_id,
                "object_name": object_name,
                "expected_object_identity": expected_object_identity,
                "expected_mesh_identity": expected_mesh_identity,
                "expected_mesh_users": expected_mesh_users,
                "expected_mesh_user_objects": [
                    item.model_dump() for item in expected_mesh_user_objects
                ],
                "expected_mesh_fingerprint": expected_mesh_fingerprint,
                "data_scope": data_scope,
                "operation": operation_payload,
            },
            expected_scene_generation=expected_scene_generation,
            idempotency_key=idempotency_key,
            read_only=False,
        )

    @server.tool(
        name="mesh.uv.edit",
        description=(
            "Edit exact UV layers, seams, pins, coordinates, islands, unwraps, or packs "
            "inside an active transaction without changing user UI selection."
        ),
        annotations=SCENE_MUTATION,
        structured_output=True,
    )
    async def mesh_uv_edit(
        transaction_id: TransactionId,
        object_name: ObjectName,
        expected_object_identity: SessionIdentity,
        expected_mesh_identity: SessionIdentity,
        expected_mesh_users: DataUsers,
        expected_mesh_user_objects: Annotated[
            tuple[MeshUserObject, ...], Field(min_length=1, max_length=256)
        ],
        expected_mesh_fingerprint: Annotated[str, Field(min_length=64, max_length=64)],
        expected_uv_fingerprint: Annotated[str, Field(min_length=64, max_length=64)],
        data_scope: MeshDataScope,
        operation: UVOperation,
        expected_scene_generation: SceneGeneration,
        idempotency_key: IdempotencyKey,
    ) -> dict[str, Any]:
        await require_capability(client, "mesh_uv")
        client.require_capability("transactions", 9)
        if len(
            {
                (item.object_name, item.expected_object_identity)
                for item in expected_mesh_user_objects
            }
        ) != len(expected_mesh_user_objects):
            raise ValueError("expected_mesh_user_objects must be unique")
        if expected_mesh_users != len(expected_mesh_user_objects):
            raise ValueError(
                "expected_mesh_users must equal the number of expected_mesh_user_objects"
            )
        return await client.call(
            "mesh.uv.edit",
            {
                "transaction_id": transaction_id,
                "object_name": object_name,
                "expected_object_identity": expected_object_identity,
                "expected_mesh_identity": expected_mesh_identity,
                "expected_mesh_users": expected_mesh_users,
                "expected_mesh_user_objects": [
                    item.model_dump() for item in expected_mesh_user_objects
                ],
                "expected_mesh_fingerprint": expected_mesh_fingerprint,
                "expected_uv_fingerprint": expected_uv_fingerprint,
                "data_scope": data_scope,
                "operation": operation.model_dump(exclude_none=True),
            },
            expected_scene_generation=expected_scene_generation,
            idempotency_key=idempotency_key,
            read_only=False,
        )

    @server.tool(
        name="mesh.weights.edit",
        description=(
            "Create or edit exact Vertex Groups and deform weights in an active transaction, "
            "with explicit shared-data scope and locked-group handling."
        ),
        annotations=SCENE_MUTATION,
        structured_output=True,
    )
    async def mesh_weights_edit(
        transaction_id: TransactionId,
        object_name: ObjectName,
        expected_object_identity: SessionIdentity,
        expected_mesh_identity: SessionIdentity,
        expected_mesh_users: DataUsers,
        expected_mesh_user_objects: Annotated[
            tuple[MeshUserObject, ...], Field(min_length=1, max_length=256)
        ],
        expected_mesh_fingerprint: Annotated[str, Field(min_length=64, max_length=64)],
        expected_group_schema_fingerprint: Annotated[str, Field(min_length=64, max_length=64)],
        expected_weights_fingerprint: Annotated[str, Field(min_length=64, max_length=64)],
        data_scope: MeshDataScope,
        operation: WeightOperation,
        expected_scene_generation: SceneGeneration,
        idempotency_key: IdempotencyKey,
    ) -> dict[str, Any]:
        await require_capability(client, "mesh_weights")
        client.require_capability("transactions", 9)
        if len(
            {
                (item.object_name, item.expected_object_identity)
                for item in expected_mesh_user_objects
            }
        ) != len(expected_mesh_user_objects):
            raise ValueError("expected_mesh_user_objects must be unique")
        if expected_mesh_users != len(expected_mesh_user_objects):
            raise ValueError(
                "expected_mesh_users must equal the number of expected_mesh_user_objects"
            )
        return await client.call(
            "mesh.weights.edit",
            {
                "transaction_id": transaction_id,
                "object_name": object_name,
                "expected_object_identity": expected_object_identity,
                "expected_mesh_identity": expected_mesh_identity,
                "expected_mesh_users": expected_mesh_users,
                "expected_mesh_user_objects": [
                    item.model_dump() for item in expected_mesh_user_objects
                ],
                "expected_mesh_fingerprint": expected_mesh_fingerprint,
                "expected_group_schema_fingerprint": expected_group_schema_fingerprint,
                "expected_weights_fingerprint": expected_weights_fingerprint,
                "data_scope": data_scope,
                "operation": operation.model_dump(exclude_none=True),
            },
            expected_scene_generation=expected_scene_generation,
            idempotency_key=idempotency_key,
            read_only=False,
        )

    @server.tool(
        name="mesh.attribute.transfer",
        description=(
            "Transfer exact UV layers or Vertex Group weights by topology lineage, nearest "
            "vertex, or nearest-surface interpolation inside an active transaction."
        ),
        annotations=SCENE_MUTATION,
        structured_output=True,
    )
    async def mesh_attribute_transfer(
        transaction_id: TransactionId,
        source: AttributeMeshTarget,
        target: AttributeMeshTarget,
        transfer: AttributeTransfer,
        expected_scene_generation: SceneGeneration,
        idempotency_key: IdempotencyKey,
    ) -> dict[str, Any]:
        await require_capability(client, "mesh_attribute_transfer")
        client.require_capability("transactions", 9)
        return await client.call(
            "mesh.attribute.transfer",
            {
                "transaction_id": transaction_id,
                "source": source.model_dump(exclude_none=True),
                "target": target.model_dump(exclude_none=True),
                "transfer": transfer.model_dump(exclude_none=True),
            },
            expected_scene_generation=expected_scene_generation,
            idempotency_key=idempotency_key,
            read_only=False,
        )

    @server.tool(
        name="rig.inspect",
        description=(
            "Inspect exact Mesh parenting, Armature Modifiers, bones, matching Vertex "
            "Groups, sparse weight coverage, and paged binding evidence."
        ),
        annotations=READ_ONLY,
        structured_output=True,
    )
    async def rig_inspect(
        object_name: ObjectName,
        armature_object_name: ObjectName | None = None,
        offset: Annotated[StrictInt, Field(ge=0)] = 0,
        limit: Annotated[StrictInt, Field(ge=1, le=512)] = 256,
    ) -> dict[str, Any]:
        await require_capability(client, "rig_binding")
        return await client.call(
            "rig.inspect",
            {
                "object_name": object_name,
                "armature_object_name": armature_object_name,
                "offset": offset,
                "limit": limit,
            },
            read_only=True,
        )

    @server.tool(
        name="rig.bind",
        description=(
            "Create or update one exact Armature Modifier and optional object parent in "
            "an active transaction without transferring or changing weights."
        ),
        annotations=SCENE_MUTATION,
        structured_output=True,
    )
    async def rig_bind(
        transaction_id: TransactionId,
        mesh_target: RigMeshTarget,
        armature_target: ArmatureTarget,
        modifier: RigModifierPolicy,
        parenting: Literal["NONE", "KEEP_WORLD", "KEEP_LOCAL"],
        group_scope: RigGroupScope,
        expected_scene_generation: SceneGeneration,
        idempotency_key: IdempotencyKey,
    ) -> dict[str, Any]:
        await require_capability(client, "rig_binding")
        client.require_capability("transactions", 10)
        return await client.call(
            "rig.bind",
            {
                "transaction_id": transaction_id,
                "mesh_target": mesh_target.model_dump(),
                "armature_target": armature_target.model_dump(),
                "modifier": modifier.model_dump(),
                "parenting": parenting,
                "group_scope": group_scope.model_dump(),
            },
            expected_scene_generation=expected_scene_generation,
            idempotency_key=idempotency_key,
            read_only=False,
        )

    @server.tool(
        name="mesh.extract.preflight",
        description=(
            "Validate a bounded, possibly disconnected proper-subset FACE SelectionSet "
            "and report exact extraction size and policy evidence without changing Blender."
        ),
        annotations=READ_ONLY,
        structured_output=True,
    )
    async def mesh_extract_preflight(
        target: ExtractMeshTarget,
        selection_id: SelectionId,
        new_object_name: MeshObjectName,
        output_policy: ExtractOutputPolicy,
        source_attribute_policy: MeshAttributePolicy,
        extracted_attribute_policy: MeshAttributePolicy,
        collection_name: MeshObjectName | None = None,
        expected_collection_identity: SessionIdentity | None = None,
    ) -> dict[str, Any]:
        await require_capability(client, "mesh_extraction")
        client.require_capability("mesh_component_map", 3)
        if (collection_name is None) != (expected_collection_identity is None):
            raise ValueError(
                "collection_name and expected_collection_identity must be supplied together"
            )
        return await client.call(
            "mesh.extract.preflight",
            {
                **target.model_dump(),
                "selection_id": selection_id,
                "new_object_name": new_object_name,
                "output_policy": output_policy.model_dump(),
                "source_attribute_policy": source_attribute_policy.model_dump(),
                "extracted_attribute_policy": extracted_attribute_policy.model_dump(),
                "collection_name": collection_name,
                "expected_collection_identity": expected_collection_identity,
            },
            read_only=True,
        )

    @server.tool(
        name="mesh.extract",
        description=(
            "Transactionally extract one or more FACE components into one exact object "
            "branch with explicit parent, Modifier, material-slot, UV, and weight policies."
        ),
        annotations=SCENE_MUTATION,
        structured_output=True,
    )
    async def mesh_extract(
        transaction_id: TransactionId,
        target: ExtractMeshTarget,
        selection_id: SelectionId,
        new_object_name: MeshObjectName,
        output_policy: ExtractOutputPolicy,
        source_attribute_policy: MeshAttributePolicy,
        extracted_attribute_policy: MeshAttributePolicy,
        expected_scene_generation: SceneGeneration,
        idempotency_key: IdempotencyKey,
        collection_name: MeshObjectName | None = None,
        expected_collection_identity: SessionIdentity | None = None,
    ) -> dict[str, Any]:
        await require_capability(client, "mesh_extraction")
        client.require_capability("mesh_component_map", 3)
        client.require_capability("transactions", 10)
        if (collection_name is None) != (expected_collection_identity is None):
            raise ValueError(
                "collection_name and expected_collection_identity must be supplied together"
            )
        return await client.call(
            "mesh.extract",
            {
                "transaction_id": transaction_id,
                **target.model_dump(),
                "selection_id": selection_id,
                "new_object_name": new_object_name,
                "output_policy": output_policy.model_dump(),
                "source_attribute_policy": source_attribute_policy.model_dump(),
                "extracted_attribute_policy": extracted_attribute_policy.model_dump(),
                "collection_name": collection_name,
                "expected_collection_identity": expected_collection_identity,
            },
            expected_scene_generation=expected_scene_generation,
            idempotency_key=idempotency_key,
            read_only=False,
        )

    @server.tool(
        name="mesh.materialize",
        description=(
            "Create one independent no-Shape-Key, no-Modifier Mesh object from exact "
            "BASE, current Shape-Key-only, or live final-evaluated evidence."
        ),
        annotations=SCENE_MUTATION,
        structured_output=True,
    )
    async def mesh_materialize(
        transaction_id: TransactionId,
        source: MaterializeSource,
        evaluation: MaterializeEvaluation,
        new_object_name: MeshObjectName,
        copy: MaterializeCopyPolicy,
        expected_scene_generation: SceneGeneration,
        idempotency_key: IdempotencyKey,
        collection_name: MeshObjectName | None = None,
        expected_collection_identity: SessionIdentity | None = None,
    ) -> dict[str, Any]:
        await require_capability(client, "mesh_materialization")
        client.require_capability("mesh_component_map", 3)
        client.require_capability("transactions", 10)
        if (collection_name is None) != (expected_collection_identity is None):
            raise ValueError(
                "collection_name and expected_collection_identity must be supplied together"
            )
        return await client.call(
            "mesh.materialize",
            {
                "transaction_id": transaction_id,
                "source": source.model_dump(),
                "evaluation": evaluation.model_dump(),
                "new_object_name": new_object_name,
                "copy": copy.model_dump(),
                "collection_name": collection_name,
                "expected_collection_identity": expected_collection_identity,
            },
            expected_scene_generation=expected_scene_generation,
            idempotency_key=idempotency_key,
            read_only=False,
        )

    @server.tool(
        name="mesh.separate",
        description=(
            "Separate one connected proper-subset FACE SelectionSet into a new exact "
            "object branch, returning source and separated ComponentMaps."
        ),
        annotations=SCENE_MUTATION,
        structured_output=True,
    )
    async def mesh_separate(
        transaction_id: TransactionId,
        object_name: ObjectName,
        expected_object_identity: SessionIdentity,
        expected_mesh_identity: SessionIdentity,
        expected_mesh_users: DataUsers,
        expected_mesh_user_objects: Annotated[
            tuple[MeshUserObject, ...], Field(min_length=1, max_length=256)
        ],
        expected_mesh_fingerprint: Annotated[str, Field(min_length=64, max_length=64)],
        selection_id: SelectionId,
        new_object_name: MeshObjectName,
        expected_scene_generation: SceneGeneration,
        idempotency_key: IdempotencyKey,
        collection_name: MeshObjectName | None = None,
        expected_collection_identity: SessionIdentity | None = None,
        source_attribute_policy: MeshAttributePolicy | None = None,
        separated_attribute_policy: MeshAttributePolicy | None = None,
    ) -> dict[str, Any]:
        await require_capability(client, "mesh_separation")
        client.require_capability("mesh_component_map", 2)
        client.require_capability("mesh_topology", 3)
        client.require_capability("transactions", 8)
        if source_attribute_policy is not None or separated_attribute_policy is not None:
            client.require_capability("mesh_separation", 2)
            client.require_capability("mesh_topology", 4)
            client.require_capability("transactions", 9)
        if (collection_name is None) != (expected_collection_identity is None):
            raise ValueError(
                "collection_name and expected_collection_identity must be supplied together"
            )
        if len(
            {
                (item.object_name, item.expected_object_identity)
                for item in expected_mesh_user_objects
            }
        ) != len(expected_mesh_user_objects):
            raise ValueError("expected_mesh_user_objects must be unique")
        if expected_mesh_users != len(expected_mesh_user_objects):
            raise ValueError(
                "expected_mesh_users must equal the number of expected_mesh_user_objects"
            )
        payload: dict[str, Any] = {
            "transaction_id": transaction_id,
            "object_name": object_name,
            "expected_object_identity": expected_object_identity,
            "expected_mesh_identity": expected_mesh_identity,
            "expected_mesh_users": expected_mesh_users,
            "expected_mesh_user_objects": [
                item.model_dump() for item in expected_mesh_user_objects
            ],
            "expected_mesh_fingerprint": expected_mesh_fingerprint,
            "selection_id": selection_id,
            "new_object_name": new_object_name,
            "collection_name": collection_name,
            "expected_collection_identity": expected_collection_identity,
        }
        if source_attribute_policy is not None:
            payload["source_attribute_policy"] = source_attribute_policy.model_dump()
        if separated_attribute_policy is not None:
            payload["separated_attribute_policy"] = separated_attribute_policy.model_dump()
        return await client.call(
            "mesh.separate",
            payload,
            expected_scene_generation=expected_scene_generation,
            idempotency_key=idempotency_key,
            read_only=False,
        )

    @server.tool(
        name="mesh.batch.execute",
        description=(
            "Execute one closed, declarative Mesh workflow with invocation-local aliases, "
            "automatic SelectionSet remapping, branch maps, validation assertions, and "
            "whole-transaction rollback on runtime failure."
        ),
        annotations=SCENE_MUTATION,
        structured_output=True,
    )
    async def mesh_batch_execute(
        transaction_id: TransactionId,
        targets: BatchTargets,
        inputs: BatchInputs,
        steps: BatchSteps,
        expected_scene_generation: SceneGeneration,
        idempotency_key: IdempotencyKey,
        on_error: Literal["ROLLBACK_TRANSACTION"] = "ROLLBACK_TRANSACTION",
    ) -> dict[str, Any]:
        await require_capability(client, "mesh_batch")
        client.require_capability("mesh_separation", 1)
        client.require_capability("mesh_component_map", 2)
        client.require_capability("mesh_topology", 3)
        client.require_capability("transactions", 8)
        step_payloads = [item.model_dump(exclude_none=True, by_alias=True) for item in steps]
        requires_batch_v2 = False
        requires_batch_v3 = False
        for payload in step_payloads:
            step_type = payload["type"]
            if step_type == "uv_edit":
                await require_capability(client, "mesh_uv")
                requires_batch_v2 = True
            elif step_type == "weights_edit":
                await require_capability(client, "mesh_weights")
                requires_batch_v2 = True
            elif step_type == "attribute_transfer":
                await require_capability(client, "mesh_attribute_transfer")
                requires_batch_v2 = True
            elif step_type == "mesh_validate" and payload.get("check") in {
                "UV_BOUNDS",
                "UV_DEGENERATE",
                "UV_OVERLAP",
                "UV_STRETCH",
                "WEIGHT_SUM",
                "WEIGHT_INFLUENCE_LIMIT",
                "WEIGHT_UNASSIGNED",
                "DEFORM_GROUP_MISMATCH",
            }:
                client.require_capability("mesh_validation", 2)
                requires_batch_v2 = True
            elif step_type == "mesh_edit":
                operation_payload = payload.get("operation", {})
                policy = operation_payload.get("attribute_policy")
                if policy == MeshAttributePolicy().model_dump():
                    operation_payload.pop("attribute_policy", None)
                elif policy is not None:
                    requires_batch_v2 = True
            elif step_type == "mesh_separate":
                source_policy = payload.get("source_attribute_policy")
                separated_policy = payload.get("separated_attribute_policy")
                default_policy = MeshAttributePolicy().model_dump()
                if source_policy == default_policy:
                    payload.pop("source_attribute_policy", None)
                else:
                    requires_batch_v2 = True
                if separated_policy == default_policy:
                    payload.pop("separated_attribute_policy", None)
                else:
                    requires_batch_v2 = True
            elif step_type in {
                "component_catalog_prepare",
                "component_catalog_select",
            }:
                await require_capability(client, "mesh_component_catalog")
                requires_batch_v3 = True
            elif step_type == "mesh_materialize":
                await require_capability(client, "mesh_materialization")
                client.require_capability("mesh_component_map", 3)
                requires_batch_v3 = True
            elif step_type == "mesh_extract":
                await require_capability(client, "mesh_extraction")
                client.require_capability("mesh_component_map", 3)
                requires_batch_v3 = True
            elif step_type in {
                "collection_create",
                "collection_link_object",
                "collection_unlink_object",
            }:
                await require_capability(client, "collection_authoring")
                requires_batch_v3 = True
            elif step_type in {"object_parent_set", "object_parent_clear"}:
                await require_capability(client, "object_parenting")
                requires_batch_v3 = True
            elif step_type == "rig_bind":
                await require_capability(client, "rig_binding")
                requires_batch_v3 = True
        if requires_batch_v2:
            client.require_capability("mesh_batch", 2)
            client.require_capability("mesh_topology", 4)
            client.require_capability("transactions", 9)
        if requires_batch_v3:
            client.require_capability("mesh_batch", 3)
            client.require_capability("transactions", 11)
        return await client.call(
            "mesh.batch.execute",
            {
                "transaction_id": transaction_id,
                "targets": [item.model_dump() for item in targets],
                "inputs": [item.model_dump() for item in inputs],
                "steps": step_payloads,
                "on_error": on_error,
            },
            expected_scene_generation=expected_scene_generation,
            idempotency_key=idempotency_key,
            read_only=False,
        )

    @server.tool(
        name="shape_key.set_value",
        description=(
            "Set one exact non-Basis, non-driven mesh shape key to an absolute value inside "
            "the active transaction."
        ),
        annotations=SCENE_MUTATION,
        structured_output=True,
    )
    async def shape_key_set_value(
        transaction_id: TransactionId,
        object_name: ObjectName,
        expected_object_identity: SessionIdentity,
        shape_key_name: ShapeKeyName,
        expected_shape_key_identity: SessionIdentity,
        value: FiniteFloat,
        expected_scene_generation: SceneGeneration,
        idempotency_key: IdempotencyKey,
    ) -> dict[str, Any]:
        return await client.call(
            "shape_key.set_value",
            {
                "transaction_id": transaction_id,
                "object_name": object_name,
                "expected_object_identity": expected_object_identity,
                "shape_key_name": shape_key_name,
                "expected_shape_key_identity": expected_shape_key_identity,
                "value": value,
            },
            expected_scene_generation=expected_scene_generation,
            idempotency_key=idempotency_key,
            read_only=False,
        )

    @server.tool(
        name="material.set_input",
        description=(
            "Set one exact unlinked, undriven scalar/vector/color material input inside "
            "the active transaction. Shared materials require explicit confirmation."
        ),
        annotations=SCENE_MUTATION,
        structured_output=True,
    )
    async def material_set_input(
        transaction_id: TransactionId,
        object_name: ObjectName,
        expected_object_identity: SessionIdentity,
        material_slot_index: MaterialSlotIndex,
        material_name: MaterialName,
        expected_material_identity: SessionIdentity,
        expected_material_users: MaterialUsers,
        node_name: NodeName,
        expected_node_identity: SessionIdentity,
        socket_identifier: SocketIdentifier,
        expected_socket_identity: SessionIdentity,
        value: MaterialInputValue,
        expected_scene_generation: SceneGeneration,
        idempotency_key: IdempotencyKey,
        allow_shared: StrictBool = False,
    ) -> dict[str, Any]:
        return await client.call(
            "material.set_input",
            {
                "transaction_id": transaction_id,
                "object_name": object_name,
                "expected_object_identity": expected_object_identity,
                "material_slot_index": material_slot_index,
                "material_name": material_name,
                "expected_material_identity": expected_material_identity,
                "expected_material_users": expected_material_users,
                "node_name": node_name,
                "expected_node_identity": expected_node_identity,
                "socket_identifier": socket_identifier,
                "expected_socket_identity": expected_socket_identity,
                "value": value,
                "allow_shared": allow_shared,
            },
            expected_scene_generation=expected_scene_generation,
            idempotency_key=idempotency_key,
            read_only=False,
        )

    @server.tool(
        name="material.create",
        description=(
            "Create a uniquely named canonical Principled PBR material with bounded semantic "
            "surface values inside the active structural transaction."
        ),
        annotations=SCENE_MUTATION,
        structured_output=True,
    )
    async def material_create(
        transaction_id: TransactionId,
        definition: MaterialDefinition,
        expected_scene_generation: SceneGeneration,
        idempotency_key: IdempotencyKey,
    ) -> dict[str, Any]:
        await require_capability(client, "material_authoring")
        client.require_capability("transactions", 3)
        return await client.call(
            "material.create",
            {"transaction_id": transaction_id, "definition": definition.model_dump()},
            expected_scene_generation=expected_scene_generation,
            idempotency_key=idempotency_key,
            read_only=False,
        )

    @server.tool(
        name="material.assign",
        description=(
            "Append, replace, or clear one exact material slot on inspected object data. "
            "Shared data requires its exact user count and explicit permission."
        ),
        annotations=SCENE_MUTATION,
        structured_output=True,
    )
    async def material_assign(
        transaction_id: TransactionId,
        object_name: ObjectName,
        expected_object_identity: SessionIdentity,
        expected_data_identity: SessionIdentity,
        expected_data_users: DataUsers,
        mode: MaterialAssignMode,
        expected_scene_generation: SceneGeneration,
        idempotency_key: IdempotencyKey,
        slot_index: MaterialSlotIndex | None = None,
        expected_slot_material_identity: SessionIdentity | None = None,
        material_name: MaterialName | None = None,
        expected_material_identity: SessionIdentity | None = None,
        expected_material_users: Annotated[StrictInt, Field(ge=0)] | None = None,
        allow_shared_data: StrictBool = False,
    ) -> dict[str, Any]:
        if mode == "append" and slot_index is not None:
            raise ValueError("append does not accept slot_index")
        if mode in {"replace", "clear"} and (
            slot_index is None or expected_slot_material_identity is None
        ):
            raise ValueError(
                "replace and clear require slot_index and expected_slot_material_identity"
            )
        if mode in {"append", "replace"} and (
            material_name is None
            or expected_material_identity is None
            or expected_material_users is None
        ):
            raise ValueError("append and replace require exact material identity and user count")
        await require_capability(client, "material_authoring")
        client.require_capability("transactions", 3)
        return await client.call(
            "material.assign",
            {
                "transaction_id": transaction_id,
                "object_name": object_name,
                "expected_object_identity": expected_object_identity,
                "expected_data_identity": expected_data_identity,
                "expected_data_users": expected_data_users,
                "mode": mode,
                "slot_index": slot_index,
                "expected_slot_material_identity": expected_slot_material_identity,
                "material_name": material_name,
                "expected_material_identity": expected_material_identity,
                "expected_material_users": expected_material_users,
                "allow_shared_data": allow_shared_data,
            },
            expected_scene_generation=expected_scene_generation,
            idempotency_key=idempotency_key,
            read_only=False,
        )

    @server.tool(
        name="image.load",
        description=(
            "Load or reuse an image from an arbitrary absolute local path with a bounded "
            "color-space policy inside the active structural transaction."
        ),
        annotations=SCENE_MUTATION,
        structured_output=True,
    )
    async def image_load(
        transaction_id: TransactionId,
        path: AssetPath,
        expected_scene_generation: SceneGeneration,
        idempotency_key: IdempotencyKey,
        colorspace: ImageColorSpace = "AUTO",
    ) -> dict[str, Any]:
        await require_capability(client, "image_assets")
        client.require_capability("transactions", 3)
        return await client.call(
            "image.load",
            {"transaction_id": transaction_id, "path": path, "colorspace": colorspace},
            expected_scene_generation=expected_scene_generation,
            idempotency_key=idempotency_key,
            read_only=False,
        )

    @server.tool(
        name="material.texture.bind",
        description=(
            "Bind one exact local image through generated semantic mapping nodes to a "
            "Principled PBR channel. Existing links require an exact replacement guard."
        ),
        annotations=SCENE_MUTATION,
        structured_output=True,
    )
    async def material_texture_bind(
        transaction_id: TransactionId,
        material_name: MaterialName,
        expected_material_identity: SessionIdentity,
        expected_material_users: Annotated[StrictInt, Field(ge=0)],
        node_name: NodeName,
        expected_node_identity: SessionIdentity,
        image_name: ImageName,
        expected_image_identity: SessionIdentity,
        expected_image_users: Annotated[StrictInt, Field(ge=0)],
        channel: MaterialTextureChannel,
        expected_scene_generation: SceneGeneration,
        idempotency_key: IdempotencyKey,
        coordinates: TextureCoordinate = "UV",
        mapping: TextureMapping | None = None,
        replace_existing: StrictBool = False,
        expected_link_identities: LinkIdentities | None = None,
        allow_shared: StrictBool = False,
    ) -> dict[str, Any]:
        if replace_existing and expected_link_identities is None:
            raise ValueError("replace_existing requires expected_link_identities")
        if not replace_existing and expected_link_identities is not None:
            raise ValueError("expected_link_identities requires replace_existing")
        await require_capability(client, "material_authoring")
        client.require_capability("image_assets", 1)
        client.require_capability("transactions", 3)
        return await client.call(
            "material.texture.bind",
            {
                "transaction_id": transaction_id,
                "material_name": material_name,
                "expected_material_identity": expected_material_identity,
                "expected_material_users": expected_material_users,
                "node_name": node_name,
                "expected_node_identity": expected_node_identity,
                "image_name": image_name,
                "expected_image_identity": expected_image_identity,
                "expected_image_users": expected_image_users,
                "channel": channel,
                "coordinates": coordinates,
                "mapping": (mapping or TextureMapping()).model_dump(),
                "replace_existing": replace_existing,
                "expected_link_identities": list(expected_link_identities or ()),
                "allow_shared": allow_shared,
            },
            expected_scene_generation=expected_scene_generation,
            idempotency_key=idempotency_key,
            read_only=False,
        )

    @server.tool(
        name="material.texture.clear",
        description=(
            "Clear the exact inspected incoming link set from one Principled semantic channel "
            "and restore it if the structural transaction rolls back."
        ),
        annotations=SCENE_MUTATION,
        structured_output=True,
    )
    async def material_texture_clear(
        transaction_id: TransactionId,
        material_name: MaterialName,
        expected_material_identity: SessionIdentity,
        expected_material_users: Annotated[StrictInt, Field(ge=0)],
        node_name: NodeName,
        expected_node_identity: SessionIdentity,
        channel: MaterialTextureChannel,
        expected_link_identities: LinkIdentities,
        expected_scene_generation: SceneGeneration,
        idempotency_key: IdempotencyKey,
        allow_shared: StrictBool = False,
    ) -> dict[str, Any]:
        await require_capability(client, "material_authoring")
        client.require_capability("transactions", 3)
        return await client.call(
            "material.texture.clear",
            {
                "transaction_id": transaction_id,
                "material_name": material_name,
                "expected_material_identity": expected_material_identity,
                "expected_material_users": expected_material_users,
                "node_name": node_name,
                "expected_node_identity": expected_node_identity,
                "channel": channel,
                "expected_link_identities": list(expected_link_identities),
                "allow_shared": allow_shared,
            },
            expected_scene_generation=expected_scene_generation,
            idempotency_key=idempotency_key,
            read_only=False,
        )

    @server.tool(
        name="world.set",
        description=(
            "Create or modify the current World background and an optional exact local "
            "environment image inside the active structural transaction."
        ),
        annotations=SCENE_MUTATION,
        structured_output=True,
    )
    async def world_set(
        transaction_id: TransactionId,
        expected_scene_generation: SceneGeneration,
        idempotency_key: IdempotencyKey,
        expected_world_identity: SessionIdentity | None = None,
        expected_world_users: Annotated[StrictInt, Field(ge=1)] | None = None,
        color: ColorSpec | None = None,
        strength: Annotated[FiniteNumber, Field(ge=0, le=1_000_000)] | None = None,
        environment_image_name: ImageName | None = None,
        expected_environment_image_identity: SessionIdentity | None = None,
        expected_environment_image_users: Annotated[StrictInt, Field(ge=0)] | None = None,
        rotation_z_degrees: Annotated[FiniteNumber, Field(ge=-360_000, le=360_000)] | None = None,
        allow_shared: StrictBool = False,
    ) -> dict[str, Any]:
        if (expected_world_identity is None) != (expected_world_users is None):
            raise ValueError("World identity and user count must be supplied together")
        environment_values = (
            environment_image_name,
            expected_environment_image_identity,
            expected_environment_image_users,
        )
        if any(value is not None for value in environment_values) and any(
            value is None for value in environment_values
        ):
            raise ValueError("Environment image name, identity, and users are required together")
        if color is None and strength is None and environment_image_name is None:
            raise ValueError("color, strength, and/or environment image is required")
        await require_capability(client, "world_authoring")
        client.require_capability("transactions", 3)
        if environment_image_name is not None:
            client.require_capability("image_assets", 1)
        return await client.call(
            "world.set",
            {
                "transaction_id": transaction_id,
                "expected_world_identity": expected_world_identity,
                "expected_world_users": expected_world_users,
                "color": color.model_dump() if color is not None else None,
                "strength": strength,
                "environment_image_name": environment_image_name,
                "expected_environment_image_identity": expected_environment_image_identity,
                "expected_environment_image_users": expected_environment_image_users,
                "rotation_z_degrees": rotation_z_degrees,
                "allow_shared": allow_shared,
            },
            expected_scene_generation=expected_scene_generation,
            idempotency_key=idempotency_key,
            read_only=False,
        )

    @server.tool(
        name="scene.camera.set",
        description=(
            "Set one exact Camera object as the active scene camera inside the structural "
            "transaction and restore the previous camera on rollback."
        ),
        annotations=SCENE_MUTATION,
        structured_output=True,
    )
    async def scene_camera_set(
        transaction_id: TransactionId,
        camera_name: ObjectName,
        expected_camera_identity: SessionIdentity,
        expected_scene_generation: SceneGeneration,
        idempotency_key: IdempotencyKey,
    ) -> dict[str, Any]:
        await require_capability(client, "world_authoring")
        client.require_capability("transactions", 3)
        return await client.call(
            "scene.camera.set",
            {
                "transaction_id": transaction_id,
                "camera_name": camera_name,
                "expected_camera_identity": expected_camera_identity,
            },
            expected_scene_generation=expected_scene_generation,
            idempotency_key=idempotency_key,
            read_only=False,
        )

    @server.tool(
        name="render.preview",
        description=(
            "Render one exact Camera with temporary bounded Eevee Next settings, return PNG "
            "evidence, and restore all camera and render settings."
        ),
        annotations=PREVIEW_MUTATION,
        structured_output=False,
    )
    async def render_preview_tool(
        camera_name: ObjectName,
        expected_camera_identity: SessionIdentity,
        width: RenderSize,
        height: RenderSize,
        samples: RenderSamples,
        expected_scene_generation: SceneGeneration,
        idempotency_key: IdempotencyKey,
        transparent: StrictBool = False,
    ) -> CallToolResult:
        image_bytes, result = await request_render_preview(
            client,
            {
                "camera_name": camera_name,
                "expected_camera_identity": expected_camera_identity,
                "width": width,
                "height": height,
                "samples": samples,
                "transparent": transparent,
            },
            expected_scene_generation=expected_scene_generation,
            idempotency_key=idempotency_key,
        )
        return CallToolResult(
            content=[
                ImageContent(
                    type="image",
                    data=base64.b64encode(image_bytes).decode("ascii"),
                    mimeType="image/png",
                ),
                TextContent(type="text", text=json.dumps(result, ensure_ascii=False, indent=2)),
            ],
            structuredContent=result,
        )

    @server.tool(
        name="render.save",
        description=(
            "Render one exact Camera with bounded Eevee Next settings and overwrite an "
            "absolute PNG or EXR output path whose parent already exists."
        ),
        annotations=SCENE_MUTATION,
        structured_output=True,
    )
    async def render_save_tool(
        camera_name: ObjectName,
        expected_camera_identity: SessionIdentity,
        path: AssetPath,
        width: RenderSize,
        height: RenderSize,
        samples: RenderSamples,
        expected_scene_generation: SceneGeneration,
        idempotency_key: IdempotencyKey,
        transparent: StrictBool = False,
    ) -> dict[str, Any]:
        return await request_render_save(
            client,
            {
                "camera_name": camera_name,
                "expected_camera_identity": expected_camera_identity,
                "path": path,
                "width": width,
                "height": height,
                "samples": samples,
                "transparent": transparent,
            },
            expected_scene_generation=expected_scene_generation,
            idempotency_key=idempotency_key,
        )

    @server.tool(
        name="transaction.commit",
        description=(
            "End a transaction while retaining its changes in the current Blender session. "
            "This never saves the blend file."
        ),
        annotations=SCENE_MUTATION,
        structured_output=True,
    )
    async def transaction_commit(
        transaction_id: TransactionId,
        expected_scene_generation: SceneGeneration,
        idempotency_key: IdempotencyKey,
    ) -> dict[str, Any]:
        return await client.call(
            "transaction.commit",
            {"transaction_id": transaction_id},
            expected_scene_generation=expected_scene_generation,
            idempotency_key=idempotency_key,
            read_only=False,
        )

    @server.tool(
        name="transaction.rollback",
        description="Restore transaction property deltas and the captured user context.",
        annotations=SCENE_MUTATION,
        structured_output=True,
    )
    async def transaction_rollback(
        transaction_id: TransactionId,
        expected_scene_generation: SceneGeneration,
        idempotency_key: IdempotencyKey,
    ) -> dict[str, Any]:
        return await client.call(
            "transaction.rollback",
            {"transaction_id": transaction_id},
            expected_scene_generation=expected_scene_generation,
            idempotency_key=idempotency_key,
            read_only=False,
        )

    return server
