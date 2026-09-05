from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_join_fixture_is_blender_311_compatible_and_semantically_bounded() -> None:
    path = ROOT / "scripts" / "create_join_fixture.py"
    source = path.read_text(encoding="utf-8")
    ast.parse(source, filename=str(path), feature_version=(3, 11))
    for expected in (
        '"Join Left"',
        '"Join Right"',
        '"JoinUV"',
        '"ModuleTint"',
        '"Root"',
        '"Join Camera"',
        "bpy.ops.wm.save_as_mainfile",
    ):
        assert expected in source


def test_join_smoke_covers_release_acceptance() -> None:
    path = ROOT / "scripts" / "live_smoke_017.py"
    source = path.read_text(encoding="utf-8")
    ast.parse(source, filename=str(path), feature_version=(3, 11))
    for expected in (
        '"mesh.join.preflight"',
        '"mesh.join"',
        '"weld_vertices"',
        '"mesh.component_map.compose"',
        '"mesh.batch.execute"',
        '"mesh_validate"',
        '"MESH_JOIN_DATA_CONFLICT"',
        '"TRANSACTION_ACCEPTED_BY_USER_SAVE"',
        '"_test.mesh.touch"',
        '"_test.native_save"',
        "client.close()",
        '"transaction.commit"',
        '"transaction.rollback"',
        "project_reload",
        '"assembly_manifest"',
        '"joined-front.png"',
        '"joined-right.png"',
        '"source_sha256_before"',
        '"source_sha256_after"',
    ):
        assert expected in source
