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
    InitialTransform,
    LocationAxisPatch,
    ObjectDefinition,
    RotationAxisPatch,
    ScaleAxisPatch,
    SceneKinds,
    require_capability,
)
from blender_research_mcp.client import BridgeClient
from blender_research_mcp.comparison import (
    ComparisonCandidates,
    ComparisonCapture,
    ComparisonRequest,
    ComparisonTarget,
    run_lookdev_comparison,
)
from blender_research_mcp.constants import DEFAULT_PORT, PACKAGE_VERSION
from blender_research_mcp.lifecycle import (
    DEFAULT_LAUNCH_TIMEOUT_SECONDS,
    ApplicationManager,
)
from blender_research_mcp.observation import (
    capture_image,
    collect_observation_bundle,
    settle_capture_generation,
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
