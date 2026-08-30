from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]


def load_smoke_module():
    path = ROOT / "scripts" / "live_smoke_011.py"
    spec = importlib.util.spec_from_file_location("live_smoke_011_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def inspected_mesh() -> dict[str, object]:
    return {
        "object": {"name": "Hull", "session_identity": "object:1"},
        "mesh": {
            "name": "Hull Data",
            "session_identity": "mesh:1",
            "users": 2,
            "uv_layers": ["UVMap"],
            "color_attributes": ["Color"],
            "material_slots": [],
            "attributes": [],
        },
        "user_objects": [
            {"object_name": "Hull", "session_identity": "object:1"},
            {"object_name": "Hull Linked", "session_identity": "object:2"},
        ],
        "mesh_fingerprint": "f" * 64,
        "topology_fingerprint": "t" * 64,
    }


def test_live_smoke_builds_exact_mesh_edit_evidence() -> None:
    smoke = load_smoke_module()
    inspected = inspected_mesh()
    operation = {
        "type": "transform",
        "target": {"type": "vertices", "indices": [0]},
        "translation": {"x": 0.0, "y": 0.0, "z": 1.0},
    }

    params = smoke.mesh_edit_params("tx", inspected, operation, "SHARED_DATA")

    assert params["expected_object_identity"] == "object:1"
    assert params["expected_mesh_identity"] == "mesh:1"
    assert params["expected_mesh_users"] == 2
    assert params["expected_mesh_user_objects"][1]["expected_object_identity"] == "object:2"
    assert params["expected_mesh_fingerprint"] == "f" * 64
    assert params["data_scope"] == "SHARED_DATA"
    assert params["operation"] is operation


def test_live_smoke_covers_release_acceptance_domains() -> None:
    source = (ROOT / "scripts" / "live_smoke_011.py").read_text(encoding="utf-8")
    for expected in (
        '"mesh.inspect"',
        '"mesh.edit"',
        '"transform"',
        '"extrude_faces"',
        '"inset_faces"',
        '"bevel_edges"',
        '"delete"',
        '"dissolve"',
        '"merge_vertices"',
        '"face_settings"',
        '"normals"',
        '"OBJECT"',
        '"SHARED_DATA"',
        '"_test.mesh.touch"',
        '"MESH_DATA_CONFLICT"',
        "client.close()",
        '"transaction.commit"',
        "project_reload",
        "request_render_preview",
        "source_unchanged",
    ):
        assert expected in source

    fixture = ROOT / "scripts" / "create_mesh_fixture.py"
    ast.parse(fixture.read_text(encoding="utf-8"), filename=str(fixture), feature_version=(3, 11))
