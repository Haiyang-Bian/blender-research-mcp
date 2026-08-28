import ast
import zipfile
from pathlib import Path

from blender_research_mcp.addon_build import PACKAGE_NAME, SOURCE, build


def test_addon_sources_parse_as_python_311() -> None:
    for path in SOURCE.rglob("*.py"):
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path), feature_version=(3, 11))


def test_addon_zip_has_an_installable_package_root(tmp_path: Path) -> None:
    output = build(tmp_path / "addon.zip")
    with zipfile.ZipFile(output) as archive:
        names = set(archive.namelist())
    assert f"{PACKAGE_NAME}/__init__.py" in names
    assert f"{PACKAGE_NAME}/runtime.py" in names
    assert all(name.startswith(f"{PACKAGE_NAME}/") for name in names)
