"""Topology-preserving semantic deformation of revision-bound SelectionSets."""

from __future__ import annotations

import math
from array import array
from typing import Any

import bmesh
import bpy
from mathutils import Matrix, Vector

from .capture_model import CaptureBook, CaptureEvidence
from .execution_budget import check_deadline
from .lookdev_ops import session_identity
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
)
from .mesh_query_ops import validate_selection
from .mesh_resource_model import MeshResourceBook, MeshResourceError, SelectionRecord
from .mesh_surface_ops import validate_surface
from .structural_ops import refresh_structure_guard_if_present
from .transaction_model import MeshEditDelta, Transaction

DEFORM_OPERATIONS = {
    "set_positions",
    "smooth",
    "relax",
    "project",
    "shrinkwrap",
    "inflate",
    "flatten",
}


def _closed(operation: Any, required: set[str], optional: set[str]) -> dict[str, Any]:
    optional = optional | {"maximum_displacement"}
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


def _integer(value: Any, field: str, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise MeshOperationError(
            "MESH_OPERATION_INVALID",
            f"{field} must be an integer between {minimum} and {maximum}",
        )
    return value


def _boolean(value: Any, field: str) -> bool:
    if type(value) is not bool:
        raise MeshOperationError("MESH_OPERATION_INVALID", f"{field} must be a boolean")
    return value


def _validate_operation(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise MeshOperationError("MESH_OPERATION_INVALID", "operation must be an object")
    operation_type = raw.get("type")
    if "maximum_displacement" in raw:
        _number(raw["maximum_displacement"], "maximum_displacement", 0, 1_000_000)
    common = {"type", "selection_id"}
    if operation_type == "set_positions":
        operation = _closed(raw, common | {"positions"}, {"mode", "space"})
        positions = operation["positions"]
        if not isinstance(positions, list) or not 1 <= len(positions) <= 4096:
            raise MeshOperationError(
                "MESH_OPERATION_INVALID", "positions must contain 1-4096 vectors"
            )
        for index, position in enumerate(positions):
            _vector(position, f"positions[{index}]")
        if operation.get("mode", "ABSOLUTE") not in {"ABSOLUTE", "OFFSET"}:
            raise MeshOperationError("MESH_OPERATION_INVALID", "mode is invalid")
        if operation.get("space", "LOCAL") not in {"LOCAL", "WORLD"}:
            raise MeshOperationError("MESH_OPERATION_INVALID", "space is invalid")
        return operation
    if operation_type in {"smooth", "relax"}:
        operation = _closed(
            raw,
            common,
            {"iterations", "factor", "preserve_boundary"},
        )
        _integer(operation.get("iterations", 1), "iterations", 1, 64)
        _number(operation.get("factor", 0.5), "factor", 0, 1)
        _boolean(operation.get("preserve_boundary", True), "preserve_boundary")
        return operation
    if operation_type == "project":
        operation = _closed(
            raw,
            common | {"surface_id", "maximum_distance"},
            {"direction", "axis", "vector", "capture_id", "offset", "side", "on_miss"},
        )
        direction = operation.get("direction", "CLOSEST_POINT")
        if direction not in {"CLOSEST_POINT", "NORMAL", "AXIS", "VECTOR", "VIEW_RAY"}:
            raise MeshOperationError("MESH_OPERATION_INVALID", "project direction is invalid")
        required_field = {"AXIS": "axis", "VECTOR": "vector", "VIEW_RAY": "capture_id"}.get(
            direction
        )
        if required_field is not None and required_field not in operation:
            raise MeshOperationError(
                "MESH_OPERATION_INVALID", f"{direction} requires {required_field}"
            )
        if "vector" in operation and _vector(operation["vector"], "vector").length_squared == 0:
            raise MeshOperationError("MESH_OPERATION_INVALID", "vector must be non-zero")
        if operation.get("axis") not in {None, "X", "Y", "Z", "-X", "-Y", "-Z"}:
            raise MeshOperationError("MESH_OPERATION_INVALID", "axis is invalid")
        _validate_projection_options(operation)
        return operation
    if operation_type == "shrinkwrap":
        operation = _closed(
            raw,
            common | {"surface_id", "maximum_distance"},
            {"iterations", "factor", "offset", "side", "on_miss"},
        )
        _integer(operation.get("iterations", 1), "iterations", 1, 16)
        factor = _number(operation.get("factor", 1), "factor", 0, 1)
        if factor == 0:
            raise MeshOperationError("MESH_OPERATION_INVALID", "factor must be greater than 0")
        _validate_projection_options(operation)
        return operation
    if operation_type == "inflate":
        operation = _closed(raw, common | {"amount"}, set())
        _number(operation["amount"], "amount", -100_000, 100_000)
        return operation
    if operation_type == "flatten":
        operation = _closed(raw, common, {"plane", "factor", "space"})
        _number(operation.get("factor", 1), "factor", 0, 1)
        if operation.get("space", "LOCAL") not in {"LOCAL", "WORLD"}:
            raise MeshOperationError("MESH_OPERATION_INVALID", "space is invalid")
        plane = operation.get("plane", {"type": "BEST_FIT"})
        if not isinstance(plane, dict) or plane.get("type") not in {"BEST_FIT", "EXPLICIT"}:
            raise MeshOperationError("MESH_OPERATION_INVALID", "plane is invalid")
        expected = {"type"} if plane.get("type") == "BEST_FIT" else {"type", "origin", "normal"}
        if set(plane) != expected:
            raise MeshOperationError("MESH_OPERATION_INVALID", "plane fields are invalid")
        if plane["type"] == "EXPLICIT":
            _vector(plane["origin"], "plane.origin")
            if _vector(plane["normal"], "plane.normal").length_squared == 0:
                raise MeshOperationError("MESH_OPERATION_INVALID", "plane normal is zero")
        return operation
    raise MeshOperationError("MESH_OPERATION_INVALID", f"Unsupported deformation: {operation_type}")


def _validate_projection_options(operation: dict[str, Any]) -> None:
    maximum = _number(operation.get("maximum_distance"), "maximum_distance", 0, 1_000_000)
    if maximum == 0:
        raise MeshOperationError(
            "MESH_OPERATION_INVALID", "maximum_distance must be greater than 0"
        )
    _number(operation.get("offset", 0), "offset", -100_000, 100_000)
    if operation.get("side", "ANY") not in {"ANY", "FRONT", "BACK"}:
        raise MeshOperationError("MESH_OPERATION_INVALID", "side is invalid")
    if operation.get("on_miss", "KEEP") not in {"KEEP", "ERROR"}:
        raise MeshOperationError("MESH_OPERATION_INVALID", "on_miss is invalid")


def _capture(book: CaptureBook, capture_id: Any) -> CaptureEvidence:
    if not isinstance(capture_id, str) or not capture_id:
        raise MeshOperationError("MESH_OPERATION_INVALID", "capture_id is required")
    evidence = book.get(capture_id)
    if evidence is None:
        raise MeshResourceError(
            "MESH_RESOURCE_NOT_FOUND", f"Capture evidence does not exist: {capture_id}"
        )
    return evidence


def _weight(selection: SelectionRecord, position: int) -> float:
    return selection.weights[position] if selection.weights is not None else 1.0


def _boundary_vertices(bm: Any) -> set[Any]:
    return {
        vertex
        for vertex in bm.verts
        if any(len(edge.link_faces) != 2 for edge in vertex.link_edges)
    }


def _selected_vertices(bm: Any, selection: SelectionRecord) -> list[Any]:
    if selection.domain != "VERTEX":
        raise MeshOperationError(
            "MESH_OPERATION_INVALID", "deformation operations require a VERTEX SelectionSet"
        )
    bm.verts.ensure_lookup_table()
    if selection.indices and selection.indices[-1] >= len(bm.verts):
        raise MeshOperationError(
            "MESH_COMPONENT_INDEX_INVALID", "SelectionSet contains an invalid vertex index"
        )
    return [bm.verts[index] for index in selection.indices]


def _move(vertex: Any, target: Any, operation: dict[str, Any]) -> None:
    check_deadline()
    limit = operation.get("maximum_displacement")
    if limit is not None:
        travel = operation["_travel"]
        distance = (operation["_distance_matrix"] @ (target - vertex.co)).length
        cumulative = travel.get(vertex.index, 0.0) + distance
        if cumulative > limit:
            raise MeshOperationError(
                "MESH_EDIT_FAILED",
                "Cumulative vertex displacement exceeds the requested limit",
                details={
                    "reason": "DISPLACEMENT_LIMIT",
                    "vertex": vertex.index,
                    "cumulative_displacement_world": cumulative,
                    "maximum_displacement": limit,
                    "writeback": False,
                },
            )
        travel[vertex.index] = cumulative
    vertex.co = target


def _set_positions(
    bm: Any,
    obj: Any,
    selection: SelectionRecord,
    operation: dict[str, Any],
) -> dict[str, Any]:
    vertices = _selected_vertices(bm, selection)
    positions = operation["positions"]
    if len(positions) != len(vertices):
        raise MeshOperationError(
            "MESH_OPERATION_INVALID", "positions must match the SelectionSet component count"
        )
    mode = operation.get("mode", "ABSOLUTE")
    space = operation.get("space", "LOCAL")
    inverse = obj.matrix_world.inverted_safe()
    inverse_direction = inverse.to_3x3()
    for index, (vertex, raw) in enumerate(zip(vertices, positions, strict=True)):
        value = _vector(raw, f"positions[{index}]")
        if space == "WORLD":
            value = inverse @ value if mode == "ABSOLUTE" else inverse_direction @ value
        target = value if mode == "ABSOLUTE" else vertex.co + value
        weight = _weight(selection, index)
        _move(vertex, vertex.co.lerp(target, weight), operation)
    return {"affected_vertices": len(vertices), "mode": mode, "space": space}


def _smooth_or_relax(
    bm: Any,
    selection: SelectionRecord,
    operation: dict[str, Any],
    *,
    relax: bool,
) -> dict[str, Any]:
    vertices = _selected_vertices(bm, selection)
    factor = float(operation.get("factor", 0.5))
    iterations = int(operation.get("iterations", 1))
    boundary = _boundary_vertices(bm) if operation.get("preserve_boundary", True) else set()
    for _iteration in range(iterations):
        bm.normal_update()
        targets: list[tuple[Any, Vector]] = []
        for position, vertex in enumerate(vertices):
            if vertex in boundary or not vertex.link_edges:
                continue
            neighbors = [edge.other_vert(vertex).co for edge in vertex.link_edges]
            average = sum(neighbors, Vector()) / len(neighbors)
            displacement = average - vertex.co
            if relax:
                displacement -= vertex.normal * displacement.dot(vertex.normal)
            amount = factor * _weight(selection, position)
            targets.append((vertex, vertex.co + displacement * amount))
        for vertex, target in targets:
            _move(vertex, target, operation)
    return {
        "affected_vertices": len(vertices),
        "iterations": iterations,
        "preserved_boundary_vertices": len(set(vertices) & boundary),
    }


def _axis_direction(value: str) -> Vector:
    direction = Vector((0.0, 0.0, 0.0))
    sign = -1.0 if value.startswith("-") else 1.0
    direction[{"X": 0, "Y": 1, "Z": 2}[value[-1]]] = sign
    return direction


def _view_direction(capture: CaptureEvidence, point: Vector) -> Vector:
    inverse = Matrix(capture.view_matrix).inverted_safe()
    if capture.projection_kind == "ORTHO":
        direction = inverse.to_3x3() @ Vector((0.0, 0.0, -1.0))
    else:
        direction = point - inverse.translation
    if direction.length_squared == 0:
        raise MeshOperationError("MESH_OPERATION_INVALID", "VIEW_RAY direction is undefined")
    direction.normalize()
    return direction


def _projection_direction(
    operation: dict[str, Any],
    capture: CaptureEvidence | None,
    obj: Any,
    vertex: Any,
    point: Vector,
) -> Vector | None:
    mode = operation.get("direction", "CLOSEST_POINT")
    if mode == "CLOSEST_POINT":
        return None
    if mode == "NORMAL":
        direction = obj.matrix_world.to_3x3() @ vertex.normal
    elif mode == "AXIS":
        direction = _axis_direction(str(operation["axis"]))
    elif mode == "VECTOR":
        direction = _vector(operation["vector"], "vector")
    else:
        if capture is None:
            raise MeshOperationError("MESH_OPERATION_INVALID", "VIEW_RAY requires capture")
        return _view_direction(capture, point)
    if direction.length_squared == 0:
        raise MeshOperationError("MESH_OPERATION_INVALID", "projection direction is zero")
    direction.normalize()
    return direction


def _side_allowed(side: str, source: Vector, location: Vector, normal: Vector) -> bool:
    if side == "ANY":
        return True
    front = (source - location).dot(normal) >= 0
    return front if side == "FRONT" else not front


def _surface_target(
    surface: Any,
    source: Vector,
    direction: Vector | None,
    maximum_distance: float,
    side: str,
    offset: float,
) -> tuple[Vector, Vector, float] | None:
    hit = (
        surface.bvh.find_nearest(source, maximum_distance)
        if direction is None
        else surface.bvh.ray_cast(source, direction, maximum_distance)
    )
    location, normal, triangle_index, distance = hit
    if location is None or normal is None or triangle_index is None or distance is None:
        return None
    if not _side_allowed(side, source, location, normal):
        return None
    return location + normal * offset, normal, float(distance)


def _project(
    bm: Any,
    obj: Any,
    selection: SelectionRecord,
    surface: Any,
    capture: CaptureEvidence | None,
    operation: dict[str, Any],
) -> dict[str, Any]:
    vertices = _selected_vertices(bm, selection)
    inverse = obj.matrix_world.inverted_safe()
    maximum = float(operation["maximum_distance"])
    offset = float(operation.get("offset", 0))
    side = str(operation.get("side", "ANY"))
    misses = 0
    distances = []
    targets: list[tuple[Any, Vector, float]] = []
    bm.normal_update()
    for position, vertex in enumerate(vertices):
        source = obj.matrix_world @ vertex.co
        direction = _projection_direction(operation, capture, obj, vertex, source)
        target = _surface_target(surface, source, direction, maximum, side, offset)
        if target is None:
            misses += 1
            continue
        location, _normal, distance = target
        targets.append((vertex, inverse @ location, _weight(selection, position)))
        distances.append(distance)
    if misses and operation.get("on_miss", "KEEP") == "ERROR":
        raise MeshOperationError(
            "MESH_EDIT_FAILED",
            "One or more selected vertices did not hit the target surface",
            details={"misses": misses, "selected": len(vertices)},
        )
    for vertex, target, weight in targets:
        _move(vertex, vertex.co.lerp(target, weight), operation)
    return {
        "affected_vertices": len(targets),
        "misses": misses,
        "maximum_input_distance": max(distances) if distances else None,
    }


def _shrinkwrap(
    bm: Any,
    obj: Any,
    selection: SelectionRecord,
    surface: Any,
    operation: dict[str, Any],
) -> dict[str, Any]:
    vertices = _selected_vertices(bm, selection)
    inverse = obj.matrix_world.inverted_safe()
    maximum = float(operation["maximum_distance"])
    offset = float(operation.get("offset", 0))
    side = str(operation.get("side", "ANY"))
    factor = float(operation.get("factor", 1))
    iterations = int(operation.get("iterations", 1))
    misses = 0
    for _iteration in range(iterations):
        iteration_misses = 0
        targets: list[tuple[Any, Vector, float]] = []
        for position, vertex in enumerate(vertices):
            source = obj.matrix_world @ vertex.co
            target = _surface_target(surface, source, None, maximum, side, offset)
            if target is None:
                iteration_misses += 1
                continue
            targets.append((vertex, inverse @ target[0], factor * _weight(selection, position)))
        if iteration_misses and operation.get("on_miss", "KEEP") == "ERROR":
            raise MeshOperationError(
                "MESH_EDIT_FAILED",
                "One or more selected vertices did not resolve a shrinkwrap target",
                details={"misses": iteration_misses, "selected": len(vertices)},
            )
        for vertex, target, weight in targets:
            _move(vertex, vertex.co.lerp(target, weight), operation)
        misses = iteration_misses
    return {"affected_vertices": len(vertices) - misses, "misses": misses, "iterations": iterations}


def _inflate(bm: Any, selection: SelectionRecord, operation: dict[str, Any]) -> dict[str, Any]:
    vertices = _selected_vertices(bm, selection)
    amount = float(operation["amount"])
    bm.normal_update()
    for position, vertex in enumerate(vertices):
        _move(vertex, vertex.co + vertex.normal * amount * _weight(selection, position), operation)
    return {"affected_vertices": len(vertices), "amount": amount}


def _smallest_eigenvector(points: list[Vector]) -> Vector:
    center = sum(points, Vector()) / len(points)
    covariance = [[0.0, 0.0, 0.0] for _ in range(3)]
    for point in points:
        delta = point - center
        for row in range(3):
            for column in range(3):
                covariance[row][column] += delta[row] * delta[column]
    vectors = [[1.0 if row == column else 0.0 for column in range(3)] for row in range(3)]
    for _iteration in range(16):
        row, column = max(
            ((0, 1), (0, 2), (1, 2)),
            key=lambda pair: abs(covariance[pair[0]][pair[1]]),
        )
        if abs(covariance[row][column]) < 1e-12:
            break
        angle = 0.5 * math.atan2(
            2 * covariance[row][column],
            covariance[column][column] - covariance[row][row],
        )
        cosine, sine = math.cos(angle), math.sin(angle)
        for index in range(3):
            left, right = covariance[index][row], covariance[index][column]
            covariance[index][row] = cosine * left - sine * right
            covariance[index][column] = sine * left + cosine * right
        for index in range(3):
            top, bottom = covariance[row][index], covariance[column][index]
            covariance[row][index] = cosine * top - sine * bottom
            covariance[column][index] = sine * top + cosine * bottom
        for index in range(3):
            left, right = vectors[index][row], vectors[index][column]
            vectors[index][row] = cosine * left - sine * right
            vectors[index][column] = sine * left + cosine * right
    smallest = min(range(3), key=lambda index: covariance[index][index])
    normal = Vector(tuple(vectors[index][smallest] for index in range(3)))
    if normal.length_squared == 0:
        raise MeshOperationError("MESH_OPERATION_INVALID", "best-fit plane is undefined")
    normal.normalize()
    return normal


def _flatten(
    bm: Any,
    obj: Any,
    selection: SelectionRecord,
    operation: dict[str, Any],
) -> dict[str, Any]:
    vertices = _selected_vertices(bm, selection)
    space = operation.get("space", "LOCAL")
    matrix = obj.matrix_world if space == "WORLD" else Matrix.Identity(4)
    inverse = matrix.inverted_safe()
    points = [matrix @ vertex.co for vertex in vertices]
    plane = operation.get("plane", {"type": "BEST_FIT"})
    if plane.get("type") == "EXPLICIT":
        origin = _vector(plane["origin"], "plane.origin")
        normal = _vector(plane["normal"], "plane.normal").normalized()
    else:
        if len(points) < 3:
            raise MeshOperationError(
                "MESH_OPERATION_INVALID", "BEST_FIT flatten requires at least three vertices"
            )
        origin = sum(points, Vector()) / len(points)
        normal = _smallest_eigenvector(points)
    factor = float(operation.get("factor", 1))
    for position, (vertex, point) in enumerate(zip(vertices, points, strict=True)):
        target = point - normal * (point - origin).dot(normal)
        amount = factor * _weight(selection, position)
        _move(vertex, vertex.co.lerp(inverse @ target, amount), operation)
    return {
        "affected_vertices": len(vertices),
        "plane_origin": list(origin),
        "plane_normal": list(normal),
        "space": space,
    }


def _rebind_selection(
    book: MeshResourceBook,
    source: SelectionRecord,
    obj: Any,
    mesh: Any,
    operation_type: str,
) -> SelectionRecord:
    return book.add_selection(
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
            "type": "rebind_after_edit",
            "operation": operation_type,
            "source_selection_id": source.selection_id,
        },
    )


def _write_vertex_positions(mesh: Any, bm: Any) -> None:
    """Write deformation coordinates without rebuilding Mesh topology or data layers."""

    bm.verts.index_update()
    if len(mesh.vertices) != len(bm.verts):
        raise MeshOperationError(
            "MESH_EDIT_FAILED",
            "A topology-preserving deformation changed the vertex count",
        )
    coordinates = array("f", [0.0]) * (len(bm.verts) * 3)
    for vertex in bm.verts:
        offset = int(vertex.index) * 3
        coordinates[offset : offset + 3] = array("f", vertex.co)
    if coordinates:
        mesh.vertices.foreach_set("co", coordinates)
    mesh.update()


def edit_mesh_deform(
    transaction: Transaction,
    book: MeshResourceBook,
    captures: CaptureBook,
    params: dict[str, Any],
) -> dict[str, Any]:
    obj, initial_mesh, data_scope, _refs = _validate_mesh_target(params)
    initial_mesh_reference = _mesh_reference(initial_mesh)
    operation = _validate_operation(params.get("operation"))
    operation = {**operation, "_travel": {}, "_distance_matrix": obj.matrix_world.to_3x3()}
    operation_type = str(operation["type"])
    selection_id = operation.get("selection_id")
    if not isinstance(selection_id, str) or not selection_id:
        raise MeshOperationError("MESH_OPERATION_INVALID", "selection_id is required")
    selection = book.selection(selection_id)
    selection_obj, selection_mesh = validate_selection(selection)
    if selection_obj is not obj or selection_mesh is not initial_mesh:
        raise MeshOperationError(
            "MESH_RESOURCE_STALE",
            "SelectionSet does not target the requested Mesh",
            kind="conflict",
        )
    surface = None
    if operation_type in {"project", "shrinkwrap"}:
        surface = book.surface(str(operation["surface_id"]))
        validate_surface(surface)
    capture = None
    if operation_type == "project" and operation.get("direction") == "VIEW_RAY":
        capture = _capture(captures, operation.get("capture_id"))

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
    before_revision = mesh_revision_id(initial_mesh)
    before_counts = mesh_counts(mesh)
    call_snapshot = mesh.copy()
    call_snapshot.name = f"{mesh.name}.MCP-Call-Snapshot"
    bm = bmesh.new()
    try:
        bm.from_mesh(mesh)
        component_baseline = _bmesh_baseline(bm)
        requested = {"vertices": _index_page(list(selection.indices))}
        if operation_type == "set_positions":
            evidence = _set_positions(bm, obj, selection, operation)
        elif operation_type in {"smooth", "relax"}:
            evidence = _smooth_or_relax(bm, selection, operation, relax=operation_type == "relax")
        elif operation_type == "project":
            evidence = _project(bm, obj, selection, surface, capture, operation)
        elif operation_type == "shrinkwrap":
            evidence = _shrinkwrap(bm, obj, selection, surface, operation)
        elif operation_type == "inflate":
            evidence = _inflate(bm, selection, operation)
        else:
            evidence = _flatten(bm, obj, selection, operation)
        if operation.get("maximum_displacement") is not None:
            evidence["maximum_cumulative_displacement_world"] = max(
                operation["_travel"].values(), default=0.0
            )
            evidence["displacement_limit"] = operation["maximum_displacement"]
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
                "MESH_BUDGET_EXCEEDED", "Deformation result exceeds the Mesh budget"
            )
        _write_vertex_positions(mesh, bm)
        if topology_fingerprint(mesh) != before_topology:
            raise MeshOperationError(
                "MESH_EDIT_FAILED", "A topology-preserving operation changed topology"
            )
    except (MeshOperationError, MeshResourceError) as exc:
        _restore_failed_edit(mesh, call_snapshot, before_fingerprint, exc)
        if new_guard:
            _remove_new_guard(transaction, guard)
        raise
    except Exception as exc:
        _restore_failed_edit(mesh, call_snapshot, before_fingerprint, exc)
        if new_guard:
            _remove_new_guard(transaction, guard)
        raise MeshOperationError(
            "MESH_EDIT_FAILED",
            f"Mesh deformation failed: {type(exc).__name__}",
            kind="blender_api",
            details={"error_type": type(exc).__name__, "message": str(exc)},
        ) from exc
    finally:
        bm.free()
        _remove_temporary_mesh(call_snapshot)

    after_fingerprint = mesh_fingerprint(mesh)
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
            "rebound_selection": selection.summary(),
            "delta": {"type": "mesh_edit", "recorded": False},
            "warnings": _component_warnings(components),
        }

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
    rebound = _rebind_selection(book, selection, obj, mesh, operation_type)
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
        "rebound_selection": rebound.summary(),
        "delta": {"type": "mesh_edit", "recorded": True, "snapshot_reused": not new_guard},
        "warnings": _component_warnings(components),
    }
