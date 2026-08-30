"""Revision-bound Mesh component queries and immutable SelectionSet derivation."""

from __future__ import annotations

import heapq
import math
from collections import deque
from typing import Any

import bpy
from mathutils import Matrix, Vector

from .capture_model import CaptureBook, CaptureEvidence
from .context_ops import raycast_capture
from .lookdev_ops import session_identity
from .mesh_ops import mesh_fingerprint, mesh_revision_id, mesh_user_refs
from .mesh_resource_model import (
    MeshResourceBook,
    MeshResourceError,
    SelectionRecord,
)

DOMAINS = {"VERTEX", "EDGE", "FACE"}


def _object_and_mesh(params: dict[str, Any]) -> tuple[Any, Any]:
    object_name = params.get("object_name")
    if not isinstance(object_name, str) or not object_name:
        raise MeshResourceError("OBJECT_NAME_INVALID", "object_name must be non-empty")
    obj = bpy.data.objects.get(object_name)
    if obj is None:
        raise MeshResourceError("OBJECT_NOT_FOUND", f"Object does not exist: {object_name}")
    if obj.type != "MESH" or obj.data is None:
        raise MeshResourceError(
            "MESH_OBJECT_UNSUPPORTED", f"Mesh selection requires a MESH object: {object_name}"
        )
    mesh = obj.data
    expected_object_identity = params.get("expected_object_identity")
    if session_identity("object", obj) != expected_object_identity:
        raise MeshResourceError(
            "OBJECT_IDENTITY_MISMATCH", "Object identity changed", kind="conflict"
        )
    expected_mesh_identity = params.get("expected_mesh_identity")
    if session_identity("mesh", mesh) != expected_mesh_identity:
        raise MeshResourceError(
            "MESH_IDENTITY_MISMATCH", "Mesh identity changed", kind="conflict"
        )
    actual_revision = mesh_revision_id(mesh)
    if params.get("expected_mesh_revision_id") != actual_revision:
        raise MeshResourceError(
            "MESH_RESOURCE_STALE",
            "Mesh revision changed",
            kind="conflict",
            details={
                "expected_mesh_revision_id": params.get("expected_mesh_revision_id"),
                "actual_mesh_revision_id": actual_revision,
            },
        )
    return obj, mesh


def validate_selection(record: SelectionRecord) -> tuple[Any, Any]:
    obj = bpy.data.objects.get(record.object_name)
    mesh = bpy.data.meshes.get(record.mesh_name)
    if obj is None or session_identity("object", obj) != record.object_identity:
        raise MeshResourceError(
            "MESH_RESOURCE_STALE", "Selection object identity changed", kind="conflict"
        )
    if (
        mesh is None
        or obj.data is not mesh
        or session_identity("mesh", mesh) != record.mesh_identity
    ):
        raise MeshResourceError(
            "MESH_RESOURCE_STALE", "Selection Mesh identity changed", kind="conflict"
        )
    actual_fingerprint = mesh_fingerprint(mesh)
    actual_refs = mesh_user_refs(mesh)
    if (
        actual_fingerprint != record.mesh_fingerprint
        or int(mesh.users) != record.expected_users
        or actual_refs != record.expected_user_objects
        or mesh_revision_id(mesh) != record.mesh_revision_id
    ):
        raise MeshResourceError(
            "MESH_RESOURCE_STALE",
            "SelectionSet no longer matches the exact Mesh revision",
            kind="conflict",
            details={
                "expected_mesh_revision_id": record.mesh_revision_id,
                "actual_mesh_revision_id": mesh_revision_id(mesh),
            },
        )
    return obj, mesh


def _collection(mesh: Any, domain: str) -> Any:
    return {"VERTEX": mesh.vertices, "EDGE": mesh.edges, "FACE": mesh.polygons}[domain]


def _position(mesh: Any, domain: str, index: int) -> Vector:
    if domain == "VERTEX":
        return mesh.vertices[index].co.copy()
    if domain == "EDGE":
        edge = mesh.edges[index]
        return (mesh.vertices[edge.vertices[0]].co + mesh.vertices[edge.vertices[1]].co) * 0.5
    return mesh.polygons[index].center.copy()


