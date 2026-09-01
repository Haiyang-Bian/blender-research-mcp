"""Create guarded independent Mesh objects from explicit source evaluation states."""

from __future__ import annotations

import contextlib
import uuid
from typing import Any

import bpy

from .authoring_ops import AuthoringOperationError, object_summary, require_collection
from .lookdev_ops import session_identity
from .mesh_component_map_model import DOMAINS, ComponentRelation, make_component_map
from .mesh_ops import (
    MAX_EDGES,
    MAX_FACES,
    MAX_LOOPS,
    MAX_VERTICES,
    MeshOperationError,
    _copy_mesh_snapshot,
    _is_protected_attribute,
    mesh_counts,
    mesh_fingerprint,
    mesh_revision_id,
    mesh_user_refs,
    shape_key_state_fingerprint,
    topology_fingerprint,
)
from .mesh_resource_model import MeshResourceBook, MeshResourceError
from .mesh_surface_ops import validate_surface
from .mesh_topology_ops import _map_evidence
from .mesh_uv_ops import uv_fingerprint
from .mesh_weight_ops import (
    _capture_weights,
    _group_schema,
    group_schema_fingerprint,
    weights_fingerprint,
)
from .structural_ops import make_structure_guard
from .transaction_model import StructuralDelta, Transaction


class MeshMaterializationError(MeshOperationError):
    pass


def _source(params: dict[str, Any]) -> tuple[Any, Any, dict[str, Any]]:
    raw = params.get("source")
    if not isinstance(raw, dict):
        raise MeshMaterializationError(
            "MESH_MATERIALIZATION_SOURCE_INVALID", "source must be an exact Mesh target"
        )
    name = raw.get("object_name")
    obj = bpy.data.objects.get(name) if isinstance(name, str) else None
    if obj is None:
        raise MeshMaterializationError(
            "OBJECT_NOT_FOUND", f"Object does not exist: {name}", kind="not_found"
        )
    if obj.type != "MESH" or obj.data is None:
        raise MeshMaterializationError(
            "MESH_OBJECT_UNSUPPORTED", f"Materialization requires a MESH object: {name}"
        )
    mesh = obj.data
    actual = {
        "object_identity": session_identity("object", obj),
        "mesh_identity": session_identity("mesh", mesh),
        "mesh_revision_id": mesh_revision_id(mesh),
    }
    expected = {
        "object_identity": raw.get("expected_object_identity"),
        "mesh_identity": raw.get("expected_mesh_identity"),
        "mesh_revision_id": raw.get("expected_mesh_revision_id"),
    }
    if actual != expected:
        raise MeshMaterializationError(
            "MESH_MATERIALIZATION_EVIDENCE_MISMATCH",
            "Materialization source evidence changed",
            kind="conflict",
            details={"expected": expected, "actual": actual},
        )
    return obj, mesh, raw


def _ensure_budget(mesh: Any) -> None:
    counts = mesh_counts(mesh)
    limits = {
        "vertices": MAX_VERTICES,
        "edges": MAX_EDGES,
        "faces": MAX_FACES,
        "loops": MAX_LOOPS,
    }
    if any(counts[name] > limits[name] for name in limits):
        raise MeshMaterializationError(
            "MESH_BUDGET_EXCEEDED",
            "Materialized Mesh exceeds the bounded geometry budget",
            details={"counts": counts, "limits": limits},
        )


def _temporary_source(source: Any, *, keep_modifiers: bool) -> Any:
    temp = source.copy()
    temp.data = source.data
    temp.name = f".MCP-Materialize-{uuid.uuid4()}"
    if not keep_modifiers:
        for modifier in list(temp.modifiers):
            temp.modifiers.remove(modifier)
    bpy.context.scene.collection.objects.link(temp)
    temp.select_set(False)
    return temp


def _remove_temporary(temp: Any | None) -> None:
    if temp is not None and bpy.data.objects.get(temp.name) is temp:
        bpy.data.objects.remove(temp)


