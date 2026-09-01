"""Closed public schemas for exact cross-object Mesh composition."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator

from blender_research_mcp.mesh_authoring import MeshUserObject
from blender_research_mcp.mesh_resources import SelectionId

JoinName = Annotated[str, Field(min_length=1, max_length=255)]
JoinIdentity = Annotated[str, Field(min_length=1, max_length=128)]
JoinFingerprint = Annotated[str, Field(min_length=64, max_length=64)]


class MeshJoinSource(BaseModel):
    model_config = ConfigDict(extra="forbid")

    object_name: JoinName
    expected_object_identity: JoinIdentity
    expected_object_structure_fingerprint: JoinFingerprint
    mesh_name: JoinName
    expected_mesh_identity: JoinIdentity
    expected_mesh_users: Annotated[StrictInt, Field(ge=1, le=256)]
    expected_mesh_user_objects: Annotated[
        tuple[MeshUserObject, ...], Field(min_length=1, max_length=256)
    ]
    expected_mesh_fingerprint: JoinFingerprint
    expected_mesh_revision_id: JoinFingerprint
    expected_uv_fingerprint: JoinFingerprint
    expected_group_schema_fingerprint: JoinFingerprint
    expected_weights_fingerprint: JoinFingerprint
    expected_shape_key_state_fingerprint: JoinFingerprint
    expected_modifier_stack_fingerprint: JoinFingerprint
    selection_ids: Annotated[tuple[SelectionId, ...], Field(max_length=8)] = ()

    @model_validator(mode="after")
    def validate_exact_scope(self) -> MeshJoinSource:
        refs = {
            (item.object_name, item.expected_object_identity)
            for item in self.expected_mesh_user_objects
        }
        if len(refs) != len(self.expected_mesh_user_objects):
            raise ValueError("expected_mesh_user_objects must be unique")
        if self.expected_mesh_users != len(self.expected_mesh_user_objects):
            raise ValueError(
                "expected_mesh_users must equal expected_mesh_user_objects length"
            )
        if len(set(self.selection_ids)) != len(self.selection_ids):
            raise ValueError("selection_ids must be unique")
        return self


MeshJoinSources = Annotated[tuple[MeshJoinSource, ...], Field(min_length=2, max_length=32)]


class WorldCoordinateFrame(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["WORLD"]


class SourceObjectCoordinateFrame(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["SOURCE_OBJECT"]
    source_object_name: JoinName
    expected_source_object_identity: JoinIdentity


MeshJoinCoordinateFrame = Annotated[
    WorldCoordinateFrame | SourceObjectCoordinateFrame,
    Field(discriminator="type"),
]


class MeshJoinOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    new_object_name: JoinName
    new_mesh_name: JoinName
    collection_name: JoinName
    expected_collection_identity: JoinIdentity
    expected_collection_structure_fingerprint: JoinFingerprint
    coordinate_frame: MeshJoinCoordinateFrame
    source_disposition: Literal["KEEP", "DELETE_ON_COMMIT"] = "KEEP"


class MeshJoinAttributes(BaseModel):
    model_config = ConfigDict(extra="forbid")

    materials: Literal["PRESERVE_BY_IDENTITY", "ERROR_IF_DIFFERENT", "DROP"]
    uv: Literal["MERGE_BY_NAME", "ERROR_IF_SCHEMA_DIFF", "DROP"]
    weights: Literal["MERGE_BY_NAME", "ERROR_IF_SCHEMA_DIFF", "DROP"]
    colors: Literal["MERGE_BY_NAME", "ERROR_IF_PRESENT", "DROP"]
    generic: Literal["ERROR_IF_PRESENT", "DROP"]
    custom_normals: Literal["ERROR_IF_PRESENT", "DROP_RECALCULATE"]


class MeshJoinDependencies(BaseModel):
    model_config = ConfigDict(extra="forbid")

    shape_keys: Literal["ERROR_IF_PRESENT", "DROP_OUTPUT"]
    modifiers: Literal["ERROR_IF_PRESENT", "DROP_OUTPUT"]


class MeshJoinRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sources: MeshJoinSources
    output: MeshJoinOutput
    attributes: MeshJoinAttributes
    dependencies: MeshJoinDependencies

    @model_validator(mode="after")
    def validate_sources(self) -> MeshJoinRequest:
        object_refs = [
            (item.object_name, item.expected_object_identity) for item in self.sources
        ]
        if len(set(object_refs)) != len(object_refs):
            raise ValueError("sources must contain unique exact objects")
        selection_count = sum(len(item.selection_ids) for item in self.sources)
        if selection_count > 32:
            raise ValueError("sources may reference at most 32 SelectionSets in total")
        frame = self.output.coordinate_frame
        if frame.type == "SOURCE_OBJECT" and (
            frame.source_object_name,
            frame.expected_source_object_identity,
        ) not in set(object_refs):
            raise ValueError("SOURCE_OBJECT coordinate frame must reference one source")
        return self


__all__ = [
    "MeshJoinAttributes",
    "MeshJoinDependencies",
    "MeshJoinOutput",
    "MeshJoinRequest",
    "MeshJoinSource",
    "MeshJoinSources",
]