def _normal(mesh: Any, domain: str, index: int) -> Vector:
    if domain == "VERTEX":
        return mesh.vertices[index].normal.copy()
    if domain == "FACE":
        return mesh.polygons[index].normal.copy()
    raise MeshResourceError(
        "MESH_SELECTION_QUERY_INVALID", "Normal queries support VERTEX or FACE domains"
    )


def _vector(raw: Any, field: str) -> Vector:
    if not isinstance(raw, dict) or set(raw) != {"x", "y", "z"}:
        raise MeshResourceError("MESH_SELECTION_QUERY_INVALID", f"{field} must be a vector")
    values = [raw[name] for name in ("x", "y", "z")]
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) for value in values):
        raise MeshResourceError("MESH_SELECTION_QUERY_INVALID", f"{field} must be finite")
    vector = Vector(tuple(float(value) for value in values))
    if not all(math.isfinite(value) for value in vector):
        raise MeshResourceError("MESH_SELECTION_QUERY_INVALID", f"{field} must be finite")
    return vector


def _indices(raw: Any, maximum: int) -> tuple[int, ...]:
    if not isinstance(raw, list) or not raw:
        raise MeshResourceError("MESH_SELECTION_QUERY_INVALID", "indices must be non-empty")
    if any(isinstance(value, bool) or not isinstance(value, int) for value in raw):
        raise MeshResourceError("MESH_SELECTION_QUERY_INVALID", "indices must be integers")
    result = tuple(sorted(set(raw)))
    if len(result) != len(raw) or result[0] < 0 or result[-1] >= maximum:
        raise MeshResourceError(
            "MESH_COMPONENT_INDEX_INVALID", "indices are duplicated or outside the domain"
        )
    return result


def _query_spatial(obj: Any, mesh: Any, domain: str, query: dict[str, Any]) -> tuple[int, ...]:
    query_type = query["type"]
    space = query.get("space", "LOCAL")
    transform = obj.matrix_world if space == "WORLD" else Matrix.Identity(4)
    indices: list[int] = []
    if query_type == "sphere":
        center = _vector(query.get("center"), "center")
        radius = float(query.get("radius", 0))
        for index in range(len(_collection(mesh, domain))):
            if (transform @ _position(mesh, domain, index) - center).length <= radius:
                indices.append(index)
    elif query_type == "box":
        minimum = _vector(query.get("minimum"), "minimum")
        maximum = _vector(query.get("maximum"), "maximum")
        for index in range(len(_collection(mesh, domain))):
            point = transform @ _position(mesh, domain, index)
            if all(minimum[axis] <= point[axis] <= maximum[axis] for axis in range(3)):
                indices.append(index)
    else:
        origin = _vector(query.get("origin"), "origin")
        normal = _vector(query.get("normal"), "normal")
        if normal.length_squared == 0:
            raise MeshResourceError("MESH_SELECTION_QUERY_INVALID", "normal must be non-zero")
        normal.normalize()
        tolerance = float(query.get("tolerance", 1e-5))
        side = query.get("side", "POSITIVE")
        for index in range(len(_collection(mesh, domain))):
            distance = (transform @ _position(mesh, domain, index) - origin).dot(normal)
            if (
                (side == "POSITIVE" and distance >= -tolerance)
                or (side == "NEGATIVE" and distance <= tolerance)
                or (side == "ON" and abs(distance) <= tolerance)
            ):
                indices.append(index)
    return tuple(indices)


def _adjacency(mesh: Any, domain: str) -> list[set[int]]:
    size = len(_collection(mesh, domain))
    result = [set() for _ in range(size)]
    if domain == "VERTEX":
        for edge in mesh.edges:
            first, second = map(int, edge.vertices)
            result[first].add(second)
            result[second].add(first)
    elif domain == "EDGE":
        vertex_edges = [set() for _ in mesh.vertices]
        for edge in mesh.edges:
            for vertex in edge.vertices:
                vertex_edges[int(vertex)].add(int(edge.index))
        for edges in vertex_edges:
            for edge in edges:
                result[edge].update(edges - {edge})
    else:
        edge_faces = [set() for _ in mesh.edges]
        for polygon in mesh.polygons:
            for loop_index in range(polygon.loop_start, polygon.loop_start + polygon.loop_total):
                edge_faces[int(mesh.loops[loop_index].edge_index)].add(int(polygon.index))
        for faces in edge_faces:
            for face in faces:
                result[face].update(faces - {face})
    return result


