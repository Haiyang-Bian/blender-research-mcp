import asyncio

import pytest
from pydantic import TypeAdapter, ValidationError

from blender_research_mcp.server import MaterialInputValue, create_server


def test_first_mcp_tool_uses_documented_dotted_name() -> None:
    server = create_server()
    assert server._mcp_server.version == "0.7.0"
    tools = asyncio.run(server.list_tools())
    assert [tool.name for tool in tools] == [
        "application.status",
        "application.launch",
        "application.quit",
        "project.status",
        "project.save",
        "project.open",
        "project.reload",
        "connection.ping",
        "context.get",
        "context.snapshot",
        "context.restore",
        "object.inspect",
        "object.geometry.inspect",
        "object.lookdev.inspect",
        "material.inspect",
        "viewport.capture",
        "viewport.raycast",
        "observation.bundle",
        "lookdev.compare",
        "transaction.begin",
        "object.transform",
        "object.visibility.set",
        "modifier.set_state",
        "shape_key.set_value",
        "material.set_input",
        "transaction.commit",
        "transaction.rollback",
    ]
    tools_by_name = {tool.name: tool for tool in tools}
    application_status = tools_by_name["application.status"]
    assert application_status.annotations is not None
    assert application_status.annotations.readOnlyHint is True
    application_launch = tools_by_name["application.launch"]
    assert application_launch.annotations is not None
    assert application_launch.annotations.readOnlyHint is False
    assert application_launch.annotations.destructiveHint is False
    assert application_launch.annotations.idempotentHint is True
    assert application_launch.annotations.openWorldHint is False
    assert application_launch.inputSchema["properties"] == {}
    application_quit = tools_by_name["application.quit"]
    assert application_quit.annotations is not None
    assert application_quit.annotations.readOnlyHint is False
    assert application_quit.annotations.destructiveHint is True
    project_status = tools_by_name["project.status"]
    assert project_status.annotations is not None
    assert project_status.annotations.readOnlyHint is True
    for name in ("project.save", "project.open", "project.reload"):
        tool = tools_by_name[name]
        assert tool.annotations is not None
        assert tool.annotations.readOnlyHint is False
        assert tool.annotations.destructiveHint is True
        assert tool.annotations.idempotentHint is True
        assert tool.annotations.openWorldHint is False
    project_open = tools_by_name["project.open"]
    assert project_open.inputSchema["properties"]["save_current"]["default"] is True
    assert project_open.inputSchema["properties"]["use_scripts"]["default"] is True
    assert project_open.inputSchema["properties"]["load_ui"]["default"] is True
    project_reload = tools_by_name["project.reload"]
    assert project_reload.inputSchema["properties"]["save_current"]["default"] is False
    capture = tools_by_name["viewport.capture"]
    assert capture.annotations is not None
    assert capture.annotations.readOnlyHint is True
    assert capture.inputSchema["properties"]["max_size"]["minimum"] == 256
    assert capture.inputSchema["properties"]["max_size"]["maximum"] == 1600
    assert capture.inputSchema["properties"]["display_mode"]["enum"] == [
        "CURRENT",
        "WIREFRAME",
        "SOLID",
        "MATERIAL",
        "RENDERED",
    ]
    assert capture.inputSchema["$defs"]["OrbitRequest"]["additionalProperties"] is False
    geometry = tools_by_name["object.geometry.inspect"]
    assert geometry.annotations is not None
    assert geometry.annotations.readOnlyHint is True
    lookdev = tools_by_name["object.lookdev.inspect"]
    assert lookdev.annotations is not None
    assert lookdev.annotations.readOnlyHint is True
    material_inspect = tools_by_name["material.inspect"]
    assert material_inspect.annotations is not None
    assert material_inspect.annotations.readOnlyHint is True
    assert material_inspect.inputSchema["properties"]["material_slot_index"]["maximum"] == 63
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
    assert "display_mode" in bundle.inputSchema["properties"]
    comparison = tools_by_name["lookdev.compare"]
    assert comparison.annotations is not None
    assert comparison.annotations.readOnlyHint is False
    assert comparison.annotations.destructiveHint is False
    assert comparison.annotations.idempotentHint is True
    assert comparison.annotations.openWorldHint is False
    assert comparison.inputSchema["properties"]["candidates"]["minItems"] == 1
    assert comparison.inputSchema["properties"]["candidates"]["maxItems"] == 3
    assert comparison.inputSchema["$defs"]["ComparisonCapture"]["properties"]["max_size"][
        "maximum"
    ] == 1000
    target_schema = comparison.inputSchema["properties"]["target"]
    assert target_schema["discriminator"]["propertyName"] == "type"
    assert len(target_schema["oneOf"]) == 5
    transform = tools_by_name["object.transform"]
    assert transform.annotations is not None
    assert transform.annotations.readOnlyHint is False
    assert transform.annotations.destructiveHint is True
    assert transform.annotations.idempotentHint is True
    visibility = tools_by_name["object.visibility.set"]
    assert visibility.annotations is not None
    assert visibility.annotations.destructiveHint is True
    assert visibility.inputSchema["properties"]["hide_viewport"]["anyOf"][0]["type"] == (
        "boolean"
    )
    modifier = tools_by_name["modifier.set_state"]
    assert modifier.annotations is not None
    assert modifier.annotations.idempotentHint is True
    assert modifier.inputSchema["properties"]["expected_modifier_identity"]["maxLength"] == 128
    shape_key = tools_by_name["shape_key.set_value"]
    assert shape_key.annotations is not None
    assert shape_key.annotations.destructiveHint is True
    assert shape_key.inputSchema["properties"]["expected_shape_key_identity"]["maxLength"] == 128
    material_input = tools_by_name["material.set_input"]
    assert material_input.annotations is not None
    assert material_input.annotations.destructiveHint is True
    assert material_input.annotations.idempotentHint is True
    assert material_input.inputSchema["properties"]["expected_material_users"]["minimum"] == 1
    assert len(material_input.inputSchema["properties"]["value"]["oneOf"]) == 5


def test_material_input_value_preserves_json_types() -> None:
    adapter = TypeAdapter(MaterialInputValue)

    assert adapter.validate_python(True) is True
    assert adapter.validate_python(3) == 3
    assert adapter.validate_python(0.25) == 0.25
    assert adapter.validate_python([0.1, 0.2, 0.3]) == [0.1, 0.2, 0.3]
    with pytest.raises(ValidationError):
        adapter.validate_python([1, 0.2, 0.3])
    with pytest.raises(ValidationError):
        adapter.validate_python(float("inf"))
