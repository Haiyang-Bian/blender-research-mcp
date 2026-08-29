import ast
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]


def load_smoke_module():
    path = ROOT / "scripts" / "live_smoke_010.py"
    spec = importlib.util.spec_from_file_location("live_smoke_010_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_live_smoke_builds_exact_modifier_target_and_comparison() -> None:
    smoke = load_smoke_module()
    inspected = {
        "object_name": "Mesh",
        "object_identity": "object:1",
        "scene_generation": 7,
        "stack_fingerprint": "f" * 64,
        "modifiers": [
            {
                "name": "Soft Edges",
                "session_identity": "modifier:1",
                "type": "BEVEL",
                "stack_index": 0,
            }
        ],
    }
    item = smoke.modifier_item(inspected, "Soft Edges")

    params = smoke.target_params("tx", inspected, item)
    request = smoke.comparison_request(inspected, item, "width", (0.2, 0.4))

    assert params["expected_modifier_identity"] == "modifier:1"
    assert params["expected_stack_fingerprint"] == "f" * 64
    assert request.target.type == "modifier_setting"
    assert [candidate.value for candidate in request.candidates] == [0.2, 0.4]


def test_live_smoke_covers_modifier_acceptance_domains() -> None:
    source = (ROOT / "scripts" / "live_smoke_010.py").read_text(encoding="utf-8")
    for expected in (
        '"BEVEL"',
        '"SUBSURF"',
        '"SOLIDIFY"',
        '"BOOLEAN"',
        '"modifier.create"',
        '"modifier.set"',
        '"modifier.move"',
        '"modifier.delete"',
        '"modifier_setting"',
        '"BOOLEAN_CYCLE"',
        '"_test.modifier.touch"',
        '"MODIFIER_STACK_CONFLICT"',
        "disconnect_and_verify",
        '"transaction.commit"',
        "project_reload",
        "request_render_preview",
        "source_unchanged",
    ):
        assert expected in source

    fixture = ROOT / "scripts" / "create_modifier_fixture.py"
    ast.parse(fixture.read_text(encoding="utf-8"), filename=str(fixture), feature_version=(3, 11))
