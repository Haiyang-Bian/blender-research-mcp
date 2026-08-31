"""Transactionally separate one connected face region into an object branch."""

from __future__ import annotations

import contextlib
import uuid
from typing import Any

import bmesh
import bpy

from .authoring_ops import AuthoringOperationError, duplicate_object, object_summary
from .lookdev_ops import session_identity
from .mesh_component_map import remap_selection
from .mesh_component_map_model import DOMAINS, ComponentMapRecord, make_component_map
from .mesh_ops import (
    MAX_EDGES,
    MAX_FACES,
    MAX_LOOPS,
    MAX_VERTICES,
    MeshOperationError,
    _create_guard,
    _mesh_reference,
    _remove_new_guard,
    _remove_temporary_mesh,
    _restore_failed_edit,
    _validate_guard,
    _validate_mesh_target,
    finish_topology_attributes,
    mesh_counts,
    mesh_fingerprint,
    mesh_revision_id,
    mesh_user_refs,
    prepare_topology_attributes,
    topology_fingerprint,
    unsupported_attributes,
)
from .mesh_query_ops import validate_selection
from .mesh_resource_model import MeshResourceBook, MeshResourceError, SelectionRecord
from .mesh_separation_model import face_region_component_count
from .mesh_topology_ops import (
    _attribute_signature,
    _finish_lineage,
    _map_evidence,
    _start_lineage,
)
from .structural_ops import make_structure_guard, refresh_structure_guard_if_present
from .transaction_model import MeshEditDelta, MeshSnapshotGuard, StructuralDelta, Transaction


def _selection(
    book: MeshResourceBook,
    selection_id: Any,
    obj: Any,
    mesh: Any,
) -> SelectionRecord:
    if not isinstance(selection_id, str) or not selection_id:
        raise MeshOperationError(
            "MESH_SEPARATION_SELECTION_INVALID",
            "selection_id must identify one FACE SelectionSet",
        )
    selection = book.selection(selection_id)
    selected_obj, selected_mesh = validate_selection(selection)
    if selected_obj is not obj or selected_mesh is not mesh or selection.domain != "FACE":
        raise MeshOperationError(
            "MESH_SEPARATION_SELECTION_INVALID",
            "SelectionSet must target the exact source Mesh in FACE domain",
        )
    if not selection.indices or len(selection.indices) >= len(mesh.polygons):
        raise MeshOperationError(
            "MESH_SEPARATION_EMPTY_SOURCE",
            "Separation requires a non-empty proper subset of source faces",
        )
    return selection


def _validate_connected(mesh: Any, indices: tuple[int, ...]) -> None:
    face_edges = tuple(
        tuple(
            int(mesh.loops[loop_index].edge_index)
            for loop_index in range(
                int(polygon.loop_start),
                int(polygon.loop_start) + int(polygon.loop_total),
            )
        )
        for polygon in mesh.polygons
    )
    try:
        component_count = face_region_component_count(face_edges, indices)
    except ValueError as exc:
        raise MeshOperationError(
            "MESH_SEPARATION_SELECTION_INVALID",
            "SelectionSet contains a face outside the current Mesh",
        ) from exc
    if component_count != 1:
        raise MeshOperationError(
            "MESH_SEPARATION_DISCONNECTED",
            "FACE SelectionSet must contain exactly one edge-connected region",
            details={
                "selected_faces": len(indices),
                "connected_components": component_count,
            },
        )


def _validate_separation_attributes(obj: Any, mesh: Any) -> None:
    if bool(getattr(mesh, "has_custom_normals", False)):
        raise MeshOperationError(
            "MESH_SEPARATION_ATTRIBUTE_UNSUPPORTED",
            "Mesh separation does not yet migrate custom split normals",
        )
    unsupported = unsupported_attributes(mesh)
    if unsupported:
        raise MeshOperationError(
            "MESH_SEPARATION_ATTRIBUTE_UNSUPPORTED",
            "Mesh contains unsupported protected attributes",
            details={"attributes": list(unsupported)},
        )