def _boundary_indices(mesh: Any, domain: str, selected: set[int] | None = None) -> tuple[int, ...]:
    edge_faces = [set() for _ in mesh.edges]
    for polygon in mesh.polygons:
        for loop_index in range(polygon.loop_start, polygon.loop_start + polygon.loop_total):
            edge_faces[int(mesh.loops[loop_index].edge_index)].add(int(polygon.index))
    if domain == "EDGE":
        if selected is None:
            return tuple(index for index, faces in enumerate(edge_faces) if len(faces) == 1)
        result = []
        for index, faces in enumerate(edge_faces):
            inside = len(faces & selected)
            if (inside and inside != len(faces)) or (inside == 1 and len(faces) == 1):
                result.append(index)
        return tuple(result)
    adjacency = _adjacency(mesh, domain)
    candidates = selected if selected is not None else set(range(len(adjacency)))
    return tuple(
        sorted(
            index
            for index in candidates
            if any(neighbor not in candidates for neighbor in adjacency[index])
        )
    )


def _point_in_polygon(point: tuple[float, float], polygon: list[tuple[float, float]]) -> bool:
    inside = False
    x, y = point
    previous = polygon[-1]
    for current in polygon:
        x1, y1 = previous
        x2, y2 = current
        if (y1 > y) != (y2 > y):
            crossing = (x2 - x1) * (y - y1) / (y2 - y1) + x1
            if x < crossing:
                inside = not inside
        previous = current
    return inside


def _project_screen(evidence: CaptureEvidence, point: Vector) -> tuple[float, float, float] | None:
    clip = Matrix(evidence.perspective_matrix) @ Vector((point.x, point.y, point.z, 1.0))
    if clip.w == 0:
        return None
    ndc = clip.xyz / clip.w
    return (float((ndc.x + 1) * 0.5), float((1 - ndc.y) * 0.5), float(ndc.z))


def _capture_ray_direction(evidence: CaptureEvidence, x: float, y: float) -> Vector:
    inverse = Matrix(evidence.perspective_matrix).inverted_safe()
    near_h = inverse @ Vector((2.0 * x - 1.0, 1.0 - 2.0 * y, -1.0, 1.0))
    far_h = inverse @ Vector((2.0 * x - 1.0, 1.0 - 2.0 * y, 1.0, 1.0))
    if abs(float(near_h.w)) < 1e-12 or abs(float(far_h.w)) < 1e-12:
        raise MeshResourceError(
            "MESH_SELECTION_CAPTURE_INVALID",
            "Capture evidence cannot be unprojected for screen selection",
        )
    near = near_h.xyz / near_h.w
    far = far_h.xyz / far_h.w
    direction = far - near
    if direction.length_squared <= 1e-18:
        raise MeshResourceError(
            "MESH_SELECTION_CAPTURE_INVALID",
            "Capture evidence produced a zero-length selection ray",
        )
    return direction.normalized()


