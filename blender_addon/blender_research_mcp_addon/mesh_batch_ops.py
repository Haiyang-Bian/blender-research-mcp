"""Declarative, revision-aware Mesh batch execution inside one Blender tick."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from typing import Any

import bpy

from .authoring_ops import AuthoringOperationError, object_summary
from .capture_model import CaptureBook
from .mesh_attribute_transfer_ops import transfer_attribute
from .mesh_component_catalog_ops import (
    prepare_component_catalog,
    select_component_catalog,
    validate_component_catalog,
)
from .mesh_component_map import compose_component_map, remap_selection
from .mesh_deform_ops import DEFORM_OPERATIONS, edit_mesh_deform
from .mesh_materialization_ops import materialize_mesh
from .mesh_ops import (
    MeshOperationError,
    mesh_fingerprint,
    mesh_revision_id,
    mesh_user_refs,
    shape_key_state_fingerprint,
)
from .mesh_query_ops import derive_selection, query_selection, validate_selection
from .mesh_resource_model import (
    MAX_COMPONENT_CATALOGS,
    MeshResourceBook,
    MeshResourceError,
)
from .mesh_separation_ops import extract_mesh, extract_preflight, separate_mesh
from .mesh_surface_ops import validate_mesh, validate_surface
from .mesh_topology_ops import TOPOLOGY_OPERATIONS, edit_mesh_topology
from .mesh_uv_ops import edit_uv, uv_fingerprint
from .mesh_weight_ops import (
    edit_weights,
    group_schema_fingerprint,
    weights_fingerprint,
)
from .rig_ops import bind_rig, bone_schema_fingerprint
from .scene_organization_ops import (
    change_collection_link,
    change_object_parent,
    collection_summary,
    create_collection,
    object_collection_fingerprint,
    organization_result,
)
from .structural_ops import session_identity, structure_fingerprint
from .transaction_model import Transaction

MAX_TARGETS = 8
MAX_STEPS = 32
MAX_ALIASES = 64
MAX_TOPOLOGY_STEPS = 8


@dataclass
class MeshBatchExecutionError(RuntimeError):
    cause: Exception
    batch_id: str
    step_index: int
    step_type: str
    aliases: tuple[str, ...]

    def __str__(self) -> str:
        return str(self.cause)


def _batch_error(code: str, message: str, *, details: dict[str, Any] | None = None) -> None:
    raise MeshOperationError(code, message, details=details)


def _alias(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 64:
        _batch_error("MESH_BATCH_INVALID", f"{field} must be a non-empty batch alias")
    return value


def _target_params(target: dict[str, Any], data_scope: str = "OBJECT") -> dict[str, Any]:
    return {
        "object_name": target["object_name"],
        "expected_object_identity": target["expected_object_identity"],
        "expected_mesh_identity": target["expected_mesh_identity"],
        "expected_mesh_users": target["expected_mesh_users"],
        "expected_mesh_user_objects": target["expected_mesh_user_objects"],
        "expected_mesh_fingerprint": target["expected_mesh_fingerprint"],
        "data_scope": data_scope,
    }


def _live_target(alias: str, obj: Any) -> dict[str, Any]:
    mesh = obj.data
    refs = mesh_user_refs(mesh)
    return {
        "alias": alias,
        "object_name": obj.name,
        "expected_object_identity": session_identity("object", obj),
        "expected_mesh_identity": session_identity("mesh", mesh),
        "expected_mesh_users": int(mesh.users),
        "expected_mesh_user_objects": [
            {"object_name": name, "expected_object_identity": identity} for name, identity in refs
        ],
        "expected_mesh_fingerprint": mesh_fingerprint(mesh),
        "mesh_revision_id": mesh_revision_id(mesh),
        "uv_fingerprint": uv_fingerprint(mesh),
        "group_schema_fingerprint": group_schema_fingerprint(obj),
        "weights_fingerprint": weights_fingerprint(mesh),
    }


def _attribute_target(target: dict[str, Any], *, data_scope: str = "OBJECT") -> dict[str, Any]:
    return {
        **_target_params(target, data_scope),
        "expected_group_schema_fingerprint": target["group_schema_fingerprint"],
        "expected_weights_fingerprint": target["weights_fingerprint"],
    }


def _validate_target(target: dict[str, Any]) -> tuple[Any, Any]:
    object_name = target.get("object_name")
    obj = bpy.data.objects.get(object_name) if isinstance(object_name, str) else None
    if obj is None or obj.type != "MESH" or obj.data is None:
        _batch_error(
            "MESH_BATCH_TARGET_MISMATCH",
            f"Mesh batch target does not exist: {object_name}",
        )
    mesh = obj.data
    actual_refs = mesh_user_refs(mesh)
    expected_raw = target.get("expected_mesh_user_objects")
    if not isinstance(expected_raw, list):
        _batch_error(
            "MESH_BATCH_TARGET_MISMATCH",
            f"Mesh batch target has invalid user evidence: {object_name}",
        )
    expected_refs = tuple(
        sorted(
            (
                str(item.get("object_name")),
                str(item.get("expected_object_identity")),
            )
            for item in expected_raw
            if isinstance(item, dict)
        )
    )
    expected = {
        "object_identity": target.get("expected_object_identity"),
        "mesh_identity": target.get("expected_mesh_identity"),
        "mesh_users": target.get("expected_mesh_users"),
        "mesh_user_objects": expected_refs,
        "mesh_fingerprint": target.get("expected_mesh_fingerprint"),
    }
    actual = {
        "object_identity": session_identity("object", obj),
        "mesh_identity": session_identity("mesh", mesh),
        "mesh_users": int(mesh.users),
        "mesh_user_objects": actual_refs,
        "mesh_fingerprint": mesh_fingerprint(mesh),
    }
    if expected != actual:
        _batch_error(
            "MESH_BATCH_TARGET_MISMATCH",
            f"Mesh batch target evidence changed: {object_name}",
            details={"expected": expected, "actual": actual},
        )
    return obj, mesh


def _reserve(alias_kinds: dict[str, str], alias: Any, kind: str) -> str:
    name = _alias(alias, f"{kind} alias")
    if name in alias_kinds:
        _batch_error(
            "MESH_BATCH_ALIAS_CONFLICT",
            f"Batch alias is already defined: {name}",
            details={"alias": name, "existing_kind": alias_kinds[name], "new_kind": kind},
        )
    alias_kinds[name] = kind
    if len(alias_kinds) > MAX_ALIASES:
        _batch_error("MESH_BATCH_BUDGET_EXCEEDED", "A batch may define at most 64 aliases")
    return name


def _require_alias(alias_kinds: dict[str, str], alias: Any, kind: str) -> str:
    name = _alias(alias, f"{kind} reference")
    if alias_kinds.get(name) != kind:
        _batch_error(
            "MESH_BATCH_REFERENCE_NOT_FOUND",
            f"Batch {kind} alias does not exist before this step: {name}",
            details={"alias": name, "expected_kind": kind, "actual_kind": alias_kinds.get(name)},
        )
    return name


def _require_alias_any(
    alias_kinds: dict[str, str], alias: Any, kinds: tuple[str, ...]
) -> str:
    name = _alias(alias, "/".join(kinds) + " reference")
    if alias_kinds.get(name) not in kinds:
        _batch_error(
            "MESH_BATCH_REFERENCE_NOT_FOUND",
            f"Batch alias does not exist with an accepted kind: {name}",
            details={
                "alias": name,
                "expected_kinds": list(kinds),
                "actual_kind": alias_kinds.get(name),
            },
        )
    return name


def _live_object(obj: Any) -> dict[str, Any]:
    return {
        "object_name": obj.name,
        "expected_object_identity": session_identity("object", obj),
        "expected_object_structure_fingerprint": structure_fingerprint("object", obj),
        "expected_object_collections_fingerprint": object_collection_fingerprint(obj),
    }


def _live_collection(collection: Any) -> dict[str, Any]:
    return {
        "collection_name": collection.name,
        "expected_collection_identity": session_identity("collection", collection),
        "expected_collection_structure_fingerprint": structure_fingerprint(
            "collection", collection
        ),
    }


def _resource_counts(book: MeshResourceBook) -> dict[str, int]:
    return {
        "objects": len(bpy.data.objects),
        "meshes": len(bpy.data.meshes),
        "collections": len(bpy.data.collections),
        "modifiers": sum(len(obj.modifiers) for obj in bpy.data.objects),
        "selection_sets": book.selection_count,
        "surface_refs": book.surface_count,
        "component_maps": book.component_map_count,
        "component_catalogs": book.component_catalog_count,
    }


def _content_sha256(payload: Any) -> str:
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _object_for_alias(
    alias: str,
    targets: dict[str, dict[str, Any]],
    objects: dict[str, dict[str, Any]],
) -> Any:
    evidence = targets.get(alias) or objects.get(alias)
    if evidence is None:
        _batch_error("MESH_BATCH_REFERENCE_NOT_FOUND", f"Object alias disappeared: {alias}")
    obj = bpy.data.objects.get(evidence["object_name"])
    if obj is None or session_identity("object", obj) != evidence[
        "expected_object_identity"
    ]:
        _batch_error("MESH_BATCH_TARGET_MISMATCH", f"Object alias changed: {alias}")
    return obj


def _collection_for_alias(alias: str, collections: dict[str, dict[str, Any]]) -> Any:
    evidence = collections.get(alias)
    if evidence is None:
        _batch_error(
            "MESH_BATCH_REFERENCE_NOT_FOUND", f"Collection alias disappeared: {alias}"
        )
    collection = bpy.data.collections.get(evidence["collection_name"])
    if collection is None or session_identity("collection", collection) != evidence[
        "expected_collection_identity"
    ]:
        _batch_error("MESH_BATCH_TARGET_MISMATCH", f"Collection alias changed: {alias}")
    return collection


def _selection_refs(operation: dict[str, Any]) -> tuple[str, ...]:
    if operation.get("type") == "combine":
        values = operation.get("selection_aliases")
        if not isinstance(values, list) or len(values) < 2:
            _batch_error("MESH_BATCH_INVALID", "combine requires selection_aliases")
        return tuple(_alias(value, "selection_aliases") for value in values)
    return (_alias(operation.get("selection_alias"), "selection_alias"),)


def _preflight(
    transaction: Transaction,
    book: MeshResourceBook,
    params: dict[str, Any],
) -> dict[str, Any]:
    targets_raw = params.get("targets")
    inputs = params.get("inputs", [])
    steps = params.get("steps")
    if not isinstance(targets_raw, list) or not 1 <= len(targets_raw) <= MAX_TARGETS:
        _batch_error("MESH_BATCH_INVALID", "targets must contain 1 to 8 exact Mesh targets")
    if not isinstance(inputs, list) or len(inputs) > MAX_ALIASES:
        _batch_error("MESH_BATCH_INVALID", "inputs must contain at most 64 resources")
    if not isinstance(steps, list) or not 1 <= len(steps) <= MAX_STEPS:
        _batch_error("MESH_BATCH_INVALID", "steps must contain 1 to 32 operations")
    if params.get("on_error", "ROLLBACK_TRANSACTION") != "ROLLBACK_TRANSACTION":
        _batch_error("MESH_BATCH_INVALID", "on_error must be ROLLBACK_TRANSACTION")

    alias_kinds: dict[str, str] = {}
    targets: dict[str, dict[str, Any]] = {}
    for raw in targets_raw:
        if not isinstance(raw, dict):
            _batch_error("MESH_BATCH_INVALID", "Each target must be an object")
        alias = _reserve(alias_kinds, raw.get("alias"), "target")
        target = dict(raw)
        target["alias"] = alias
        obj, _mesh = _validate_target(target)
        targets[alias] = _live_target(alias, obj)

    input_selections: dict[str, dict[str, Any]] = {}
    input_surfaces: dict[str, str] = {}
    input_objects: dict[str, dict[str, Any]] = {}
    input_armatures: dict[str, dict[str, Any]] = {}
    input_collections: dict[str, dict[str, Any]] = {}
    input_catalogs: dict[str, dict[str, str]] = {}
    for raw in inputs:
        if not isinstance(raw, dict):
            _batch_error("MESH_BATCH_INVALID", "Each input must be an object")
        kind = raw.get("type")
        if kind == "selection":
            alias = _reserve(alias_kinds, raw.get("alias"), "selection")
            target_alias = _require_alias(alias_kinds, raw.get("target_alias"), "target")
            selection_id = raw.get("selection_id")
            if not isinstance(selection_id, str):
                _batch_error("MESH_BATCH_INVALID", "selection input requires selection_id")
            record = book.selection(selection_id)
            obj, _mesh = validate_selection(record)
            if session_identity("object", obj) != targets[target_alias]["expected_object_identity"]:
                _batch_error(
                    "MESH_BATCH_TARGET_MISMATCH",
                    f"Selection input {alias} does not belong to target {target_alias}",
                )
            input_selections[alias] = {
                "selection_id": selection_id,
                "target_alias": target_alias,
                "remap_mode": raw.get("remap_mode", "ALL_MAPPED"),
                "weight_merge": raw.get("weight_merge", "MAX"),
            }
        elif kind == "surface":
            alias = _reserve(alias_kinds, raw.get("alias"), "surface")
            surface_id = raw.get("surface_id")
            if not isinstance(surface_id, str):
                _batch_error("MESH_BATCH_INVALID", "surface input requires surface_id")
            surface = book.surface(surface_id)
            validate_surface(surface)
            input_surfaces[alias] = surface_id
        elif kind == "object":
            alias = _reserve(alias_kinds, raw.get("alias"), "object")
            object_name = raw.get("object_name")
            obj = bpy.data.objects.get(object_name) if isinstance(object_name, str) else None
            if obj is None:
                _batch_error(
                    "MESH_BATCH_TARGET_MISMATCH", f"Object input does not exist: {object_name}"
                )
            actual = _live_object(obj)
            expected = {
                "object_name": object_name,
                "expected_object_identity": raw.get("expected_object_identity"),
                "expected_object_structure_fingerprint": raw.get(
                    "expected_object_structure_fingerprint"
                ),
            }
            if any(actual[key] != value for key, value in expected.items()):
                _batch_error(
                    "MESH_BATCH_TARGET_MISMATCH",
                    f"Object input evidence changed: {object_name}",
                    details={"expected": expected, "actual": actual},
                )
            input_objects[alias] = actual
        elif kind == "armature":
            alias = _reserve(alias_kinds, raw.get("alias"), "armature")
            target = raw.get("target")
            if not isinstance(target, dict):
                _batch_error("MESH_BATCH_INVALID", "armature input requires target")
            object_name = target.get("object_name")
            obj = bpy.data.objects.get(object_name) if isinstance(object_name, str) else None
            if obj is None or obj.type != "ARMATURE" or obj.data is None:
                _batch_error(
                    "MESH_BATCH_TARGET_MISMATCH",
                    f"Armature input does not exist: {object_name}",
                )
            actual = {
                "object_name": obj.name,
                "expected_object_identity": session_identity("object", obj),
                "expected_data_identity": session_identity("armature", obj.data),
                "expected_bone_schema_fingerprint": bone_schema_fingerprint(obj.data),
            }
            if actual != target:
                _batch_error(
                    "MESH_BATCH_TARGET_MISMATCH",
                    f"Armature input evidence changed: {object_name}",
                    details={"expected": target, "actual": actual},
                )
            input_armatures[alias] = actual
            input_objects[alias] = _live_object(obj)
        elif kind == "collection":
            alias = _reserve(alias_kinds, raw.get("alias"), "collection")
            name = raw.get("collection_name")
            collection = (
                bpy.data.collections.get(name) if isinstance(name, str) else None
            )
            if collection is None:
                _batch_error(
                    "MESH_BATCH_TARGET_MISMATCH", f"Collection input does not exist: {name}"
                )
            actual = _live_collection(collection)
            expected = {
                "collection_name": name,
                "expected_collection_identity": raw.get("expected_collection_identity"),
                "expected_collection_structure_fingerprint": raw.get(
                    "expected_collection_structure_fingerprint"
                ),
            }
            if actual != expected:
                _batch_error(
                    "MESH_BATCH_TARGET_MISMATCH",
                    f"Collection input evidence changed: {name}",
                    details={"expected": expected, "actual": actual},
                )
            input_collections[alias] = actual
        elif kind == "component_catalog":
            alias = _reserve(alias_kinds, raw.get("alias"), "component_catalog")
            target_alias = _require_alias(alias_kinds, raw.get("target_alias"), "target")
            catalog_id = raw.get("component_catalog_id")
            if not isinstance(catalog_id, str):
                _batch_error(
                    "MESH_BATCH_INVALID",
                    "component_catalog input requires component_catalog_id",
                )
            catalog = book.component_catalog(catalog_id)
            obj, _mesh = validate_component_catalog(catalog)
            if session_identity("object", obj) != targets[target_alias][
                "expected_object_identity"
            ]:
                _batch_error(
                    "MESH_BATCH_TARGET_MISMATCH",
                    f"ComponentCatalog input {alias} does not belong to {target_alias}",
                )
            input_catalogs[alias] = {
                "component_catalog_id": catalog_id,
                "target_alias": target_alias,
            }
        else:
            _batch_error("MESH_BATCH_INVALID", f"Unsupported input type: {kind}")

    topology_steps = 0
    catalog_steps = 0
    capacity = 0
    reserved_object_names = {obj.name for obj in bpy.data.objects}
    reserved_collection_names = {collection.name for collection in bpy.data.collections}
    for index, step in enumerate(steps):
        if not isinstance(step, dict) or not isinstance(step.get("type"), str):
            _batch_error("MESH_BATCH_INVALID", f"Step {index} must be a typed object")
        step_type = step["type"]
        if step_type == "selection_query":
            _require_alias(alias_kinds, step.get("target_alias"), "target")
            _reserve(alias_kinds, step.get("output_alias"), "selection")
        elif step_type == "selection_derive":
            operation = step.get("operation")
            if not isinstance(operation, dict):
                _batch_error("MESH_BATCH_INVALID", "selection_derive requires operation")
            for alias in _selection_refs(operation):
                _require_alias(alias_kinds, alias, "selection")
            _reserve(alias_kinds, step.get("output_alias"), "selection")
        elif step_type == "mesh_edit":
            _require_alias(alias_kinds, step.get("target_alias"), "target")
            operation = step.get("operation")
            if not isinstance(operation, dict):
                _batch_error("MESH_BATCH_INVALID", "mesh_edit requires operation")
            _require_alias(alias_kinds, operation.get("selection_alias"), "selection")
            if operation.get("type") in {"project", "shrinkwrap"}:
                _require_alias(alias_kinds, operation.get("surface_alias"), "surface")
            if operation.get("type") in TOPOLOGY_OPERATIONS:
                topology_steps += 1
            if step.get("map_alias") is not None:
                _reserve(alias_kinds, step.get("map_alias"), "component_map")
            created = step.get("created_selection_aliases")
            if created is not None:
                if not isinstance(created, dict):
                    _batch_error(
                        "MESH_BATCH_INVALID",
                        "created_selection_aliases must be an object",
                    )
                for value in created.values():
                    if value is not None:
                        _reserve(alias_kinds, value, "selection")
            capacity += 1
        elif step_type == "mesh_separate":
            _require_alias(alias_kinds, step.get("target_alias"), "target")
            _require_alias(alias_kinds, step.get("selection_alias"), "selection")
            _reserve(alias_kinds, step.get("new_target_alias"), "target")
            _reserve(alias_kinds, step.get("new_selection_alias"), "selection")
            _reserve(alias_kinds, step.get("source_map_alias"), "component_map")
            _reserve(alias_kinds, step.get("separated_map_alias"), "component_map")
            topology_steps += 1
            capacity += 2
        elif step_type == "component_catalog_prepare":
            selection_alias = _require_alias(
                alias_kinds, step.get("selection_alias"), "selection"
            )
            existing_selection = input_selections.get(selection_alias)
            if existing_selection is not None:
                record = book.selection(existing_selection["selection_id"])
                validate_selection(record)
                if record.domain != "FACE" or not record.indices:
                    _batch_error(
                        "MESH_BATCH_INVALID",
                        "ComponentCatalog preparation requires a non-empty FACE SelectionSet",
                    )
            _reserve(alias_kinds, step.get("output_catalog_alias"), "component_catalog")
            catalog_steps += 1
        elif step_type == "component_catalog_select":
            catalog_alias = _require_alias(
                alias_kinds, step.get("catalog_alias"), "component_catalog"
            )
            existing_catalog = input_catalogs.get(catalog_alias)
            if existing_catalog is not None:
                catalog = book.component_catalog(existing_catalog["component_catalog_id"])
                available = {
                    component.component_identity for component in catalog.components
                }
                requested = step.get("component_identities")
                if not isinstance(requested, list) or any(
                    identity not in available for identity in requested
                ):
                    _batch_error(
                        "MESH_BATCH_REFERENCE_NOT_FOUND",
                        "ComponentCatalog selection contains an unknown component identity",
                    )
            _reserve(alias_kinds, step.get("output_selection_alias"), "selection")
        elif step_type == "mesh_materialize":
            source_alias = _require_alias(
                alias_kinds, step.get("source_target_alias"), "target"
            )
            if step.get("collection_alias") is not None:
                _require_alias(alias_kinds, step.get("collection_alias"), "collection")
            _reserve(alias_kinds, step.get("output_target_alias"), "target")
            if step.get("map_alias") is not None:
                _reserve(alias_kinds, step.get("map_alias"), "component_map")
            new_name = step.get("new_object_name")
            if not isinstance(new_name, str) or new_name in reserved_object_names:
                _batch_error(
                    "MESH_BATCH_INVALID",
                    f"Batch object output name is not unique: {new_name}",
                )
            reserved_object_names.add(new_name)
            source = targets.get(source_alias)
            evaluation = step.get("evaluation")
            if source is not None and isinstance(evaluation, dict):
                source_obj = bpy.data.objects.get(source["object_name"])
                if source_obj is None:
                    _batch_error(
                        "MESH_BATCH_TARGET_MISMATCH", "Materialization source disappeared"
                    )
                if evaluation.get("type") == "SHAPE_KEYS_CURRENT" and (
                    shape_key_state_fingerprint(source_obj)
                    != evaluation.get("expected_shape_key_state_fingerprint")
                ):
                    _batch_error(
                        "MESH_BATCH_TARGET_MISMATCH",
                        "Materialization Shape Key evidence changed",
                    )
                if evaluation.get("type") == "FINAL_EVALUATED":
                    surface_id = evaluation.get("surface_id")
                    if not isinstance(surface_id, str):
                        _batch_error(
                            "MESH_BATCH_INVALID",
                            "FINAL_EVALUATED materialization requires surface_id",
                        )
                    validate_surface(book.surface(surface_id))
            capacity += 1
        elif step_type == "mesh_extract":
            target_alias = _require_alias(
                alias_kinds, step.get("target_alias"), "target"
            )
            selection_alias = _require_alias(
                alias_kinds, step.get("selection_alias"), "selection"
            )
            if step.get("collection_alias") is not None:
                _require_alias(alias_kinds, step.get("collection_alias"), "collection")
            _reserve(alias_kinds, step.get("new_target_alias"), "target")
            _reserve(alias_kinds, step.get("new_selection_alias"), "selection")
            _reserve(alias_kinds, step.get("source_map_alias"), "component_map")
            _reserve(alias_kinds, step.get("extracted_map_alias"), "component_map")
            new_name = step.get("new_object_name")
            if not isinstance(new_name, str) or new_name in reserved_object_names:
                _batch_error(
                    "MESH_BATCH_INVALID",
                    f"Batch object output name is not unique: {new_name}",
                )
            reserved_object_names.add(new_name)
            if (
                target_alias in targets
                and selection_alias in input_selections
                and (
                    step.get("collection_alias") is None
                    or str(step["collection_alias"]) in input_collections
                )
            ):
                collection_evidence = (
                    input_collections[str(step["collection_alias"])]
                    if step.get("collection_alias") is not None
                    else {}
                )
                extract_preflight(
                    book,
                    {
                        **_target_params(targets[target_alias]),
                        "selection_id": input_selections[selection_alias]["selection_id"],
                        "new_object_name": new_name,
                        "output_policy": step.get("output_policy"),
                        "source_attribute_policy": step.get(
                            "source_attribute_policy", {}
                        ),
                        "extracted_attribute_policy": step.get(
                            "extracted_attribute_policy", {}
                        ),
                        "collection_name": collection_evidence.get("collection_name"),
                        "expected_collection_identity": collection_evidence.get(
                            "expected_collection_identity"
                        ),
                    },
                )
            topology_steps += 1
            capacity += 2
        elif step_type == "collection_create":
            parent = step.get("parent")
            if not isinstance(parent, dict):
                _batch_error("MESH_BATCH_INVALID", "collection_create requires parent")
            if parent.get("type") == "COLLECTION_ALIAS":
                _require_alias(alias_kinds, parent.get("collection_alias"), "collection")
            elif parent.get("type") == "SCENE_ROOT":
                scene_name = parent.get("scene_name")
                scene = (
                    bpy.data.scenes.get(scene_name)
                    if isinstance(scene_name, str)
                    else None
                )
                actual = (
                    {
                        "expected_scene_identity": session_identity("scene", scene),
                        "expected_scene_structure_fingerprint": structure_fingerprint(
                            "scene", scene
                        ),
                    }
                    if scene is not None
                    else None
                )
                expected = {
                    "expected_scene_identity": parent.get("expected_scene_identity"),
                    "expected_scene_structure_fingerprint": parent.get(
                        "expected_scene_structure_fingerprint"
                    ),
                }
                if actual != expected:
                    _batch_error(
                        "MESH_BATCH_TARGET_MISMATCH",
                        f"Scene-root Collection evidence changed: {scene_name}",
                        details={"expected": expected, "actual": actual},
                    )
            else:
                _batch_error("MESH_BATCH_INVALID", "Unsupported collection parent")
            _reserve(alias_kinds, step.get("output_collection_alias"), "collection")
            name = step.get("name")
            if not isinstance(name, str) or name in reserved_collection_names:
                _batch_error(
                    "MESH_BATCH_INVALID",
                    f"Batch Collection output name is not unique: {name}",
                )
            reserved_collection_names.add(name)
            capacity += 1
        elif step_type in {"collection_link_object", "collection_unlink_object"}:
            _require_alias(alias_kinds, step.get("collection_alias"), "collection")
            _require_alias_any(alias_kinds, step.get("object_alias"), ("target", "object"))
            capacity += 1
        elif step_type == "object_parent_set":
            _require_alias_any(alias_kinds, step.get("child_alias"), ("target", "object"))
            _require_alias_any(alias_kinds, step.get("parent_alias"), ("target", "object"))
            capacity += 1
        elif step_type == "object_parent_clear":
            _require_alias_any(alias_kinds, step.get("child_alias"), ("target", "object"))
            _require_alias_any(
                alias_kinds, step.get("expected_parent_alias"), ("target", "object")
            )
            capacity += 1
        elif step_type == "rig_bind":
            _require_alias(alias_kinds, step.get("mesh_target_alias"), "target")
            _require_alias(alias_kinds, step.get("armature_alias"), "armature")
            _reserve(alias_kinds, step.get("output_binding_alias"), "rig_binding")
            capacity += 1
        elif step_type in {"uv_edit", "weights_edit"}:
            _require_alias(alias_kinds, step.get("target_alias"), "target")
            operation = step.get("operation")
            if not isinstance(operation, dict):
                _batch_error("MESH_BATCH_INVALID", f"{step_type} requires operation")
            if operation.get("selection_alias") is not None:
                _require_alias(alias_kinds, operation.get("selection_alias"), "selection")
            capacity += 1
        elif step_type == "attribute_transfer":
            _require_alias(alias_kinds, step.get("source_target_alias"), "target")
            _require_alias(alias_kinds, step.get("target_alias"), "target")
            transfer = step.get("transfer")
            if not isinstance(transfer, dict):
                _batch_error("MESH_BATCH_INVALID", "attribute_transfer requires transfer")
            _require_alias(alias_kinds, transfer.get("target_selection_alias"), "selection")
            capacity += 1
        elif step_type == "mesh_validate":
            _require_alias(alias_kinds, step.get("selection_alias"), "selection")
            if step.get("surface_alias") is not None:
                _require_alias(alias_kinds, step.get("surface_alias"), "surface")
            _reserve(alias_kinds, step.get("output_alias"), "validation")
        else:
            _batch_error("MESH_BATCH_INVALID", f"Unsupported step type: {step_type}")
    if topology_steps > MAX_TOPOLOGY_STEPS:
        _batch_error("MESH_BATCH_BUDGET_EXCEEDED", "A batch may contain at most 8 topology steps")
    if book.component_catalog_count + catalog_steps > MAX_COMPONENT_CATALOGS:
        _batch_error(
            "MESH_BATCH_BUDGET_EXCEEDED",
            "Batch ComponentCatalog outputs would exceed the retained catalog budget",
        )
    transaction.ensure_capacity(capacity)
    return {
        "targets": targets,
        "selections": input_selections,
        "surfaces": input_surfaces,
        "objects": input_objects,
        "armatures": input_armatures,
        "collections": input_collections,
        "catalogs": input_catalogs,
        "alias_kinds": alias_kinds,
        "topology_steps": topology_steps,
        "reserved_deltas": capacity,
    }


def _derive_operation(
    operation: dict[str, Any], selections: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    result = {
        key: value
        for key, value in operation.items()
        if key not in {"selection_alias", "selection_aliases"}
    }
    if operation.get("type") == "combine":
        result["selection_ids"] = [
            selections[str(alias)]["selection_id"] for alias in operation["selection_aliases"]
        ]
    else:
        result["selection_id"] = selections[str(operation["selection_alias"])]["selection_id"]
    return result


def _edit_operation(
    operation: dict[str, Any],
    selections: dict[str, dict[str, Any]],
    surfaces: dict[str, str],
) -> dict[str, Any]:
    result = {
        key: value
        for key, value in operation.items()
        if key not in {"selection_alias", "surface_alias"}
    }
    result["selection_id"] = selections[str(operation["selection_alias"])]["selection_id"]
    if operation.get("surface_alias") is not None:
        result["surface_id"] = surfaces[str(operation["surface_alias"])]
    return result


def _attribute_operation(
    operation: dict[str, Any], selections: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    result = {key: value for key, value in operation.items() if key != "selection_alias"}
    if operation.get("selection_alias") is not None:
        result["selection_id"] = selections[str(operation["selection_alias"])]["selection_id"]
    return result


def _transfer_operation(
    transfer: dict[str, Any], selections: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    result = {key: value for key, value in transfer.items() if key != "target_selection_alias"}
    result["target_selection_id"] = selections[str(transfer["target_selection_alias"])][
        "selection_id"
    ]
    return result


def _rebind_target_selections_same_topology(
    book: MeshResourceBook,
    selections: dict[str, dict[str, Any]],
    target_alias: str,
    obj: Any,
) -> list[dict[str, Any]]:
    mesh = obj.data
    reports = []
    for alias, binding in selections.items():
        if binding["target_alias"] != target_alias:
            continue
        source = book.selection(binding["selection_id"])
        rebound = book.add_selection(
            object_name=obj.name,
            object_identity=session_identity("object", obj),
            mesh_name=mesh.name,
            mesh_identity=session_identity("mesh", mesh),
            mesh_revision_id=mesh_revision_id(mesh),
            mesh_fingerprint=mesh_fingerprint(mesh),
            expected_users=int(mesh.users),
            expected_user_objects=mesh_user_refs(mesh),
            domain=source.domain,
            indices=source.indices,
            weights=source.weights,
            source_query={
                "type": "batch_attribute_rebound",
                "source": source.selection_id,
            },
        )
        binding["selection_id"] = rebound.selection_id
        reports.append({"alias": alias, "selection": rebound.summary()})
    return reports


def _remap_target_selections(
    book: MeshResourceBook,
    selections: dict[str, dict[str, Any]],
    target_alias: str,
    component_map_id: str,
    *,
    destination_alias: str | None = None,
) -> list[dict[str, Any]]:
    reports = []
    for alias, binding in selections.items():
        if binding["target_alias"] != target_alias:
            continue
        result = remap_selection(
            book,
            {
                "selection_id": binding["selection_id"],
                "component_map_id": component_map_id,
                "mode": binding["remap_mode"],
                "weight_merge": binding["weight_merge"],
            },
        )
        binding["selection_id"] = result["selection"]["selection_id"]
        if destination_alias is not None:
            binding["target_alias"] = destination_alias
        reports.append({"alias": alias, **result})
    return reports


def _append_map(
    book: MeshResourceBook,
    target_state: dict[str, Any],
    component_map_id: str,
) -> dict[str, Any] | None:
    target_state["direct_component_map_ids"].append(component_map_id)
    previous = target_state.get("composed_component_map_id")
    ids = (
        [previous, component_map_id] if previous else target_state["direct_component_map_ids"][-2:]
    )
    if len(ids) < 2:
        return None
    result = compose_component_map(book, {"component_map_ids": ids})
    target_state["composed_component_map_id"] = result["component_map"]["component_map_id"]
    target_state["composed_component_map"] = result["component_map"]
    return result["component_map"]


def _assert_validation(result: dict[str, Any], assertions: list[dict[str, Any]]) -> None:
    distances = result.get("distances") if isinstance(result.get("distances"), dict) else {}
    for assertion in assertions:
        kind = assertion.get("type")
        expected = assertion.get("value")
        if kind == "count_at_most":
            actual = result.get("count")
            if actual is None:
                actual = distances.get("count")
            passed = isinstance(actual, int) and actual <= int(expected)
        elif kind == "p95_at_most":
            actual = distances.get("p95")
            passed = actual is not None and float(actual) <= float(expected)
        elif kind == "maximum_at_most":
            actual = distances.get("maximum")
            passed = actual is not None and float(actual) <= float(expected)
        elif kind == "penetration_at_most":
            signed_minimum = distances.get("signed_minimum")
            actual = max(0.0, -float(signed_minimum)) if signed_minimum is not None else None
            passed = actual is not None and actual <= float(expected)
        else:
            actual = result.get("sign_reliable")
            passed = bool(actual) is bool(expected)
        if not passed:
            _batch_error(
                "MESH_BATCH_ASSERTION_FAILED",
                f"Batch validation assertion failed: {kind}",
                details={"assertion": assertion, "actual": actual},
            )


def _step_aliases(step: dict[str, Any]) -> tuple[str, ...]:
    values = []
    for key, value in step.items():
        if key.endswith("alias") and isinstance(value, str):
            values.append(value)
        elif key.endswith("aliases") and isinstance(value, list):
            values.extend(item for item in value if isinstance(item, str))
    return tuple(sorted(set(values)))


def execute_mesh_batch(
    transaction: Transaction,
    book: MeshResourceBook,
    captures: CaptureBook,
    params: dict[str, Any],
) -> dict[str, Any]:
    batch_id = str(uuid.uuid4())
    prepared = _preflight(transaction, book, params)
    targets: dict[str, dict[str, Any]] = prepared["targets"]
    selections: dict[str, dict[str, Any]] = prepared["selections"]
    surfaces: dict[str, str] = prepared["surfaces"]
    objects: dict[str, dict[str, Any]] = prepared["objects"]
    armatures: dict[str, dict[str, Any]] = prepared["armatures"]
    collections: dict[str, dict[str, Any]] = prepared["collections"]
    catalogs: dict[str, dict[str, str]] = prepared["catalogs"]
    maps: dict[str, str] = {}
    validations: dict[str, dict[str, Any]] = {}
    bindings: dict[str, dict[str, Any]] = {}
    resources_before = _resource_counts(book)
    branches = {
        alias: {
            "direct_component_map_ids": [],
            "composed_component_map_id": None,
            "composed_component_map": None,
        }
        for alias in targets
    }
    step_reports: list[dict[str, Any]] = []
    changed = False

    for index, step in enumerate(params["steps"]):
        step_type = str(step["type"])
        try:
            if step_type == "selection_query":
                target_alias = str(step["target_alias"])
                target = targets[target_alias]
                result = query_selection(
                    book,
                    captures,
                    {
                        **_target_params(target),
                        "expected_mesh_revision_id": target["mesh_revision_id"],
                        "domain": step["domain"],
                        "query": step["query"],
                    },
                )
                selections[str(step["output_alias"])] = {
                    "selection_id": result["selection_id"],
                    "target_alias": target_alias,
                    "remap_mode": step.get("remap_mode", "ALL_MAPPED"),
                    "weight_merge": step.get("weight_merge", "MAX"),
                }
                report = result
            elif step_type == "selection_derive":
                operation = _derive_operation(step["operation"], selections)
                result = derive_selection(book, {"operation": operation})
                source_alias = _selection_refs(step["operation"])[0]
                source = selections[source_alias]
                selections[str(step["output_alias"])] = {
                    "selection_id": result["selection_id"],
                    "target_alias": source["target_alias"],
                    "remap_mode": step.get("remap_mode", "ALL_MAPPED"),
                    "weight_merge": step.get("weight_merge", "MAX"),
                }
                report = result
            elif step_type == "mesh_edit":
                target_alias = str(step["target_alias"])
                operation = _edit_operation(step["operation"], selections, surfaces)
                selection_alias = str(step["operation"]["selection_alias"])
                if selections[selection_alias]["target_alias"] != target_alias:
                    _batch_error(
                        "MESH_BATCH_TARGET_MISMATCH",
                        f"Selection {selection_alias} does not belong to {target_alias}",
                    )
                call_params = {
                    **_target_params(targets[target_alias], str(step["data_scope"])),
                    "transaction_id": transaction.transaction_id,
                    "operation": operation,
                }
                operation_type = operation["type"]
                if operation_type in DEFORM_OPERATIONS:
                    result = edit_mesh_deform(transaction, book, captures, call_params)
                elif operation_type in TOPOLOGY_OPERATIONS:
                    result = edit_mesh_topology(transaction, book, call_params)
                else:
                    _batch_error("MESH_BATCH_INVALID", f"Unsupported batch edit: {operation_type}")
                changed = changed or bool(result.get("changed"))
                obj = bpy.data.objects.get(targets[target_alias]["object_name"])
                if obj is None:
                    _batch_error("MESH_BATCH_TARGET_MISMATCH", "Edited target disappeared")
                targets[target_alias] = _live_target(target_alias, obj)
                component_map = result.get("component_map")
                remaps: list[dict[str, Any]] = []
                composed = None
                if isinstance(component_map, dict):
                    component_map_id = str(component_map["component_map_id"])
                    remaps = _remap_target_selections(
                        book, selections, target_alias, component_map_id
                    )
                    composed = _append_map(book, branches[target_alias], component_map_id)
                    if step.get("map_alias") is not None:
                        maps[str(step["map_alias"])] = component_map_id
                elif result.get("rebound_selection") is not None:
                    selections[selection_alias]["selection_id"] = result["rebound_selection"][
                        "selection_id"
                    ]
                created_aliases = step.get("created_selection_aliases") or {}
                created = result.get("created_selections") or {}
                for domain_key, alias in created_aliases.items():
                    domain = {"vertex": "VERTEX", "edge": "EDGE", "face": "FACE"}[domain_key]
                    if alias is not None:
                        if domain not in created:
                            _batch_error(
                                "MESH_BATCH_REFERENCE_NOT_FOUND",
                                f"Mesh edit did not create a {domain} SelectionSet",
                            )
                        selections[str(alias)] = {
                            "selection_id": created[domain]["selection_id"],
                            "target_alias": target_alias,
                            "remap_mode": "ALL_MAPPED",
                            "weight_merge": "MAX",
                        }
                report = {**result, "automatic_remaps": remaps, "composed_component_map": composed}
            elif step_type == "mesh_separate":
                target_alias = str(step["target_alias"])
                selection_alias = str(step["selection_alias"])
                if selections[selection_alias]["target_alias"] != target_alias:
                    _batch_error(
                        "MESH_BATCH_TARGET_MISMATCH",
                        f"Selection {selection_alias} does not belong to {target_alias}",
                    )
                call_params = {
                    **_target_params(targets[target_alias]),
                    "transaction_id": transaction.transaction_id,
                    "selection_id": selections[selection_alias]["selection_id"],
                    "new_object_name": step["new_object_name"],
                    "collection_name": step.get("collection_name"),
                    "expected_collection_identity": step.get("expected_collection_identity"),
                    "source_attribute_policy": step.get("source_attribute_policy", {}),
                    "separated_attribute_policy": step.get("separated_attribute_policy", {}),
                }
                result = separate_mesh(transaction, book, call_params)
                changed = True
                source_map = result["source_component_map"]
                separated_map = result["separated_component_map"]
                source_map_id = str(source_map["component_map_id"])
                separated_map_id = str(separated_map["component_map_id"])
                source_remaps = _remap_target_selections(
                    book, selections, target_alias, source_map_id
                )
                source_obj = bpy.data.objects.get(result["source_object"]["name"])
                separated_obj = bpy.data.objects.get(result["separated_object"]["name"])
                if source_obj is None or separated_obj is None:
                    _batch_error("MESH_SEPARATION_FAILED", "Separated branch objects disappeared")
                targets[target_alias] = _live_target(target_alias, source_obj)
                new_target_alias = str(step["new_target_alias"])
                targets[new_target_alias] = _live_target(new_target_alias, separated_obj)
                prior_chain = list(branches[target_alias]["direct_component_map_ids"])
                branches.setdefault(
                    new_target_alias,
                    {
                        "direct_component_map_ids": prior_chain,
                        "composed_component_map_id": branches[target_alias].get(
                            "composed_component_map_id"
                        ),
                        "composed_component_map": branches[target_alias].get(
                            "composed_component_map"
                        ),
                    },
                )
                source_composed = _append_map(book, branches[target_alias], source_map_id)
                separated_composed = _append_map(book, branches[new_target_alias], separated_map_id)
                maps[str(step["source_map_alias"])] = source_map_id
                maps[str(step["separated_map_alias"])] = separated_map_id
                selections[str(step["new_selection_alias"])] = {
                    "selection_id": result["separated_selection"]["selection_id"],
                    "target_alias": new_target_alias,
                    "remap_mode": "ALL_MAPPED",
                    "weight_merge": "MAX",
                }
                report = {
                    **result,
                    "automatic_source_remaps": source_remaps,
                    "source_composed_component_map": source_composed,
                    "separated_composed_component_map": separated_composed,
                }
            elif step_type == "component_catalog_prepare":
                selection_alias = str(step["selection_alias"])
                selection = selections[selection_alias]
                result = prepare_component_catalog(
                    book,
                    {
                        "selection_id": selection["selection_id"],
                        "include": step.get(
                            "include",
                            ["COUNT", "AREA", "BOUNDS", "MATERIALS", "BOUNDARY_COUNT"],
                        ),
                    },
                )
                catalogs[str(step["output_catalog_alias"])] = {
                    "component_catalog_id": str(result["component_catalog_id"]),
                    "target_alias": str(selection["target_alias"]),
                }
                report = result
            elif step_type == "component_catalog_select":
                catalog_alias = str(step["catalog_alias"])
                catalog = catalogs[catalog_alias]
                result = select_component_catalog(
                    book,
                    {
                        "component_catalog_id": catalog["component_catalog_id"],
                        "component_identities": step["component_identities"],
                    },
                )
                selections[str(step["output_selection_alias"])] = {
                    "selection_id": result["selection_id"],
                    "target_alias": catalog["target_alias"],
                    "remap_mode": step.get("remap_mode", "ALL_MAPPED"),
                    "weight_merge": step.get("weight_merge", "MAX"),
                }
                report = result
            elif step_type == "mesh_materialize":
                source_alias = str(step["source_target_alias"])
                source = targets[source_alias]
                collection_alias = step.get("collection_alias")
                collection_evidence = (
                    collections[str(collection_alias)]
                    if collection_alias is not None
                    else {}
                )
                result = materialize_mesh(
                    transaction,
                    book,
                    {
                        "transaction_id": transaction.transaction_id,
                        "source": {
                            "object_name": source["object_name"],
                            "expected_object_identity": source["expected_object_identity"],
                            "expected_mesh_identity": source["expected_mesh_identity"],
                            "expected_mesh_revision_id": source["mesh_revision_id"],
                        },
                        "evaluation": step["evaluation"],
                        "new_object_name": step["new_object_name"],
                        "copy": step["copy"],
                        "collection_name": collection_evidence.get("collection_name"),
                        "expected_collection_identity": collection_evidence.get(
                            "expected_collection_identity"
                        ),
                    },
                )
                changed = True
                output = bpy.data.objects.get(result["output_object"]["name"])
                if output is None:
                    _batch_error(
                        "MESH_MATERIALIZATION_FAILED", "Materialized batch output disappeared"
                    )
                output_alias = str(step["output_target_alias"])
                targets[output_alias] = _live_target(output_alias, output)
                branches[output_alias] = {
                    "direct_component_map_ids": list(
                        branches[source_alias]["direct_component_map_ids"]
                    ),
                    "composed_component_map_id": branches[source_alias].get(
                        "composed_component_map_id"
                    ),
                    "composed_component_map": branches[source_alias].get(
                        "composed_component_map"
                    ),
                }
                component_map = result.get("component_map")
                composed = None
                automatic_selection_remaps: list[dict[str, Any]] = []
                if isinstance(component_map, dict):
                    component_map_id = str(component_map["component_map_id"])
                    automatic_selection_remaps = _remap_target_selections(
                        book,
                        selections,
                        source_alias,
                        component_map_id,
                        destination_alias=output_alias,
                    )
                    composed = _append_map(book, branches[output_alias], component_map_id)
                    if step.get("map_alias") is not None:
                        maps[str(step["map_alias"])] = component_map_id
                elif step.get("map_alias") is not None:
                    _batch_error(
                        "MESH_BATCH_REFERENCE_NOT_FOUND",
                        "Materialization changed topology and did not produce an exact "
                        "ComponentMap",
                    )
                report = {
                    **result,
                    "automatic_selection_remaps": automatic_selection_remaps,
                    "composed_component_map": composed,
                }
            elif step_type == "mesh_extract":
                target_alias = str(step["target_alias"])
                selection_alias = str(step["selection_alias"])
                if selections[selection_alias]["target_alias"] != target_alias:
                    _batch_error(
                        "MESH_BATCH_TARGET_MISMATCH",
                        f"Selection {selection_alias} does not belong to {target_alias}",
                    )
                collection_alias = step.get("collection_alias")
                collection_evidence = (
                    collections[str(collection_alias)]
                    if collection_alias is not None
                    else {}
                )
                result = extract_mesh(
                    transaction,
                    book,
                    {
                        **_target_params(targets[target_alias]),
                        "transaction_id": transaction.transaction_id,
                        "selection_id": selections[selection_alias]["selection_id"],
                        "new_object_name": step["new_object_name"],
                        "output_policy": step["output_policy"],
                        "source_attribute_policy": step["source_attribute_policy"],
                        "extracted_attribute_policy": step["extracted_attribute_policy"],
                        "collection_name": collection_evidence.get("collection_name"),
                        "expected_collection_identity": collection_evidence.get(
                            "expected_collection_identity"
                        ),
                    },
                )
                changed = True
                source_map = result["source_component_map"]
                extracted_map = result["extracted_component_map"]
                source_map_id = str(source_map["component_map_id"])
                extracted_map_id = str(extracted_map["component_map_id"])
                source_remaps = _remap_target_selections(
                    book, selections, target_alias, source_map_id
                )
                source_obj = bpy.data.objects.get(result["source_object"]["name"])
                extracted_obj = bpy.data.objects.get(result["extracted_object"]["name"])
                if source_obj is None or extracted_obj is None:
                    _batch_error("MESH_EXTRACTION_FAILED", "Extracted batch branches disappeared")
                targets[target_alias] = _live_target(target_alias, source_obj)
                new_target_alias = str(step["new_target_alias"])
                targets[new_target_alias] = _live_target(new_target_alias, extracted_obj)
                prior_chain = list(branches[target_alias]["direct_component_map_ids"])
                branches[new_target_alias] = {
                    "direct_component_map_ids": prior_chain,
                    "composed_component_map_id": branches[target_alias].get(
                        "composed_component_map_id"
                    ),
                    "composed_component_map": branches[target_alias].get(
                        "composed_component_map"
                    ),
                }
                source_composed = _append_map(book, branches[target_alias], source_map_id)
                extracted_composed = _append_map(
                    book, branches[new_target_alias], extracted_map_id
                )
                maps[str(step["source_map_alias"])] = source_map_id
                maps[str(step["extracted_map_alias"])] = extracted_map_id
                selections[str(step["new_selection_alias"])] = {
                    "selection_id": result["extracted_selection"]["selection_id"],
                    "target_alias": new_target_alias,
                    "remap_mode": "ALL_MAPPED",
                    "weight_merge": "MAX",
                }
                report = {
                    **result,
                    "automatic_source_remaps": source_remaps,
                    "source_composed_component_map": source_composed,
                    "extracted_composed_component_map": extracted_composed,
                }
            elif step_type == "collection_create":
                parent = step["parent"]
                if parent["type"] == "COLLECTION_ALIAS":
                    parent_collection = _collection_for_alias(
                        str(parent["collection_alias"]), collections
                    )
                    parent_params = {
                        "type": "COLLECTION",
                        **_live_collection(parent_collection),
                    }
                else:
                    parent_params = parent
                collection, delta = create_collection(
                    transaction,
                    {"name": step["name"], "parent": parent_params},
                )
                transaction.record(delta)
                changed = True
                collection_alias = str(step["output_collection_alias"])
                collections[collection_alias] = _live_collection(collection)
                report = organization_result(
                    transaction, changed=True, collection=collection
                )
            elif step_type in {"collection_link_object", "collection_unlink_object"}:
                collection_alias = str(step["collection_alias"])
                object_alias = str(step["object_alias"])
                collection = _collection_for_alias(collection_alias, collections)
                obj = _object_for_alias(object_alias, targets, objects)
                link_changed, delta, _collection, _obj = change_collection_link(
                    transaction,
                    {
                        **_live_collection(collection),
                        **_live_object(obj),
                    },
                    link=step_type == "collection_link_object",
                )
                if delta is not None:
                    transaction.record(delta)
                changed = changed or link_changed
                collections[collection_alias] = _live_collection(collection)
                if object_alias in targets:
                    targets[object_alias] = _live_target(object_alias, obj)
                else:
                    objects[object_alias] = _live_object(obj)
                report = organization_result(
                    transaction,
                    changed=link_changed,
                    collection=collection,
                    obj=obj,
                )
            elif step_type in {"object_parent_set", "object_parent_clear"}:
                child_alias = str(step["child_alias"])
                child = _object_for_alias(child_alias, targets, objects)
                parent_alias = str(
                    step.get("parent_alias") or step.get("expected_parent_alias")
                )
                parent = _object_for_alias(parent_alias, targets, objects)
                parent_params = {
                    "child_name": child.name,
                    "expected_child_identity": session_identity("object", child),
                    "expected_child_structure_fingerprint": structure_fingerprint(
                        "object", child
                    ),
                    "transform_mode": step["transform_mode"],
                }
                if step_type == "object_parent_set":
                    parent_params.update(
                        {
                            "parent_name": parent.name,
                            "expected_parent_identity": session_identity("object", parent),
                            "expected_parent_structure_fingerprint": structure_fingerprint(
                                "object", parent
                            ),
                        }
                    )
                else:
                    parent_params.update(
                        {
                            "expected_parent_name": parent.name,
                            "expected_parent_identity": session_identity("object", parent),
                            "expected_parent_structure_fingerprint": structure_fingerprint(
                                "object", parent
                            ),
                        }
                    )
                parent_changed, delta, _obj = change_object_parent(
                    transaction,
                    parent_params,
                    clear=step_type == "object_parent_clear",
                )
                if delta is not None:
                    transaction.record(delta)
                changed = changed or parent_changed
                if child_alias in targets:
                    targets[child_alias] = _live_target(child_alias, child)
                else:
                    objects[child_alias] = _live_object(child)
                report = organization_result(
                    transaction, changed=parent_changed, obj=child
                )
            elif step_type == "rig_bind":
                target_alias = str(step["mesh_target_alias"])
                armature_alias = str(step["armature_alias"])
                target = targets[target_alias]
                result = bind_rig(
                    transaction,
                    {
                        "transaction_id": transaction.transaction_id,
                        "mesh_target": {
                            "object_name": target["object_name"],
                            "expected_object_identity": target["expected_object_identity"],
                            "expected_mesh_identity": target["expected_mesh_identity"],
                            "expected_mesh_revision_id": target["mesh_revision_id"],
                            "expected_group_schema_fingerprint": target[
                                "group_schema_fingerprint"
                            ],
                            "expected_weights_fingerprint": target["weights_fingerprint"],
                        },
                        "armature_target": armatures[armature_alias],
                        "modifier": step["modifier"],
                        "parenting": step["parenting"],
                        "group_scope": step["group_scope"],
                    },
                )
                binding_alias = str(step["output_binding_alias"])
                bindings[binding_alias] = result
                changed = changed or bool(result.get("changed"))
                obj = bpy.data.objects.get(target["object_name"])
                if obj is None:
                    _batch_error("RIG_MESH_TARGET_INVALID", "Bound Mesh target disappeared")
                targets[target_alias] = _live_target(target_alias, obj)
                report = result
            elif step_type == "uv_edit":
                target_alias = str(step["target_alias"])
                operation = _attribute_operation(step["operation"], selections)
                selection_alias = step["operation"].get("selection_alias")
                if selection_alias is not None and (
                    selections[str(selection_alias)]["target_alias"] != target_alias
                ):
                    _batch_error(
                        "MESH_BATCH_TARGET_MISMATCH",
                        f"Selection {selection_alias} does not belong to {target_alias}",
                    )
                target = targets[target_alias]
                call_params = {
                    **_target_params(target, str(step["data_scope"])),
                    "transaction_id": transaction.transaction_id,
                    "expected_uv_fingerprint": target["uv_fingerprint"],
                    "operation": operation,
                }
                result = edit_uv(transaction, book, call_params)
                changed = changed or bool(result.get("changed"))
                obj = bpy.data.objects.get(target["object_name"])
                if obj is None:
                    _batch_error("MESH_BATCH_TARGET_MISMATCH", "UV target disappeared")
                targets[target_alias] = _live_target(target_alias, obj)
                remaps = (
                    _rebind_target_selections_same_topology(book, selections, target_alias, obj)
                    if result.get("changed")
                    else []
                )
                report = {**result, "automatic_rebinds": remaps}
            elif step_type == "weights_edit":
                target_alias = str(step["target_alias"])
                operation = _attribute_operation(step["operation"], selections)
                selection_alias = step["operation"].get("selection_alias")
                if selection_alias is not None and (
                    selections[str(selection_alias)]["target_alias"] != target_alias
                ):
                    _batch_error(
                        "MESH_BATCH_TARGET_MISMATCH",
                        f"Selection {selection_alias} does not belong to {target_alias}",
                    )
                target = targets[target_alias]
                call_params = {
                    **_attribute_target(target, data_scope=str(step["data_scope"])),
                    "transaction_id": transaction.transaction_id,
                    "operation": operation,
                }
                result = edit_weights(transaction, book, call_params)
                changed = changed or bool(result.get("changed"))
                obj = bpy.data.objects.get(target["object_name"])
                if obj is None:
                    _batch_error("MESH_BATCH_TARGET_MISMATCH", "Weight target disappeared")
                targets[target_alias] = _live_target(target_alias, obj)
                report = result
            elif step_type == "attribute_transfer":
                source_alias = str(step["source_target_alias"])
                target_alias = str(step["target_alias"])
                transfer = _transfer_operation(step["transfer"], selections)
                selection_alias = str(step["transfer"]["target_selection_alias"])
                if selections[selection_alias]["target_alias"] != target_alias:
                    _batch_error(
                        "MESH_BATCH_TARGET_MISMATCH",
                        f"Selection {selection_alias} does not belong to {target_alias}",
                    )
                source = targets[source_alias]
                target = targets[target_alias]
                result = transfer_attribute(
                    transaction,
                    book,
                    {
                        "transaction_id": transaction.transaction_id,
                        "source": _attribute_target(source),
                        "target": _attribute_target(
                            target, data_scope=str(step["target_data_scope"])
                        ),
                        "transfer": transfer,
                    },
                )
                changed = changed or bool(result.get("changed"))
                obj = bpy.data.objects.get(target["object_name"])
                if obj is None:
                    _batch_error("MESH_BATCH_TARGET_MISMATCH", "Attribute target disappeared")
                targets[target_alias] = _live_target(target_alias, obj)
                remaps = (
                    _rebind_target_selections_same_topology(book, selections, target_alias, obj)
                    if result.get("changed") and transfer["type"] == "UV"
                    else []
                )
                report = {**result, "automatic_rebinds": remaps}
            else:
                selection_alias = str(step["selection_alias"])
                target_alias = selections[selection_alias]["target_alias"]
                target = targets[target_alias]
                surface_alias = step.get("surface_alias")
                validate_params = {
                    "selection_id": selections[selection_alias]["selection_id"],
                    "check": step["check"],
                    "surface_id": (
                        surfaces[str(surface_alias)] if surface_alias is not None else None
                    ),
                    "tolerance": step.get("tolerance", 1e-6),
                    "maximum_distance": step.get("maximum_distance", 1_000_000),
                    "threshold": step.get("threshold"),
                    "sample_limit": step.get("sample_limit", 64),
                    "group_names": step.get("group_names"),
                    "expected_group_schema_fingerprint": target["group_schema_fingerprint"],
                    "expected_weights_fingerprint": target["weights_fingerprint"],
                    "target_total": step.get("target_total", 1.0),
                    "maximum_influences": step.get("maximum_influences", 4),
                }
                result = validate_mesh(book, validate_params)
                _assert_validation(result, step.get("assertions", []))
                validations[str(step["output_alias"])] = result
                report = result
            step_reports.append(
                {"index": index, "type": step_type, "status": "succeeded", "result": report}
            )
        except (AuthoringOperationError, MeshOperationError, MeshResourceError) as exc:
            raise MeshBatchExecutionError(
                cause=exc,
                batch_id=batch_id,
                step_index=index,
                step_type=step_type,
                aliases=_step_aliases(step),
            ) from exc
        except Exception as exc:
            wrapped = MeshOperationError(
                "MESH_BATCH_INVALID",
                f"Batch step failed: {type(exc).__name__}",
                kind="blender_api",
                details={"error_type": type(exc).__name__, "message": str(exc)},
            )
            raise MeshBatchExecutionError(
                cause=wrapped,
                batch_id=batch_id,
                step_index=index,
                step_type=step_type,
                aliases=_step_aliases(step),
            ) from exc

    final_aliases: dict[str, Any] = {}
    final_aliases.update(
        {alias: {"kind": "target", **target} for alias, target in sorted(targets.items())}
    )
    final_aliases.update(
        {alias: {"kind": "selection", **binding} for alias, binding in sorted(selections.items())}
    )
    final_aliases.update(
        {
            alias: {"kind": "surface", "surface_id": value}
            for alias, value in sorted(surfaces.items())
        }
    )
    final_aliases.update(
        {
            alias: {"kind": "component_map", "component_map_id": value}
            for alias, value in sorted(maps.items())
        }
    )
    final_aliases.update(
        {
            alias: {"kind": "validation", "result": value}
            for alias, value in sorted(validations.items())
        }
    )
    final_aliases.update(
        {
            alias: {"kind": "object", **value}
            for alias, value in sorted(objects.items())
            if alias not in final_aliases
        }
    )
    final_aliases.update(
        {
            alias: {"kind": "armature", "target": value}
            for alias, value in sorted(armatures.items())
        }
    )
    final_aliases.update(
        {
            alias: {"kind": "collection", **value}
            for alias, value in sorted(collections.items())
        }
    )
    final_aliases.update(
        {
            alias: {"kind": "component_catalog", **value}
            for alias, value in sorted(catalogs.items())
        }
    )
    final_aliases.update(
        {
            alias: {"kind": "rig_binding", "result": value}
            for alias, value in sorted(bindings.items())
        }
    )
    manifest_objects: dict[str, Any] = {}
    for alias in sorted(set(targets) | set(objects)):
        obj = _object_for_alias(alias, targets, objects)
        manifest_objects[alias] = {
            **object_summary(obj),
            "structure_fingerprint": structure_fingerprint("object", obj),
            "collections_fingerprint": object_collection_fingerprint(obj),
        }
    manifest = {
        "objects": manifest_objects,
        "meshes": {
            alias: target for alias, target in sorted(targets.items())
        },
        "collections": {
            alias: collection_summary(_collection_for_alias(alias, collections))
            for alias in sorted(collections)
        },
        "rig_bindings": bindings,
        "component_maps": maps,
        "selection_sets": selections,
        "component_catalogs": {
            alias: book.component_catalog(value["component_catalog_id"]).summary()
            for alias, value in sorted(catalogs.items())
        },
        "validations": validations,
        "resource_counts": {
            "before": resources_before,
            "after": _resource_counts(book),
        },
    }
    manifest["content_sha256"] = _content_sha256(manifest)
    return {
        "transaction_id": transaction.transaction_id,
        "batch_id": batch_id,
        "changed": changed,
        "step_reports": step_reports,
        "aliases": final_aliases,
        "target_branches": branches,
        "assembly_manifest": manifest,
        "preflight": {
            "target_count": len(prepared["targets"]),
            "step_count": len(params["steps"]),
            "alias_count": len(prepared["alias_kinds"]),
            "topology_steps": prepared["topology_steps"],
            "reserved_deltas": prepared["reserved_deltas"],
        },
    }
