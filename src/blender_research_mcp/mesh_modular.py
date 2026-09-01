"""Closed public schemas for materialized Mesh modules and Armature binding."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, model_validator

from blender_research_mcp.mesh_authoring import MeshUserObject
from blender_research_mcp.mesh_topology import MeshAttributePolicy

Name = Annotated[str, Field(min_length=1, max_length=255)]
Identity = Annotated[str, Field(min_length=1, max_length=128)]
Fingerprint = Annotated[str, Field(min_length=64, max_length=64)]


class MaterializeSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    object_name: Name
    expected_object_identity: Identity
    expected_mesh_identity: Identity
    expected_mesh_revision_id: Fingerprint


class BaseEvaluation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["BASE"]


class ShapeKeysCurrentEvaluation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["SHAPE_KEYS_CURRENT"]
    expected_shape_key_state_fingerprint: Fingerprint


class FinalEvaluatedEvaluation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["FINAL_EVALUATED"]
    surface_id: Annotated[str, Field(min_length=1, max_length=128)]


MaterializeEvaluation = Annotated[
    BaseEvaluation | ShapeKeysCurrentEvaluation | FinalEvaluatedEvaluation,
    Field(discriminator="type"),
]


class MaterializeCopyPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    materials: StrictBool
    uv: StrictBool
    weights: StrictBool


class ExtractOutputPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    parent: Literal["COPY", "CLEAR_KEEP_WORLD"]
    modifiers: Literal["COPY", "DROP"]
    material_slots: Literal["PRESERVE_INDICES", "COMPACT"]


class ExtractMeshTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    object_name: Name
    expected_object_identity: Identity
    expected_mesh_identity: Identity
    expected_mesh_users: Annotated[int, Field(strict=True, ge=1)]
    expected_mesh_user_objects: Annotated[
        tuple[MeshUserObject, ...], Field(min_length=1, max_length=256)
    ]
    expected_mesh_fingerprint: Fingerprint

    @model_validator(mode="after")
    def validate_users(self) -> ExtractMeshTarget:
        refs = {
            (item.object_name, item.expected_object_identity)
            for item in self.expected_mesh_user_objects
        }
        if len(refs) != len(self.expected_mesh_user_objects):
            raise ValueError("expected_mesh_user_objects must be unique")
        if self.expected_mesh_users != len(self.expected_mesh_user_objects):
            raise ValueError(
                "expected_mesh_users must equal the number of expected_mesh_user_objects"
            )
        return self


class RigMeshTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    object_name: Name
    expected_object_identity: Identity
    expected_mesh_identity: Identity
    expected_mesh_revision_id: Fingerprint
    expected_group_schema_fingerprint: Fingerprint
    expected_weights_fingerprint: Fingerprint


class ArmatureTarget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    object_name: Name
    expected_object_identity: Identity
    expected_data_identity: Identity
    expected_bone_schema_fingerprint: Fingerprint


class ExistingArmatureModifier(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Name
    expected_identity: Identity
    expected_stack_index: Annotated[int, Field(strict=True, ge=0, le=255)]
    expected_stack_fingerprint: Fingerprint


class RigModifierPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Name
    expected_existing: ExistingArmatureModifier | None
    use_vertex_groups: StrictBool = True
    use_bone_envelopes: StrictBool = False
    preserve_volume: StrictBool = False
    use_multi_modifier: StrictBool = False
    vertex_group: Name | None = None


class AllMatchedGroups(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["ALL_MATCHED"]


class ExplicitGroups(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["EXPLICIT"]
    group_names: Annotated[tuple[Name, ...], Field(min_length=1, max_length=256)]

    @model_validator(mode="after")
    def validate_unique(self) -> ExplicitGroups:
        if len(set(self.group_names)) != len(self.group_names):
            raise ValueError("group_names must be unique")
        return self


RigGroupScope = Annotated[AllMatchedGroups | ExplicitGroups, Field(discriminator="type")]


__all__ = [
    "ArmatureTarget",
    "ExtractMeshTarget",
    "ExtractOutputPolicy",
    "MaterializeCopyPolicy",
    "MaterializeEvaluation",
    "MaterializeSource",
    "MeshAttributePolicy",
    "RigGroupScope",
    "RigMeshTarget",
    "RigModifierPolicy",
]
