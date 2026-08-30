import ast
import zipfile
from pathlib import Path

from blender_research_mcp.addon_build import PACKAGE_NAME, SOURCE, build


def test_addon_sources_parse_as_python_311() -> None:
    for path in SOURCE.rglob("*.py"):
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path), feature_version=(3, 11))


def test_addon_registers_compact_view3d_and_full_scene_properties_panels() -> None:
    source_path = SOURCE / "__init__.py"
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    assignments: dict[str, dict[str, object]] = {}
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        assignments[node.name] = {
            statement.targets[0].id: ast.literal_eval(statement.value)
            for statement in node.body
            if isinstance(statement, ast.Assign)
            and len(statement.targets) == 1
            and isinstance(statement.targets[0], ast.Name)
            and isinstance(statement.value, ast.Constant)
        }

    assert assignments["BRMCP_PT_status"]["bl_space_type"] == "VIEW_3D"
    assert assignments["BRMCP_PT_status"]["bl_region_type"] == "UI"
    assert assignments["BRMCP_PT_scene_status"]["bl_space_type"] == "PROPERTIES"
    assert assignments["BRMCP_PT_scene_status"]["bl_region_type"] == "WINDOW"
    assert assignments["BRMCP_PT_scene_status"]["bl_context"] == "scene"
    source = source_path.read_text(encoding="utf-8")
    assert "area_split" not in source
    assert "session_token" not in source
    assert "Semantic scene authoring" in source
    compact_status = source.split("def _draw_compact_status", 1)[1].split(
        "def _draw_full_status", 1
    )[0]
    full_status = source.split("def _draw_full_status", 1)[1].split("class BRMCP_PT_status", 1)[0]
    assert "Label:" not in compact_status
    assert 'transaction_box.label(text=f"Label:' in full_status
    assert 'transaction_box.label(text=f"Deltas:' in source
    assert "Material input default value" in source


def test_addon_registers_bounded_object_local_write_commands() -> None:
    source = (SOURCE / "state.py").read_text(encoding="utf-8")

    for command in (
        "object.lookdev.inspect",
        "material.inspect",
        "object.visibility.set",
        "modifier.set_state",
        "modifier.inspect",
        "modifier.create",
        "modifier.set",
        "modifier.move",
        "modifier.delete",
        "shape_key.set_value",
        "material.set_input",
    ):
        assert command in source


def test_addon_registers_project_lifecycle_without_expanding_compact_panel() -> None:
    state = (SOURCE / "state.py").read_text(encoding="utf-8")
    source = (SOURCE / "__init__.py").read_text(encoding="utf-8")

    for command in (
        "project.status",
        "project.save",
        "project.open",
        "project.reload",
        "application.quit",
    ):
        assert command in state
    assert '"project_lifecycle": 1' in state
    assert '"application_lifecycle": 1' in state
    compact_status = source.split("def _draw_compact_status", 1)[1].split(
        "def _draw_full_status", 1
    )[0]
    full_status = source.split("def _draw_full_status", 1)[1].split(
        "class BRMCP_PT_status",
        1,
    )[0]
    assert "Project lifecycle" not in compact_status
    assert "Project lifecycle" in full_status
    assert "last_operation" in full_status
    for handler in ("save_pre", "save_post", "save_post_fail"):
        assert f"bpy.app.handlers.{handler}.append" in source
        assert f"bpy.app.handlers.{handler}.remove" in source


def test_addon_registers_structural_authoring_without_expanding_compact_panel() -> None:
    state = (SOURCE / "state.py").read_text(encoding="utf-8")
    source = (SOURCE / "__init__.py").read_text(encoding="utf-8")

    for command in (
        "scene.inspect",
        "object.create",
        "object.duplicate",
        "object.delete",
        "object.set",
        "mesh.inspect",
        "mesh.selection.query",
        "mesh.selection.derive",
        "mesh.selection.inspect",
        "mesh.selection.release",
        "mesh.surface.prepare",
        "mesh.surface.query",
        "mesh.validate",
        "mesh.edit",
        "material.create",
        "material.assign",
        "image.load",
        "material.texture.bind",
        "material.texture.clear",
        "world.set",
        "scene.camera.set",
        "render.preview",
        "render.save",
    ):
        assert command in state
    for capability in (
        '"transactions": 6',
        '"scene_inspection": 1',
        '"object_authoring": 1',
        '"object_settings": 1',
        '"modifier_authoring": 1',
        '"mesh_topology": 1',
        '"mesh_selection": 1',
        '"mesh_surface_query": 1',
        '"mesh_deformation": 1',
        '"mesh_validation": 1',
        '"material_authoring": 1',
        '"image_assets": 1',
        '"world_authoring": 1',
        '"render_preview": 1',
        '"render_export": 1',
    ):
        assert capability in state
    compact_status = source.split("def _draw_compact_status", 1)[1].split(
        "def _draw_full_status", 1
    )[0]
    full_status = source.split("def _draw_full_status", 1)[1].split(
        "class BRMCP_PT_status",
        1,
    )[0]
    assert "Semantic scene authoring" not in compact_status
    assert "Semantic scene authoring" in full_status
    capabilities_source = state.split("CAPABILITIES =", 1)[1].split(
        "CAPABILITY_VERSIONS =",
        1,
    )[0]
    assert "_test.structure.touch" not in capabilities_source
    assert "_test.property.touch" not in capabilities_source
    assert "_test.modifier.touch" not in capabilities_source
    assert "_test.mesh.touch" not in capabilities_source
    assert "_test.context.touch" not in capabilities_source
    assert "_test.native_save" not in capabilities_source
    assert 'os.environ.get("BLENDER_RESEARCH_MCP_TEST_HOOKS") != "1"' in state
    assert "self.active_command in MUTATION_COMMANDS" in state
    assert "view_layer.update()" in state
    authoring = (SOURCE / "authoring_ops.py").read_text(encoding="utf-8")
    assert "duplicate.select_set(False)" in authoring
    assert "transaction.refresh_object_data_users" in authoring
    world_render = (SOURCE / "world_render_ops.py").read_text(encoding="utf-8")
    assert 'session_identity("node", link.from_node) != background_identity' in world_render
    assert "bpy.data.images.load(str(output_path), check_existing=False)" in world_render
    assert "os.replace(temporary_path, output_path)" in world_render


