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


def test_batch_inputs_cover_objects_armatures_collections_and_catalogs() -> None:
    inputs = TypeAdapter(BatchInputs).validate_python(
        [
            {
                "type": "object",
                "alias": "root",
                "object_name": "Root",
                "expected_object_identity": "object:1",
                "expected_object_structure_fingerprint": "a" * 64,
            },
            {
                "type": "armature",
                "alias": "rig",
                "target": {
                    "object_name": "Rig",
                    "expected_object_identity": "object:2",
                    "expected_data_identity": "armature:1",
                    "expected_bone_schema_fingerprint": "b" * 64,
                },
            },
            {
                "type": "collection",
                "alias": "modules",
                "collection_name": "Modules",
                "expected_collection_identity": "collection:1",
                "expected_collection_structure_fingerprint": "c" * 64,
            },
            {
                "type": "component_catalog",
                "alias": "shells",
                "component_catalog_id": "catalog:1",
                "target_alias": "source",
            },
        ]
    )
    assert [item.type for item in inputs] == [
        "object",
        "armature",
        "collection",
        "component_catalog",
    ]


def test_batch_v4_library_input_and_template_steps_are_closed() -> None:
    inputs = TypeAdapter(BatchInputs).validate_python(
        [
            {
                "type": "library",
                "alias": "templates",
                "path": "C:/fixtures/templates.blend",
                "expected_file_sha256": "a" * 64,
                "expected_size_bytes": 1024,
            }
        ]
    )
    assert inputs[0].type == "library"

    steps = TypeAdapter(BatchSteps).validate_python(
        [
            {
                "type": "library_append",
                "library_alias": "templates",
                "entry": {
                    "type": "COLLECTION",
                    "name": "HeadTemplate",
                    "expected_entry_identity": "b" * 64,
                },
                "output": {
                    "type": "COLLECTION",
                    "new_collection_name": "HeadTemplateInstance",
                    "parent": {
                        "type": "SCENE_ROOT",
                        "scene_name": "Scene",
                        "expected_scene_identity": "scene:1",
                        "expected_scene_structure_fingerprint": "c" * 64,
                    },
                },
                "output_root_alias": "template_collection",
                "root_alias_kind": "COLLECTION",
                "exports": [
                    {
                        "source_object_name": "HeadCage",
                        "expected_entry_identity": "d" * 64,
                        "output_alias": "head",
                        "alias_kind": "MESH_TARGET",
                    }
                ],
            },
            {
                "type": "object_set",
                "object_alias": "head",
                "patches": [
                    {
                        "type": "transform",
                        "location": {"x": 1.0},
                        "scale": {"x": 1.1, "y": 1.1, "z": 1.1},
                    }
                ],
            },
            {
                "type": "mesh_surface_prepare",
                "target_alias": "head",
                "geometry": "EVALUATED",
                "output_surface_alias": "head_surface",
            },
        ]
    )
    assert [step.type for step in steps] == [
        "library_append",
        "object_set",
        "mesh_surface_prepare",
    ]

    invalid = steps[0].model_dump()
    invalid["root_alias_kind"] = "OBJECT"
    with pytest.raises(ValidationError):
        TypeAdapter(BatchSteps).validate_python([invalid])


