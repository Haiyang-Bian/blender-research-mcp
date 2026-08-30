"""Revision-aware bounded topology editing and exact ComponentMap generation."""

from __future__ import annotations

import contextlib
import math
from dataclasses import dataclass
from typing import Any

import bmesh
import bpy

from .lookdev_ops import session_identity
from .mesh_component_map import remap_selection
from .mesh_component_map_model import (
    DOMAINS,
    ComponentMapRecord,
    ComponentRelation,
    make_component_map,
)
from .mesh_ops import (
    MAX_EDGES,
    MAX_FACES,
    MAX_LOOPS,
    MAX_VERTICES,
    MeshOperationError,
    _bmesh_baseline,
    _component_changes,
    _component_warnings,
    _create_guard,
    _index_page,
    _is_protected_attribute,
    _mesh_reference,
    _remove_new_guard,
    _remove_temporary_mesh,
    _restore_failed_edit,
    _validate_guard,
    _validate_mesh_target,
    _vector,
    mesh_counts,
    mesh_fingerprint,
    mesh_revision_id,
    mesh_user_refs,
    topology_fingerprint,
    unsupported_attributes,
)
from .mesh_query_ops import validate_selection
from .mesh_resource_model import MeshResourceBook, MeshResourceError, SelectionRecord
from .structural_ops import refresh_structure_guard_if_present
from .transaction_model import MeshEditDelta, Transaction

TOPOLOGY_OPERATIONS = {
    "subdivide",
    "loop_cut",
    "bisect",
    "split",
    "bridge",
    "fill",
    "grid_fill",
}

_DOMAIN_COLLECTION = {
    "VERTEX": "verts",
    "EDGE": "edges",
    "FACE": "faces",
}


def _closed(operation: Any, required: set[str], optional: set[str]) -> dict[str, Any]:
    if not isinstance(operation, dict):
        raise MeshOperationError("MESH_OPERATION_INVALID", "operation must be an object")
    missing = required - set(operation)
    extra = set(operation) - required - optional
    if missing or extra:
        raise MeshOperationError(
            "MESH_OPERATION_INVALID",
            "operation has invalid fields",
            details={"missing": sorted(missing), "extra": sorted(extra)},
        )
    return operation


