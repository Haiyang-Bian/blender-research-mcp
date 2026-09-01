from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]


def load_smoke_module():
    scripts = str(ROOT / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    path = ROOT / "scripts" / "live_smoke_0151.py"
    spec = importlib.util.spec_from_file_location("live_smoke_0151_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_assembly_smoke_builds_exact_scene_and_batch_evidence() -> None:
    smoke = load_smoke_module()
    inspected = {
        "object": {"name": "Source", "session_identity": "object:1"},
        "mesh": {"session_identity": "mesh:1", "users": 1},
        "user_objects": [{"object_name": "Source", "session_identity": "object:1"}],
        "mesh_fingerprint": "a" * 64,
    }
    target = smoke.batch_target("source", inspected)
    assert target["alias"] == "source"
    assert target["expected_mesh_user_objects"] == [
        {"object_name": "Source", "expected_object_identity": "object:1"}
    ]
    root = smoke.scene_root_parent(
        {
            "scene_root": {
                "scene_name": "Scene",
                "scene_identity": "scene:1",
                "scene_structure_fingerprint": "b" * 64,
            }
        }
    )
    steps = smoke.assembly_steps(
        components=["component:1", "component:2"],
        prefix="Smoke",
        shape_fingerprint="c" * 64,
        root_parent=root,
    )
    assert steps[0]["type"] == "component_catalog_select"
    assert [step["type"] for step in steps][3:11] == [
        "mesh_materialize",
        "mesh_extract",
        "collection_link_object",
        "collection_unlink_object",
        "object_parent_set",
        "object_parent_clear",
        "selection_derive",
        "rig_bind",
    ]


def test_assembly_smoke_covers_release_acceptance() -> None:
    path = ROOT / "scripts" / "live_smoke_0151.py"
    source = path.read_text(encoding="utf-8")
    ast.parse(source, filename=str(path), feature_version=(3, 11))
    for expected in (
        '"mesh.component_catalog.prepare"',
        '"mesh.component_catalog.inspect"',
        '"mesh.component_catalog.select"',
        '"collection.create"',
        '"collection.link_object"',
        '"collection.unlink_object"',
        '"object.parent.set"',
        '"mesh.batch.execute"',
        '"assembly_manifest"',
        '"MESH_BATCH_ASSERTION_FAILED"',
        '"MESH_COMPONENT_CATALOG_STALE"',
        '"_test.native_save"',
        "client.close()",
        '"transaction.commit"',
        '"transaction.rollback"',
        "project_reload",
        '"绯雪_edit_mesh"',
        '"绯雪_edit_arm"',
        '"Hair"',
        '"source_sha256_before"',
        '"source_sha256_after"',
    ):
        assert expected in source
