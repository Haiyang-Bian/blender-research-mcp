import asyncio

import pytest
from pydantic import TypeAdapter, ValidationError

import blender_research_mcp.server as server_module
from blender_research_mcp.library_assets import library_entry_identity
from blender_research_mcp.server import MaterialInputValue, create_server


def test_first_mcp_tool_uses_documented_dotted_name() -> None:
    server = create_server()
    assert server._mcp_server.version == "0.17.5"
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
        "collection.inspect",
        "object.inspect",
        "object.geometry.inspect",
        "mesh.inspect",
        "mesh.uv.inspect",
        "mesh.weights.inspect",
        "mesh.selection.query",
        "mesh.selection.derive",
        "mesh.selection.inspect",
        "mesh.boundary.inspect",
        "mesh.selection.release",
        "mesh.component_catalog.prepare",
        "mesh.component_catalog.inspect",
        "mesh.component_catalog.select",
        "mesh.component_catalog.release",
        "mesh.component_map.inspect",
        "mesh.component_map.release",
        "mesh.component_map.compose",
        "mesh.selection.remap",
        "mesh.surface.prepare",
        "mesh.surface.query",
        "mesh.validate",
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
        "collection.create",
        "library.inspect",
        "library.append",
        "collection.link_object",
        "collection.unlink_object",
        "object.parent.set",
        "object.parent.clear",
        "object.set",
        "object.transform",
        "object.visibility.set",
        "modifier.set_state",
        "modifier.create",
        "modifier.set",
        "modifier.move",
        "modifier.delete",
        "mesh.join.preflight",
        "mesh.join",
        "mesh.edit",
        "mesh.uv.edit",
        "mesh.weights.edit",
        "mesh.attribute.transfer",
        "rig.inspect",
        "rig.bind",
        "mesh.extract.preflight",
        "mesh.extract",
        "mesh.materialize",
        "mesh.separate",
        "mesh.batch.execute",
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
    mesh_uv_inspect = tools_by_name["mesh.uv.inspect"]
    assert mesh_uv_inspect.annotations is not None
    assert mesh_uv_inspect.annotations.readOnlyHint is True
    assert mesh_uv_inspect.inputSchema["properties"]["component"]["enum"] == [
        "SUMMARY",
        "FACES",
        "LOOPS",
        "ISLANDS",
        "SEAMS",
    ]
    mesh_weights_inspect = tools_by_name["mesh.weights.inspect"]
    assert mesh_weights_inspect.annotations is not None
    assert mesh_weights_inspect.annotations.readOnlyHint is True
    assert mesh_weights_inspect.inputSchema["properties"]["component"]["enum"] == [
        "SUMMARY",
        "GROUPS",
        "VERTICES",
    ]
    mesh_separate = tools_by_name["mesh.separate"]
    assert mesh_separate.annotations is not None
    assert mesh_separate.annotations.readOnlyHint is False
    assert mesh_separate.annotations.destructiveHint is True
    assert mesh_separate.annotations.idempotentHint is True
    assert mesh_separate.annotations.openWorldHint is False
    assert "data_scope" not in mesh_separate.inputSchema["properties"]
    assert mesh_separate.inputSchema["properties"]["new_object_name"]["maxLength"] == 255
    materialize = tools_by_name["mesh.materialize"]
    assert materialize.annotations is not None
    assert materialize.annotations.destructiveHint is True
    evaluation_schema = materialize.inputSchema["properties"]["evaluation"]
    assert evaluation_schema["discriminator"]["propertyName"] == "type"
    assert len(evaluation_schema["oneOf"]) == 3
    extract_preflight = tools_by_name["mesh.extract.preflight"]
    assert extract_preflight.annotations is not None
    assert extract_preflight.annotations.readOnlyHint is True
    extract = tools_by_name["mesh.extract"]
    assert extract.annotations is not None
    assert extract.annotations.destructiveHint is True
    assert "data_scope" not in extract.inputSchema["properties"]
    rig_inspect = tools_by_name["rig.inspect"]
    assert rig_inspect.annotations is not None
    assert rig_inspect.annotations.readOnlyHint is True
    rig_bind = tools_by_name["rig.bind"]
    assert rig_bind.annotations is not None
    assert rig_bind.annotations.destructiveHint is True
    assert rig_bind.inputSchema["properties"]["parenting"]["enum"] == [
        "NONE",
        "KEEP_WORLD",
        "KEEP_LOCAL",
    ]
    collection_inspect = tools_by_name["collection.inspect"]
    assert collection_inspect.annotations is not None
    assert collection_inspect.annotations.readOnlyHint is True
    assert collection_inspect.inputSchema["properties"]["limit"]["maximum"] == 256
    library_inspect = tools_by_name["library.inspect"]
    assert library_inspect.annotations is not None
    assert library_inspect.annotations.readOnlyHint is True
    assert library_inspect.inputSchema["properties"]["limit"]["maximum"] == 256
    assert library_inspect.inputSchema["properties"]["kinds"]["maxItems"] == 3
    library_append = tools_by_name["library.append"]
    assert library_append.annotations is not None
    assert library_append.annotations.readOnlyHint is False
    assert library_append.annotations.destructiveHint is True
    assert library_append.annotations.idempotentHint is True
    assert library_append.annotations.openWorldHint is False
    assert library_append.inputSchema["properties"]["output"]["discriminator"][
        "propertyName"
    ] == "type"
    mesh_join_preflight = tools_by_name["mesh.join.preflight"]
    assert mesh_join_preflight.annotations is not None
    assert mesh_join_preflight.annotations.readOnlyHint is True
    mesh_join = tools_by_name["mesh.join"]
    assert mesh_join.annotations is not None
    assert mesh_join.annotations.readOnlyHint is False
    assert mesh_join.annotations.destructiveHint is True
    assert mesh_join.annotations.idempotentHint is True
    assert mesh_join.inputSchema["properties"]["sources"]["minItems"] == 2
    assert mesh_join.inputSchema["properties"]["sources"]["maxItems"] == 32
    collection_create = tools_by_name["collection.create"]
    parent_schema = collection_create.inputSchema["properties"]["parent"]
    assert parent_schema["discriminator"]["propertyName"] == "type"
    assert len(parent_schema["oneOf"]) == 2
    for name in (
        "collection.create",
        "collection.link_object",
        "collection.unlink_object",
        "object.parent.set",
        "object.parent.clear",
    ):
        tool = tools_by_name[name]
        assert tool.annotations is not None
        assert tool.annotations.readOnlyHint is False
        assert tool.annotations.destructiveHint is True
        assert tool.annotations.idempotentHint is True
        assert tool.annotations.openWorldHint is False
    parent_set = tools_by_name["object.parent.set"]
    assert parent_set.inputSchema["properties"]["transform_mode"]["enum"] == [
        "KEEP_WORLD",
        "KEEP_LOCAL",
    ]
    mesh_batch = tools_by_name["mesh.batch.execute"]
    assert mesh_batch.annotations is not None
    assert mesh_batch.annotations.readOnlyHint is False
    assert mesh_batch.annotations.destructiveHint is True
    assert mesh_batch.annotations.idempotentHint is True
    assert mesh_batch.annotations.openWorldHint is False
    assert mesh_batch.inputSchema["properties"]["targets"]["minItems"] == 1
    assert mesh_batch.inputSchema["properties"]["targets"]["maxItems"] == 8
    assert mesh_batch.inputSchema["properties"]["steps"]["minItems"] == 1
    assert mesh_batch.inputSchema["properties"]["steps"]["maxItems"] == 32
    step_schema = mesh_batch.inputSchema["properties"]["steps"]["items"]
    assert step_schema["discriminator"]["propertyName"] == "type"
    assert len(step_schema["oneOf"]) == 21
    input_schema = mesh_batch.inputSchema["properties"]["inputs"]["items"]
    assert input_schema["discriminator"]["propertyName"] == "type"
    assert len(input_schema["oneOf"]) == 7
    selection_query = tools_by_name["mesh.selection.query"]
    assert selection_query.annotations is not None
    assert selection_query.annotations.readOnlyHint is True
    query_schema = selection_query.inputSchema["properties"]["query"]
    assert query_schema["discriminator"]["propertyName"] == "type"
    assert len(query_schema["oneOf"]) == 10
    selection_derive = tools_by_name["mesh.selection.derive"]
    derivation_schema = selection_derive.inputSchema["properties"]["operation"]
    assert derivation_schema["discriminator"]["propertyName"] == "type"
    assert len(derivation_schema["oneOf"]) == 6
    for name in (
        "mesh.selection.inspect",
        "mesh.selection.release",
        "mesh.component_catalog.prepare",
        "mesh.component_catalog.inspect",
        "mesh.component_catalog.select",
        "mesh.component_catalog.release",
        "mesh.component_map.compose",
        "mesh.surface.prepare",
        "mesh.surface.query",
        "mesh.validate",
    ):
        tool = tools_by_name[name]
        assert tool.annotations is not None
        assert tool.annotations.readOnlyHint is True
        assert tool.annotations.destructiveHint is False
    catalog_prepare = tools_by_name["mesh.component_catalog.prepare"]
    include_schema = catalog_prepare.inputSchema["properties"]["include"]
    assert include_schema["minItems"] == 1
    assert include_schema["maxItems"] == 5
    catalog_inspect = tools_by_name["mesh.component_catalog.inspect"]
    assert catalog_inspect.inputSchema["properties"]["limit"]["default"] == 128
    assert catalog_inspect.inputSchema["properties"]["limit"]["maximum"] == 256
    catalog_select = tools_by_name["mesh.component_catalog.select"]
    identities = catalog_select.inputSchema["properties"]["component_identities"]
    assert identities["minItems"] == 1
    assert identities["maxItems"] == 4096
    map_compose = tools_by_name["mesh.component_map.compose"]
    map_ids = map_compose.inputSchema["properties"]["component_map_ids"]
    assert map_ids["minItems"] == 2
    assert map_ids["maxItems"] == 8
    surface_prepare = tools_by_name["mesh.surface.prepare"]
    assert surface_prepare.inputSchema["properties"]["geometry"]["enum"] == [
        "BASE",
        "EVALUATED",
    ]
    surface_query = tools_by_name["mesh.surface.query"]
    assert surface_query.inputSchema["properties"]["mode"]["enum"] == [
        "CLOSEST_POINT",
        "RAYCAST",
    ]
    validation = tools_by_name["mesh.validate"]
    assert len(validation.inputSchema["properties"]["check"]["enum"]) == 16
    assert "layer_name" in validation.inputSchema["properties"]
    assert "expected_uv_fingerprint" in validation.inputSchema["properties"]
    assert "expected_weights_fingerprint" in validation.inputSchema["properties"]
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
    assert (
        comparison.inputSchema["$defs"]["ComparisonCapture"]["properties"]["max_size"]["maximum"]
        == 1000
    )
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
    assert object_delete.inputSchema["properties"]["expected_object_identity"]["maxLength"] == 128
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
    assert visibility.inputSchema["properties"]["hide_viewport"]["anyOf"][0]["type"] == ("boolean")
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
    assert len(operation_schema["oneOf"]) == 26
    mesh_uv_edit = tools_by_name["mesh.uv.edit"]
    assert mesh_uv_edit.annotations is not None
    assert mesh_uv_edit.annotations.readOnlyHint is False
    assert mesh_uv_edit.annotations.destructiveHint is True
    assert mesh_uv_edit.annotations.idempotentHint is True
    assert mesh_uv_edit.annotations.openWorldHint is False
    uv_operation = mesh_uv_edit.inputSchema["properties"]["operation"]
    assert uv_operation["discriminator"]["propertyName"] == "type"
    assert len(uv_operation["oneOf"]) == 9
    mesh_weights_edit = tools_by_name["mesh.weights.edit"]
    assert mesh_weights_edit.annotations is not None
    assert mesh_weights_edit.annotations.destructiveHint is True
    weight_operation = mesh_weights_edit.inputSchema["properties"]["operation"]
    assert weight_operation["discriminator"]["propertyName"] == "type"
    assert len(weight_operation["oneOf"]) == 7
    attribute_transfer = tools_by_name["mesh.attribute.transfer"]
    assert attribute_transfer.annotations is not None
    assert attribute_transfer.annotations.destructiveHint is True
    transfer_schema = attribute_transfer.inputSchema["properties"]["transfer"]
    assert transfer_schema["discriminator"]["propertyName"] == "type"
    assert len(transfer_schema["oneOf"]) == 2
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
    assert texture_clear.inputSchema["properties"]["expected_link_identities"]["minItems"] == 1
    world_set = tools_by_name["world.set"]
    assert world_set.inputSchema["properties"]["allow_shared"]["default"] is False
    assert world_set.annotations is not None
    assert world_set.annotations.destructiveHint is True
    scene_camera = tools_by_name["scene.camera.set"]
    assert scene_camera.inputSchema["properties"]["expected_camera_identity"]["maxLength"] == 128
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


