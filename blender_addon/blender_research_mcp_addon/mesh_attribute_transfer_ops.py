"""Transactional UV and deform-weight transfer between exact Mesh targets."""

from __future__ import annotations

from typing import Any

import bpy
from mathutils import Vector
from mathutils.bvhtree import BVHTree
from mathutils.kdtree import KDTree

from .lookdev_ops import session_identity
from .mesh_component_map_model import component_map_chain_mismatch
from .mesh_ops import (
    MeshEditDelta,
    MeshOperationError,
    _create_guard,
    _remove_new_guard,
    _remove_temporary_mesh,
    _restore_failed_edit,
    _validate_guard,
    mesh_fingerprint,
    mesh_revision_id,
    mesh_user_refs,
    topology_fingerprint,
    validate_mesh_attribute_target,
)
from .mesh_query_ops import validate_selection
from .mesh_resource_model import MeshResourceBook, SelectionRecord
from .mesh_uv_ops import _layer, uv_fingerprint
from .mesh_weight_ops import (
    _capture_weights,
    _create_weight_guard,
    _group,
    _group_schema,
    _restore_call_state,
    _schema_fingerprints,
    _validate_weight_guard,
    group_schema_fingerprint,
    weights_fingerprint,
)
from .structural_ops import refresh_structure_guard_if_present
from .transaction_model import Transaction, WeightEditDelta


class MeshAttributeTransferError(MeshOperationError):
    pass


def _read_source(raw: Any) -> tuple[Any, Any]:
    if not isinstance(raw, dict):
        raise MeshAttributeTransferError(
            "MESH_ATTRIBUTE_TRANSFER_INVALID", "source must be an exact Mesh target"
        )
    object_name = raw.get("object_name")
    if not isinstance(object_name, str) or not object_name:
        raise MeshAttributeTransferError(
            "MESH_ATTRIBUTE_TRANSFER_INVALID", "source.object_name must be non-empty"
        )
    obj = bpy.data.objects.get(object_name)
    if obj is None:
        raise MeshAttributeTransferError(
            "OBJECT_NOT_FOUND", f"Source object does not exist: {object_name}", kind="not_found"
        )
    if obj.type != "MESH" or obj.data is None:
        raise MeshAttributeTransferError(
            "MESH_OBJECT_UNSUPPORTED", "Attribute source must be a MESH object"
        )
    mesh = obj.data
    if session_identity("object", obj) != raw.get("expected_object_identity"):
        raise MeshAttributeTransferError(
            "OBJECT_IDENTITY_MISMATCH", "Source object identity changed", kind="conflict"
        )
    if session_identity("mesh", mesh) != raw.get("expected_mesh_identity"):
        raise MeshAttributeTransferError(
            "MESH_IDENTITY_MISMATCH", "Source Mesh identity changed", kind="conflict"
        )
    if mesh_fingerprint(mesh) != raw.get("expected_mesh_fingerprint"):
        raise MeshAttributeTransferError(
            "MESH_FINGERPRINT_MISMATCH", "Source Mesh fingerprint changed", kind="conflict"
        )
    expected_users = raw.get("expected_mesh_users")
    expected_refs_raw = raw.get("expected_mesh_user_objects")
    if not isinstance(expected_refs_raw, list):
        raise MeshAttributeTransferError("MESH_USER_SET_MISMATCH", "Source Mesh users are invalid")
    expected_refs = tuple(
        sorted(
            (
                str(item.get("object_name")),
                str(item.get("expected_object_identity")),
            )
            for item in expected_refs_raw
            if isinstance(item, dict)
        )
    )
    if int(mesh.users) != expected_users or mesh_user_refs(mesh) != expected_refs:
        raise MeshAttributeTransferError(
            "MESH_USER_SET_MISMATCH", "Source Mesh users changed", kind="conflict"
        )
    return obj, mesh


