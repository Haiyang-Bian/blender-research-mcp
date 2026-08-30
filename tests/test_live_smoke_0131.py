from __future__ import annotations

import ast
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]


def load_smoke_module():
    path = ROOT / "scripts" / "live_smoke_0131.py"
    spec = importlib.util.spec_from_file_location("live_smoke_0131_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def inspected_mesh() -> dict[str, object]:
    return {
        "object": {"name": "Topology", "session_identity": "object:1"},
        "mesh": {"session_identity": "mesh:1", "users": 1},
        "user_objects": [
            {"object_name": "Topology", "session_identity": "object:1"}
        ],
        "mesh_fingerprint": "f" * 64,
    }


def test_live_smoke_builds_exact_separation_and_batch_targets() -> None:
    smoke = load_smoke_module()
    inspected = inspected_mesh()

    target = smoke.batch_target("source", inspected)
    separate = smoke.exact_params("tx", inspected, "selection-1", "Patch")

    assert target["alias"] == "source"
    assert target["expected_object_identity"] == "object:1"
    assert target["expected_mesh_identity"] == "mesh:1"
    assert target["expected_mesh_users"] == 1
    assert target["expected_mesh_fingerprint"] == "f" * 64
    assert separate["selection_id"] == "selection-1"
    assert separate["new_object_name"] == "Patch"
    assert "data_scope" not in separate


def test_live_smoke_covers_separation_batch_and_recovery_acceptance() -> None:
    path = ROOT / "scripts" / "live_smoke_0131.py"
    source = path.read_text(encoding="utf-8")
    ast.parse(source, filename=str(path), feature_version=(3, 11))
    for expected in (
        '"mesh.separate"',
        '"mesh.batch.execute"',
        '"mesh.component_map.compose"',
        '"selection_query"',
        '"mesh_edit"',
        '"mesh_separate"',
        '"mesh_validate"',
        '"subdivide"',
        '"MESH_BATCH_ASSERTION_FAILED"',
        '"_test.native_save"',
        "client.close()",
        '"transaction.commit"',
        '"transaction.rollback"',
        "project_reload",
        "cross_transaction_composition",
        "batch_disconnect_rollback",
        "batch_assertion_rollback",
        "native_save_persistence",
        "fixture_source_unchanged",
    ):
        assert expected in source