def test_addon_supports_session_only_managed_enable_without_saved_preferences() -> None:
    source = (SOURCE / "__init__.py").read_text(encoding="utf-8")

    preference_port = source.split("def _preference_port", 1)[1].split(
        "class BRMCP_AddonPreferences",
        1,
    )[0]
    register = source.split("def register()", 1)[1].split("def unregister()", 1)[0]
    assert "preferences.addons.get(__package__)" in preference_port
    assert "return DEFAULT_PORT" in preference_port
    assert "_preference_port(bpy.context)" in register


def test_mesh_authoring_uses_bounded_data_api_snapshots_without_operators() -> None:
    source = (SOURCE / "mesh_ops.py").read_text(encoding="utf-8")

    for limit in ("MAX_VERTICES", "MAX_EDGES", "MAX_FACES", "MAX_LOOPS"):
        assert limit in source
    for operation in (
        "transform",
        "extrude_faces",
        "inset_faces",
        "bevel_edges",
        "delete",
        "dissolve",
        "merge_vertices",
        "face_settings",
        "normals",
    ):
        assert f'"{operation}"' in source
    assert "def _identity_transform(" in source
    restore = source.split("def _restore_mesh_geometry", 1)[1].split(
        "def restore_mesh_snapshots",
        1,
    )[0]
    assert "_copy_mesh_snapshot(mesh, snapshot)" in restore
    assert "bmesh.new()" not in restore
    assert "def _restore_attributes(" in source
    assert 'foreach_set("vertex_index"' in source
    assert "mesh.copy()" in source
    assert "mesh.clear_geometry()" in source
    assert "bmesh.ops" in source
    assert "bpy.ops" not in source

    resources = (SOURCE / "mesh_resource_model.py").read_text(encoding="utf-8")
    assert "MAX_SELECTIONS = 64" in resources
    assert "MAX_SELECTION_COMPONENTS = 2_000_000" in resources
    assert "MAX_SURFACES = 8" in resources
    assert "MAX_SURFACE_TRIANGLES = 2_000_000" in resources
    query = (SOURCE / "mesh_query_ops.py").read_text(encoding="utf-8")
    assert "raycast_capture" in query
    assert "MESH_SELECTION_CAPTURE_TARGET_MISMATCH" in query
    assert 'hit.get("hit_target") is True' in query
    assert "_capture_ray_direction" in query
    assert "normal.dot(-view_direction)" in query

    deform = (SOURCE / "mesh_deform_ops.py").read_text(encoding="utf-8")
    assert "_write_vertex_positions" in deform
    assert "bm.to_mesh(mesh)" not in deform
    assert "select_set" not in query
    surface = (SOURCE / "mesh_surface_ops.py").read_text(encoding="utf-8")
    assert "evaluated_get" in surface
    assert "BVHTree.FromPolygons" in surface
    deformation = (SOURCE / "mesh_deform_ops.py").read_text(encoding="utf-8")
    for operation in (
        "set_positions",
        "smooth",
        "relax",
        "project",
        "shrinkwrap",
        "inflate",
        "flatten",
    ):
        assert f'"{operation}"' in deformation
    assert "bpy.ops" not in deformation


def test_addon_zip_has_an_installable_package_root(tmp_path: Path) -> None:
    output = build(tmp_path / "addon.zip")
    with zipfile.ZipFile(output) as archive:
        names = set(archive.namelist())
    assert f"{PACKAGE_NAME}/__init__.py" in names
    assert f"{PACKAGE_NAME}/capture_codec.py" in names
    assert f"{PACKAGE_NAME}/capture_model.py" in names
    assert f"{PACKAGE_NAME}/generation.py" in names
    assert f"{PACKAGE_NAME}/geometry_model.py" in names
    assert f"{PACKAGE_NAME}/lookdev_ops.py" in names
    assert f"{PACKAGE_NAME}/lookdev_model.py" in names
    assert f"{PACKAGE_NAME}/mesh_ops.py" in names
    assert f"{PACKAGE_NAME}/project_ops.py" in names
    assert f"{PACKAGE_NAME}/runtime.py" in names
    assert all(name.startswith(f"{PACKAGE_NAME}/") for name in names)