def _target_selection(
    resources: MeshResourceBook,
    selection_id: Any,
    obj: Any,
    mesh: Any,
    domain: str,
) -> SelectionRecord:
    if not isinstance(selection_id, str):
        raise MeshAttributeTransferError(
            "MESH_ATTRIBUTE_TRANSFER_INVALID", "transfer requires target_selection_id"
        )
    record = resources.selection(selection_id)
    selected_obj, selected_mesh = validate_selection(record)
    if selected_obj is not obj or selected_mesh is not mesh or record.domain != domain:
        raise MeshAttributeTransferError(
            "MESH_ATTRIBUTE_TARGET_MISMATCH",
            f"transfer requires a {domain} SelectionSet on the exact target revision",
        )
    return record


def _source_geometry(obj: Any, mesh: Any, geometry: str) -> tuple[Any, Any | None]:
    if geometry == "BASE":
        return mesh, None
    if geometry != "EVALUATED_DEFORM_ONLY":
        raise MeshAttributeTransferError(
            "MESH_ATTRIBUTE_TRANSFER_INVALID",
            "source_geometry must be BASE or EVALUATED_DEFORM_ONLY",
        )
    evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    evaluated_mesh = evaluated.to_mesh(
        preserve_all_data_layers=True,
        depsgraph=bpy.context.evaluated_depsgraph_get(),
    )
    if topology_fingerprint(evaluated_mesh) != topology_fingerprint(mesh):
        evaluated.to_mesh_clear()
        raise MeshAttributeTransferError(
            "MESH_ATTRIBUTE_EVALUATED_TOPOLOGY_CHANGED",
            "EVALUATED_DEFORM_ONLY source changed Mesh topology",
        )
    return evaluated_mesh, evaluated


def _chain_reverse(
    resources: MeshResourceBook,
    ids: Any,
    source_obj: Any,
    source_mesh: Any,
    target_obj: Any,
    target_mesh: Any,
    domain: str,
) -> dict[int, tuple[int, ...]]:
    if not isinstance(ids, list) or not 1 <= len(ids) <= 8:
        raise MeshAttributeTransferError(
            "MESH_ATTRIBUTE_TRANSFER_INVALID",
            "TOPOLOGY transfer across revisions requires 1-8 ComponentMaps",
        )
    records = tuple(resources.component_map(str(item)) for item in ids)
    mismatch = component_map_chain_mismatch(records)
    if mismatch is not None:
        raise MeshAttributeTransferError(
            "MESH_COMPONENT_MAP_CHAIN_INVALID",
            "Attribute transfer ComponentMap chain is not continuous",
        )
    first, last = records[0], records[-1]
    if (
        first.before_object_identity != session_identity("object", source_obj)
        or first.before_mesh_identity != session_identity("mesh", source_mesh)
        or last.after_object_identity != session_identity("object", target_obj)
        or last.after_mesh_identity != session_identity("mesh", target_mesh)
        or last.after_mesh_fingerprint != mesh_fingerprint(target_mesh)
    ):
        raise MeshAttributeTransferError(
            "MESH_COMPONENT_MAP_CHAIN_INVALID",
            "Attribute transfer ComponentMap endpoints do not match source and target",
        )
    mapping = {
        relation.source_index: set(relation.target_indices)
        for relation in records[0].relations.get(domain, ())
    }
    for record in records[1:]:
        rows = {
            relation.source_index: relation.target_indices
            for relation in record.relations.get(domain, ())
        }
        mapping = {
            source: {
                target for intermediate in intermediates for target in rows.get(intermediate, ())
            }
            for source, intermediates in mapping.items()
        }
    reverse: dict[int, list[int]] = {}
    for source, targets in mapping.items():
        for target in targets:
            reverse.setdefault(target, []).append(source)
    return {target: tuple(sorted(values)) for target, values in reverse.items()}


def _barycentric(point: Vector, a: Vector, b: Vector, c: Vector) -> tuple[float, float, float]:
    first = b - a
    second = c - a
    relative = point - a
    d00 = first.dot(first)
    d01 = first.dot(second)
    d11 = second.dot(second)
    d20 = relative.dot(first)
    d21 = relative.dot(second)
    denominator = d00 * d11 - d01 * d01
    if abs(denominator) <= 1e-20:
        return (1.0, 0.0, 0.0)
    second_weight = (d11 * d20 - d01 * d21) / denominator
    third_weight = (d00 * d21 - d01 * d20) / denominator
    return (1.0 - second_weight - third_weight, second_weight, third_weight)