def _screen_query(
    obj: Any,
    mesh: Any,
    domain: str,
    query: dict[str, Any],
    captures: CaptureBook,
) -> tuple[int, ...]:
    capture_id = query.get("capture_id")
    evidence = captures.get(str(capture_id))
    if evidence is None:
        raise MeshResourceError(
            "CAPTURE_NOT_FOUND", f"Capture evidence does not exist: {capture_id}", kind="not_found"
        )
    if (
        evidence.scene != bpy.context.scene.name
        or evidence.view_layer != bpy.context.view_layer.name
    ):
        raise MeshResourceError("MESH_RESOURCE_STALE", "Capture scene or view layer changed")
    object_identity = session_identity("object", obj)
    if (
        evidence.target_name != obj.name
        or evidence.target_identity != object_identity
    ):
        raise MeshResourceError(
            "MESH_SELECTION_CAPTURE_TARGET_MISMATCH",
            "Screen selection requires capture evidence for the queried object",
        )
    points = [(float(item["x"]), float(item["y"])) for item in query.get("points", [])]
    shape = query.get("shape")
    candidates: list[tuple[int, float, float, float, Vector]] = []
    for index in range(len(_collection(mesh, domain))):
        world = obj.matrix_world @ _position(mesh, domain, index)
        projected = _project_screen(evidence, world)
        if projected is None:
            continue
        x, y, depth = projected
        selected = False
        if shape == "POINT":
            selected = (x - points[0][0]) ** 2 + (y - points[0][1]) ** 2 <= 0.0004
        elif shape == "BOX":
            selected = (
                min(points[0][0], points[1][0]) <= x <= max(points[0][0], points[1][0])
                and min(points[0][1], points[1][1]) <= y <= max(points[0][1], points[1][1])
            )
        else:
            selected = _point_in_polygon((x, y), points)
        if selected and -1.0001 <= depth <= 1.0001:
            if not query.get("include_backface", False) and domain in {"VERTEX", "FACE"}:
                normal_matrix = obj.matrix_world.to_3x3().inverted_safe().transposed()
                normal = normal_matrix @ _normal(mesh, domain, index)
                view_direction = _capture_ray_direction(evidence, x, y)
                if normal.dot(-view_direction) <= 0:
                    continue
            candidates.append((index, x, y, depth, world))
    if shape == "POINT" and candidates:
        candidates = [
            min(
                candidates,
                key=lambda item: (item[1] - points[0][0]) ** 2
                + (item[2] - points[0][1]) ** 2,
            )
        ]
    if query.get("visibility", "VISIBLE_ONLY") == "VISIBLE_ONLY":
        if len(candidates) > 4096:
            raise MeshResourceError(
                "MESH_RESOURCE_BUDGET_EXCEEDED",
                "VISIBLE_ONLY screen selection is limited to 4096 projected candidates",
            )
        visible = []
        for index, x, y, _depth, world in candidates:
            hit = raycast_capture(evidence, x, y)
            ray = hit.get("ray", {})
            origin = Vector(tuple(ray.get("origin", (0.0, 0.0, 0.0))))
            candidate_distance = float((world - origin).length)
            hit_distance = hit.get("distance")
            if (
                hit.get("hit")
                and hit.get("hit_target") is True
                and isinstance(hit_distance, (int, float))
                and candidate_distance <= float(hit_distance) + 1e-3
            ):
                visible.append(index)
        return tuple(sorted(visible))
    return tuple(sorted(item[0] for item in candidates))


def _selection_boundary(
    mesh: Any, domain: str, selected: set[int]
) -> tuple[str, tuple[int, ...]]:
    if domain == "FACE":
        edge_faces = [set() for _ in mesh.edges]
        for polygon in mesh.polygons:
            for loop_index in range(polygon.loop_start, polygon.loop_start + polygon.loop_total):
                edge_faces[int(mesh.loops[loop_index].edge_index)].add(int(polygon.index))
        boundary = tuple(
            index
            for index, faces in enumerate(edge_faces)
            if faces & selected and (not faces <= selected or len(faces) == 1)
        )
        return "EDGE", boundary
    if domain == "EDGE":
        selected_degree: dict[int, int] = {}
        for index in selected:
            for vertex in mesh.edges[index].vertices:
                vertex_index = int(vertex)
                selected_degree[vertex_index] = selected_degree.get(vertex_index, 0) + 1
        boundary = tuple(sorted(index for index, degree in selected_degree.items() if degree == 1))
        return "VERTEX", boundary
    return "VERTEX", _boundary_indices(mesh, "VERTEX", selected)