def _bake_mesh(
    source: Any,
    evaluation: dict[str, Any],
    book: MeshResourceBook,
) -> tuple[Any, dict[str, Any]]:
    mode = evaluation.get("type")
    temp = None
    result = None
    try:
        if mode == "BASE":
            result = bpy.data.meshes.new(f".MCP-Base-{uuid.uuid4()}")
            _copy_mesh_snapshot(result, source.data)
            baked = {"shape_keys": False, "armature": False, "modifiers": []}
        elif mode == "SHAPE_KEYS_CURRENT":
            expected = evaluation.get("expected_shape_key_state_fingerprint")
            actual = shape_key_state_fingerprint(source)
            if actual != expected:
                raise MeshMaterializationError(
                    "MESH_MATERIALIZATION_SHAPE_KEY_MISMATCH",
                    "Shape-Key state changed before materialization",
                    kind="conflict",
                    details={"expected": expected, "actual": actual},
                )
            temp = _temporary_source(source, keep_modifiers=False)
            depsgraph = bpy.context.evaluated_depsgraph_get()
            depsgraph.update()
            evaluated = temp.evaluated_get(depsgraph)
            result = bpy.data.meshes.new_from_object(
                evaluated, preserve_all_data_layers=True, depsgraph=depsgraph
            )
            baked = {"shape_keys": True, "armature": False, "modifiers": []}
        elif mode == "FINAL_EVALUATED":
            surface_id = evaluation.get("surface_id")
            if not isinstance(surface_id, str) or not surface_id:
                raise MeshMaterializationError(
                    "MESH_MATERIALIZATION_SURFACE_INVALID",
                    "FINAL_EVALUATED requires a live surface_id",
                )
            surface = book.surface(surface_id)
            if surface.geometry != "EVALUATED" or surface.object_name != source.name:
                raise MeshMaterializationError(
                    "MESH_MATERIALIZATION_SURFACE_INVALID",
                    "SurfaceRef must be EVALUATED and target the exact source object",
                )
            validate_surface(surface)
            depsgraph = bpy.context.evaluated_depsgraph_get()
            evaluated = source.evaluated_get(depsgraph)
            result = bpy.data.meshes.new_from_object(
                evaluated, preserve_all_data_layers=True, depsgraph=depsgraph
            )
            modifier_types = [str(item.type) for item in source.modifiers]
            baked = {
                "shape_keys": source.data.shape_keys is not None,
                "armature": "ARMATURE" in modifier_types,
                "modifiers": [
                    {"name": item.name, "type": str(item.type)} for item in source.modifiers
                ],
                "surface_id": surface_id,
                "surface_fingerprint": surface.evaluated_fingerprint,
            }
        else:
            raise MeshMaterializationError(
                "MESH_MATERIALIZATION_EVALUATION_INVALID",
                f"Unsupported materialization evaluation: {mode}",
            )
        result.name = f"{source.name} Materialized Mesh"
        _ensure_budget(result)
        if result.shape_keys is not None:
            raise MeshMaterializationError(
                "MESH_MATERIALIZATION_OUTPUT_INVALID",
                "Materialized output unexpectedly retained Shape Keys",
            )
        return result, {"type": mode, **baked}
    except Exception:
        if (
            result is not None
            and bpy.data.meshes.get(result.name) is result
            and int(result.users) == 0
        ):
            bpy.data.meshes.remove(result)
        raise
    finally:
        _remove_temporary(temp)


def _clear_weights(obj: Any) -> None:
    vertex_indices = list(range(len(obj.data.vertices)))
    for group in list(obj.vertex_groups):
        if vertex_indices:
            with contextlib.suppress(RuntimeError):
                group.remove(vertex_indices)
        obj.vertex_groups.remove(group)


