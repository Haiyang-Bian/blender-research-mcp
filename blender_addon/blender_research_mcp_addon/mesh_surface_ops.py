"""Bounded evaluated-surface references, spatial queries, and Mesh validation."""

from __future__ import annotations

import hashlib
import math
import struct
import uuid
from statistics import fmean
from typing import Any

import bpy
from mathutils import Matrix, Vector
from mathutils.bvhtree import BVHTree

from .lookdev_ops import session_identity
from .mesh_ops import mesh_revision_id
from .mesh_query_ops import validate_selection
from .mesh_resource_model import (
    MeshResourceBook,
    MeshResourceError,
    SelectionRecord,
    SurfaceRecord,
)


def _matrix_rows(matrix: Matrix) -> tuple[tuple[float, float, float, float], ...]:
    return tuple(tuple(float(value) for value in row) for row in matrix)


def _matrix_matches(matrix: Matrix, rows: tuple[tuple[float, float, float, float], ...]) -> bool:
    return all(
        math.isclose(float(matrix[row][column]), rows[row][column], rel_tol=1e-6, abs_tol=1e-6)
        for row in range(4)
        for column in range(4)
    )


def _surface_fingerprint(
    vertices: tuple[tuple[float, float, float], ...],
    triangles: tuple[tuple[int, int, int], ...],
) -> str:
    hasher = hashlib.sha256()
    hasher.update(struct.pack("<QQ", len(vertices), len(triangles)))
    for vertex in vertices:
        hasher.update(struct.pack("<fff", *vertex))
    for triangle in triangles:
        hasher.update(struct.pack("<III", *triangle))
    return hasher.hexdigest()


def _closed_and_oriented(triangles: tuple[tuple[int, int, int], ...]) -> tuple[bool, bool]:
    edges: dict[tuple[int, int], list[int]] = {}
    for triangle in triangles:
        for first, second in zip(triangle, (triangle[1], triangle[2], triangle[0]), strict=True):
            key = (min(first, second), max(first, second))
            direction = 1 if (first, second) == key else -1
            edges.setdefault(key, []).append(direction)
    closed = bool(edges) and all(len(directions) == 2 for directions in edges.values())
    oriented = all(sum(directions) == 0 for directions in edges.values() if len(directions) == 2)
    return closed, oriented


def _geometry_from_object(
    obj: Any,
    geometry: str,
) -> tuple[
    tuple[tuple[float, float, float], ...],
    tuple[tuple[int, int, int], ...],
]:
    evaluated = None
    if geometry == "EVALUATED":
        evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
        depsgraph = bpy.context.evaluated_depsgraph_get()
        mesh = evaluated.to_mesh(preserve_all_data_layers=True, depsgraph=depsgraph)
    else:
        mesh = obj.data
    try:
        mesh.calc_loop_triangles()
        world = obj.matrix_world
        vertices = tuple(
            tuple(float(value) for value in (world @ vertex.co))
            for vertex in mesh.vertices
        )
        triangles = tuple(tuple(map(int, triangle.vertices)) for triangle in mesh.loop_triangles)
        if not vertices or not triangles:
            raise MeshResourceError(
                "MESH_SURFACE_EMPTY", f"Surface geometry is empty: {obj.name}"
            )
        if len(triangles) > 2_000_000:
            raise MeshResourceError(
                "MESH_RESOURCE_BUDGET_EXCEEDED", "Surface exceeds 2000000 triangles"
            )
        return vertices, triangles
    finally:
        if evaluated is not None:
            evaluated.to_mesh_clear()


def _surface_object(params: dict[str, Any]) -> Any:
    object_name = params.get("object_name")
    if not isinstance(object_name, str) or not object_name:
        raise MeshResourceError("OBJECT_NAME_INVALID", "object_name must be non-empty")
    obj = bpy.data.objects.get(object_name)
    if obj is None:
        raise MeshResourceError("OBJECT_NOT_FOUND", f"Object does not exist: {object_name}")
    if obj.type != "MESH" or obj.data is None:
        raise MeshResourceError(
            "MESH_OBJECT_UNSUPPORTED", f"Surface preparation requires MESH: {object_name}"
        )
    if session_identity("object", obj) != params.get("expected_object_identity"):
        raise MeshResourceError(
            "OBJECT_IDENTITY_MISMATCH", "Surface object identity changed", kind="conflict"
        )
    actual_revision = mesh_revision_id(obj.data)
    if actual_revision != params.get("expected_mesh_revision_id"):
        raise MeshResourceError(
            "MESH_RESOURCE_STALE",
            "Surface base Mesh revision changed",
            kind="conflict",
            details={"actual_mesh_revision_id": actual_revision},
        )
    return obj


