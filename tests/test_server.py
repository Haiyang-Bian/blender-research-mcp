import asyncio

import pytest
from pydantic import TypeAdapter, ValidationError

from blender_research_mcp.server import MaterialInputValue, create_server


def test_first_mcp_tool_uses_documented_dotted_name() -> None:
    server = create_server()
    assert server._mcp_server.version == "0.11.1"
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
        "scene.inspect",
        "object.inspect",
        "object.geometry.inspect",
        "mesh.inspect",
        "object.lookdev.inspect",
        "modifier.inspect",
        "material.inspect",
        "image.inspect",
        "viewport.capture",
        "viewport.raycast",
        "observation.bundle",
        "lookdev.compare",
        "transaction.begin",
        "object.create",
        "object.duplicate",
        "object.delete",
        "object.set",
        "object.transform",
        "object.visibility.set",
        "modifier.set_state",
        "modifier.create",
        "modifier.set",
        "modifier.move",
        "modifier.delete",
        "mesh.edit",
        "shape_key.set_value",
        "material.set_input",
        "material.create",
        "material.assign",
        "image.load",
        "material.texture.bind",
        "material.texture.clear",
        "world.set",
        "scene.camera.set",
        "render.preview",
        "render.save",
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
    mesh_inspect = tools_by_name["mesh.inspect"]
    assert mesh_inspect.annotations is not None
    assert mesh_inspect.annotations.readOnlyHint is True
    assert mesh_inspect.inputSchema["properties"]["component"]["enum"] == [
        "summary",
        "vertices",
        "edges",
        "faces",
    ]
    assert mesh_inspect.inputSchema["properties"]["limit"]["maximum"] == 512
    lookdev = tools_by_name["object.lookdev.inspect"]
    assert lookdev.annotations is not None
    assert lookdev.annotations.readOnlyHint is True
    modifier_inspect = tools_by_name["modifier.inspect"]
    assert modifier_inspect.annotations is not None
    assert modifier_inspect.annotations.readOnlyHint is True
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
    assert len(target_schema["oneOf"]) == 7
    scene_inspect = tools_by_name["scene.inspect"]
    assert scene_inspect.annotations is not None
    assert scene_inspect.annotations.readOnlyHint is True
    assert scene_inspect.inputSchema["properties"]["kinds"]["minItems"] == 1
    assert scene_inspect.inputSchema["properties"]["kinds"]["maxItems"] == 7
    assert scene_inspect.inputSchema["properties"]["limit"]["maximum"] == 256
    object_create = tools_by_name["object.create"]
    assert object_create.annotations is not None
    assert object_create.annotations.destructiveHint is True
    definition_schema = object_create.inputSchema["properties"]["definition"]
    assert definition_schema["discriminator"]["propertyName"] == "type"
    assert len(definition_schema["oneOf"]) == 10
    object_duplicate = tools_by_name["object.duplicate"]
    assert object_duplicate.inputSchema["properties"]["linked_data"]["default"] is False
    object_delete = tools_by_name["object.delete"]
    assert object_delete.inputSchema["properties"]["expected_object_identity"][
        "maxLength"
    ] == 128
    object_set = tools_by_name["object.set"]
    assert object_set.annotations is not None
    assert object_set.annotations.readOnlyHint is False
    assert object_set.annotations.destructiveHint is True
    assert object_set.annotations.idempotentHint is True
    assert object_set.annotations.openWorldHint is False
    patches_schema = object_set.inputSchema["properties"]["patches"]
    assert patches_schema["minItems"] == 1
    assert patches_schema["maxItems"] == 4
    assert patches_schema["items"]["discriminator"]["propertyName"] == "type"
    assert len(patches_schema["items"]["oneOf"]) == 4
    transform = tools_by_name["object.transform"]
    assert transform.annotations is not None
    assert transform.annotations.readOnlyHint is False
    assert transform.annotations.destructiveHint is True
    assert transform.annotations.idempotentHint is True
    assert "location" in transform.inputSchema["properties"]
    assert "rotation_euler_degrees" in transform.inputSchema["properties"]
    assert "expected_object_identity" in transform.inputSchema["properties"]
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
    modifier_create = tools_by_name["modifier.create"]
    assert modifier_create.annotations is not None
    assert modifier_create.annotations.destructiveHint is True
    modifier_definition = modifier_create.inputSchema["properties"]["definition"]
    assert modifier_definition["discriminator"]["propertyName"] == "type"
    assert len(modifier_definition["oneOf"]) == 4
    modifier_set = tools_by_name["modifier.set"]
    modifier_settings = modifier_set.inputSchema["properties"]["settings"]
    assert modifier_settings["discriminator"]["propertyName"] == "type"
    assert len(modifier_settings["oneOf"]) == 4
    for name in ("modifier.set", "modifier.move", "modifier.delete"):
        tool = tools_by_name[name]
        assert tool.annotations is not None
        assert tool.annotations.readOnlyHint is False
        assert tool.annotations.destructiveHint is True
        assert tool.annotations.idempotentHint is True
        assert tool.annotations.openWorldHint is False
    mesh_edit = tools_by_name["mesh.edit"]
    assert mesh_edit.annotations is not None
    assert mesh_edit.annotations.readOnlyHint is False
    assert mesh_edit.annotations.destructiveHint is True
    assert mesh_edit.annotations.idempotentHint is True
    assert mesh_edit.annotations.openWorldHint is False
    assert mesh_edit.inputSchema["properties"]["data_scope"]["enum"] == [
        "OBJECT",
        "SHARED_DATA",
    ]
    operation_schema = mesh_edit.inputSchema["properties"]["operation"]
    assert operation_schema["discriminator"]["propertyName"] == "type"
    assert len(operation_schema["oneOf"]) == 9
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
    image_inspect = tools_by_name["image.inspect"]
    assert image_inspect.annotations is not None
    assert image_inspect.annotations.readOnlyHint is True
    material_create = tools_by_name["material.create"]
    color_schema = material_create.inputSchema["$defs"]["MaterialDefinition"]["properties"][
        "base_color"
    ]
    assert color_schema["discriminator"]["propertyName"] == "type"
    assert len(color_schema["oneOf"]) == 2
    material_assign = tools_by_name["material.assign"]
    assert material_assign.inputSchema["properties"]["mode"]["enum"] == [
        "append",
        "replace",
        "clear",
    ]
    image_load = tools_by_name["image.load"]
    assert image_load.inputSchema["properties"]["colorspace"]["enum"] == [
        "AUTO",
        "SRGB",
        "NON_COLOR",
    ]
    texture_bind = tools_by_name["material.texture.bind"]
    assert texture_bind.inputSchema["properties"]["channel"]["enum"] == [
        "base_color",
        "roughness",
        "metallic",
        "normal",
        "bump",
        "emission",
        "alpha",
    ]
    assert texture_bind.inputSchema["properties"]["replace_existing"]["default"] is False
    texture_clear = tools_by_name["material.texture.clear"]
    assert texture_clear.inputSchema["properties"]["expected_link_identities"][
        "minItems"
    ] == 1
    world_set = tools_by_name["world.set"]
    assert world_set.inputSchema["properties"]["allow_shared"]["default"] is False
    assert world_set.annotations is not None
    assert world_set.annotations.destructiveHint is True
    scene_camera = tools_by_name["scene.camera.set"]
    assert scene_camera.inputSchema["properties"]["expected_camera_identity"][
        "maxLength"
    ] == 128
    render_preview = tools_by_name["render.preview"]
    assert render_preview.annotations is not None
    assert render_preview.annotations.readOnlyHint is False
    assert render_preview.annotations.destructiveHint is False
    assert render_preview.inputSchema["properties"]["width"]["minimum"] == 256
    assert render_preview.inputSchema["properties"]["width"]["maximum"] == 1000
    assert render_preview.inputSchema["properties"]["samples"]["maximum"] == 64
    render_save = tools_by_name["render.save"]
    assert render_save.annotations is not None
    assert render_save.annotations.destructiveHint is True
    assert render_save.inputSchema["properties"]["transparent"]["default"] is False


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