def test_batch_v5_join_and_boundary_weld_are_closed() -> None:
    policies = {
        "materials": "PRESERVE_BY_IDENTITY",
        "uv": "MERGE_BY_NAME",
        "weights": "MERGE_BY_NAME",
        "colors": "MERGE_BY_NAME",
        "generic": "ERROR_IF_PRESENT",
        "custom_normals": "DROP_RECALCULATE",
    }
    steps = TypeAdapter(BatchSteps).validate_python(
        [
            {
                "type": "mesh_join",
                "sources": [
                    {
                        "target_alias": "head",
                        "map_alias": "head_join_map",
                        "boundary_selection_alias": "head_boundary",
                    },
                    {
                        "target_alias": "body",
                        "map_alias": "body_join_map",
                        "boundary_selection_alias": "body_boundary",
                    },
                ],
                "output_target_alias": "joined",
                "new_object_name": "Joined",
                "new_mesh_name": "Joined Mesh",
                "collection_alias": "modules",
                "coordinate_frame": {"type": "SOURCE_OBJECT", "source_target_alias": "body"},
                "attributes": policies,
                "dependencies": {
                    "shape_keys": "ERROR_IF_PRESENT",
                    "modifiers": "ERROR_IF_PRESENT",
                },
            },
            {
                "type": "mesh_edit",
                "target_alias": "joined",
                "data_scope": "OBJECT",
                "operation": {
                    "type": "weld_vertices",
                    "selection_aliases": ["head_boundary", "body_boundary"],
                    "maximum_distance": 0.002,
                },
                "map_alias": "weld_map",
            },
        ]
    )
    assert [step.type for step in steps] == ["mesh_join", "mesh_edit"]
    assert steps[1].operation.type == "weld_vertices"

    invalid = steps[0].model_dump()
    invalid["sources"][1]["boundary_selection_alias"] = "head_boundary"
    with pytest.raises(ValidationError):
        TypeAdapter(BatchSteps).validate_python([invalid])


def test_batch_steps_cover_catalog_materialization_assembly_and_binding() -> None:
    steps = TypeAdapter(BatchSteps).validate_python(
        [
            {
                "type": "component_catalog_prepare",
                "selection_alias": "all_faces",
                "output_catalog_alias": "shells",
            },
            {
                "type": "component_catalog_select",
                "catalog_alias": "shells",
                "component_identities": ["component:1"],
                "output_selection_alias": "hair_faces",
            },
            {
                "type": "mesh_materialize",
                "source_target_alias": "source",
                "evaluation": {
                    "type": "SHAPE_KEYS_CURRENT",
                    "expected_shape_key_state_fingerprint": "d" * 64,
                },
                "new_object_name": "Working Copy",
                "copy": {"materials": True, "uv": True, "weights": True},
                "output_target_alias": "working",
                "map_alias": "materialization_map",
            },
            {
                "type": "mesh_extract",
                "target_alias": "working",
                "selection_alias": "hair_faces",
                "new_target_alias": "hair",
                "new_selection_alias": "hair_output_faces",
                "source_map_alias": "working_map",
                "extracted_map_alias": "hair_map",
                "new_object_name": "Hair Module",
                "output_policy": {
                    "parent": "CLEAR_KEEP_WORLD",
                    "modifiers": "DROP",
                    "material_slots": "COMPACT",
                },
                "source_attribute_policy": {},
                "extracted_attribute_policy": {},
                "collection_alias": "modules",
            },
            {
                "type": "collection_create",
                "name": "Hair",
                "parent": {
                    "type": "SCENE_ROOT",
                    "scene_name": "Scene",
                    "expected_scene_identity": "scene:1",
                    "expected_scene_structure_fingerprint": "e" * 64,
                },
                "output_collection_alias": "hair_collection",
            },
            {
                "type": "collection_link_object",
                "collection_alias": "hair_collection",
                "object_alias": "hair",
            },
            {
                "type": "collection_unlink_object",
                "collection_alias": "modules",
                "object_alias": "hair",
            },
            {
                "type": "object_parent_set",
                "child_alias": "hair",
                "parent_alias": "root",
                "transform_mode": "KEEP_WORLD",
            },
            {
                "type": "object_parent_clear",
                "child_alias": "hair",
                "expected_parent_alias": "root",
                "transform_mode": "KEEP_LOCAL",
            },
            {
                "type": "rig_bind",
                "mesh_target_alias": "hair",
                "armature_alias": "rig",
                "modifier": {
                    "name": "Armature",
                    "expected_existing": None,
                },
                "parenting": "KEEP_WORLD",
                "group_scope": {"type": "ALL_MATCHED"},
                "output_binding_alias": "hair_binding",
            },
        ]
    )
    assert [step.type for step in steps] == [
        "component_catalog_prepare",
        "component_catalog_select",
        "mesh_materialize",
        "mesh_extract",
        "collection_create",
        "collection_link_object",
        "collection_unlink_object",
        "object_parent_set",
        "object_parent_clear",
        "rig_bind",
    ]
    assert steps[2].model_dump(by_alias=True)["copy"] == {
        "materials": True,
        "uv": True,
        "weights": True,
    }


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