def prepare_surface(book: MeshResourceBook, params: dict[str, Any]) -> dict[str, Any]:
    obj = _surface_object(params)
    geometry = params.get("geometry", "EVALUATED")
    if geometry not in {"BASE", "EVALUATED"}:
        raise MeshResourceError(
            "MESH_SURFACE_QUERY_INVALID", "geometry must be BASE or EVALUATED"
        )
    vertices, triangles = _geometry_from_object(obj, geometry)
    closed, oriented = _closed_and_oriented(triangles)
    surface = SurfaceRecord(
        surface_id=str(uuid.uuid4()),
        scene=bpy.context.scene.name,
        view_layer=bpy.context.view_layer.name,
        frame=int(bpy.context.scene.frame_current),
        object_name=obj.name,
        object_identity=session_identity("object", obj),
        mesh_name=obj.data.name,
        mesh_identity=session_identity("mesh", obj.data),
        mesh_revision_id=mesh_revision_id(obj.data),
        geometry=geometry,
        object_matrix=_matrix_rows(obj.matrix_world),
        evaluated_fingerprint=_surface_fingerprint(vertices, triangles),
        vertex_count=len(vertices),
        triangle_count=len(triangles),
        closed_manifold=closed,
        consistently_oriented=oriented,
        bvh=BVHTree.FromPolygons(vertices, triangles, all_triangles=True),
        vertices=vertices,
        triangles=triangles,
    )
    book.add_surface(surface)
    result = surface.summary()
    result["resource_counts"] = {
        "selections": book.selection_count,
        "surfaces": book.surface_count,
    }
    return result


def validate_surface(surface: SurfaceRecord) -> Any:
    if (
        bpy.context.scene.name != surface.scene
        or bpy.context.view_layer.name != surface.view_layer
        or int(bpy.context.scene.frame_current) != surface.frame
    ):
        raise MeshResourceError(
            "MESH_RESOURCE_STALE", "Surface scene, view layer, or frame changed", kind="conflict"
        )
    obj = bpy.data.objects.get(surface.object_name)
    if obj is None or session_identity("object", obj) != surface.object_identity:
        raise MeshResourceError(
            "MESH_RESOURCE_STALE", "Surface object identity changed", kind="conflict"
        )
    if (
        obj.data is None
        or session_identity("mesh", obj.data) != surface.mesh_identity
        or mesh_revision_id(obj.data) != surface.mesh_revision_id
        or not _matrix_matches(obj.matrix_world, surface.object_matrix)
    ):
        raise MeshResourceError(
            "MESH_RESOURCE_STALE", "Surface base evidence changed", kind="conflict"
        )
    vertices, triangles = _geometry_from_object(obj, surface.geometry)
    fingerprint = _surface_fingerprint(vertices, triangles)
    if fingerprint != surface.evaluated_fingerprint:
        raise MeshResourceError(
            "MESH_RESOURCE_STALE",
            "Evaluated surface geometry changed",
            kind="conflict",
            details={
                "expected_evaluated_fingerprint": surface.evaluated_fingerprint,
                "actual_evaluated_fingerprint": fingerprint,
            },
        )
    return obj


def _selection_world_vertices(record: SelectionRecord, obj: Any, mesh: Any) -> list[Vector]:
    if record.domain != "VERTEX":
        raise MeshResourceError(
            "MESH_SURFACE_QUERY_INVALID", "Surface queries require a VERTEX SelectionSet"
        )
    return [obj.matrix_world @ mesh.vertices[index].co for index in record.indices]


def _percentile(values: list[float], fraction: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1 - weight) + ordered[upper] * weight


def _distance_summary(values: list[float], signed: list[float]) -> dict[str, Any]:
    if not values:
        return {
            "count": 0,
            "minimum": None,
            "maximum": None,
            "mean": None,
            "rms": None,
            "p50": None,
            "p95": None,
            "p99": None,
            "signed_minimum": None,
            "signed_maximum": None,
        }
    return {
        "count": len(values),
        "minimum": min(values),
        "maximum": max(values),
        "mean": fmean(values),
        "rms": math.sqrt(fmean(value * value for value in values)),
        "p50": _percentile(values, 0.5),
        "p95": _percentile(values, 0.95),
        "p99": _percentile(values, 0.99),
        "signed_minimum": min(signed) if signed else None,
        "signed_maximum": max(signed) if signed else None,
    }


