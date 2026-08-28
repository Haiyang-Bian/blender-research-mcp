import asyncio

from blender_research_mcp.server import create_server


def test_first_mcp_tool_uses_documented_dotted_name() -> None:
    tools = asyncio.run(create_server().list_tools())
    assert [tool.name for tool in tools] == ["connection.ping"]
