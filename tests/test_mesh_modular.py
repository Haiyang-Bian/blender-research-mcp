from __future__ import annotations

import pytest
from pydantic import TypeAdapter, ValidationError

from blender_research_mcp.mesh_modular import (
    ExtractMeshTarget,
    ExtractOutputPolicy,
    MaterializeCopyPolicy,
    MaterializeEvaluation,
    RigGroupScope,
    RigModifierPolicy,
)

FINGERPRINT = "a" * 64


@pytest.mark.parametrize(
    "payload",
    [
        {"type": "BASE"},
        {
            "type": "SHAPE_KEYS_CURRENT",
            "expected_shape_key_state_fingerprint": FINGERPRINT,
        },
        {"type": "FINAL_EVALUATED", "surface_id": "surface"},
    ],
)
def test_materialize_evaluation_is_explicit_and_closed(payload: dict[str, object]) -> None:
    parsed = TypeAdapter(MaterializeEvaluation).validate_python(payload)
    assert parsed.type == payload["type"]


@pytest.mark.parametrize(
    "payload",
    [
        {"type": "SHAPE_KEYS_CURRENT"},
        {"type": "FINAL_EVALUATED"},
        {"type": "BASE", "surface_id": "surface"},
        {"type": "UNKNOWN"},
    ],
)
def test_materialize_evaluation_rejects_implicit_or_extra_evidence(
    payload: dict[str, object],
) -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(MaterializeEvaluation).validate_python(payload)


def test_materialize_copy_policy_requires_strict_domain_choices() -> None:
    policy = MaterializeCopyPolicy(materials=True, uv=False, weights=True)
    assert policy.model_dump() == {"materials": True, "uv": False, "weights": True}
    with pytest.raises(ValidationError):
        MaterializeCopyPolicy.model_validate({"materials": 1, "uv": False, "weights": True})
    with pytest.raises(ValidationError):
        MaterializeCopyPolicy.model_validate({"materials": True, "uv": False})


def test_extract_target_requires_complete_unique_user_evidence() -> None:
    target = {
        "object_name": "Source",
        "expected_object_identity": "object:1",
        "expected_mesh_identity": "mesh:1",
        "expected_mesh_users": 1,
        "expected_mesh_user_objects": [
            {"object_name": "Source", "expected_object_identity": "object:1"}
        ],
        "expected_mesh_fingerprint": FINGERPRINT,
    }
    assert ExtractMeshTarget.model_validate(target).expected_mesh_users == 1
    with pytest.raises(ValidationError):
        ExtractMeshTarget.model_validate({**target, "expected_mesh_users": 2})


def test_extract_output_policy_is_closed() -> None:
    policy = ExtractOutputPolicy(
        parent="CLEAR_KEEP_WORLD",
        modifiers="DROP",
        material_slots="COMPACT",
    )
    assert policy.modifiers == "DROP"
    with pytest.raises(ValidationError):
        ExtractOutputPolicy.model_validate(
            {
                "parent": "CLEAR_KEEP_WORLD",
                "modifiers": "APPLY",
                "material_slots": "COMPACT",
            }
        )


def test_rig_modifier_and_group_scope_are_bounded() -> None:
    policy = RigModifierPolicy(
        name="Armature",
        expected_existing=None,
        use_vertex_groups=True,
        use_bone_envelopes=False,
        preserve_volume=True,
        use_multi_modifier=False,
    )
    assert policy.preserve_volume is True
    explicit = TypeAdapter(RigGroupScope).validate_python(
        {"type": "EXPLICIT", "group_names": ["Head", "Neck"]}
    )
    assert explicit.type == "EXPLICIT"
    with pytest.raises(ValidationError):
        TypeAdapter(RigGroupScope).validate_python(
            {"type": "EXPLICIT", "group_names": ["Head", "Head"]}
        )