def _surface_data(obj: Any, mesh: Any) -> tuple[Any, tuple[tuple[int, int, int], ...], BVHTree]:
    mesh.calc_loop_triangles()
    vertices = tuple(obj.matrix_world @ vertex.co for vertex in mesh.vertices)
    triangles = tuple(
        tuple(int(value) for value in triangle.vertices) for triangle in mesh.loop_triangles
    )
    bvh = BVHTree.FromPolygons(vertices, triangles, all_triangles=True)
    return vertices, triangles, bvh


def _nearest_vertex_map(source_obj: Any, source_mesh: Any) -> tuple[KDTree, tuple[Vector, ...]]:
    positions = tuple(source_obj.matrix_world @ vertex.co for vertex in source_mesh.vertices)
    tree = KDTree(len(positions))
    for index, point in enumerate(positions):
        tree.insert(point, index)
    tree.balance()
    return tree, positions


def _rebound_selection(
    resources: MeshResourceBook,
    record: SelectionRecord,
    obj: Any,
    mesh: Any,
) -> dict[str, Any]:
    rebound = resources.add_selection(
        object_name=obj.name,
        object_identity=session_identity("object", obj),
        mesh_name=mesh.name,
        mesh_identity=session_identity("mesh", mesh),
        mesh_revision_id=mesh_revision_id(mesh),
        mesh_fingerprint=mesh_fingerprint(mesh),
        expected_users=int(mesh.users),
        expected_user_objects=mesh_user_refs(mesh),
        domain=record.domain,
        indices=record.indices,
        weights=record.weights,
        source_query={"type": "attribute_transfer_rebound", "source": record.selection_id},
    )
    return rebound.summary()


def _uv_source_assignments(
    resources: MeshResourceBook,
    source_obj: Any,
    source_base_mesh: Any,
    source_mesh: Any,
    source_layer: Any,
    target_obj: Any,
    target_mesh: Any,
    target_record: SelectionRecord,
    transfer: dict[str, Any],
) -> tuple[dict[int, tuple[float, float]], list[float], int]:
    mapping = transfer.get("mapping")
    maximum = float(transfer.get("maximum_distance", 0))
    assignments: dict[int, tuple[float, float]] = {}
    distances: list[float] = []
    misses = 0
    if mapping == "TOPOLOGY":
        face_reverse = vertex_reverse = None
        if topology_fingerprint(source_base_mesh) != topology_fingerprint(target_mesh):
            face_reverse = _chain_reverse(
                resources,
                transfer.get("component_map_ids"),
                source_obj,
                source_base_mesh,
                target_obj,
                target_mesh,
                "FACE",
            )
            vertex_reverse = _chain_reverse(
                resources,
                transfer.get("component_map_ids"),
                source_obj,
                source_base_mesh,
                target_obj,
                target_mesh,
                "VERTEX",
            )
        for target_face_index in target_record.indices:
            source_faces = (
                (target_face_index,)
                if face_reverse is None
                else face_reverse.get(target_face_index, ())
            )
            if len(source_faces) != 1:
                misses += int(target_mesh.polygons[target_face_index].loop_total)
                continue
            source_face = source_mesh.polygons[source_faces[0]]
            source_loops = range(
                int(source_face.loop_start),
                int(source_face.loop_start + source_face.loop_total),
            )
            by_vertex = {int(source_mesh.loops[loop].vertex_index): loop for loop in source_loops}
            target_face = target_mesh.polygons[target_face_index]
            for target_loop in range(
                int(target_face.loop_start),
                int(target_face.loop_start + target_face.loop_total),
            ):
                target_vertex = int(target_mesh.loops[target_loop].vertex_index)
                source_vertices = (
                    (target_vertex,)
                    if vertex_reverse is None
                    else vertex_reverse.get(target_vertex, ())
                )
                if len(source_vertices) != 1 or source_vertices[0] not in by_vertex:
                    misses += 1
                    continue
                uv = source_layer.data[by_vertex[source_vertices[0]]].uv
                assignments[target_loop] = (float(uv[0]), float(uv[1]))
                distances.append(0.0)
        return assignments, distances, misses
    if mapping != "NEAREST_SURFACE":
        raise MeshAttributeTransferError(
            "MESH_ATTRIBUTE_TRANSFER_INVALID", "UV mapping must be TOPOLOGY or NEAREST_SURFACE"
        )
    source_vertices, _triangles, bvh = _surface_data(source_obj, source_mesh)
    source_mesh.calc_loop_triangles()
    for face_index in target_record.indices:
        face = target_mesh.polygons[face_index]
        for target_loop in range(int(face.loop_start), int(face.loop_start + face.loop_total)):
            target_vertex = int(target_mesh.loops[target_loop].vertex_index)
            point = target_obj.matrix_world @ target_mesh.vertices[target_vertex].co
            location, _normal, triangle_index, distance = bvh.find_nearest(point, maximum)
            if location is None or triangle_index is None or distance is None:
                misses += 1
                continue
            triangle = source_mesh.loop_triangles[int(triangle_index)]
            vertex_indices = tuple(int(value) for value in triangle.vertices)
            weights = _barycentric(
                location,
                source_vertices[vertex_indices[0]],
                source_vertices[vertex_indices[1]],
                source_vertices[vertex_indices[2]],
            )
            loops = tuple(int(value) for value in triangle.loops)
            uv = sum(
                (
                    source_layer.data[loop].uv * weight
                    for loop, weight in zip(loops, weights, strict=True)
                ),
                Vector((0.0, 0.0)),
            )
            assignments[target_loop] = (float(uv[0]), float(uv[1]))
            distances.append(float(distance))
    return assignments, distances, misses