def _threshold_selection(
    book: MeshResourceBook,
    source: SelectionRecord,
    positions: list[int],
    query: dict[str, Any],
) -> dict[str, Any] | None:
    if not positions:
        return None
    indices = tuple(source.indices[position] for position in positions)
    weights = (
        tuple(source.weights[position] for position in positions)
        if source.weights is not None
        else None
    )
    record = book.add_selection(
        object_name=source.object_name,
        object_identity=source.object_identity,
        mesh_name=source.mesh_name,
        mesh_identity=source.mesh_identity,
        mesh_revision_id=source.mesh_revision_id,
        mesh_fingerprint=source.mesh_fingerprint,
        expected_users=source.expected_users,
        expected_user_objects=source.expected_user_objects,
        domain=source.domain,
        indices=indices,
        weights=weights,
        source_query=query,
    )
    return record.summary()


def query_surface(book: MeshResourceBook, params: dict[str, Any]) -> dict[str, Any]:
    selection_id = params.get("selection_id")
    surface_id = params.get("surface_id")
    if not isinstance(selection_id, str) or not isinstance(surface_id, str):
        raise MeshResourceError(
            "MESH_SURFACE_QUERY_INVALID", "selection_id and surface_id are required"
        )
    selection = book.selection(selection_id)
    obj, mesh = validate_selection(selection)
    surface = book.surface(surface_id)
    validate_surface(surface)
    mode = params.get("mode", "CLOSEST_POINT")
    maximum_distance = float(params.get("maximum_distance", 1_000_000))
    threshold = params.get("threshold")
    threshold_value = float(threshold) if threshold is not None else None
    sample_limit = int(params.get("sample_limit", 64))
    points = _selection_world_vertices(selection, obj, mesh)
    direction_raw = params.get("direction")
    direction = None
    if mode == "RAYCAST":
        if not isinstance(direction_raw, dict):
            raise MeshResourceError(
                "MESH_SURFACE_QUERY_INVALID", "RAYCAST requires a world-space direction"
            )
        direction = Vector(
            tuple(float(direction_raw[name]) for name in ("x", "y", "z"))
        )
        if direction.length_squared == 0:
            raise MeshResourceError(
                "MESH_SURFACE_QUERY_INVALID", "Ray direction must be non-zero"
            )
        direction.normalize()
    values: list[float] = []
    signed_values: list[float] = []
    misses = 0
    violations: list[int] = []
    samples = []
    paired_points = zip(selection.indices, points, strict=True)
    for position, (component_index, point) in enumerate(paired_points):
        if mode == "CLOSEST_POINT":
            hit = surface.bvh.find_nearest(point, maximum_distance)
        elif mode == "RAYCAST":
            hit = surface.bvh.ray_cast(point, direction, maximum_distance)
        else:
            raise MeshResourceError(
                "MESH_SURFACE_QUERY_INVALID", f"Unsupported surface query mode: {mode}"
            )
        location, normal, triangle_index, distance = hit
        if location is None or normal is None or triangle_index is None or distance is None:
            misses += 1
            continue
        distance_value = float(distance)
        signed_value = (
            math.copysign(distance_value, (point - location).dot(normal))
            if surface.sign_reliable
            else distance_value
        )
        values.append(distance_value)
        signed_values.append(signed_value)
        if threshold_value is not None and distance_value > threshold_value:
            violations.append(position)
        if len(samples) < sample_limit:
            samples.append(
                {
                    "component_index": component_index,
                    "point": list(point),
                    "location": list(location),
                    "normal": list(normal),
                    "triangle_index": int(triangle_index),
                    "distance": distance_value,
                    "signed_distance": signed_value if surface.sign_reliable else None,
                }
            )
    violation_selection = _threshold_selection(
        book,
        selection,
        violations,
        {"type": "surface_threshold", "surface_id": surface_id, "threshold": threshold},
    )
    return {
        "selection": selection.summary(),
        "surface": surface.summary(),
        "mode": mode,
        "sign_reliable": surface.sign_reliable,
        "distances": _distance_summary(values, signed_values),
        "misses": misses,
        "samples": samples,
        "sample_limit": sample_limit,
        "violation_selection": violation_selection,
    }


def _mesh_edge_face_counts(mesh: Any) -> list[int]:
    counts = [0] * len(mesh.edges)
    for loop in mesh.loops:
        counts[int(loop.edge_index)] += 1
    return counts


