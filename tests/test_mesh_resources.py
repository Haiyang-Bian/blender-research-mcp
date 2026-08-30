from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest
from pydantic import TypeAdapter, ValidationError

from blender_research_mcp.constants import MAX_REQUEST_BYTES
from blender_research_mcp.mesh_authoring import MeshOperation
from blender_research_mcp.mesh_resources import SelectionDerivation, SelectionQuery

OPERATIONS = TypeAdapter(MeshOperation)
QUERIES = TypeAdapter(SelectionQuery)
DERIVATIONS = TypeAdapter(SelectionDerivation)


def load_resource_model():
    path = (
        Path(__file__).parents[1]
        / "blender_addon"
        / "blender_research_mcp_addon"
        / "mesh_resource_model.py"
    )
    spec = importlib.util.spec_from_file_location("mesh_resource_model_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "payload",
    [
        {"type": "indices", "indices": [0, 2, 4]},
        {"type": "all"},
        {
            "type": "sphere",
            "center": {"x": 0, "y": 0, "z": 0},
            "radius": 1,
            "space": "WORLD",
        },
        {
            "type": "box",
            "minimum": {"x": -1, "y": -1, "z": -1},
            "maximum": {"x": 1, "y": 1, "z": 1},
        },
        {
            "type": "plane",
            "origin": {"x": 0, "y": 0, "z": 0},
            "normal": {"x": 0, "y": 0, "z": 1},
        },
        {"type": "material", "slot_indices": [0, 2]},
        {
            "type": "normal",
            "direction": {"x": 0, "y": 0, "z": 1},
            "minimum_dot": 0.5,
        },
        {"type": "measure", "field": "FACE_AREA", "minimum": 0.1},
        {"type": "topology", "kind": "CONNECTED", "seed_indices": [0]},
        {
            "type": "screen",
            "capture_id": "capture-1",
            "shape": "BOX",
            "points": [{"x": 0.1, "y": 0.2}, {"x": 0.8, "y": 0.9}],
            "visibility": "VISIBLE_ONLY",
        },
    ],
)
def test_selection_queries_are_closed_and_typed(payload: dict[str, object]) -> None:
    assert QUERIES.validate_python(payload).type == payload["type"]
    with pytest.raises(ValidationError):
        QUERIES.validate_python({**payload, "unsupported": True})


@pytest.mark.parametrize(
    "payload",
    [
        {"type": "combine", "mode": "UNION", "selection_ids": ["a", "b"]},
        {"type": "expand", "selection_id": "a", "steps": 2},
        {"type": "contract", "selection_id": "a"},
        {"type": "boundary", "selection_id": "a"},
        {"type": "connected", "selection_id": "a"},
        {"type": "convert", "selection_id": "a", "domain": "FACE"},
        {
            "type": "falloff",
            "selection_id": "a",
            "radius": 2,
            "profile": "SMOOTH",
        },
    ],
)
def test_selection_derivations_are_closed_and_typed(payload: dict[str, object]) -> None:
    assert DERIVATIONS.validate_python(payload).type == payload["type"]
    with pytest.raises(ValidationError):
        DERIVATIONS.validate_python({**payload, "unsupported": True})


@pytest.mark.parametrize(
    "payload",
    [
        {
            "type": "set_positions",
            "selection_id": "selection",
            "mode": "OFFSET",
            "space": "WORLD",
            "positions": [{"x": 0, "y": 0, "z": 0.1}],
        },
        {"type": "smooth", "selection_id": "selection", "iterations": 2},
        {"type": "relax", "selection_id": "selection", "preserve_boundary": False},
        {
            "type": "project",
            "selection_id": "selection",
            "surface_id": "surface",
            "direction": "AXIS",
            "axis": "-Z",
            "maximum_distance": 10,
        },
        {
            "type": "shrinkwrap",
            "selection_id": "selection",
            "surface_id": "surface",
            "maximum_distance": 10,
        },
        {"type": "inflate", "selection_id": "selection", "amount": 0.1},
        {"type": "flatten", "selection_id": "selection"},
    ],
)
def test_semantic_deformations_are_closed_and_typed(payload: dict[str, object]) -> None:
    assert OPERATIONS.validate_python(payload).type == payload["type"]
    with pytest.raises(ValidationError):
        OPERATIONS.validate_python({**payload, "unsupported": True})


