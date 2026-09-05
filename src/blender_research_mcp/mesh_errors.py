"""Keep Blender error evidence intact across the FastMCP boundary."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from functools import wraps
from typing import Any, ParamSpec

from mcp.types import CallToolResult, TextContent

from blender_research_mcp.errors import BridgeError

P = ParamSpec("P")


def mesh_errors(function: Callable[P, Awaitable[Any]]) -> Callable[P, Awaitable[Any]]:
    @wraps(function)
    async def wrapped(*args: P.args, **kwargs: P.kwargs) -> Any:
        try:
            return await function(*args, **kwargs)
        except BridgeError as exc:
            error = exc.error.model_dump(mode="json")
            return CallToolResult(
                isError=True,
                structuredContent={"error": error},
                content=[TextContent(type="text", text=f"{error['code']}: {error['message']}")],
            )

    return wrapped