def _copy_weights(source: Any, target: Any) -> None:
    source_weights = _capture_weights(source.data)
    if len(source_weights) != len(target.data.vertices):
        raise MeshMaterializationError(
            "MESH_MATERIALIZATION_ATTRIBUTE_UNPROVABLE",
            "Weight preservation requires identical source and output vertex counts",
        )
    _clear_weights(target)
    groups = []
    for source_group in source.vertex_groups:
        group = target.vertex_groups.new(name=source_group.name)
        group.lock_weight = bool(source_group.lock_weight)
        groups.append(group)
    for vertex_index, sparse in enumerate(source_weights):
        for group_index, weight in sparse:
            if group_index >= len(groups):
                raise MeshMaterializationError(
                    "MESH_MATERIALIZATION_ATTRIBUTE_UNPROVABLE",
                    "Source weights reference a missing Vertex Group",
                )
            groups[group_index].add([vertex_index], float(weight), "REPLACE")
    if _group_schema(source, identities=False) != _group_schema(target, identities=False):
        raise MeshMaterializationError(
            "MESH_MATERIALIZATION_ATTRIBUTE_UNPROVABLE",
            "Vertex Group schema was not copied exactly",
        )
    if weights_fingerprint(source.data) != weights_fingerprint(target.data):
        raise MeshMaterializationError(
            "MESH_MATERIALIZATION_ATTRIBUTE_UNPROVABLE",
            "Deform weights were not copied exactly",
        )


def _clear_custom_normals(obj: Any) -> bool:
    if not bool(getattr(obj.data, "has_custom_normals", False)):
        return False
    previous_active = bpy.context.view_layer.objects.active
    previous_selected = tuple(bpy.context.selected_objects)
    previous_mode = str(bpy.context.mode)
    try:
        if previous_mode != "OBJECT" and previous_active is not None:
            bpy.ops.object.mode_set(mode="OBJECT")
        for item in tuple(bpy.context.selected_objects):
            item.select_set(False)
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj
        result = bpy.ops.mesh.customdata_custom_splitnormals_clear()
        if "FINISHED" not in result or bool(getattr(obj.data, "has_custom_normals", False)):
            raise MeshMaterializationError(
                "MESH_MATERIALIZATION_ATTRIBUTE_UNPROVABLE",
                "Custom split normals could not be discarded from the independent output",
                kind="blender_api",
                details={"operator_result": sorted(result)},
            )
        return True
    finally:
        with contextlib.suppress(Exception):
            if bpy.context.mode != "OBJECT":
                bpy.ops.object.mode_set(mode="OBJECT")
        for item in tuple(bpy.context.selected_objects):
            item.select_set(False)
        for item in previous_selected:
            if bpy.data.objects.get(item.name) is item:
                item.select_set(True)
        if (
            previous_active is not None
            and bpy.data.objects.get(previous_active.name) is previous_active
        ):
            bpy.context.view_layer.objects.active = previous_active
            if previous_mode != "OBJECT":
                mode = {
                    "EDIT_MESH": "EDIT",
                    "SCULPT": "SCULPT",
                    "VERTEX_PAINT": "VERTEX_PAINT",
                    "WEIGHT_PAINT": "WEIGHT_PAINT",
                    "TEXTURE_PAINT": "TEXTURE_PAINT",
                }.get(previous_mode)
                if mode is not None:
                    with contextlib.suppress(Exception):
                        bpy.ops.object.mode_set(mode=mode)


