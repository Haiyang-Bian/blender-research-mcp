from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "live_smoke_070.py"
SPEC = importlib.util.spec_from_file_location("live_smoke_070", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def test_choose_object_prefers_active_then_selected() -> None:
    assert MODULE.choose_object({"active_object": "Cube", "selected_objects": ["Other"]}) == (
        "Cube"
    )
    assert MODULE.choose_object({"active_object": None, "selected_objects": ["Other"]}) == (
        "Other"
    )
    with pytest.raises(RuntimeError):
        MODULE.choose_object({"active_object": None, "selected_objects": []})


def test_blender_version_accepts_the_official_lts_suffix() -> None:
    assert MODULE.is_blender_4_2_23("4.2.23")
    assert MODULE.is_blender_4_2_23("4.2.23 LTS")
    assert not MODULE.is_blender_4_2_23("4.3.0")
