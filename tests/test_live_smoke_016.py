from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_library_fixture_is_blender_311_compatible_and_bounded() -> None:
    path = ROOT / "scripts" / "create_library_fixture.py"
    source = path.read_text(encoding="utf-8")
    ast.parse(source, filename=str(path), feature_version=(3, 11))
    for expected in (
        '"Template Assembly"',
        '"Template Nested"',
        '"Template Rig"',
        '"Template Head"',
        '"Template Body"',
        '"Loose Template Mesh"',
        '"Unsupported Constrained"',
        'bpy.ops.wm.save_as_mainfile',
    ):
        assert expected in source


def test_library_smoke_covers_release_acceptance() -> None:
    path = ROOT / "scripts" / "live_smoke_016.py"
    source = path.read_text(encoding="utf-8")
    ast.parse(source, filename=str(path), feature_version=(3, 11))
    for expected in (
        '"library.inspect"',
        '"library.append"',
        '"mesh.batch.execute"',
        '"library_append"',
        '"object_set"',
        '"mesh_surface_prepare"',
        '"mesh_validate"',
        '"rig_bind"',
        '"LIBRARY_DEPENDENCY_UNSUPPORTED"',
        '"transaction.commit"',
        '"transaction.rollback"',
        '"_test.native_save"',
        "client.close()",
        "project_reload",
        '"assembly_manifest"',
        '"绯雪_edit_mesh"',
        '"绯雪_edit_arm"',
        '"MCP 0.16 Coverage Head"',
        '"p95_improvement_ratio"',
        '"hidden_maximum_displacement"',
        '"target_intersection"',
        '"mesh.attribute.transfer"',
        '"fixture_source_sha256_before"',
        '"fixture_source_sha256_after"',
        '"library_source_sha256_before"',
        '"library_source_sha256_after"',
    ):
        assert expected in source
