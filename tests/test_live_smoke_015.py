from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]


def load_smoke_module():
    path = ROOT / "scripts" / "live_smoke_015.py"
    spec = importlib.util.spec_from_file_location("live_smoke_015_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def inspected_mesh() -> dict[str, object]:
    return {
        "object": {"name": "Source", "session_identity": "object:1"},
        "mesh": {"session_identity": "mesh:1", "users": 1},
        "user_objects": [
            {"object_name": "Source", "session_identity": "object:1"}
        ],
        "mesh_fingerprint": "f" * 64,
        "mesh_revision_id": "r" * 64,
    }


def test_live_smoke_builds_exact_materialize_and_extract_evidence() -> None:
    smoke = load_smoke_module()
    inspected = inspected_mesh()

    source = smoke.materialize_source(inspected)
    target = smoke.extract_target(inspected)

    assert source == {
        "object_name": "Source",
        "expected_object_identity": "object:1",
        "expected_mesh_identity": "mesh:1",
        "expected_mesh_revision_id": "r" * 64,
    }
    assert target["expected_mesh_users"] == 1
    assert target["expected_mesh_user_objects"] == [
        {"object_name": "Source", "expected_object_identity": "object:1"}
    ]
    assert target["expected_mesh_fingerprint"] == "f" * 64


def test_live_smoke_covers_modular_character_and_recovery_acceptance() -> None:
    path = ROOT / "scripts" / "live_smoke_015.py"
    source = path.read_text(encoding="utf-8")
    ast.parse(source, filename=str(path), feature_version=(3, 11))
    for expected in (
        '"mesh.materialize"',
        '"mesh.extract.preflight"',
        '"mesh.extract"',
        '"rig.inspect"',
        '"rig.bind"',
        '"BASE"',
        '"SHAPE_KEYS_CURRENT"',
        '"FINAL_EVALUATED"',
        '"MATERIALIZATION"',
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
        '"repeat_bind"',
    ):
        assert expected in source

    fixture = ROOT / "scripts" / "create_modular_fixture.py"
    ast.parse(
        fixture.read_text(encoding="utf-8"),
        filename=str(fixture),
        feature_version=(3, 11),
    )
