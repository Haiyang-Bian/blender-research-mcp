import asyncio

from blender_research_mcp.server import create_server


def test_first_mcp_tool_uses_documented_dotted_name() -> None:
    tools = asyncio.run(create_server().list_tools())
    assert [tool.name for tool in tools] == [
        "connection.ping",
        "context.get",
        "context.snapshot",
        "context.restore",
        "object.inspect",
        "viewport.capture",
    ]
    capture = tools[-1]
    assert capture.annotations is not None
    assert capture.annotations.readOnlyHint is True
    assert capture.inputSchema["properties"]["max_size"]["minimum"] == 256
    assert capture.inputSchema["properties"]["max_size"]["maximum"] == 1600