def test_batch_v3_rig_bind_does_not_depend_on_separation_policy(monkeypatch) -> None:
    class FakeClient:
        def __init__(self, *, port: int) -> None:
            self.port = port
            self.calls: list[tuple[str, dict[str, object]]] = []

        async def connect(self) -> object:
            return object()

        def require_capability(self, _name: str, _version: int = 1) -> None:
            return None

        async def call(self, command: str, params=None, **_kwargs):
            payload = {} if params is None else params
            self.calls.append((command, payload))
            return {"command": command, "steps": payload.get("steps", [])}

        async def close(self) -> None:
            return None

    fake = FakeClient(port=9877)
    monkeypatch.setattr(server_module, "BridgeClient", lambda **_kwargs: fake)
    server = server_module.create_server()
    asyncio.run(
        server.call_tool(
            "mesh.batch.execute",
            {
                "transaction_id": "tx-1",
                "targets": [
                    {
                        "alias": "mesh",
                        "object_name": "Mesh",
                        "expected_object_identity": "object:1",
                        "expected_mesh_identity": "mesh:1",
                        "expected_mesh_users": 1,
                        "expected_mesh_user_objects": [
                            {
                                "object_name": "Mesh",
                                "expected_object_identity": "object:1",
                            }
                        ],
                        "expected_mesh_fingerprint": "a" * 64,
                    }
                ],
                "inputs": [
                    {
                        "type": "armature",
                        "alias": "rig",
                        "target": {
                            "object_name": "Rig",
                            "expected_object_identity": "object:2",
                            "expected_data_identity": "armature:1",
                            "expected_bone_schema_fingerprint": "b" * 64,
                        },
                    }
                ],
                "steps": [
                    {
                        "type": "rig_bind",
                        "mesh_target_alias": "mesh",
                        "armature_alias": "rig",
                        "modifier": {"name": "Armature", "expected_existing": None},
                        "parenting": "KEEP_WORLD",
                        "group_scope": {"type": "ALL_MATCHED"},
                        "output_binding_alias": "binding",
                    }
                ],
                "expected_scene_generation": 4,
                "idempotency_key": "123e4567-e89b-12d3-a456-426614174000",
            },
        )
    )
    assert fake.calls[-1][0] == "mesh.batch.execute"
    assert fake.calls[-1][1]["steps"][0]["type"] == "rig_bind"


