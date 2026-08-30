from __future__ import annotations

import pytest
from pydantic import TypeAdapter, ValidationError

from blender_research_mcp.mesh_authoring import MeshOperation, MeshUserObject

OPERATIONS = TypeAdapter(MeshOperation)


def test_mesh_user_object_is_exact_and_closed() -> None:
    user = MeshUserObject(
        object_name="Shared Cube",
        expected_object_identity="object:1234",
    )
    assert user.object_name == "Shared Cube"
    with pytest.raises(ValidationError):
        MeshUserObject.model_validate(
            {
                "object_name": "Shared Cube",
                "expected_object_identity": "object:1234",
                "extra": True,
            }
        )


def test_transform_requires_unique_exact_components_and_a_patch() -> None:
    operation = OPERATIONS.validate_python(
        {
            "type": "transform",
            "target": {"type": "faces", "indices": [2, 3]},
            "translation": {"x": 0.0, "y": 0.0, "z": 1.0},
            "pivot": {"type": "MEDIAN"},
        }
    )
    assert operation.type == "transform"
    with pytest.raises(ValidationError):
        OPERATIONS.validate_python(
            {"type": "transform", "target": {"type": "vertices", "indices": [0, 0]}}
        )
    with pytest.raises(ValidationError):
        OPERATIONS.validate_python(
            {"type": "transform", "target": {"type": "vertices", "indices": [0]}}
        )


@pytest.mark.parametrize(
    "payload",
    [
        {
            "type": "extrude_faces",
            "face_indices": [0],
            "offset": {"x": 0, "y": 0, "z": 1},
        },
        {"type": "inset_faces", "face_indices": [0], "thickness": 0.1},
        {"type": "bevel_edges", "edge_indices": [0], "width": 0.1},
        {"type": "delete", "target": {"type": "faces", "indices": [0]}},
        {"type": "dissolve", "target": {"type": "edges", "indices": [0]}},
        {"type": "merge_vertices", "vertex_indices": [0, 1]},
        {"type": "face_settings", "face_indices": [0], "smooth": True},
        {"type": "normals", "mode": "FLIP", "face_indices": [0]},
        {"type": "normals", "mode": "RECALCULATE_OUTSIDE"},
    ],
)
def test_all_semantic_mesh_operations_are_closed_and_typed(payload: dict[str, object]) -> None:
    operation = OPERATIONS.validate_python(payload)
    assert operation.type == payload["type"]
    with pytest.raises(ValidationError):
        OPERATIONS.validate_python({**payload, "unsupported": 1})


def test_operation_dependencies_reject_ambiguous_or_empty_writes() -> None:
    invalid = [
        {
            "type": "extrude_faces",
            "face_indices": [0],
            "offset": {"x": 0, "y": 0, "z": 0},
        },
        {"type": "inset_faces", "face_indices": [0], "thickness": 0, "depth": 0},
        {"type": "merge_vertices", "vertex_indices": [0, 1], "destination": "TARGET"},
        {"type": "face_settings", "face_indices": [0]},
        {"type": "normals", "mode": "FLIP"},
        {"type": "normals", "mode": "RECALCULATE_OUTSIDE", "face_indices": [0]},
    ]
    for payload in invalid:
        with pytest.raises(ValidationError):
            OPERATIONS.validate_python(payload)


def test_component_indices_are_strict_integers_and_bounded() -> None:
    with pytest.raises(ValidationError):
        OPERATIONS.validate_python(
            {"type": "delete", "target": {"type": "vertices", "indices": [True]}}
        )
    with pytest.raises(ValidationError):
        OPERATIONS.validate_python(
            {"type": "delete", "target": {"type": "vertices", "indices": [-1]}}
        )
    with pytest.raises(ValidationError):
        OPERATIONS.validate_python(
            {
                "type": "delete",
                "target": {"type": "vertices", "indices": list(range(4097))},
            }
        )
