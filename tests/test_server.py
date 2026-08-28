import asyncio

from blender_research_mcp.server import create_server


def test_first_mcp_tool_uses_documented_dotted_name() -> None:
    server = create_server()
    assert server._mcp_server.version == "0.3.0"
    tools = asyncio.run(server.list_tools())
    assert [tool.name for tool in tools] == [
        "connection.ping",
        "context.get",
        "context.snapshot",
        "context.restore",
        "object.inspect",
        "viewport.capture",
        "transaction.begin",
        "object.transform",
        "transaction.commit",
        "transaction.rollback",
    ]
    capture = tools[5]
    assert capture.annotations is not None
    assert capture.annotations.readOnlyHint is True
    assert capture.inputSchema["properties"]["max_size"]["minimum"] == 256
    assert capture.inputSchema["properties"]["max_size"]["maximum"] == 1600
    transform = tools[7]
    assert transform.annotations is not None
    assert transform.annotations.readOnlyHint is False
    assert transform.annotations.destructiveHint is True
    assert transform.annotations.idempotentHint is True