def _selection_from_indices(
    book: MeshResourceBook,
    source: SelectionRecord,
    domain: str,
    indices: tuple[int, ...],
    check: str,
) -> dict[str, Any] | None:
    if not indices:
        return None
    record = book.add_selection(
        object_name=source.object_name,
        object_identity=source.object_identity,
        mesh_name=source.mesh_name,
        mesh_identity=source.mesh_identity,
        mesh_revision_id=source.mesh_revision_id,
        mesh_fingerprint=source.mesh_fingerprint,
        expected_users=source.expected_users,
        expected_user_objects=source.expected_user_objects,
        domain=domain,
        indices=indices,
        weights=None,
        source_query={"type": "validation", "check": check},
    )
    return record.summary()


def validate_mesh(book: MeshResourceBook, params: dict[str, Any]) -> dict[str, Any]:
    selection_id = params.get("selection_id")
    check = params.get("check")
    if not isinstance(selection_id, str) or not isinstance(check, str):
        raise MeshResourceError(
            "MESH_VALIDATION_INVALID", "selection_id and check are required"
        )
    selection = book.selection(selection_id)
    obj, mesh = validate_selection(selection)
    tolerance = float(params.get("tolerance", 1e-6))
    if check == "NON_MANIFOLD":
        counts = _mesh_edge_face_counts(mesh)
        indices = tuple(index for index, count in enumerate(counts) if count != 2)
        result_selection = _selection_from_indices(book, selection, "EDGE", indices, check)
        return {"check": check, "count": len(indices), "selection": result_selection}
    if check == "DEGENERATE":
        indices = tuple(
            int(polygon.index) for polygon in mesh.polygons if float(polygon.area) <= tolerance
        )
        result_selection = _selection_from_indices(book, selection, "FACE", indices, check)
        return {"check": check, "count": len(indices), "selection": result_selection}
    if check == "ORIENTATION":
        mesh.calc_loop_triangles()
        triangles = tuple(tuple(map(int, triangle.vertices)) for triangle in mesh.loop_triangles)
        _closed, oriented = _closed_and_oriented(triangles)
        return {"check": check, "consistently_oriented": oriented}
    if check in {"DISTANCE", "PENETRATION"}:
        surface_id = params.get("surface_id")
        if not isinstance(surface_id, str):
            raise MeshResourceError(
                "MESH_VALIDATION_INVALID", f"{check} requires surface_id"
            )
        query_params = {
            "selection_id": selection_id,
            "surface_id": surface_id,
            "mode": "CLOSEST_POINT",
            "maximum_distance": params.get("maximum_distance", 1_000_000),
            "threshold": params.get("threshold"),
            "sample_limit": params.get("sample_limit", 64),
        }
        result = query_surface(book, query_params)
        result["check"] = check
        if check == "PENETRATION" and not result["sign_reliable"]:
            result["status"] = "SIGN_UNRELIABLE"
        else:
            result["status"] = "OK"
        return result
    if check in {"SELF_INTERSECTION", "TARGET_INTERSECTION"}:
        mesh.calc_loop_triangles()
        vertices = tuple(
            tuple(float(value) for value in (obj.matrix_world @ vertex.co))
            for vertex in mesh.vertices
        )
        triangles = tuple(tuple(map(int, triangle.vertices)) for triangle in mesh.loop_triangles)
        source_bvh = BVHTree.FromPolygons(vertices, triangles, all_triangles=True)
        if check == "SELF_INTERSECTION":
            pairs = source_bvh.overlap(source_bvh)
            intersecting = {
                first
                for first, second in pairs
                if first != second and not set(triangles[first]) & set(triangles[second])
            }
        else:
            surface_id = params.get("surface_id")
            if not isinstance(surface_id, str):
                raise MeshResourceError(
                    "MESH_VALIDATION_INVALID", "TARGET_INTERSECTION requires surface_id"
                )
            surface = book.surface(surface_id)
            validate_surface(surface)
            intersecting = {first for first, _second in source_bvh.overlap(surface.bvh)}
        polygon_indices = tuple(
            sorted({int(mesh.loop_triangles[index].polygon_index) for index in intersecting})
        )
        result_selection = _selection_from_indices(
            book, selection, "FACE", polygon_indices, check
        )
        return {"check": check, "count": len(polygon_indices), "selection": result_selection}
    raise MeshResourceError("MESH_VALIDATION_INVALID", f"Unsupported validation: {check}")