def _copy_domains(source: Any, target: Any, policy: dict[str, Any]) -> dict[str, Any]:
    source_mesh = source.data
    target_mesh = target.data
    topology_equal = topology_fingerprint(source_mesh) == topology_fingerprint(target_mesh)
    discarded_custom_normals = _clear_custom_normals(target)
    uv_attribute_names = {layer.name for layer in target_mesh.uv_layers}
    discarded_attributes = []
    for color in tuple(target_mesh.color_attributes):
        discarded_attributes.append(str(color.name))
        target_mesh.color_attributes.remove(color)
    for attribute in tuple(target_mesh.attributes):
        if _is_protected_attribute(attribute) and attribute.name not in uv_attribute_names:
            discarded_attributes.append(str(attribute.name))
            target_mesh.attributes.remove(attribute)
    if not bool(policy.get("materials")):
        target_mesh.materials.clear()
        for polygon in target_mesh.polygons:
            polygon.material_index = 0
    elif [item for item in source_mesh.materials] != [item for item in target_mesh.materials]:
        raise MeshMaterializationError(
            "MESH_MATERIALIZATION_ATTRIBUTE_UNPROVABLE",
            "Material slots were not preserved exactly",
        )

    if not bool(policy.get("uv")):
        while target_mesh.uv_layers:
            target_mesh.uv_layers.remove(target_mesh.uv_layers[-1])
    else:
        if not all(
            all(abs(float(value)) <= 1_000_000 for value in item.uv)
            for layer in target_mesh.uv_layers
            for item in layer.data
        ):
            raise MeshMaterializationError(
                "MESH_MATERIALIZATION_ATTRIBUTE_UNPROVABLE",
                "Materialized UV coordinates are not finite and bounded",
            )
        if topology_equal and uv_fingerprint(source_mesh) != uv_fingerprint(target_mesh):
            raise MeshMaterializationError(
                "MESH_MATERIALIZATION_ATTRIBUTE_UNPROVABLE",
                "Topology-identical UV data was not copied exactly",
            )

    if bool(policy.get("weights")):
        if not topology_equal:
            raise MeshMaterializationError(
                "MESH_MATERIALIZATION_ATTRIBUTE_UNPROVABLE",
                "Weight preservation is not exact after topology-changing evaluation",
            )
        _copy_weights(source, target)
    else:
        _clear_weights(target)
    return {
        "discarded_attributes": sorted(set(discarded_attributes)),
        "discarded_custom_normals": discarded_custom_normals,
        "materials": {
            "requested": bool(policy.get("materials")),
            "slots": len(target_mesh.materials),
        },
        "uv": {
            "requested": bool(policy.get("uv")),
            "layers": len(target_mesh.uv_layers),
            "fingerprint": uv_fingerprint(target_mesh),
        },
        "weights": {
            "requested": bool(policy.get("weights")),
            "groups": len(target.vertex_groups),
            "group_schema_fingerprint": group_schema_fingerprint(target),
            "weights_fingerprint": weights_fingerprint(target_mesh),
        },
    }


def _exact_map(
    transaction: Transaction,
    source: Any,
    output: Any,
) -> Any | None:
    if topology_fingerprint(source.data) != topology_fingerprint(output.data):
        return None
    counts = {
        "VERTEX": len(source.data.vertices),
        "EDGE": len(source.data.edges),
        "FACE": len(source.data.polygons),
    }
    relations = {
        domain: tuple(ComponentRelation(index, (index,), "SURVIVED") for index in range(count))
        for domain, count in counts.items()
    }
    empty = {domain: () for domain in DOMAINS}
    return make_component_map(
        transaction_id=transaction.transaction_id,
        operation="materialize",
        before=_map_evidence(source, source.data),
        after=_map_evidence(output, output.data),
        after_users=int(output.data.users),
        after_user_objects=mesh_user_refs(output.data),
        relations=relations,
        created=empty,
        deleted=empty,
        map_kind="MATERIALIZATION",
    )


