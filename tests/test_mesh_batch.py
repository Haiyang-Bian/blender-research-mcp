from __future__ import annotations

import pytest
from pydantic import TypeAdapter, ValidationError

from blender_research_mcp.mesh_batch import BatchInputs, BatchSteps, BatchTargets


def _target() -> dict[str, object]:
    return {
        "alias": "source",
        "object_name": "Cube",
        "expected_object_identity": "object:1",
        "expected_mesh_identity": "mesh:1",
        "expected_mesh_users": 1,
        "expected_mesh_user_objects": [
            {"object_name": "Cube", "expected_object_identity": "object:1"}
        ],
        "expected_mesh_fingerprint": "a" * 64,
    }


def test_batch_target_requires_exact_unique_mesh_users() -> None:
    target = TypeAdapter(BatchTargets).validate_python([_target()])
    assert target[0].alias == "source"

    invalid = _target()
    invalid["expected_mesh_users"] = 2
    with pytest.raises(ValidationError):
        TypeAdapter(BatchTargets).validate_python([invalid])


def test_batch_inputs_are_closed_and_revision_resource_typed() -> None:
    inputs = TypeAdapter(BatchInputs).validate_python(
        [
            {
                "type": "selection",
                "alias": "faces",
                "selection_id": "selection-1",
                "target_alias": "source",
            },
            {"type": "surface", "alias": "reference", "surface_id": "surface-1"},
        ]
    )
    assert [item.type for item in inputs] == ["selection", "surface"]

    with pytest.raises(ValidationError):
        TypeAdapter(BatchInputs).validate_python(
            [{"type": "surface", "alias": "reference", "surface_id": "x", "extra": 1}]
        )


def test_batch_steps_cover_query_derive_edit_separate_and_validation() -> None:
    steps = TypeAdapter(BatchSteps).validate_python(
        [
            {
                "type": "selection_query",
                "target_alias": "source",
                "output_alias": "faces",
                "domain": "FACE",
                "query": {"type": "all"},
            },
            {
                "type": "selection_derive",
                "output_alias": "boundary",
                "operation": {"type": "boundary", "selection_alias": "faces"},
            },
            {
                "type": "mesh_edit",
                "target_alias": "source",
                "data_scope": "OBJECT",
                "operation": {
                    "type": "subdivide",
                    "selection_alias": "boundary",
                    "cuts": 2,
                },
                "map_alias": "subdivision_map",
            },
            {
                "type": "mesh_separate",
                "target_alias": "source",
                "selection_alias": "faces",
                "new_target_alias": "patch",
                "new_selection_alias": "patch_faces",
                "source_map_alias": "source_map",
                "separated_map_alias": "patch_map",
                "new_object_name": "Patch",
            },
            {
                "type": "mesh_validate",
                "selection_alias": "patch_faces",
                "check": "NON_MANIFOLD",
                "output_alias": "manifold",
                "assertions": [{"type": "count_at_most", "value": 4}],
            },
        ]
    )
    assert [step.type for step in steps] == [
        "selection_query",
        "selection_derive",
        "mesh_edit",
        "mesh_separate",
        "mesh_validate",
    ]


def test_batch_steps_cover_uv_weights_transfer_and_attribute_validation() -> None:
    steps = TypeAdapter(BatchSteps).validate_python(
        [
            {
                "type": "uv_edit",
                "target_alias": "source",
                "data_scope": "OBJECT",
                "operation": {
                    "type": "unwrap",
                    "layer": {"layer_name": "UVMap", "expected_layer_identity": "uv:1"},
                    "selection_alias": "faces",
                },
            },
            {
                "type": "weights_edit",
                "target_alias": "source",
                "data_scope": "OBJECT",
                "operation": {
                    "type": "set",
                    "group": {
                        "group_name": "Bone",
                        "expected_group_identity": "group:1",
                    },
                    "selection_alias": "vertices",
                    "value": 0.5,
                },
            },
            {
                "type": "attribute_transfer",
                "source_target_alias": "source",
                "target_alias": "patch",
                "transfer": {
                    "type": "WEIGHTS",
                    "groups": [
                        {
                            "source": {
                                "group_name": "Bone",
                                "expected_group_identity": "group:1",
                            },
                            "target_group_name": "Bone",
                        }
                    ],
                    "target_selection_alias": "patch_vertices",
                    "mapping": "NEAREST_SURFACE",
                    "maximum_distance": 1.0,
                },
            },
            {
                "type": "mesh_validate",
                "selection_alias": "patch_vertices",
                "check": "WEIGHT_SUM",
                "output_alias": "weight_sum",
                "group_names": ["Bone"],
                "target_total": 1.0,
            },
        ]
    )
    assert [step.type for step in steps] == [
        "uv_edit",
        "weights_edit",
        "attribute_transfer",
        "mesh_validate",
    ]


def test_topology_and_separation_attribute_policies_are_closed() -> None:
    steps = TypeAdapter(BatchSteps).validate_python(
        [
            {
                "type": "mesh_edit",
                "target_alias": "source",
                "data_scope": "OBJECT",
                "operation": {
                    "type": "subdivide",
                    "selection_alias": "edges",
                    "attribute_policy": {"uv": "DISCARD"},
                },
            },
            {
                "type": "mesh_separate",
                "target_alias": "source",
                "selection_alias": "faces",
                "new_target_alias": "patch",
                "new_selection_alias": "patch_faces",
                "source_map_alias": "source_map",
                "separated_map_alias": "patch_map",
                "new_object_name": "Patch",
                "source_attribute_policy": {"uv": "PRESERVE_INTERPOLATE"},
                "separated_attribute_policy": {"weights": "DISCARD"},
            },
        ]
    )
    assert steps[0].operation.attribute_policy.uv == "DISCARD"
    assert steps[1].separated_attribute_policy.weights == "DISCARD"

    with pytest.raises(ValidationError):
        TypeAdapter(BatchSteps).validate_python(
            [
                {
                    "type": "mesh_edit",
                    "target_alias": "source",
                    "data_scope": "OBJECT",
                    "operation": {
                        "type": "subdivide",
                        "selection_alias": "edges",
                        "attribute_policy": {"uv": "GUESS"},
                    },
                }
            ]
        )


@pytest.mark.parametrize(
    "operation",
    [
        {"type": "smooth", "selection_alias": "vertices", "factor": 2},
        {"type": "project", "selection_alias": "vertices", "surface_alias": "surface"},
        {
            "type": "bisect",
            "selection_alias": "faces",
            "plane_origin": {"x": 0, "y": 0, "z": 0},
            "plane_normal": {"x": 0, "y": 0, "z": 0},
        },
    ],
)
def test_batch_edit_operations_reject_invalid_closed_settings(
    operation: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(BatchSteps).validate_python(
            [
                {
                    "type": "mesh_edit",
                    "target_alias": "source",
                    "data_scope": "OBJECT",
                    "operation": operation,
                }
            ]
        )


def test_batch_validation_surface_contract_is_closed() -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(BatchSteps).validate_python(
            [
                {
                    "type": "mesh_validate",
                    "selection_alias": "vertices",
                    "check": "DISTANCE",
                    "output_alias": "distance",
                }
            ]
        )