def _branch_mesh(
    mesh: Any,
    selected_indices: tuple[int, ...],
    *,
    keep_selected: bool,
) -> tuple[
    dict[str, tuple[Any, ...]],
    dict[str, tuple[int, ...]],
    dict[str, tuple[int, ...]],
]:
    bm = bmesh.new()
    lineage = None
    try:
        bm.from_mesh(mesh)
        bm.faces.ensure_lookup_table()
        lineage = _start_lineage(bm)
        bm.faces.ensure_lookup_table()
        selected = {bm.faces[index] for index in selected_indices}
        remove = [face for face in bm.faces if (face in selected) is not keep_selected]
        bmesh.ops.delete(bm, geom=remove, context="FACES")
        bm.normal_update()
        if (
            len(bm.verts) > MAX_VERTICES
            or len(bm.edges) > MAX_EDGES
            or len(bm.faces) > MAX_FACES
            or sum(len(face.loops) for face in bm.faces) > MAX_LOOPS
        ):
            raise MeshOperationError(
                "MESH_BUDGET_EXCEEDED",
                "Separated Mesh branch exceeds the bounded topology budget",
            )
        relations, created, deleted = _finish_lineage(bm, lineage, "separate")
        lineage = None
        bm.to_mesh(mesh)
        mesh.update(calc_edges=True, calc_edges_loose=True)
        return relations, created, deleted
    finally:
        if lineage is not None:
            for state in lineage.values():
                with contextlib.suppress(Exception):
                    state.sequence.layers.int.remove(state.layer)
        bm.free()


def _branch_map(
    *,
    transaction: Transaction,
    separation_id: str,
    role: str,
    before: dict[str, Any],
    obj: Any,
    mesh: Any,
    relations: dict[str, tuple[Any, ...]],
    created: dict[str, tuple[int, ...]],
    deleted: dict[str, tuple[int, ...]],
) -> ComponentMapRecord:
    return make_component_map(
        transaction_id=transaction.transaction_id,
        operation="separate",
        before=before,
        after=_map_evidence(obj, mesh),
        after_users=int(mesh.users),
        after_user_objects=mesh_user_refs(mesh),
        relations=relations,
        created=created,
        deleted=deleted,
        map_kind="SEPARATION_BRANCH",
        separation_id=separation_id,
        branch_role=role,
    )


def _duplicated_boundary_counts(
    source: ComponentMapRecord,
    separated: ComponentMapRecord,
) -> dict[str, int]:
    result = {}
    for domain in DOMAINS:
        source_indices = {
            relation.source_index
            for relation in source.relations.get(domain, ())
            if relation.target_indices
        }
        separated_indices = {
            relation.source_index
            for relation in separated.relations.get(domain, ())
            if relation.target_indices
        }
        result[domain] = len(source_indices & separated_indices)
    return result


def _cleanup_duplicate(obj: Any | None, mesh: Any | None) -> None:
    if obj is not None and bpy.data.objects.get(obj.name) is obj:
        bpy.data.objects.remove(obj)
    if mesh is not None and bpy.data.meshes.get(mesh.name) is mesh and int(mesh.users) == 0:
        bpy.data.meshes.remove(mesh)


def _restore_separation_call(
    *,
    transaction: Transaction,
    separated_guard: MeshSnapshotGuard | None,
    duplicate: Any | None,
    duplicate_mesh: Any | None,
    source_mesh: Any,
    call_snapshot: Any,
    before_fingerprint: str,
    source_guard: MeshSnapshotGuard,
    source_guard_new: bool,
    failure: Exception,
) -> None:
    errors = []
    if separated_guard is not None:
        try:
            _remove_new_guard(transaction, separated_guard)
        except Exception as exc:  # noqa: BLE001 - restore every remaining resource
            errors.append(("separated_guard", exc))
    try:
        _cleanup_duplicate(duplicate, duplicate_mesh)
    except Exception as exc:  # noqa: BLE001 - continue restoring the source
        errors.append(("duplicate", exc))
    try:
        _restore_failed_edit(source_mesh, call_snapshot, before_fingerprint, failure)
    except Exception as exc:  # noqa: BLE001 - aggregate complete restore evidence
        errors.append(("source_mesh", exc))
    if source_guard_new:
        try:
            _remove_new_guard(transaction, source_guard)
        except Exception as exc:  # noqa: BLE001 - aggregate complete restore evidence
            errors.append(("source_guard", exc))
    if errors:
        raise MeshOperationError(
            "MESH_SEPARATION_RESTORE_FAILED",
            "Mesh separation failed and the complete call state could not be restored",
            kind="blender_api",
            details={
                "failure_type": type(failure).__name__,
                "failure": str(failure),
                "restore_errors": [
                    {
                        "phase": phase,
                        "error_type": type(error).__name__,
                        "message": str(error),
                    }
                    for phase, error in errors
                ],
            },
        ) from errors[0][1]