def query_selection(
    book: MeshResourceBook,
    captures: CaptureBook,
    params: dict[str, Any],
) -> dict[str, Any]:
    obj, mesh = _object_and_mesh(params)
    domain = params.get("domain")
    if domain not in DOMAINS:
        raise MeshResourceError("MESH_SELECTION_DOMAIN_INVALID", f"Unsupported domain: {domain}")
    query = params.get("query")
    if not isinstance(query, dict) or not isinstance(query.get("type"), str):
        raise MeshResourceError("MESH_SELECTION_QUERY_INVALID", "query must be a typed object")
    query_type = query["type"]
    collection = _collection(mesh, domain)
    if query_type == "indices":
        indices = _indices(query.get("indices"), len(collection))
    elif query_type == "all":
        indices = tuple(range(len(collection)))
    elif query_type in {"sphere", "box", "plane"}:
        indices = _query_spatial(obj, mesh, domain, query)
    elif query_type == "material":
        if domain != "FACE":
            raise MeshResourceError("MESH_SELECTION_QUERY_INVALID", "material requires FACE")
        slots = set(_indices(query.get("slot_indices"), max(1, len(mesh.materials))))
        indices = tuple(
            int(polygon.index) for polygon in mesh.polygons if int(polygon.material_index) in slots
        )
    elif query_type == "normal":
        direction = _vector(query.get("direction"), "direction")
        if direction.length_squared == 0:
            raise MeshResourceError("MESH_SELECTION_QUERY_INVALID", "direction must be non-zero")
        direction.normalize()
        if query.get("space", "LOCAL") == "WORLD":
            direction = (obj.matrix_world.to_3x3().inverted() @ direction).normalized()
        minimum_dot = float(query.get("minimum_dot", -1))
        indices = tuple(
            index
            for index in range(len(collection))
            if _normal(mesh, domain, index).normalized().dot(direction) >= minimum_dot
        )
    elif query_type == "measure":
        field = query.get("field")
        if field == "FACE_AREA" and domain != "FACE":
            raise MeshResourceError("MESH_SELECTION_QUERY_INVALID", "FACE_AREA requires FACE")
        if field == "EDGE_LENGTH" and domain != "EDGE":
            raise MeshResourceError("MESH_SELECTION_QUERY_INVALID", "EDGE_LENGTH requires EDGE")
        minimum = query.get("minimum")
        maximum = query.get("maximum")
        measured = []
        for index in range(len(collection)):
            value = (
                float(mesh.polygons[index].area)
                if field == "FACE_AREA"
                else float(
                    (
                        mesh.vertices[mesh.edges[index].vertices[0]].co
                        - mesh.vertices[mesh.edges[index].vertices[1]].co
                    ).length
                )
            )
            if (minimum is None or value >= float(minimum)) and (
                maximum is None or value <= float(maximum)
            ):
                measured.append(index)
        indices = tuple(measured)
    elif query_type == "topology":
        kind = query.get("kind")
        if kind == "BOUNDARY":
            indices = _boundary_indices(mesh, domain)
        elif kind == "NON_MANIFOLD":
            if domain != "EDGE":
                raise MeshResourceError(
                    "MESH_SELECTION_QUERY_INVALID", "NON_MANIFOLD requires EDGE"
                )
            edge_faces = [0] * len(mesh.edges)
            for loop in mesh.loops:
                edge_faces[int(loop.edge_index)] += 1
            indices = tuple(index for index, count in enumerate(edge_faces) if count != 2)
        else:
            seeds = _indices(query.get("seed_indices"), len(collection))
            adjacency = _adjacency(mesh, domain)
            reached = set(seeds)
            queue = deque(seeds)
            while queue:
                current = queue.popleft()
                for neighbor in adjacency[current]:
                    if neighbor not in reached:
                        reached.add(neighbor)
                        queue.append(neighbor)
            indices = tuple(sorted(reached))
    elif query_type == "screen":
        indices = _screen_query(obj, mesh, domain, query, captures)
    else:
        raise MeshResourceError(
            "MESH_SELECTION_QUERY_INVALID", f"Unsupported selection query: {query_type}"
        )
    revision = mesh_revision_id(mesh)
    record = book.add_selection(
        object_name=obj.name,
        object_identity=session_identity("object", obj),
        mesh_name=mesh.name,
        mesh_identity=session_identity("mesh", mesh),
        mesh_revision_id=revision,
        mesh_fingerprint=mesh_fingerprint(mesh),
        expected_users=int(mesh.users),
        expected_user_objects=mesh_user_refs(mesh),
        domain=domain,
        indices=tuple(sorted(indices)),
        weights=None,
        source_query=query,
    )
    result = record.summary()
    result["resource_counts"] = {
        "selections": book.selection_count,
        "surfaces": book.surface_count,
    }
    return result


def _base_selection(book: MeshResourceBook, selection_id: str) -> tuple[SelectionRecord, Any, Any]:
    record = book.selection(selection_id)
    obj, mesh = validate_selection(record)
    return record, obj, mesh