def _weight_source_values(
    resources: MeshResourceBook,
    source_obj: Any,
    source_base_mesh: Any,
    source_mesh: Any,
    source_group_indices: tuple[int, ...],
    target_obj: Any,
    target_mesh: Any,
    target_record: SelectionRecord,
    transfer: dict[str, Any],
) -> tuple[dict[int, tuple[float, ...]], list[float], int]:
    mapping = transfer.get("mapping")
    maximum = float(transfer.get("maximum_distance", 0))
    source_weights = _capture_weights(source_mesh)
    sparse = [dict(values) for values in source_weights]
    result: dict[int, tuple[float, ...]] = {}
    distances: list[float] = []
    misses = 0
    reverse = None
    if mapping == "TOPOLOGY" and topology_fingerprint(source_base_mesh) != topology_fingerprint(
        target_mesh
    ):
        reverse = _chain_reverse(
            resources,
            transfer.get("component_map_ids"),
            source_obj,
            source_base_mesh,
            target_obj,
            target_mesh,
            "VERTEX",
        )
    kd_tree = None
    if mapping == "NEAREST_VERTEX":
        kd_tree, _positions = _nearest_vertex_map(source_obj, source_mesh)
    surface = None
    if mapping == "NEAREST_SURFACE":
        surface = _surface_data(source_obj, source_mesh)
        source_mesh.calc_loop_triangles()
    if mapping not in {"TOPOLOGY", "NEAREST_VERTEX", "NEAREST_SURFACE"}:
        raise MeshAttributeTransferError(
            "MESH_ATTRIBUTE_TRANSFER_INVALID", "Unsupported WEIGHTS mapping"
        )
    for target_index in target_record.indices:
        target_point = target_obj.matrix_world @ target_mesh.vertices[target_index].co
        if mapping == "TOPOLOGY":
            sources = (target_index,) if reverse is None else reverse.get(target_index, ())
            if len(sources) != 1:
                misses += 1
                continue
            source_index = sources[0]
            values = tuple(sparse[source_index].get(index, 0.0) for index in source_group_indices)
            distance = 0.0
        elif mapping == "NEAREST_VERTEX":
            assert kd_tree is not None
            _location, source_index, distance = kd_tree.find(target_point)
            if distance > maximum:
                misses += 1
                continue
            values = tuple(
                sparse[int(source_index)].get(index, 0.0) for index in source_group_indices
            )
        else:
            assert surface is not None
            source_vertices, _triangles, bvh = surface
            location, _normal, triangle_index, distance = bvh.find_nearest(target_point, maximum)
            if location is None or triangle_index is None or distance is None:
                misses += 1
                continue
            triangle = source_mesh.loop_triangles[int(triangle_index)]
            vertices = tuple(int(value) for value in triangle.vertices)
            barycentric = _barycentric(
                location,
                source_vertices[vertices[0]],
                source_vertices[vertices[1]],
                source_vertices[vertices[2]],
            )
            values = tuple(
                sum(
                    sparse[vertex].get(group_index, 0.0) * weight
                    for vertex, weight in zip(vertices, barycentric, strict=True)
                )
                for group_index in source_group_indices
            )
        result[target_index] = values
        distances.append(float(distance))
    return result, distances, misses


