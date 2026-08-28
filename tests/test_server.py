import asyncio

from blender_research_mcp.server import create_server


def test_first_mcp_tool_uses_documented_dotted_name() -> None:
    server = create_server()
    assert server._mcp_server.version == "0.4.0"
    tools = asyncio.run(server.list_tools())
    assert [tool.name for tool in tools] == [
        "connection.ping",
        "context.get",
        "context.snapshot",
        "context.restore",
        "object.inspect",
        "viewport.capture",
        "viewport.raycast",
        "observation.bundle",
        "transaction.begin",
        "object.transform",
        "transaction.commit",
        "transaction.rollback",
    ]
    tools_by_name = {tool.name: tool for tool in tools}
    capture = tools_by_name["viewport.capture"]
    assert capture.annotations is not None
    assert capture.annotations.readOnlyHint is True
    assert capture.inputSchema["properties"]["max_size"]["minimum"] == 256
    assert capture.inputSchema["properties"]["max_size"]["maximum"] == 1600
    raycast = tools_by_name["viewport.raycast"]
    assert raycast.annotations is not None
    assert raycast.annotations.readOnlyHint is True
    assert raycast.annotations.idempotentHint is True
    assert raycast.inputSchema["properties"]["x"]["minimum"] == 0.0
    assert raycast.inputSchema["properties"]["x"]["maximum"] == 1.0
    bundle = tools_by_name["observation.bundle"]
    assert bundle.annotations is not None
    assert bundle.annotations.readOnlyHint is True
    assert bundle.inputSchema["properties"]["views"]["minItems"] == 1
    assert bundle.inputSchema["properties"]["views"]["maxItems"] == 3
    assert bundle.inputSchema["properties"]["max_size"]["maximum"] == 1200
    transform = tools_by_name["object.transform"]
    assert transform.annotations is not None
    assert transform.annotations.readOnlyHint is False
    assert transform.annotations.destructiveHint is True
    assert transform.annotations.idempotentHint is True