def _record_like(
    book: MeshResourceBook,
    base: SelectionRecord,
    indices: tuple[int, ...],
    weights: tuple[float, ...] | None,
    source: dict[str, Any],
    domain: str | None = None,
) -> dict[str, Any]:
    record = book.add_selection(
        object_name=base.object_name,
        object_identity=base.object_identity,
        mesh_name=base.mesh_name,
        mesh_identity=base.mesh_identity,
        mesh_revision_id=base.mesh_revision_id,
        mesh_fingerprint=base.mesh_fingerprint,
        expected_users=base.expected_users,
        expected_user_objects=base.expected_user_objects,
        domain=domain or base.domain,
        indices=indices,
        weights=weights,
        source_query=source,
    )
    return record.summary()


def derive_selection(book: MeshResourceBook, params: dict[str, Any]) -> dict[str, Any]:
    operation = params.get("operation")
    if not isinstance(operation, dict) or not isinstance(operation.get("type"), str):
        raise MeshResourceError(
            "MESH_SELECTION_DERIVATION_INVALID", "operation must be a typed object"
        )
    operation_type = operation["type"]
    if operation_type == "combine":
        selection_ids = operation.get("selection_ids")
        if not isinstance(selection_ids, list) or len(selection_ids) < 2:
            raise MeshResourceError(
                "MESH_SELECTION_DERIVATION_INVALID", "combine requires selections"
            )
        records = [book.selection(str(selection_id)) for selection_id in selection_ids]
        for record in records:
            validate_selection(record)
        base = records[0]
        if any(
            record.mesh_revision_id != base.mesh_revision_id or record.domain != base.domain
            for record in records[1:]
        ):
            raise MeshResourceError(
                "MESH_SELECTION_DERIVATION_INVALID",
                "Combined SelectionSets must share revision and domain",
            )
        sets = [set(record.indices) for record in records]
        mode = operation.get("mode")
        if mode == "UNION":
            selected = set().union(*sets)
        elif mode == "INTERSECTION":
            selected = set.intersection(*sets)
        elif mode == "DIFFERENCE":
            selected = sets[0].copy()
            for item in sets[1:]:
                selected.difference_update(item)
        else:
            raise MeshResourceError(
                "MESH_SELECTION_DERIVATION_INVALID", f"Unsupported combine mode: {mode}"
            )
        return _record_like(book, base, tuple(sorted(selected)), None, operation)
    selection_id = operation.get("selection_id")
    if not isinstance(selection_id, str):
        raise MeshResourceError(
            "MESH_SELECTION_DERIVATION_INVALID", "selection_id is required"
        )
    base, obj, mesh = _base_selection(book, selection_id)
    selected = set(base.indices)
    adjacency = _adjacency(mesh, base.domain)
    if operation_type in {"expand", "contract"}:
        steps = int(operation.get("steps", 1))
        current = selected
        universe = set(range(len(adjacency)))
        for _index in range(steps):
            if operation_type == "expand":
                current = current | {neighbor for item in current for neighbor in adjacency[item]}
            else:
                current = {
                    item
                    for item in current
                    if all(neighbor in current for neighbor in adjacency[item])
                }
            if current == universe or not current:
                break
        return _record_like(book, base, tuple(sorted(current)), None, operation)
    if operation_type == "boundary":
        output_domain, boundary = _selection_boundary(mesh, base.domain, selected)
        return _record_like(book, base, boundary, None, operation, output_domain)
    if operation_type == "connected":
        reached = set(selected)
        queue = deque(selected)
        while queue:
            current = queue.popleft()
            for neighbor in adjacency[current]:
                if neighbor not in reached:
                    reached.add(neighbor)
                    queue.append(neighbor)
        return _record_like(book, base, tuple(sorted(reached)), None, operation)
    if operation_type == "convert":
        target = operation.get("domain")
        if target not in DOMAINS:
            raise MeshResourceError(
                "MESH_SELECTION_DERIVATION_INVALID", f"Unsupported target domain: {target}"
            )
        if target == base.domain:
            converted = selected
        else:
            vertices: set[int] = set()
            if base.domain == "VERTEX":
                vertices = selected
            elif base.domain == "EDGE":
                for index in selected:
                    vertices.update(map(int, mesh.edges[index].vertices))
            else:
                for index in selected:
                    vertices.update(map(int, mesh.polygons[index].vertices))
            mode = operation.get("mode", "ANY")
            if target == "VERTEX":
                converted = vertices
            elif target == "EDGE":
                converted = {
                    int(edge.index)
                    for edge in mesh.edges
                    if (any if mode == "ANY" else all)(
                        int(vertex) in vertices for vertex in edge.vertices
                    )
                }
            else:
                converted = {
                    int(face.index)
                    for face in mesh.polygons
                    if (any if mode == "ANY" else all)(
                        int(vertex) in vertices for vertex in face.vertices
                    )
                }
        return _record_like(book, base, tuple(sorted(converted)), None, operation, str(target))
    if operation_type == "falloff":
        if base.domain != "VERTEX":
            raise MeshResourceError(
                "MESH_SELECTION_DERIVATION_INVALID", "falloff requires VERTEX domain"
            )
        radius = float(operation.get("radius", 0))
        if radius <= 0:
            raise MeshResourceError(
                "MESH_SELECTION_DERIVATION_INVALID", "falloff radius must be positive"
            )
        world = operation.get("space", "LOCAL") == "WORLD"
        distances = {index: 0.0 for index in selected}
        queue: list[tuple[float, int]] = [(0.0, index) for index in selected]
        heapq.heapify(queue)
        while queue:
            distance, current = heapq.heappop(queue)
            if distance != distances.get(current) or distance > radius:
                continue
            current_co = (
                obj.matrix_world @ mesh.vertices[current].co
                if world
                else mesh.vertices[current].co
            )
            for neighbor in adjacency[current]:
                neighbor_co = (
                    obj.matrix_world @ mesh.vertices[neighbor].co
                    if world
                    else mesh.vertices[neighbor].co
                )
                candidate = distance + (neighbor_co - current_co).length
                if candidate <= radius and candidate < distances.get(neighbor, math.inf):
                    distances[neighbor] = candidate
                    heapq.heappush(queue, (candidate, neighbor))
        indices = tuple(sorted(distances))
        profile = operation.get("profile", "SMOOTH")
        weights = []
        for index in indices:
            value = max(0.0, 1.0 - distances[index] / radius)
            if profile == "SMOOTH":
                value = value * value * (3 - 2 * value)
            elif profile == "SHARP":
                value *= value
            weights.append(value)
        return _record_like(book, base, indices, tuple(weights), operation)
    raise MeshResourceError(
        "MESH_SELECTION_DERIVATION_INVALID", f"Unsupported derivation: {operation_type}"
    )


