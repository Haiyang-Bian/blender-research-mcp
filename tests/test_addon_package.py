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
    assert f"{PACKAGE_NAME}/runtime.py" in names
    assert all(name.startswith(f"{PACKAGE_NAME}/") for name in names)
