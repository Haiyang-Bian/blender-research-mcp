import ast
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]


def load_smoke_module():
    path = ROOT / "scripts" / "live_smoke_linked_data_guards.py"
    spec = importlib.util.spec_from_file_location("linked_data_smoke_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_linked_data_smoke_definitions_are_normalized_and_python_311_compatible() -> None:
    smoke = load_smoke_module()

    cube = smoke.cube_definition("Probe")
    material = smoke.material_definition("Probe Material")

    assert cube["type"] == "cube"
    assert cube["transform"]["scale"] == {"x": 1.0, "y": 1.0, "z": 1.0}
    assert material["base_color"]["value"] == "#7096C4"
    path = ROOT / "scripts" / "live_smoke_linked_data_guards.py"
    ast.parse(path.read_text(encoding="utf-8"), filename=str(path), feature_version=(3, 11))


def test_linked_data_smoke_covers_reported_and_discovered_regressions() -> None:
    source = (ROOT / "scripts" / "live_smoke_linked_data_guards.py").read_text(
        encoding="utf-8"
    )
    for expected in (
        '"material.assign"',
        '"object.duplicate"',
        'linked=True',
        'linked=False',
        '"transaction.commit"',
        '"transaction.rollback"',
        '"STRUCTURE_CONFLICT"',
        '"_test.structure.touch"',
        '"linked_duplicate"',
        "project_save",
        "project_reload",
        "selected_objects",
        "source_unchanged",
    ):
        assert expected in source
