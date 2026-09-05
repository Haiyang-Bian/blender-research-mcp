from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_transaction_collection_regression_smoke_is_python_311_and_bounded() -> None:
    path = ROOT / "scripts" / "live_smoke_0171.py"
    source = path.read_text(encoding="utf-8")
    ast.parse(source, filename=str(path), feature_version=(3, 11))
    for expected in (
        '"collection.create"',
        '"mesh.materialize"',
        '"mesh.uv.inspect"',
        '"SUMMARY"',
        '"LOOPS"',
        '"transaction.rollback"',
        "client.close()",
        '"rolled_back_disconnect"',
        'source_name="绯雪_edit_mesh"',
        '"fixture_source_sha256_before"',
        '"fixture_source_sha256_after"',
        '"character_source_sha256_before"',
        '"character_source_sha256_after"',
    ):
        assert expected in source
