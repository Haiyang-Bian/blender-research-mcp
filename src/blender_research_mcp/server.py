"""FastMCP stdio surface for the Blender research bridge."""

from __future__ import annotations

import base64
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from importlib.metadata import version as package_version
from typing import Annotated, Any, Literal

from mcp.server.fastmcp import FastMCP
from mcp.types import CallToolResult, ContentBlock, ImageContent, TextContent, ToolAnnotations
from pydantic import BaseModel, ConfigDict, Field, model_validator

from blender_research_mcp.client import BridgeClient
from blender_research_mcp.constants import DEFAULT_PORT
from blender_research_mcp.observation import (
    capture_image,
    collect_observation_bundle,
    settle_scene_generation,
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
BundleViews = Annotated[tuple[SemanticView, ...], Field(min_length=1, max_length=3)]


class ScalePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    x: float | None = Field(default=None, ge=0.000001, le=1000)
    y: float | None = Field(default=None, ge=0.000001, le=1000)
    z: float | None = Field(default=None, ge=0.000001, le=1000)

    @model_validator(mode="after")
    def require_one_axis(self) -> ScalePatch:
        if self.x is None and self.y is None and self.z is None:
            raise ValueError("at least one scale axis is required")
        return self


def create_server(*, port: int = DEFAULT_PORT) -> FastMCP[Any]:
    client = BridgeClient(port=port)

    @asynccontextmanager
    async def lifespan(_server: FastMCP[Any]) -> AsyncIterator[BridgeClient]:
        try:
            yield client
        finally:
            await client.close()

    server = FastMCP(
        name="blender-research-mcp",
        instructions=(
            "Local semantic Blender research tools. Operations never save the blend file "
            "or execute arbitrary Python."
        ),
        lifespan=lifespan,
    )
    # FastMCP 1.x does not expose the low-level Server version in its constructor.
    server._mcp_server.version = package_version("blender-research-mcp")

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
    ) -> CallToolResult:
        image_bytes, result = await capture_image(
            client,
            object_name=object_name,
            view=view,
            max_size=max_size,
            viewport_id=viewport_id,
        )
        settled = await settle_scene_generation(client)
        result["scene_generation"] = int(settled["scene_generation"])
        result["settled_heartbeat"] = int(settled["heartbeat"])
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
    ) -> CallToolResult:
        images, result = await collect_observation_bundle(
            client,
            object_name=object_name,
            views=views,
            max_size=max_size,
            viewport_id=viewport_id,
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
        scale: ScalePatch,
        expected_scene_generation: SceneGeneration,
        idempotency_key: IdempotencyKey,
    ) -> dict[str, Any]:
        return await client.call(
            "object.transform",
            {
                "transaction_id": transaction_id,
                "object_name": object_name,
                "scale": scale.model_dump(exclude_none=True),
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
