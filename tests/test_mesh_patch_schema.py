"""Public exact-reference schemas reject implicit or conflicting authoring intent."""

import pytest
from pydantic import TypeAdapter, ValidationError

from blender_research_mcp.mesh_authoring import MeshOperation

ADAPTER = TypeAdapter(MeshOperation)


def test_explicit_grid_has_one_boundary_source():
    boundary = {"type": "CLOSED_LOOP", "selection_id": "loop", "corners": ["a", "b", "c", "d"]}
    result = ADAPTER.validate_python({"type": "grid_fill", "boundary": boundary})
    assert result.model_dump(mode="json")["boundary"] == boundary
    for payload in (
        {"type": "grid_fill"},
        {"type": "grid_fill", "boundary": boundary, "selection_id": "extra"},
        {"type": "grid_fill", "selection_id": "auto", "allow_hidden": True},
        {"type": "grid_fill", "boundary": {**boundary, "corners": ["a", "b", "c"]}},
    ):
        with pytest.raises(ValidationError):
            ADAPTER.validate_python(payload)


def test_open_bridge_correspondence_and_bounds():
    paths = [
        {"selection_id": "a", "start_vertex": "a0"},
        {"selection_id": "b", "start_vertex": "b0"},
    ]
    assert ADAPTER.validate_python({"type": "bridge", "paths": paths, "cuts": 32}).cuts == 32
    for updates in ({"cuts": 33}, {"cuts": True}, {"twist_offset": 1}, {"selection_id": "loop"}):
        with pytest.raises(ValidationError):
            ADAPTER.validate_python({"type": "bridge", "paths": paths, **updates})


def test_exact_creation_is_bounded_and_closed():
    assert ADAPTER.validate_python({"type": "create_face", "vertices": ["a", "b", "c"]})
    assert ADAPTER.validate_python({"type": "create_edge", "vertices": ["a", "b"]})
    for payload in (
        {"type": "create_face", "vertices": ["a", "b"]},
        {"type": "create_face", "vertices": ["a", "b", "c", "d", "e"]},
        {"type": "create_edge", "vertices": [0, 1]},
        {"type": "create_edge", "vertices": ["a", "b"], "merge": True},
    ):
        with pytest.raises(ValidationError):
            ADAPTER.validate_python(payload)
