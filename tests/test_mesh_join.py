from __future__ import annotations

import pytest
from pydantic import TypeAdapter, ValidationError

from blender_research_mcp.mesh_authoring import MeshOperation
from blender_research_mcp.mesh_join import MeshJoinRequest


def _source(name: str, identity: str) -> dict[str, object]:
    return {
        "object_name": name,
        "expected_object_identity": identity,
        "expected_object_structure_fingerprint": "a" * 64,
        "mesh_name": f"{name} Mesh",
        "expected_mesh_identity": f"mesh:{identity}",
        "expected_mesh_users": 1,
        "expected_mesh_user_objects": [
            {"object_name": name, "expected_object_identity": identity}
        ],
        "expected_mesh_fingerprint": "b" * 64,
        "expected_mesh_revision_id": "c" * 64,
        "expected_uv_fingerprint": "d" * 64,
        "expected_group_schema_fingerprint": "e" * 64,
        "expected_weights_fingerprint": "f" * 64,
        "expected_shape_key_state_fingerprint": "1" * 64,
        "expected_modifier_stack_fingerprint": "2" * 64,
        "selection_ids": [],
    }


def _request() -> dict[str, object]:
    return {
        "sources": [_source("Head", "object:head"), _source("Body", "object:body")],
        "output": {
            "new_object_name": "Joined",
            "new_mesh_name": "Joined Mesh",
            "collection_name": "Modules",
            "expected_collection_identity": "collection:1",
            "expected_collection_structure_fingerprint": "3" * 64,
            "coordinate_frame": {"type": "WORLD"},
            "source_disposition": "KEEP",
        },
        "attributes": {
            "materials": "PRESERVE_BY_IDENTITY",
            "uv": "MERGE_BY_NAME",
            "weights": "MERGE_BY_NAME",
            "colors": "MERGE_BY_NAME",
            "generic": "ERROR_IF_PRESENT",
            "custom_normals": "DROP_RECALCULATE",
        },
        "dependencies": {
            "shape_keys": "ERROR_IF_PRESENT",
            "modifiers": "ERROR_IF_PRESENT",
        },
    }


def test_mesh_join_request_is_exact_closed_and_ordered() -> None:
    request = MeshJoinRequest.model_validate(_request())
    assert [source.object_name for source in request.sources] == ["Head", "Body"]
    assert request.output.coordinate_frame.type == "WORLD"

    invalid = _request()
    invalid["extra"] = True
    with pytest.raises(ValidationError):
        MeshJoinRequest.model_validate(invalid)


def test_source_object_coordinate_frame_must_reference_exact_source() -> None:
    payload = _request()
    payload["output"]["coordinate_frame"] = {
        "type": "SOURCE_OBJECT",
        "source_object_name": "Other",
        "expected_source_object_identity": "object:other",
    }
    with pytest.raises(ValidationError):
        MeshJoinRequest.model_validate(payload)


def test_weld_vertices_requires_exact_selection_groups_and_positive_distance() -> None:
    adapter = TypeAdapter(MeshOperation)
    operation = adapter.validate_python(
        {
            "type": "weld_vertices",
            "selection_ids": ["head-boundary", "body-boundary"],
            "maximum_distance": 0.001,
        }
    )
    assert operation.mode == "CROSS_SELECTIONS"

    for invalid in (
        {
            "type": "weld_vertices",
            "selection_ids": ["one"],
            "maximum_distance": 0.001,
        },
        {
            "type": "weld_vertices",
            "selection_ids": ["one", "two"],
            "maximum_distance": 0,
        },
        {
            "type": "weld_vertices",
            "selection_ids": ["one", "one"],
            "maximum_distance": 0.001,
        },
    ):
        with pytest.raises(ValidationError):
            adapter.validate_python(invalid)