def test_batch_v4_rechecks_and_enriches_library_file_evidence(monkeypatch) -> None:
    digest = "a" * 64

    class FakeClient:
        def __init__(self, *, port: int) -> None:
            self.port = port
            self.calls: list[tuple[str, dict[str, object]]] = []

        async def connect(self) -> object:
            return object()

        def require_capability(self, _name: str, _version: int = 1) -> None:
            return None

        async def call(self, command: str, params=None, **_kwargs):
            payload = {} if params is None else params
            self.calls.append((command, payload))
            return {"command": command}

        async def close(self) -> None:
            return None

    fake = FakeClient(port=9877)
    monkeypatch.setattr(server_module, "BridgeClient", lambda **_kwargs: fake)
    monkeypatch.setattr(
        server_module,
        "inspect_local_library_file",
        lambda _path: {
            "path": "C:\\fixtures\\templates.blend",
            "file_sha256": digest,
            "size_bytes": 4096,
            "modified_ns": 123456789,
            "blend_header": {"version": "420"},
        },
    )
    server = server_module.create_server()
    asyncio.run(
        server.call_tool(
            "mesh.batch.execute",
            {
                "transaction_id": "tx-1",
                "targets": [
                    {
                        "alias": "source",
                        "object_name": "Mesh",
                        "expected_object_identity": "object:1",
                        "expected_mesh_identity": "mesh:1",
                        "expected_mesh_users": 1,
                        "expected_mesh_user_objects": [
                            {
                                "object_name": "Mesh",
                                "expected_object_identity": "object:1",
                            }
                        ],
                        "expected_mesh_fingerprint": "b" * 64,
                    }
                ],
                "inputs": [
                    {
                        "type": "collection",
                        "alias": "templates_collection",
                        "collection_name": "Templates",
                        "expected_collection_identity": "collection:1",
                        "expected_collection_structure_fingerprint": "c" * 64,
                    },
                    {
                        "type": "library",
                        "alias": "templates",
                        "path": "C:\\fixtures\\templates.blend",
                        "expected_file_sha256": digest,
                        "expected_size_bytes": 4096,
                    },
                ],
                "steps": [
                    {
                        "type": "library_append",
                        "library_alias": "templates",
                        "entry": {
                            "type": "OBJECT",
                            "name": "HeadCage",
                            "expected_entry_identity": library_entry_identity(
                                digest, "OBJECT", "HeadCage"
                            ),
                        },
                        "output": {
                            "type": "OBJECT",
                            "new_object_name": "HeadCageInstance",
                            "collection_alias": "templates_collection",
                        },
                        "output_root_alias": "head",
                        "root_alias_kind": "MESH_TARGET",
                    },
                    {
                        "type": "mesh_surface_prepare",
                        "target_alias": "head",
                        "output_surface_alias": "head_surface",
                    },
                ],
                "expected_scene_generation": 4,
                "idempotency_key": "123e4567-e89b-12d3-a456-426614174001",
            },
        )
    )
    payload = fake.calls[-1][1]
    assert payload["inputs"][1]["expected_modified_ns"] == 123456789
    assert payload["steps"][0]["type"] == "library_append"