def _distance_summary(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"minimum": None, "maximum": None, "mean": None}
    return {
        "minimum": min(values),
        "maximum": max(values),
        "mean": sum(values) / len(values),
    }


def _transfer_uv(
    transaction: Transaction,
    resources: MeshResourceBook,
    source_obj: Any,
    source_base_mesh: Any,
    source_mesh: Any,
    target_obj: Any,
    target_initial_mesh: Any,
    data_scope: str,
    transfer: dict[str, Any],
) -> dict[str, Any]:
    source_layer_raw = transfer.get("source_layer")
    if not isinstance(source_layer_raw, dict):
        raise MeshAttributeTransferError(
            "MESH_ATTRIBUTE_TRANSFER_INVALID", "UV transfer requires source_layer"
        )
    source_layer = _layer(
        source_base_mesh,
        str(source_layer_raw.get("layer_name", "")),
        str(source_layer_raw.get("expected_layer_identity", "")),
    )
    evaluated_source_layer = source_mesh.uv_layers.get(source_layer.name)
    if evaluated_source_layer is None:
        raise MeshAttributeTransferError(
            "MESH_UV_LAYER_NOT_FOUND", "Evaluated source does not retain the UV layer"
        )
    target_record = _target_selection(
        resources,
        transfer.get("target_selection_id"),
        target_obj,
        target_initial_mesh,
        "FACE",
    )
    assignments, distances, misses = _uv_source_assignments(
        resources,
        source_obj,
        source_base_mesh,
        source_mesh,
        evaluated_source_layer,
        target_obj,
        target_initial_mesh,
        target_record,
        transfer,
    )
    if misses and transfer.get("on_miss", "ERROR") == "ERROR":
        raise MeshAttributeTransferError(
            "MESH_ATTRIBUTE_TRANSFER_MISS",
            "UV transfer did not match every target corner",
            details={"misses": misses},
        )
    transaction.ensure_capacity()
    mesh_guard = transaction.mesh_snapshot_guard(
        target_initial_mesh.name, session_identity("mesh", target_initial_mesh)
    )
    new_guard = mesh_guard is None
    if mesh_guard is None:
        mesh_guard = _create_guard(transaction, target_obj, target_initial_mesh, data_scope)
    else:
        _validate_guard(mesh_guard)
    target_mesh = bpy.data.meshes.get(mesh_guard.mesh_name)
    if target_mesh is None:
        raise MeshAttributeTransferError(
            "MESH_ATTRIBUTE_DATA_CONFLICT", "Guarded target Mesh is missing", kind="conflict"
        )
    layer_name = transfer.get("target_layer_name")
    if not isinstance(layer_name, str) or not layer_name:
        raise MeshAttributeTransferError(
            "MESH_ATTRIBUTE_TRANSFER_INVALID", "target_layer_name must be non-empty"
        )
    expected_identity = transfer.get("expected_target_layer_identity")
    initial_layer = target_initial_mesh.uv_layers.get(layer_name)
    if initial_layer is None:
        if expected_identity is not None:
            raise MeshAttributeTransferError(
                "MESH_UV_LAYER_IDENTITY_MISMATCH",
                "Target UV layer no longer exists",
                kind="conflict",
            )
    elif (
        expected_identity is None
        or session_identity("uv_layer", initial_layer) != expected_identity
    ):
        raise MeshAttributeTransferError(
            "MESH_UV_LAYER_IDENTITY_MISMATCH", "Target UV layer identity changed", kind="conflict"
        )
    before_mesh = mesh_fingerprint(target_mesh)
    before_uv = uv_fingerprint(target_mesh)
    call_snapshot = target_mesh.copy()
    call_snapshot.name = f"{target_mesh.name}.MCP-Attribute-Call-Snapshot"
    try:
        target_layer = target_mesh.uv_layers.get(layer_name)
        if target_layer is None:
            target_layer = target_mesh.uv_layers.new(name=layer_name, do_init=False)
        for loop_index, uv in assignments.items():
            target_layer.data[loop_index].uv = uv
        target_mesh.update()
    except Exception as exc:
        _restore_failed_edit(target_mesh, call_snapshot, before_mesh, exc)
        if new_guard:
            _remove_new_guard(transaction, mesh_guard)
        raise MeshAttributeTransferError(
            "MESH_ATTRIBUTE_TRANSFER_FAILED", "UV transfer application failed", kind="blender_api"
        ) from exc
    finally:
        _remove_temporary_mesh(call_snapshot)
    after_mesh = mesh_fingerprint(target_mesh)
    after_uv = uv_fingerprint(target_mesh)
    changed = after_mesh != before_mesh
    if not changed:
        if new_guard:
            _remove_new_guard(transaction, mesh_guard)
    else:
        mesh_guard.expected_fingerprint = after_mesh
        mesh_guard.expected_users = int(target_mesh.users)
        mesh_guard.expected_user_objects = mesh_user_refs(target_mesh)
        transaction.record(
            MeshEditDelta(
                object_name=target_obj.name,
                object_identity=session_identity("object", target_obj),
                mesh_name=target_mesh.name,
                mesh_identity=session_identity("mesh", target_mesh),
                operation="attribute_transfer.UV",
                before_fingerprint=before_mesh,
                after_fingerprint=after_mesh,
                data_scope=data_scope,
            )
        )
        refresh_structure_guard_if_present(transaction, "object", target_obj)
        refresh_structure_guard_if_present(transaction, "mesh", target_mesh)
    return {
        "changed": changed,
        "before_mesh_fingerprint": before_mesh,
        "after_mesh_fingerprint": after_mesh,
        "before_uv_fingerprint": before_uv,
        "after_uv_fingerprint": after_uv,
        "created_layer": expected_identity is None,
        "target_layer_name": layer_name,
        "hits": len(assignments),
        "misses": misses,
        "distances": _distance_summary(distances),
        "rebound_selection": _rebound_selection(resources, target_record, target_obj, target_mesh),
    }