def materialize_mesh(
    transaction: Transaction,
    book: MeshResourceBook,
    params: dict[str, Any],
) -> dict[str, Any]:
    transaction.ensure_capacity()
    source, source_mesh, _source_raw = _source(params)
    new_name = params.get("new_object_name")
    if not isinstance(new_name, str) or not new_name:
        raise MeshMaterializationError(
            "MESH_MATERIALIZATION_NAME_CONFLICT", "new_object_name must be non-empty"
        )
    if bpy.data.objects.get(new_name) is not None:
        raise MeshMaterializationError(
            "MESH_MATERIALIZATION_NAME_CONFLICT",
            f"An object already uses the exact name: {new_name}",
            kind="conflict",
        )
    collection_name = params.get("collection_name")
    collection_identity = params.get("expected_collection_identity")
    if (collection_name is None) != (collection_identity is None):
        raise MeshMaterializationError(
            "MESH_MATERIALIZATION_COLLECTION_INVALID",
            "collection_name and expected_collection_identity must be supplied together",
        )
    collection = (
        require_collection(str(collection_name), str(collection_identity))
        if collection_name is not None
        else (
            source.users_collection[0]
            if source.users_collection
            else bpy.context.scene.collection
        )
    )
    evaluation = params.get("evaluation")
    copy_policy = params.get("copy")
    if not isinstance(evaluation, dict) or not isinstance(copy_policy, dict):
        raise MeshMaterializationError(
            "MESH_MATERIALIZATION_REQUEST_INVALID", "evaluation and copy must be objects"
        )

    output_mesh = None
    output = None
    component_map = None
    try:
        output_mesh, evaluation_result = _bake_mesh(source, evaluation, book)
        output_mesh.name = f"{new_name} Mesh"
        output = bpy.data.objects.new(new_name, output_mesh)
        output.matrix_world = source.matrix_world.copy()
        output.parent = None
        collection.objects.link(output)
        output.select_set(False)
        copy_result = _copy_domains(source, output, copy_policy)
        if output.parent is not None or len(output.modifiers) or output.data.shape_keys is not None:
            raise MeshMaterializationError(
                "MESH_MATERIALIZATION_OUTPUT_INVALID",
                "Materialized output must have no parent, Modifier, or Shape Key",
            )
        component_map = _exact_map(transaction, source, output)
        if component_map is not None:
            book.add_component_map(component_map)
        delta = StructuralDelta(
            kind="mesh_materialize",
            action="create_resource",
            before=(),
            after=(
                make_structure_guard("object", output),
                make_structure_guard("mesh", output_mesh),
            ),
            payload={
                "resource": output,
                "resource_kind": "object",
                "resource_name": output.name,
                "owned_resources": (("mesh", output_mesh),),
            },
        )
        transaction.record(delta)
    except (MeshOperationError, MeshResourceError, AuthoringOperationError):
        if component_map is not None:
            book.release_component_map(component_map.component_map_id)
        if output is not None and bpy.data.objects.get(output.name) is output:
            bpy.data.objects.remove(output)
        if (
            output_mesh is not None
            and bpy.data.meshes.get(output_mesh.name) is output_mesh
            and int(output_mesh.users) == 0
        ):
            bpy.data.meshes.remove(output_mesh)
        raise
    except Exception as exc:
        if component_map is not None:
            book.release_component_map(component_map.component_map_id)
        if output is not None and bpy.data.objects.get(output.name) is output:
            bpy.data.objects.remove(output)
        if (
            output_mesh is not None
            and bpy.data.meshes.get(output_mesh.name) is output_mesh
            and int(output_mesh.users) == 0
        ):
            bpy.data.meshes.remove(output_mesh)
        raise MeshMaterializationError(
            "MESH_MATERIALIZATION_FAILED",
            f"Mesh materialization failed: {type(exc).__name__}",
            kind="blender_api",
            details={"error_type": type(exc).__name__, "message": str(exc)},
        ) from exc

    assert output is not None and output_mesh is not None
    return {
        "transaction_id": transaction.transaction_id,
        "changed": True,
        "evaluation": evaluation_result,
        "source_object": object_summary(source),
        "output_object": object_summary(output),
        "source_mesh_revision_id": mesh_revision_id(source_mesh),
        "output_mesh_revision_id": mesh_revision_id(output_mesh),
        "source_mesh_fingerprint": mesh_fingerprint(source_mesh),
        "output_mesh_fingerprint": mesh_fingerprint(output_mesh),
        "topology_identical": (
            topology_fingerprint(source_mesh) == topology_fingerprint(output_mesh)
        ),
        "copy": copy_result,
        "component_map": component_map.summary() if component_map is not None else None,
        "delta": {"types": ["object_create", "mesh_materialize"], "recorded": True},
    }