def inspect_selection(book: MeshResourceBook, params: dict[str, Any]) -> dict[str, Any]:
    selection_id = params.get("selection_id")
    if not isinstance(selection_id, str) or not selection_id:
        raise MeshResourceError("MESH_RESOURCE_NOT_FOUND", "selection_id is required")
    record, _obj, _mesh = _base_selection(book, selection_id)
    offset = params.get("offset", 0)
    limit = params.get("limit", 256)
    if (
        isinstance(offset, bool)
        or not isinstance(offset, int)
        or offset < 0
        or isinstance(limit, bool)
        or not isinstance(limit, int)
        or not 1 <= limit <= 4096
    ):
        raise MeshResourceError(
            "MESH_PAGINATION_INVALID", "offset and limit are outside the allowed range"
        )
    stop = min(len(record.indices), offset + limit)
    items = [
        {
            "index": record.indices[position],
            "weight": record.weights[position] if record.weights is not None else None,
        }
        for position in range(offset, stop)
    ]
    result = record.summary()
    result["items"] = items
    result["pagination"] = {
        "offset": offset,
        "limit": limit,
        "total": len(record.indices),
        "returned": len(items),
        "truncated": stop < len(record.indices),
        "next_offset": stop if stop < len(record.indices) else None,
    }
    return result


def release_selection(book: MeshResourceBook, params: dict[str, Any]) -> dict[str, Any]:
    selection_id = params.get("selection_id")
    if not isinstance(selection_id, str) or not selection_id:
        raise MeshResourceError("MESH_RESOURCE_NOT_FOUND", "selection_id is required")
    return {"selection_id": selection_id, "released": book.release_selection(selection_id)}


def rebind_selection(
    book: MeshResourceBook,
    source: SelectionRecord,
    obj: Any,
    mesh: Any,
    *,
    source_query: dict[str, Any],
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
        source_query=source_query,
    )
