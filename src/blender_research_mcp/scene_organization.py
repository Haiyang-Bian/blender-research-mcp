"""Closed public schemas for exact Collection and object-parent authoring."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

SceneOrganizationName = Annotated[str, Field(min_length=1, max_length=255)]
SceneOrganizationIdentity = Annotated[str, Field(min_length=1, max_length=128)]
SceneOrganizationFingerprint = Annotated[str, Field(min_length=64, max_length=64)]
ParentTransformMode = Literal["KEEP_WORLD", "KEEP_LOCAL"]


class SceneRootCollectionParent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["SCENE_ROOT"]
    scene_name: SceneOrganizationName
    expected_scene_identity: SceneOrganizationIdentity
    expected_scene_structure_fingerprint: SceneOrganizationFingerprint


class ExistingCollectionParent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["COLLECTION"]
    collection_name: SceneOrganizationName
    expected_collection_identity: SceneOrganizationIdentity
    expected_collection_structure_fingerprint: SceneOrganizationFingerprint


CollectionParent = Annotated[
    SceneRootCollectionParent | ExistingCollectionParent,
    Field(discriminator="type"),
]


__all__ = [
    "CollectionParent",
    "ParentTransformMode",
    "SceneOrganizationFingerprint",
    "SceneOrganizationIdentity",
    "SceneOrganizationName",
]