def test_batch_v5_requires_join_capabilities_and_preserves_boundary_aliases(
    monkeypatch,
) -> None:
    class FakeClient:
        def __init__(self, *, port: int) -> None:
            self.port = port
            self.requirements: list[tuple[str, int]] = []
            self.calls: list[tuple[str, dict[str, object]]] = []

        async def connect(self) -> object:
            return object()

        def require_capability(self, name: str, version: int = 1) -> None:
            self.requirements.append((name, version))

        async def call(self, command: str, params=None, **_kwargs):
            payload = {} if params is None else params
            self.calls.append((command, payload))
            return {"command": command}

        async def close(self) -> None:
            return None

    fake = FakeClient(port=9877)
    monkeypatch.setattr(server_module, "BridgeClient", lambda **_kwargs: fake)
    target = {
        "object_name": "Head",
        "expected_object_identity": "object:head",
        "expected_mesh_identity": "mesh:head",
        "expected_mesh_users": 1,
        "expected_mesh_user_objects": [
            {"object_name": "Head", "expected_object_identity": "object:head"}
        ],
        "expected_mesh_fingerprint": "a" * 64,
    }
    second = {
        **target,
        "object_name": "Body",
        "expected_object_identity": "object:body",
        "expected_mesh_identity": "mesh:body",
        "expected_mesh_user_objects": [
            {"object_name": "Body", "expected_object_identity": "object:body"}
        ],
    }
    server = server_module.create_server()
    asyncio.run(
        server.call_tool(
            "mesh.batch.execute",
            {
                "transaction_id": "tx-1",
                "targets": [
                    {"alias": "head", **target},
                    {"alias": "body", **second},
                ],
                "inputs": [
                    {
                        "type": "collection",
                        "alias": "modules",
                        "collection_name": "Modules",
                        "expected_collection_identity": "collection:1",
                        "expected_collection_structure_fingerprint": "b" * 64,
                    }
                ],
                "steps": [
                    {
                        "type": "mesh_join",
                        "sources": [
                            {
                                "target_alias": "head",
                                "map_alias": "head_map",
                                "boundary_selection_alias": "head_boundary",
                            },
                            {
                                "target_alias": "body",
                                "map_alias": "body_map",
                                "boundary_selection_alias": "body_boundary",
                            },
                        ],
                        "output_target_alias": "joined",
                        "new_object_name": "Joined",
                        "new_mesh_name": "Joined Mesh",
                        "collection_alias": "modules",
                        "coordinate_frame": {"type": "WORLD"},
                        "attributes": {
                            "materials": "PRESERVE_BY_IDENTITY",
                            "uv": "MERGE_BY_NAME",
                            "weights": "MERGE_BY_NAME",
                            "colors": "MERGE_BY_NAME",
                            "generic": "ERROR_IF_PRESENT",
                            "custom_normals": "DROP_RECALCULATE",
                        },
                        "dependencies": {
                            "shape_keys": "ERROR_IF_PRESENT",
                            "modifiers": "ERROR_IF_PRESENT",
                        },
                    },
                    {
                        "type": "mesh_edit",
                        "target_alias": "joined",
                        "data_scope": "OBJECT",
                        "operation": {
                            "type": "weld_vertices",
                            "selection_aliases": ["head_boundary", "body_boundary"],
                            "maximum_distance": 0.001,
                        },
                    },
                ],
                "expected_scene_generation": 4,
                "idempotency_key": "123e4567-e89b-12d3-a456-426614174002",
            },
        )
    )
    assert ("mesh_join", 1) in fake.requirements
    assert ("mesh_component_map", 4) in fake.requirements
    assert ("mesh_topology", 5) in fake.requirements
    assert ("mesh_batch", 5) in fake.requirements
    assert ("transactions", 13) in fake.requirements
    assert fake.calls[-1][1]["steps"][1]["operation"]["selection_aliases"] == (
        "head_boundary",
        "body_boundary",
    )


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