def _transfer_weights(
    transaction: Transaction,
    resources: MeshResourceBook,
    source_obj: Any,
    source_base_mesh: Any,
    source_mesh: Any,
    target_obj: Any,
    target_initial_mesh: Any,
    data_scope: str,
    source_raw: dict[str, Any],
    target_raw: dict[str, Any],
    transfer: dict[str, Any],
) -> dict[str, Any]:
    for raw, obj, mesh, label in (
        (source_raw, source_obj, source_base_mesh, "source"),
        (target_raw, target_obj, target_initial_mesh, "target"),
    ):
        if raw.get("expected_group_schema_fingerprint") != group_schema_fingerprint(obj):
            raise MeshAttributeTransferError(
                "MESH_WEIGHT_SCHEMA_FINGERPRINT_MISMATCH",
                f"{label} Group schema changed",
                kind="conflict",
            )
        if raw.get("expected_weights_fingerprint") != weights_fingerprint(mesh):
            raise MeshAttributeTransferError(
                "MESH_WEIGHT_FINGERPRINT_MISMATCH", f"{label} weights changed", kind="conflict"
            )
    mappings = transfer.get("groups")
    if not isinstance(mappings, list) or not mappings:
        raise MeshAttributeTransferError(
            "MESH_ATTRIBUTE_TRANSFER_INVALID", "WEIGHTS transfer requires Group mappings"
        )
    source_groups = []
    target_names = []
    for mapping in mappings:
        if not isinstance(mapping, dict):
            raise MeshAttributeTransferError(
                "MESH_ATTRIBUTE_TRANSFER_INVALID", "Group mappings must be objects"
            )
        source_groups.append(_group(source_obj, mapping.get("source"), allow_locked=True))
        name = mapping.get("target_group_name")
        if not isinstance(name, str) or not name:
            raise MeshAttributeTransferError(
                "MESH_ATTRIBUTE_TRANSFER_INVALID", "target_group_name must be non-empty"
            )
        target_names.append(name)
    if len(set(target_names)) != len(target_names):
        raise MeshAttributeTransferError(
            "MESH_ATTRIBUTE_TRANSFER_INVALID", "Target Group names must be unique"
        )
    target_record = _target_selection(
        resources,
        transfer.get("target_selection_id"),
        target_obj,
        target_initial_mesh,
        "VERTEX",
    )
    values, distances, misses = _weight_source_values(
        resources,
        source_obj,
        source_base_mesh,
        source_mesh,
        tuple(int(group.index) for group in source_groups),
        target_obj,
        target_initial_mesh,
        target_record,
        transfer,
    )
    if misses and transfer.get("on_miss", "ERROR") == "ERROR":
        raise MeshAttributeTransferError(
            "MESH_ATTRIBUTE_TRANSFER_MISS",
            "Weight transfer did not match every target vertex",
            details={"misses": misses},
        )
    transaction.ensure_capacity()
    mesh_guard = transaction.mesh_snapshot_guard(
        target_initial_mesh.name, session_identity("mesh", target_initial_mesh)
    )
    new_mesh_guard = mesh_guard is None
    if mesh_guard is None:
        mesh_guard = _create_guard(transaction, target_obj, target_initial_mesh, data_scope)
    else:
        _validate_guard(mesh_guard)
    target_mesh = bpy.data.meshes.get(mesh_guard.mesh_name)
    if target_mesh is None:
        raise MeshAttributeTransferError(
            "MESH_ATTRIBUTE_DATA_CONFLICT", "Guarded target Mesh is missing", kind="conflict"
        )
    weight_guard = transaction.weight_snapshot_guard(
        target_mesh.name, session_identity("mesh", target_mesh)
    )
    new_weight_guard = weight_guard is None
    if weight_guard is None:
        weight_guard = _create_weight_guard(transaction, target_obj, target_mesh, data_scope)
    else:
        _validate_weight_guard(weight_guard)
    objects = tuple(bpy.data.objects[name] for name in weight_guard.object_identities)
    call_schemas = {item.name: _group_schema(item, identities=False) for item in objects}
    call_identities = {item.name: session_identity("object", item) for item in objects}
    call_weights = _capture_weights(target_mesh)
    before_schema = group_schema_fingerprint(target_obj)
    before_weights = weights_fingerprint(target_mesh)
    target_groups = []
    try:
        for name in target_names:
            group = target_obj.vertex_groups.get(name)
            if group is None:
                for item in objects:
                    item.vertex_groups.new(name=name)
                group = target_obj.vertex_groups.get(name)
            if group is None:
                raise MeshAttributeTransferError(
                    "MESH_WEIGHT_GROUP_NOT_FOUND", f"Could not create target Group: {name}"
                )
            target_groups.append(group)
        for vertex_index, submitted in values.items():
            for group, weight in zip(target_groups, submitted, strict=True):
                if weight <= 0:
                    group.remove([vertex_index])
                else:
                    group.add([vertex_index], min(1.0, max(0.0, weight)), "REPLACE")
    except Exception as exc:
        _restore_call_state(target_mesh, call_identities, call_schemas, call_weights, exc)
        if new_weight_guard:
            transaction.remove_weight_snapshot_guard(weight_guard)
        if new_mesh_guard:
            _remove_new_guard(transaction, mesh_guard)
        raise MeshAttributeTransferError(
            "MESH_ATTRIBUTE_TRANSFER_FAILED",
            "Weight transfer application failed",
            kind="blender_api",
        ) from exc
    after_schema = group_schema_fingerprint(target_obj)
    after_weights = weights_fingerprint(target_mesh)
    changed = before_schema != after_schema or before_weights != after_weights
    if not changed:
        if new_weight_guard:
            transaction.remove_weight_snapshot_guard(weight_guard)
        if new_mesh_guard:
            _remove_new_guard(transaction, mesh_guard)
    else:
        mesh_guard.expected_fingerprint = mesh_fingerprint(target_mesh)
        weight_guard.expected_schema_fingerprints = _schema_fingerprints(objects)
        weight_guard.expected_weights_fingerprint = after_weights
        transaction.record(
            WeightEditDelta(
                object_name=target_obj.name,
                object_identity=session_identity("object", target_obj),
                mesh_name=target_mesh.name,
                mesh_identity=session_identity("mesh", target_mesh),
                operation="attribute_transfer.WEIGHTS",
                before_schema_fingerprint=before_schema,
                after_schema_fingerprint=after_schema,
                before_weights_fingerprint=before_weights,
                after_weights_fingerprint=after_weights,
                data_scope=data_scope,
            )
        )
        for item in objects:
            refresh_structure_guard_if_present(transaction, "object", item)
        refresh_structure_guard_if_present(transaction, "mesh", target_mesh)
    return {
        "changed": changed,
        "before_group_schema_fingerprint": before_schema,
        "after_group_schema_fingerprint": after_schema,
        "before_weights_fingerprint": before_weights,
        "after_weights_fingerprint": after_weights,
        "groups": target_names,
        "hits": len(values),
        "misses": misses,
        "distances": _distance_summary(distances),
        "rebound_selection": _rebound_selection(resources, target_record, target_obj, target_mesh),
    }


