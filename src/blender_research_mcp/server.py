"""FastMCP stdio surface for the Blender research bridge."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from blender_research_mcp.client import BridgeClient
from blender_research_mcp.constants import DEFAULT_PORT

READ_ONLY = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=False,
)


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

    @server.tool(
        name="connection.ping",
        description="Check the authenticated Blender connection, versions, and UI heartbeat.",
        annotations=READ_ONLY,
        structured_output=True,
    )
    async def connection_ping() -> dict[str, Any]:
        return await client.call("connection.ping", read_only=True)

    return server