def test_deformation_dependencies_and_strict_ranges() -> None:
    invalid = [
        {"type": "set_positions", "selection_id": "a", "positions": []},
        {"type": "smooth", "selection_id": "a", "iterations": 0},
        {"type": "relax", "selection_id": "a", "factor": True},
        {
            "type": "project",
            "selection_id": "a",
            "surface_id": "b",
            "direction": "VECTOR",
            "maximum_distance": 1,
        },
        {
            "type": "project",
            "selection_id": "a",
            "surface_id": "b",
            "direction": "CLOSEST_POINT",
            "axis": "Z",
            "maximum_distance": 1,
        },
        {
            "type": "shrinkwrap",
            "selection_id": "a",
            "surface_id": "b",
            "factor": 0,
            "maximum_distance": 1,
        },
        {
            "type": "flatten",
            "selection_id": "a",
            "plane": {
                "type": "EXPLICIT",
                "origin": {"x": 0, "y": 0, "z": 0},
                "normal": {"x": 0, "y": 0, "z": 0},
            },
        },
    ]
    for payload in invalid:
        with pytest.raises(ValidationError):
            OPERATIONS.validate_python(payload)


def test_maximum_set_positions_request_remains_below_protocol_limit() -> None:
    operation = OPERATIONS.validate_python(
        {
            "type": "set_positions",
            "selection_id": "selection",
            "positions": [
                {"x": float(index), "y": float(index + 1), "z": float(index + 2)}
                for index in range(4096)
            ],
        }
    )
    payload = json.dumps(operation.model_dump(mode="json"), separators=(",", ":")).encode()
    assert len(payload) < MAX_REQUEST_BYTES


def test_selection_resource_book_is_lru_bounded_and_distinguishes_expiry() -> None:
    module = load_resource_model()
    module.MAX_SELECTIONS = 2
    book = module.MeshResourceBook()

    def add(index: int):
        return book.add_selection(
            object_name="Cube",
            object_identity="object:1",
            mesh_name="Mesh",
            mesh_identity="mesh:1",
            mesh_revision_id="a" * 64,
            mesh_fingerprint="b" * 64,
            expected_users=1,
            expected_user_objects=(("Cube", "object:1"),),
            domain="VERTEX",
            indices=(index,),
            weights=(1.0,),
            source_query={"type": "indices"},
        )

    first = add(0)
    second = add(1)
    assert book.selection(first.selection_id) is first
    third = add(2)
    assert book.selection(first.selection_id) is first
    assert book.selection(third.selection_id) is third
    with pytest.raises(module.MeshResourceError) as expired:
        book.selection(second.selection_id)
    assert expired.value.code == "MESH_RESOURCE_EXPIRED"
    with pytest.raises(module.MeshResourceError) as missing:
        book.selection("never-created")
    assert missing.value.code == "MESH_RESOURCE_NOT_FOUND"


def test_selection_resource_hash_covers_revision_domain_indices_and_weights() -> None:
    module = load_resource_model()
    baseline = module.selection_content_hash("a" * 64, "VERTEX", (1, 2), (1.0, 0.5))
    assert baseline != module.selection_content_hash("b" * 64, "VERTEX", (1, 2), (1.0, 0.5))
    assert baseline != module.selection_content_hash("a" * 64, "EDGE", (1, 2), (1.0, 0.5))
    assert baseline != module.selection_content_hash("a" * 64, "VERTEX", (1, 3), (1.0, 0.5))
    assert baseline != module.selection_content_hash("a" * 64, "VERTEX", (1, 2), None)