def _integer(value: Any, field: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise MeshOperationError(
            "MESH_OPERATION_INVALID",
            f"{field} must be an integer between {minimum} and {maximum}",
        )
    return value


def _number(value: Any, field: str, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise MeshOperationError("MESH_OPERATION_INVALID", f"{field} must be a number")
    result = float(value)
    if not math.isfinite(result) or not minimum <= result <= maximum:
        raise MeshOperationError(
            "MESH_OPERATION_INVALID",
            f"{field} must be finite and between {minimum} and {maximum}",
        )
    return result


def _boolean(value: Any, field: str) -> bool:
    if type(value) is not bool:
        raise MeshOperationError("MESH_OPERATION_INVALID", f"{field} must be a boolean")
    return value


def _enum(value: Any, field: str, allowed: set[str]) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise MeshOperationError(
            "MESH_OPERATION_INVALID",
            f"{field} must be one of {sorted(allowed)}",
        )
    return value


def _material_index(operation: dict[str, Any], material_count: int) -> int | None:
    value = operation.get("material_slot_index")
    if value is None:
        return None
    index = _integer(value, "material_slot_index", 0, 63)
    if index >= material_count:
        raise MeshOperationError(
            "MESH_OPERATION_INVALID",
            f"material_slot_index must be less than the Mesh material count {material_count}",
        )
    return index


def _validate_operation(raw: Any, material_count: int) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise MeshOperationError("MESH_OPERATION_INVALID", "operation must be an object")
    operation_type = raw.get("type")
    common = {"type", "selection_id"}
    if operation_type == "subdivide":
        operation = _closed(
            raw,
            common,
            {"cuts", "smooth", "smooth_falloff", "quad_corner", "use_grid_fill"},
        )
        _integer(operation.get("cuts", 1), "cuts", 1, 32)
        _number(operation.get("smooth", 0), "smooth", 0, 1)
        _enum(operation.get("smooth_falloff", "SMOOTH"), "smooth_falloff", {"LINEAR", "SMOOTH"})
        _enum(
            operation.get("quad_corner", "STRAIGHT_CUT"),
            "quad_corner",
            {"STRAIGHT_CUT", "INNER_VERT", "PATH", "FAN"},
        )
        _boolean(operation.get("use_grid_fill", False), "use_grid_fill")
        return operation
    if operation_type == "loop_cut":
        operation = _closed(raw, common, {"cuts", "interpolation", "smooth"})
        _integer(operation.get("cuts", 1), "cuts", 1, 32)
        _enum(
            operation.get("interpolation", "LINEAR"),
            "interpolation",
            {"LINEAR", "PATH", "SURFACE"},
        )
        _number(operation.get("smooth", 0), "smooth", 0, 1)
        return operation
    if operation_type == "bisect":
        operation = _closed(
            raw,
            common | {"plane_origin", "plane_normal"},
            {"space", "tolerance", "snap_to_plane", "clear_side"},
        )
        _vector(operation["plane_origin"], "plane_origin")
        if _vector(operation["plane_normal"], "plane_normal").length_squared == 0:
            raise MeshOperationError("MESH_OPERATION_INVALID", "plane_normal must be non-zero")
        _enum(operation.get("space", "LOCAL"), "space", {"LOCAL", "WORLD"})
        _number(operation.get("tolerance", 1e-6), "tolerance", 0, 1)
        _boolean(operation.get("snap_to_plane", False), "snap_to_plane")
        _enum(
            operation.get("clear_side", "NONE"),
            "clear_side",
            {"NONE", "POSITIVE", "NEGATIVE"},
        )
        return operation
    if operation_type == "split":
        return _closed(raw, common, set())
    if operation_type == "bridge":
        operation = _closed(raw, common, {"twist_offset", "material_slot_index", "smooth"})
        _integer(operation.get("twist_offset", 0), "twist_offset", -4096, 4096)
        _material_index(operation, material_count)
        _boolean(operation.get("smooth", False), "smooth")
        return operation
    if operation_type == "fill":
        operation = _closed(
            raw,
            common,
            {"method", "max_sides", "material_slot_index", "smooth"},
        )
        _enum(operation.get("method", "NGON"), "method", {"NGON", "TRIANGLES"})
        _integer(operation.get("max_sides", 0), "max_sides", 0, 1024)
        _material_index(operation, material_count)
        _boolean(operation.get("smooth", False), "smooth")
        return operation
    if operation_type == "grid_fill":
        operation = _closed(
            raw,
            common,
            {"use_interp_simple", "material_slot_index", "smooth"},
        )
        _boolean(operation.get("use_interp_simple", False), "use_interp_simple")
        _material_index(operation, material_count)
        _boolean(operation.get("smooth", False), "smooth")
        return operation
    raise MeshOperationError(
        "MESH_OPERATION_INVALID", f"Unsupported topology operation: {operation_type}"
    )


def _selection(
    book: MeshResourceBook,
    operation: dict[str, Any],
    obj: Any,
    mesh: Any,
) -> SelectionRecord:
    selection_id = operation.get("selection_id")
    if not isinstance(selection_id, str) or not selection_id:
        raise MeshOperationError("MESH_OPERATION_INVALID", "selection_id is required")
    selection = book.selection(selection_id)
    selection_obj, selection_mesh = validate_selection(selection)
    if selection_obj is not obj or selection_mesh is not mesh:
        raise MeshOperationError(
            "MESH_RESOURCE_STALE",
            "SelectionSet does not target the requested Mesh",
            kind="conflict",
        )
    expected_domains = {
        "subdivide": {"EDGE"},
        "loop_cut": {"EDGE"},
        "bisect": {"FACE"},
        "split": {"EDGE", "FACE"},
        "bridge": {"EDGE"},
        "fill": {"EDGE"},
        "grid_fill": {"EDGE"},
    }[str(operation["type"])]
    if selection.domain not in expected_domains:
        raise MeshOperationError(
            "MESH_OPERATION_INVALID",
            f"{operation['type']} requires a {sorted(expected_domains)} SelectionSet",
        )
    if not selection.indices:
        raise MeshOperationError("MESH_OPERATION_INVALID", "SelectionSet must not be empty")
    if operation["type"] == "loop_cut" and len(selection.indices) > 64:
        raise MeshOperationError(
            "MESH_OPERATION_INVALID", "loop_cut accepts at most 64 seed edges"
        )
    return selection


def _elements_for_selection(bm: Any, selection: SelectionRecord) -> list[Any]:
    sequence = getattr(bm, _DOMAIN_COLLECTION[selection.domain])
    sequence.ensure_lookup_table()
    if any(index >= len(sequence) for index in selection.indices):
        raise MeshOperationError(
            "MESH_COMPONENT_INDEX_INVALID",
            "SelectionSet contains an index outside the current Mesh",
        )
    return [sequence[index] for index in selection.indices]


def _boundary_components(edges: list[Any]) -> list[list[Any]]:
    if any(len(edge.link_faces) > 1 for edge in edges):
        raise MeshOperationError(
            "MESH_BOUNDARY_INVALID", "Selected edges must be loose or boundary edges"
        )
    selected = set(edges)
    remaining = set(edges)
    components: list[list[Any]] = []
    while remaining:
        seed = remaining.pop()
        component = [seed]
        queue = [seed]
        while queue:
            edge = queue.pop()
            neighbors = {
                linked
                for vertex in edge.verts
                for linked in vertex.link_edges
                if linked in selected
            }
            for neighbor in neighbors:
                if neighbor in remaining:
                    remaining.remove(neighbor)
                    queue.append(neighbor)
                    component.append(neighbor)
        components.append(component)
    return components


def _component_degrees(edges: list[Any]) -> list[int]:
    degrees: dict[Any, int] = {}
    for edge in edges:
        for vertex in edge.verts:
            degrees[vertex] = degrees.get(vertex, 0) + 1
    return sorted(degrees.values())


def _closed_loops(edges: list[Any], *, expected: int | None = None) -> list[list[Any]]:
    components = _boundary_components(edges)
    if expected is not None and len(components) != expected:
        raise MeshOperationError(
            "MESH_BOUNDARY_INVALID",
            f"Expected {expected} disjoint boundary loops, found {len(components)}",
        )
    if any(
        any(degree != 2 for degree in _component_degrees(component))
        for component in components
    ):
        raise MeshOperationError(
            "MESH_BOUNDARY_INVALID", "Selected boundary components must be closed loops"
        )
    return components


def _loop_cut_ring(seed_edges: list[Any]) -> list[Any]:
    ring = set(seed_edges)
    queue = list(seed_edges)
    while queue:
        edge = queue.pop()
        if not edge.link_faces:
            raise MeshOperationError("MESH_EDGE_RING_INVALID", "Loop-cut seed is a loose edge")
        for face in edge.link_faces:
            if len(face.edges) != 4:
                raise MeshOperationError(
                    "MESH_EDGE_RING_INVALID", "Loop cut requires an unambiguous quad edge ring"
                )
            face_edges = list(face.edges)
            opposite = face_edges[(face_edges.index(edge) + 2) % 4]
            if opposite not in ring:
                ring.add(opposite)
                queue.append(opposite)
    return list(ring)


def _new_faces(result: dict[str, Any]) -> list[Any]:
    faces = list(result.get("faces", ()))
    faces.extend(item for item in result.get("geom", ()) if isinstance(item, bmesh.types.BMFace))
    return list(dict.fromkeys(face for face in faces if face.is_valid))


def _set_face_properties(
    faces: list[Any], operation: dict[str, Any], material_count: int
) -> None:
    material = _material_index(operation, material_count)
    smooth = bool(operation.get("smooth", False))
    for face in faces:
        if material is not None:
            face.material_index = material
        face.smooth = smooth


def _operate(
    bm: Any,
    obj: Any,
    selection: SelectionRecord,
    operation: dict[str, Any],
    material_count: int,
) -> dict[str, Any]:
    selected = _elements_for_selection(bm, selection)
    operation_type = str(operation["type"])
    if operation_type == "subdivide":
        result = bmesh.ops.subdivide_edges(
            bm,
            edges=selected,
            smooth=float(operation.get("smooth", 0)),
            smooth_falloff=str(operation.get("smooth_falloff", "SMOOTH")),
            cuts=int(operation.get("cuts", 1)),
            quad_corner_type=str(operation.get("quad_corner", "STRAIGHT_CUT")),
            use_grid_fill=bool(operation.get("use_grid_fill", False)),
            use_single_edge=True,
        )
        return {"selected_edges": len(selected), "operator_result": sorted(result)}
    if operation_type == "loop_cut":
        ring = _loop_cut_ring(selected)
        result = bmesh.ops.subdivide_edgering(
            bm,
            edges=ring,
            interp_mode=str(operation.get("interpolation", "LINEAR")),
            smooth=float(operation.get("smooth", 0)),
            cuts=int(operation.get("cuts", 1)),
            profile_shape="SMOOTH",
            profile_shape_factor=0.0,
        )
        return {
            "seed_edges": len(selected),
            "resolved_ring_edges": len(ring),
            "operator_result": sorted(result),
        }
    if operation_type == "bisect":
        faces = selected
        edges = {edge for face in faces for edge in face.edges}
        verts = {vertex for face in faces for vertex in face.verts}
        plane_co = _vector(operation["plane_origin"], "plane_origin")
        plane_no = _vector(operation["plane_normal"], "plane_normal").normalized()
        if operation.get("space", "LOCAL") == "WORLD":
            plane_co = obj.matrix_world.inverted() @ plane_co
            plane_no = (obj.matrix_world.to_3x3().transposed() @ plane_no).normalized()
        clear_side = operation.get("clear_side", "NONE")
        result = bmesh.ops.bisect_plane(
            bm,
            geom=[*verts, *edges, *faces],
            dist=float(operation.get("tolerance", 1e-6)),
            plane_co=plane_co,
            plane_no=plane_no,
            use_snap_center=bool(operation.get("snap_to_plane", False)),
            clear_outer=clear_side == "POSITIVE",
            clear_inner=clear_side == "NEGATIVE",
        )
        return {
            "selected_faces": len(faces),
            "cut_geometry": len(result.get("geom_cut", ())),
            "clear_side": clear_side,
        }
    if operation_type == "split":
        if selection.domain == "EDGE":
            result = bmesh.ops.split_edges(bm, edges=selected)
        else:
            result = bmesh.ops.split(bm, geom=selected, use_only_faces=True)
        return {"selected_components": len(selected), "operator_result": sorted(result)}
    if operation_type == "bridge":
        _closed_loops(selected, expected=2)
        result = bmesh.ops.bridge_loops(
            bm,
            edges=selected,
            use_pairs=False,
            use_cyclic=False,
            use_merge=False,
            merge_factor=0.0,
            twist_offset=int(operation.get("twist_offset", 0)),
        )
        faces = _new_faces(result)
        if not faces:
            raise MeshOperationError("MESH_BOUNDARY_INVALID", "Bridge created no faces")
        _set_face_properties(faces, operation, material_count)
        return {"boundary_loops": 2, "created_faces": len(faces)}
    if operation_type == "fill":
        loops = _closed_loops(selected)
        if operation.get("method", "NGON") == "TRIANGLES":
            result = bmesh.ops.triangle_fill(
                bm,
                edges=selected,
                use_beauty=True,
                use_dissolve=False,
            )
        else:
            result = bmesh.ops.holes_fill(
                bm,
                edges=selected,
                sides=int(operation.get("max_sides", 0)),
            )
        faces = _new_faces(result)
        if not faces:
            raise MeshOperationError("MESH_BOUNDARY_INVALID", "Fill created no faces")
        _set_face_properties(faces, operation, material_count)
        return {"boundary_loops": len(loops), "created_faces": len(faces)}
    components = _boundary_components(selected)
    degrees = [_component_degrees(component) for component in components]
    valid = (
        len(components) == 1 and all(degree == 2 for degree in degrees[0])
    ) or (
        len(components) == 2
        and all(sum(degree == 1 for degree in item) in {0, 2} for item in degrees)
        and all(all(degree in {1, 2} for degree in item) for item in degrees)
    )
    if not valid:
        raise MeshOperationError(
            "MESH_BOUNDARY_INVALID",
            "Grid fill requires one closed boundary or two compatible boundary chains",
        )
    result = bmesh.ops.grid_fill(
        bm,
        edges=selected,
        mat_nr=_material_index(operation, material_count) or 0,
        use_smooth=bool(operation.get("smooth", False)),
        use_interp_simple=bool(operation.get("use_interp_simple", False)),
    )
    faces = _new_faces(result)
    if not faces:
        raise MeshOperationError("MESH_BOUNDARY_INVALID", "Grid fill created no faces")
    return {"boundary_components": len(components), "created_faces": len(faces)}


@dataclass
class _LineageLayer:
    sequence: Any
    layer: Any
    before: tuple[Any, ...]
    before_ids: frozenset[int]


def _start_lineage(bm: Any) -> dict[str, _LineageLayer]:
    result = {}
    for domain, attribute in _DOMAIN_COLLECTION.items():
        sequence = getattr(bm, attribute)
        sequence.ensure_lookup_table()
        layer = sequence.layers.int.new(f".mcp_lineage_{domain.lower()}")
        before = tuple(sequence)
        for index, item in enumerate(before):
            item[layer] = index + 1
        result[domain] = _LineageLayer(
            sequence,
            layer,
            before,
            frozenset(id(item) for item in before),
        )
    return result


def _finish_lineage(
    bm: Any,
    layers: dict[str, _LineageLayer],
    operation_type: str,
) -> tuple[
    dict[str, tuple[ComponentRelation, ...]],
    dict[str, tuple[int, ...]],
    dict[str, tuple[int, ...]],
]:
    relations: dict[str, tuple[ComponentRelation, ...]] = {}
    created: dict[str, tuple[int, ...]] = {}
    deleted: dict[str, tuple[int, ...]] = {}
    for domain in DOMAINS:
        state = layers[domain]
        state.sequence.index_update()
        state.sequence.ensure_lookup_table()
        targets: dict[int, list[int]] = {}
        created_indices = []
        for item in state.sequence:
            source = int(item[state.layer]) - 1
            if source >= 0 and source < len(state.before):
                targets.setdefault(source, []).append(int(item.index))
            if id(item) not in state.before_ids:
                created_indices.append(int(item.index))
        rows = []
        deleted_indices = []
        for source, before_item in enumerate(state.before):
            mapped = tuple(sorted(set(targets.get(source, ()))))
            if not mapped:
                deleted_indices.append(source)
                continue
            if len(mapped) > 1:
                relation = "SPLIT"
            elif id(state.sequence[mapped[0]]) == id(before_item):
                relation = "SURVIVED"
            else:
                relation = "DERIVED"
            rows.append(ComponentRelation(source, mapped, relation))
        relations[domain] = tuple(rows)
        created[domain] = tuple(sorted(created_indices))
        deleted[domain] = tuple(deleted_indices)
    for state in layers.values():
        state.sequence.layers.int.remove(state.layer)
    return relations, created, deleted


def _map_evidence(obj: Any, mesh: Any) -> dict[str, Any]:
    return {
        "object_name": obj.name,
        "object_identity": session_identity("object", obj),
        "mesh_name": mesh.name,
        "mesh_identity": session_identity("mesh", mesh),
        "mesh_revision_id": mesh_revision_id(mesh),
        "mesh_fingerprint": mesh_fingerprint(mesh),
    }


def _attribute_signature(mesh: Any) -> dict[str, tuple[tuple[str, str, str], ...]]:
    return {
        "attributes": tuple(
            sorted(
                (str(item.name), str(item.domain), str(item.data_type))
                for item in mesh.attributes
                if _is_protected_attribute(item)
            )
        ),
        "uv_layers": tuple((str(item.name), "CORNER", "FLOAT2") for item in mesh.uv_layers),
        "color_attributes": tuple(
            sorted(
                (str(item.name), str(item.domain), str(item.data_type))
                for item in mesh.color_attributes
            )
        ),
    }


def _created_selections(
    book: MeshResourceBook,
    record: ComponentMapRecord,
    obj: Any,
    mesh: Any,
) -> tuple[dict[str, dict[str, Any]], list[str]]:
    result = {}
    identifiers = []
    for domain in DOMAINS:
        indices = record.created.get(domain, ())
        if not indices:
            continue
        selection = book.add_selection(
            object_name=obj.name,
            object_identity=session_identity("object", obj),
            mesh_name=mesh.name,
            mesh_identity=session_identity("mesh", mesh),
            mesh_revision_id=mesh_revision_id(mesh),
            mesh_fingerprint=mesh_fingerprint(mesh),
            expected_users=int(mesh.users),
            expected_user_objects=mesh_user_refs(mesh),
            domain=domain,
            indices=indices,
            weights=None,
            source_query={
                "type": "component_map_created",
                "component_map_id": record.component_map_id,
                "domain": domain,
            },
        )
        result[domain] = selection.summary()
        identifiers.append(selection.selection_id)
    return result, identifiers


def edit_mesh_topology(
    transaction: Transaction,
    book: MeshResourceBook,
    params: dict[str, Any],
) -> dict[str, Any]:
    obj, initial_mesh, data_scope, _refs = _validate_mesh_target(params)
    initial_mesh_reference = _mesh_reference(initial_mesh)
    operation = _validate_operation(params.get("operation"), len(initial_mesh.materials))
    operation_type = str(operation["type"])
    selection = _selection(book, operation, obj, initial_mesh)
    before_map_evidence = _map_evidence(obj, initial_mesh)
    before_revision = mesh_revision_id(initial_mesh)
    before_attributes = _attribute_signature(initial_mesh)

    transaction.ensure_capacity()
    guard = transaction.mesh_snapshot_guard(
        initial_mesh.name, session_identity("mesh", initial_mesh)
    )
    new_guard = guard is None
    if guard is None:
        guard = _create_guard(transaction, obj, initial_mesh, data_scope)
    else:
        _validate_guard(guard)
        if guard.data_scope != data_scope:
            raise MeshOperationError(
                "MESH_OPERATION_INVALID", "data_scope must remain stable in one transaction"
            )
    mesh = bpy.data.meshes.get(guard.mesh_name)
    if mesh is None:
        raise MeshOperationError("MESH_DATA_CONFLICT", "Guarded Mesh no longer exists")
    before_fingerprint = mesh_fingerprint(mesh)
    before_topology = topology_fingerprint(mesh)
    before_counts = mesh_counts(mesh)
    call_snapshot = mesh.copy()
    call_snapshot.name = f"{mesh.name}.MCP-Call-Snapshot"
    bm = bmesh.new()
    lineage = None
    component_map = None
    created_selection_ids: list[str] = []
    rebound_selection_id = None
    try:
        bm.from_mesh(mesh)
        component_baseline = _bmesh_baseline(bm)
        requested = {
            _DOMAIN_COLLECTION[selection.domain]: _index_page(list(selection.indices))
        }
        lineage = _start_lineage(bm)
        evidence = _operate(bm, obj, selection, operation, len(mesh.materials))
        bm.normal_update()
        components = _component_changes(bm, component_baseline)
        components["affected"] = requested
        if (
            len(bm.verts) > MAX_VERTICES
            or len(bm.edges) > MAX_EDGES
            or len(bm.faces) > MAX_FACES
            or sum(len(face.loops) for face in bm.faces) > MAX_LOOPS
        ):
            raise MeshOperationError(
                "MESH_BUDGET_EXCEEDED", "Topology operation exceeds the Mesh budget"
            )
        relations, created, deleted = _finish_lineage(bm, lineage, operation_type)
        lineage = None
        bm.to_mesh(mesh)
        mesh.update(calc_edges=True, calc_edges_loose=True)
        if unsupported_attributes(mesh):
            raise MeshOperationError(
                "MESH_LINEAGE_GENERATION_FAILED",
                "Topology operation produced unsupported Mesh attributes",
            )
        after_attributes = _attribute_signature(mesh)
        if before_attributes != after_attributes:
            raise MeshOperationError(
                "MESH_LINEAGE_GENERATION_FAILED",
                "Topology operation did not preserve the supported attribute schema",
                details={"before": before_attributes, "after": after_attributes},
            )
        after_fingerprint = mesh_fingerprint(mesh)
        after_topology = topology_fingerprint(mesh)
        changed = after_fingerprint != before_fingerprint
        if not changed:
            if new_guard:
                _remove_new_guard(transaction, guard)
            return {
                "transaction_id": transaction.transaction_id,
                "changed": False,
                "operation": operation_type,
                "data_scope": data_scope,
                "before_mesh": initial_mesh_reference,
                "after_mesh": _mesh_reference(obj.data),
                "before_mesh_revision_id": before_revision,
                "after_mesh_revision_id": mesh_revision_id(obj.data),
                "before_mesh_fingerprint": before_fingerprint,
                "after_mesh_fingerprint": mesh_fingerprint(obj.data),
                "before_topology_fingerprint": before_topology,
                "after_topology_fingerprint": topology_fingerprint(obj.data),
                "before_counts": before_counts,
                "after_counts": mesh_counts(obj.data),
                "components": components,
                "evidence": evidence,
                "component_map": None,
                "rebound_selection": selection.summary(),
                "created_selections": {},
                "delta": {"type": "mesh_edit", "recorded": False},
                "warnings": _component_warnings(components),
            }
        if after_topology != before_topology:
            component_map = make_component_map(
                transaction_id=transaction.transaction_id,
                operation=operation_type,
                before=before_map_evidence,
                after=_map_evidence(obj, mesh),
                after_users=int(mesh.users),
                after_user_objects=mesh_user_refs(mesh),
                relations=relations,
                created=created,
                deleted=deleted,
            )
            book.add_component_map(component_map)
            rebound_result = remap_selection(
                book,
                {
                    "selection_id": selection.selection_id,
                    "component_map_id": component_map.component_map_id,
                    "mode": "ALL_MAPPED",
                    "weight_merge": "MAX",
                },
            )
            rebound = rebound_result["selection"]
            rebound_selection_id = str(rebound["selection_id"])
            created_selections, created_selection_ids = _created_selections(
                book, component_map, obj, mesh
            )
        else:
            rebound_record = book.add_selection(
                object_name=obj.name,
                object_identity=session_identity("object", obj),
                mesh_name=mesh.name,
                mesh_identity=session_identity("mesh", mesh),
                mesh_revision_id=mesh_revision_id(mesh),
                mesh_fingerprint=after_fingerprint,
                expected_users=int(mesh.users),
                expected_user_objects=mesh_user_refs(mesh),
                domain=selection.domain,
                indices=selection.indices,
                weights=selection.weights,
                source_query={
                    "type": "rebind_after_non_topology_result",
                    "operation": operation_type,
                    "source_selection_id": selection.selection_id,
                },
            )
            rebound = rebound_record.summary()
            rebound_selection_id = rebound_record.selection_id
            created_selections = {}
    except (MeshOperationError, MeshResourceError) as exc:
        if component_map is not None:
            book.release_component_map(component_map.component_map_id)
        for selection_id in [*created_selection_ids, rebound_selection_id]:
            if selection_id is not None:
                book.release_selection(selection_id)
        _restore_failed_edit(mesh, call_snapshot, before_fingerprint, exc)
        if new_guard:
            _remove_new_guard(transaction, guard)
        raise
    except Exception as exc:
        if component_map is not None:
            book.release_component_map(component_map.component_map_id)
        for selection_id in [*created_selection_ids, rebound_selection_id]:
            if selection_id is not None:
                book.release_selection(selection_id)
        _restore_failed_edit(mesh, call_snapshot, before_fingerprint, exc)
        if new_guard:
            _remove_new_guard(transaction, guard)
        raise MeshOperationError(
            "MESH_EDIT_FAILED",
            f"Topology operation failed: {type(exc).__name__}",
            kind="blender_api",
            details={"error_type": type(exc).__name__, "message": str(exc)},
        ) from exc
    finally:
        if lineage is not None:
            for state in lineage.values():
                with contextlib.suppress(Exception):
                    state.sequence.layers.int.remove(state.layer)
        bm.free()
        _remove_temporary_mesh(call_snapshot)

    after_fingerprint = mesh_fingerprint(mesh)
    guard.expected_fingerprint = after_fingerprint
    guard.expected_users = int(mesh.users)
    guard.expected_user_objects = mesh_user_refs(mesh)
    transaction.record(
        MeshEditDelta(
            object_name=obj.name,
            object_identity=session_identity("object", obj),
            mesh_name=mesh.name,
            mesh_identity=session_identity("mesh", mesh),
            operation=operation_type,
            before_fingerprint=before_fingerprint,
            after_fingerprint=after_fingerprint,
            data_scope=data_scope,
        )
    )
    refresh_structure_guard_if_present(transaction, "object", obj)
    refresh_structure_guard_if_present(transaction, "mesh", mesh)
    return {
        "transaction_id": transaction.transaction_id,
        "changed": True,
        "operation": operation_type,
        "data_scope": data_scope,
        "object": {"name": obj.name, "session_identity": session_identity("object", obj)},
        "mesh": _mesh_reference(mesh),
        "before_mesh": initial_mesh_reference,
        "after_mesh": _mesh_reference(mesh),
        "before_mesh_revision_id": before_revision,
        "after_mesh_revision_id": mesh_revision_id(mesh),
        "before_mesh_fingerprint": before_fingerprint,
        "after_mesh_fingerprint": after_fingerprint,
        "before_topology_fingerprint": before_topology,
        "after_topology_fingerprint": topology_fingerprint(mesh),
        "before_counts": before_counts,
        "after_counts": mesh_counts(mesh),
        "components": components,
        "evidence": evidence,
        "component_map": component_map.summary() if component_map is not None else None,
        "rebound_selection": rebound,
        "created_selections": created_selections,
        "attribute_effects": {
            "schema_preserved": True,
            "attributes": before_attributes,
            "interpolation": "BLENDER_BMESH",
        },
        "delta": {"type": "mesh_edit", "recorded": True, "snapshot_reused": not new_guard},
        "warnings": _component_warnings(components),
    }
