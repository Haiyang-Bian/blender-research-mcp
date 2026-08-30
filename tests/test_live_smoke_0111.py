from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]


def load_smoke_module():
    path = ROOT / "scripts" / "live_smoke_0111.py"
    spec = importlib.util.spec_from_file_location("live_smoke_0111_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_ui_projection_contains_only_user_collaborative_state() -> None:
    smoke = load_smoke_module()
    context = {
        "scene": "Scene",
        "mode": "OBJECT",
        "workspace": "Layout",
        "viewport_id": "view:1",
        "active_object": "Cube",
        "selected_objects": ["Cube"],
        "view": {"distance": 10.0},
    }

    assert smoke.ui_projection(context) == {
        "workspace": "Layout",
        "viewport_id": "view:1",
        "active_object": "Cube",
        "selected_objects": ["Cube"],
        "view": {"distance": 10.0},
    }


def test_persistent_mesh_summary_excludes_session_only_identities() -> None:
    smoke = load_smoke_module()
    inspected = {
        "counts": {"vertices": 4, "edges": 4, "faces": 1, "loops": 4},
        "topology_fingerprint": "t" * 64,
        "mesh": {
            "session_identity": "mesh:ephemeral",
            "uv_layers": ["UVMap"],
            "color_attributes": ["Color"],
            "material_slots": [
                {
                    "slot_index": 0,
                    "material_name": "Water",
                    "material_identity": "material:ephemeral",
                }
            ],
            "attributes": [],
        },
    }

    assert smoke.persistent_mesh_summary(inspected) == {
        "counts": inspected["counts"],
        "topology_fingerprint": "t" * 64,
        "uv_layers": ["UVMap"],
        "color_attributes": ["Color"],
        "material_slots": [{"slot_index": 0, "material_name": "Water"}],
        "attributes": [],
    }

def test_collaborative_smoke_covers_release_acceptance() -> None:
    path = ROOT / "scripts" / "live_smoke_0111.py"
    source = path.read_text(encoding="utf-8")
    ast.parse(source, filename=str(path), feature_version=(3, 11))
    for expected in (
        '"_test.context.touch"',
        '"_test.native_save"',
        '"TRANSACTION_ACCEPTED_BY_USER_SAVE"',
        '"COMPARISON_ACCEPTED_BY_USER_SAVE"',
        '"object.delete"',
        '"modifier.delete"',
        '"mesh.edit"',
        "project_reload",
        "user_ui_preserved",
        "preserved_ui_changes",
        "user_intent_revision",
        "source_unchanged",
        "heartbeat",
    ):
        assert expected in source