def transfer_attribute(
    transaction: Transaction,
    resources: MeshResourceBook,
    params: dict[str, Any],
) -> dict[str, Any]:
    source_raw = params.get("source")
    target_raw = params.get("target")
    transfer = params.get("transfer")
    if (
        not isinstance(source_raw, dict)
        or not isinstance(target_raw, dict)
        or not isinstance(transfer, dict)
    ):
        raise MeshAttributeTransferError(
            "MESH_ATTRIBUTE_TRANSFER_INVALID", "source, target, and transfer are required"
        )
    source_obj, source_base_mesh = _read_source(source_raw)
    target_obj, target_initial_mesh, data_scope, _refs = validate_mesh_attribute_target(target_raw)
    source_mesh, evaluated_owner = _source_geometry(
        source_obj, source_base_mesh, str(transfer.get("source_geometry", "BASE"))
    )
    try:
        transfer_type = transfer.get("type")
        if transfer_type == "UV":
            result = _transfer_uv(
                transaction,
                resources,
                source_obj,
                source_base_mesh,
                source_mesh,
                target_obj,
                target_initial_mesh,
                data_scope,
                transfer,
            )
        elif transfer_type == "WEIGHTS":
            result = _transfer_weights(
                transaction,
                resources,
                source_obj,
                source_base_mesh,
                source_mesh,
                target_obj,
                target_initial_mesh,
                data_scope,
                source_raw,
                target_raw,
                transfer,
            )
        else:
            raise MeshAttributeTransferError(
                "MESH_ATTRIBUTE_TRANSFER_INVALID", f"Unsupported transfer type: {transfer_type}"
            )
    finally:
        if evaluated_owner is not None:
            evaluated_owner.to_mesh_clear()
    return {
        "transaction_id": transaction.transaction_id,
        "transfer": str(transfer.get("type")),
        "mapping": str(transfer.get("mapping")),
        "source_geometry": str(transfer.get("source_geometry", "BASE")),
        "source": {
            "object_name": source_obj.name,
            "object_identity": session_identity("object", source_obj),
            "mesh_name": source_base_mesh.name,
            "mesh_identity": session_identity("mesh", source_base_mesh),
        },
        "target": {
            "object_name": target_obj.name,
            "object_identity": session_identity("object", target_obj),
            "mesh_name": target_obj.data.name,
            "mesh_identity": session_identity("mesh", target_obj.data),
        },
        **result,
        "warnings": [],
    }