def separate_mesh(
    transaction: Transaction,
    book: MeshResourceBook,
    params: dict[str, Any],
) -> dict[str, Any]:
    target_params = {**params, "data_scope": "OBJECT"}
    obj, initial_mesh, _scope, _refs = _validate_mesh_target(target_params)
    _validate_separation_attributes(obj, initial_mesh)
    selection = _selection(book, params.get("selection_id"), obj, initial_mesh)
    _validate_connected(initial_mesh, selection.indices)

    new_name = params.get("new_object_name")
    if not isinstance(new_name, str) or not new_name:
        raise MeshOperationError(
            "MESH_SEPARATION_NAME_CONFLICT", "new_object_name must be non-empty"
        )
    if bpy.data.objects.get(new_name) is not None:
        raise MeshOperationError(
            "MESH_SEPARATION_NAME_CONFLICT",
            f"An object already uses the exact name: {new_name}",
            kind="conflict",
        )
    collection_name = params.get("collection_name")
    collection_identity = params.get("expected_collection_identity")
    if (collection_name is None) != (collection_identity is None):
        raise MeshOperationError(
            "MESH_SEPARATION_SELECTION_INVALID",
            "collection_name and expected_collection_identity must be supplied together",
        )

    transaction.ensure_capacity(2)
    before_evidence = _map_evidence(obj, initial_mesh)
    before_revision = mesh_revision_id(initial_mesh)
    before_fingerprint = mesh_fingerprint(initial_mesh)
    before_topology = topology_fingerprint(initial_mesh)
    before_counts = mesh_counts(initial_mesh)
    before_attributes = _attribute_signature(initial_mesh)
    source_policy = {
        "type": "separate_source",
        "attribute_policy": params.get("source_attribute_policy", {}),
    }
    separated_policy = {
        "type": "separate_branch",
        "attribute_policy": params.get("separated_attribute_policy", {}),
    }
    source_attribute_evidence = prepare_topology_attributes(obj, initial_mesh, source_policy)
    source_collections = tuple(obj.users_collection)

    source_guard = transaction.mesh_snapshot_guard(
        initial_mesh.name, session_identity("mesh", initial_mesh)
    )
    source_guard_new = source_guard is None
    if source_guard is None:
        source_guard = _create_guard(transaction, obj, initial_mesh, "OBJECT")
    else:
        _validate_guard(source_guard)
        if source_guard.data_scope != "OBJECT":
            raise MeshOperationError(
                "MESH_OPERATION_INVALID",
                "mesh.separate requires OBJECT scope throughout the transaction",
            )
    source_mesh = bpy.data.meshes.get(source_guard.mesh_name)
    if source_mesh is None:
        raise MeshOperationError("MESH_DATA_CONFLICT", "Guarded source Mesh no longer exists")
    source_weight_guard = None
    source_weight_guard_new = False
    source_weight_call_state = None
    if source_attribute_evidence["weight_present"]:
        from .mesh_weight_ops import (
            _capture_weights,
            _create_weight_guard,
            _group_schema,
            _validate_weight_guard,
        )

        source_weight_guard = transaction.weight_snapshot_guard(
            source_mesh.name, session_identity("mesh", source_mesh)
        )
        source_weight_guard_new = source_weight_guard is None
        if source_weight_guard is None:
            source_weight_guard = _create_weight_guard(transaction, obj, source_mesh, "OBJECT")
        else:
            _validate_weight_guard(source_weight_guard)
        weight_objects = tuple(
            bpy.data.objects[name] for name in source_weight_guard.object_identities
        )
        source_weight_call_state = (
            {item.name: session_identity("object", item) for item in weight_objects},
            {item.name: _group_schema(item, identities=False) for item in weight_objects},
            _capture_weights(source_mesh),
        )
    call_snapshot = source_mesh.copy()
    call_snapshot.name = f"{source_mesh.name}.MCP-Separate-Call"

    duplicate = None
    duplicate_mesh = None
    duplicate_delta: StructuralDelta | None = None
    separated_guard: MeshSnapshotGuard | None = None
    maps: list[ComponentMapRecord] = []
    selection_ids: list[str] = []
    phase = "duplicate"
    try:
        duplicate, duplicate_delta = duplicate_object(
            transaction,
            source_name=obj.name,
            expected_source_identity=session_identity("object", obj),
            name=new_name,
            linked_data=False,
            collection_name=(str(collection_name) if collection_name is not None else None),
            expected_collection_identity=(
                str(collection_identity) if collection_identity is not None else None
            ),
            transform=None,
        )
        duplicate_mesh = duplicate.data
        separated_attribute_evidence = prepare_topology_attributes(
            duplicate, duplicate_mesh, separated_policy
        )
        if collection_name is None:
            for collection in source_collections:
                if duplicate.name not in collection.objects:
                    collection.objects.link(duplicate)

        phase = "source_branch"
        source_relations, source_created, source_deleted = _branch_mesh(
            source_mesh, selection.indices, keep_selected=False
        )
        phase = "separated_branch"
        separated_relations, separated_created, separated_deleted = _branch_mesh(
            duplicate_mesh, selection.indices, keep_selected=True
        )
        source_migration = finish_topology_attributes(obj, source_mesh, source_attribute_evidence)
        separated_migration = finish_topology_attributes(
            duplicate, duplicate_mesh, separated_attribute_evidence
        )
        if len(source_mesh.polygons) == 0:
            raise MeshOperationError(
                "MESH_SEPARATION_EMPTY_SOURCE", "Separation left the source Mesh empty"
            )
        if len(duplicate_mesh.polygons) == 0:
            raise MeshOperationError("MESH_SEPARATION_FAILED", "Separated branch contains no faces")
        after_source_attributes = _attribute_signature(source_mesh)
        after_separated_attributes = _attribute_signature(duplicate_mesh)
        if source_attribute_evidence["policy"]["uv"] != "DISCARD" and (
            before_attributes != after_source_attributes
        ):
            raise MeshOperationError(
                "MESH_SEPARATION_ATTRIBUTE_UNSUPPORTED",
                "Separation did not preserve the supported attribute schema",
                details={
                    "before": before_attributes,
                    "source": after_source_attributes,
                    "separated": after_separated_attributes,
                },
            )
        if separated_attribute_evidence["policy"]["uv"] != "DISCARD" and (
            before_attributes != after_separated_attributes
        ):
            raise MeshOperationError(
                "MESH_SEPARATION_ATTRIBUTE_UNSUPPORTED",
                "Separated branch did not preserve the supported attribute schema",
            )

        phase = "branch_maps"
        separation_id = str(uuid.uuid4())
        source_map = _branch_map(
            transaction=transaction,
            separation_id=separation_id,
            role="SOURCE",
            before=before_evidence,
            obj=obj,
            mesh=source_mesh,
            relations=source_relations,
            created=source_created,
            deleted=source_deleted,
        )
        separated_map = _branch_map(
            transaction=transaction,
            separation_id=separation_id,
            role="SEPARATED",
            before=before_evidence,
            obj=duplicate,
            mesh=duplicate_mesh,
            relations=separated_relations,
            created=separated_created,
            deleted=separated_deleted,
        )
        book.add_component_map(source_map)
        maps.append(source_map)
        book.add_component_map(separated_map)
        maps.append(separated_map)

        phase = "branch_selections"
        source_rebound_result = remap_selection(
            book,
            {
                "selection_id": selection.selection_id,
                "component_map_id": source_map.component_map_id,
                "mode": "ALL_MAPPED",
                "weight_merge": "MAX",
            },
        )
        source_rebound = source_rebound_result["selection"]
        selection_ids.append(str(source_rebound["selection_id"]))
        separated_result = remap_selection(
            book,
            {
                "selection_id": selection.selection_id,
                "component_map_id": separated_map.component_map_id,
                "mode": "ALL_MAPPED",
                "weight_merge": "MAX",
            },
        )
        separated_selection = separated_result["selection"]
        selection_ids.append(str(separated_selection["selection_id"]))

        phase = "transaction_guards"
        separated_guard = _create_guard(transaction, duplicate, duplicate_mesh, "OBJECT")
        source_after = mesh_fingerprint(source_mesh)
        source_guard.expected_fingerprint = source_after
        source_guard.expected_users = int(source_mesh.users)
        source_guard.expected_user_objects = mesh_user_refs(source_mesh)
        if source_weight_guard is not None:
            from .mesh_weight_ops import _schema_fingerprints, weights_fingerprint

            weight_objects = tuple(
                bpy.data.objects[name] for name in source_weight_guard.object_identities
            )
            source_weight_guard.expected_schema_fingerprints = _schema_fingerprints(weight_objects)
            source_weight_guard.expected_weights_fingerprint = weights_fingerprint(source_mesh)
        duplicate_delta.after = (make_structure_guard("object", duplicate),)
        transaction.record(
            MeshEditDelta(
                object_name=obj.name,
                object_identity=session_identity("object", obj),
                mesh_name=source_mesh.name,
                mesh_identity=session_identity("mesh", source_mesh),
                operation="separate",
                before_fingerprint=before_fingerprint,
                after_fingerprint=source_after,
                data_scope="OBJECT",
            )
        )
        transaction.record(duplicate_delta)
        refresh_structure_guard_if_present(transaction, "object", obj)
        refresh_structure_guard_if_present(transaction, "mesh", source_mesh)
    except (MeshOperationError, MeshResourceError, AuthoringOperationError) as exc:
        for selection_id in selection_ids:
            book.release_selection(selection_id)
        for record in maps:
            book.release_component_map(record.component_map_id)
        if source_weight_call_state is not None:
            from .mesh_weight_ops import _restore_call_state

            _restore_call_state(source_mesh, *source_weight_call_state, exc)
        _restore_separation_call(
            transaction=transaction,
            separated_guard=separated_guard,
            duplicate=duplicate,
            duplicate_mesh=duplicate_mesh,
            source_mesh=source_mesh,
            call_snapshot=call_snapshot,
            before_fingerprint=before_fingerprint,
            source_guard=source_guard,
            source_guard_new=source_guard_new,
            failure=exc,
        )
        if source_weight_guard_new and source_weight_guard is not None:
            transaction.remove_weight_snapshot_guard(source_weight_guard)
        if isinstance(exc, AuthoringOperationError):
            raise MeshOperationError(
                "MESH_SEPARATION_FAILED",
                str(exc),
                kind=exc.kind,
                details=exc.details,
            ) from exc
        raise
    except Exception as exc:
        for selection_id in selection_ids:
            book.release_selection(selection_id)
        for record in maps:
            book.release_component_map(record.component_map_id)
        if source_weight_call_state is not None:
            from .mesh_weight_ops import _restore_call_state

            _restore_call_state(source_mesh, *source_weight_call_state, exc)
        _restore_separation_call(
            transaction=transaction,
            separated_guard=separated_guard,
            duplicate=duplicate,
            duplicate_mesh=duplicate_mesh,
            source_mesh=source_mesh,
            call_snapshot=call_snapshot,
            before_fingerprint=before_fingerprint,
            source_guard=source_guard,
            source_guard_new=source_guard_new,
            failure=exc,
        )
        if source_weight_guard_new and source_weight_guard is not None:
            transaction.remove_weight_snapshot_guard(source_weight_guard)
        raise MeshOperationError(
            "MESH_SEPARATION_FAILED",
            f"Mesh separation failed: {type(exc).__name__}",
            kind="blender_api",
            details={
                "error_type": type(exc).__name__,
                "message": str(exc),
                "phase": phase,
            },
        ) from exc
    finally:
        _remove_temporary_mesh(call_snapshot)

    assert duplicate is not None and duplicate_mesh is not None and duplicate_delta is not None
    source_after = mesh_fingerprint(source_mesh)
    return {
        "transaction_id": transaction.transaction_id,
        "changed": True,
        "operation": "separate",
        "data_scope": "OBJECT",
        "separation_id": maps[0].separation_id,
        "source_object": object_summary(obj),
        "separated_object": object_summary(duplicate),
        "before_mesh": {
            **_mesh_reference(initial_mesh),
            "mesh_revision_id": before_revision,
        },
        "source_mesh": _mesh_reference(source_mesh),
        "separated_mesh": _mesh_reference(duplicate_mesh),
        "before_mesh_fingerprint": before_fingerprint,
        "before_topology_fingerprint": before_topology,
        "source_mesh_fingerprint": source_after,
        "separated_mesh_fingerprint": mesh_fingerprint(duplicate_mesh),
        "before_counts": before_counts,
        "source_counts": mesh_counts(source_mesh),
        "separated_counts": mesh_counts(duplicate_mesh),
        "source_component_map": maps[0].summary(),
        "separated_component_map": maps[1].summary(),
        "source_rebound_selection": source_rebound,
        "separated_selection": separated_selection,
        "component_effects": {
            "duplicated_boundary": _duplicated_boundary_counts(maps[0], maps[1]),
            "source_deleted": {domain: len(maps[0].deleted.get(domain, ())) for domain in DOMAINS},
            "separated_deleted": {
                domain: len(maps[1].deleted.get(domain, ())) for domain in DOMAINS
            },
        },
        "attribute_effects": {
            "schema_preserved": True,
            "attributes": before_attributes,
            "interpolation": "BLENDER_BMESH",
            "source_migration": source_migration,
            "separated_migration": separated_migration,
        },
        "delta": {"types": ["mesh_edit", "object_duplicate"], "recorded": True},
    }
