from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]


def load_smoke_module():
    path = ROOT / "scripts" / "live_smoke_012.py"
    spec = importlib.util.spec_from_file_location("live_smoke_012_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def inspected_mesh() -> dict[str, object]:
    return {
        "object": {"name": "Fit Source", "session_identity": "object:1"},
        "mesh": {"session_identity": "mesh:1", "users": 1},
        "user_objects": [
            {"object_name": "Fit Source", "session_identity": "object:1"}
        ],
        "mesh_fingerprint": "f" * 64,
        "mesh_revision_id": "r" * 64,
    }


def test_live_smoke_builds_revision_bound_selection_and_mesh_edit_evidence() -> None:
    smoke = load_smoke_module()
    inspected = inspected_mesh()
    operation = {
        "type": "inflate",
        "selection_id": "selection-1",
        "amount": 0.01,
    }

    params = smoke.mesh_edit_params("tx", inspected, operation)

    assert params["expected_object_identity"] == "object:1"
    assert params["expected_mesh_identity"] == "mesh:1"
    assert params["expected_mesh_users"] == 1
    assert params["expected_mesh_fingerprint"] == "f" * 64
    assert params["operation"] is operation


def test_live_smoke_covers_selection_surface_and_deformation_acceptance() -> None:
    path = ROOT / "scripts" / "live_smoke_012.py"
    source = path.read_text(encoding="utf-8")
    ast.parse(source, filename=str(path), feature_version=(3, 11))
    for expected in (
        '"mesh.selection.query"',
        '"mesh.selection.derive"',
        '"mesh.selection.inspect"',
        '"mesh.selection.release"',
        '"mesh.surface.prepare"',
        '"mesh.surface.query"',
        '"mesh.validate"',
        '"set_positions"',
        '"smooth"',
        '"relax"',
        '"project"',
        '"shrinkwrap"',
        '"inflate"',
        '"flatten"',
        '"VISIBLE_ONLY"',
        '"THROUGH"',
        '"OBJECT"',
        '"SHARED_DATA"',
        '"_test.context.touch"',
        '"_test.native_save"',
        "client.close()",
        '"transaction.commit"',
        '"transaction.rollback"',
        "project_reload",
        "request_render_preview",
        '"绯雪_edit_mesh"',
        "p95_improvement_ratio",
        "penetration_fallback",
        "fixture_source_unchanged",
        "real_source_unchanged",
    ):
        assert expected in source

    fixture = ROOT / "scripts" / "create_surface_fixture.py"
    ast.parse(
        fixture.read_text(encoding="utf-8"),
        filename=str(fixture),
        feature_version=(3, 11),
    )
