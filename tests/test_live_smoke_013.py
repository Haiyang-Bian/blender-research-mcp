from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]


def load_smoke_module():
    path = ROOT / "scripts" / "live_smoke_013.py"
    spec = importlib.util.spec_from_file_location("live_smoke_013_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def inspected_mesh() -> dict[str, object]:
    return {
        "object": {"name": "Topology", "session_identity": "object:1"},
        "mesh": {
            "session_identity": "mesh:1",
            "users": 1,
            "uv_layers": [],
            "color_attributes": [],
        },
        "user_objects": [
            {"object_name": "Topology", "session_identity": "object:1"}
        ],
        "mesh_fingerprint": "f" * 64,
        "mesh_revision_id": "r" * 64,
    }


def test_live_smoke_builds_exact_topology_edit_guards() -> None:
    smoke = load_smoke_module()
    inspected = inspected_mesh()
    operation = {"type": "subdivide", "selection_id": "selection-1", "cuts": 1}

    params = smoke.edit_params("tx", inspected, operation)

    assert params["expected_object_identity"] == "object:1"
    assert params["expected_mesh_identity"] == "mesh:1"
    assert params["expected_mesh_users"] == 1
    assert params["expected_mesh_fingerprint"] == "f" * 64
    assert params["operation"] is operation


def test_live_smoke_covers_topology_lineage_and_lifecycle_acceptance() -> None:
    path = ROOT / "scripts" / "live_smoke_013.py"
    source = path.read_text(encoding="utf-8")
    ast.parse(source, filename=str(path), feature_version=(3, 11))
    for expected in (
        '"mesh.component_map.inspect"',
        '"mesh.selection.remap"',
        '"subdivide"',
        '"loop_cut"',
        '"bisect"',
        '"split"',
        '"bridge"',
        '"fill"',
        '"grid_fill"',
        '"extrude_faces"',
        '"merge_vertices"',
        '"MESH_COMPONENT_MAP_STALE"',
        '"MESH_DATA_CONFLICT"',
        '"TRANSACTION_ACCEPTED_BY_USER_SAVE"',
        '"_test.context.touch"',
        '"_test.mesh.touch"',
        '"_test.native_save"',
        "client.close()",
        '"transaction.commit"',
        '"transaction.rollback"',
        "project_reload",
        '"object.duplicate"',
        '"mesh.surface.prepare"',
        '"mesh.surface.query"',
        '"mesh.validate"',
        '"shrinkwrap"',
        '"绯雪_edit_mesh"',
        '"MCP 0.13 Eye Proxy"',
        "p95_improvement_ratio",
        "fixture_source_unchanged",
        "real_source_unchanged",
    ):
        assert expected in source

    fixture = ROOT / "scripts" / "create_topology_fixture.py"
    ast.parse(
        fixture.read_text(encoding="utf-8"),
        filename=str(fixture),
        feature_version=(3, 11),
    )
