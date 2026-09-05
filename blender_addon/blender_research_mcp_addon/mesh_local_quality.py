"""Explicit local topology quality with surrounding-geometry intersection coverage."""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Any

from mathutils.bvhtree import BVHTree

from .execution_budget import check_deadline
from .mesh_patch_quality import coordinate_epsilon, illegal_contact
from .mesh_resource_model import MeshResourceError


def local_quality(mesh: Any, selection: Any, scope: str, tolerance: float) -> dict[str, Any]:
    if scope not in {"SELECTION", "SELECTION_AND_NEIGHBORS"}:
        raise MeshResourceError("MESH_VALIDATION_INVALID", "Invalid local scope")
    if not math.isfinite(tolerance) or tolerance < 0:
        raise MeshResourceError(
            "MESH_VALIDATION_INVALID", "Tolerance must be finite and non-negative"
        )
    vertices = set(selection.indices) if selection.domain == "VERTEX" else set()
    edges = set(selection.indices) if selection.domain == "EDGE" else set()
    faces = set(selection.indices) if selection.domain == "FACE" else set()
    for index in edges:
        vertices.update(mesh.edges[index].vertices)
    edge_faces: dict[int, list[int]] = defaultdict(list)
    cycles: dict[int, tuple[int, ...]] = {}
    duplicates: dict[frozenset[int], list[int]] = defaultdict(list)
    for face in mesh.polygons:
        check_deadline()
        cycle = tuple(face.vertices)
        cycles[face.index] = cycle
        duplicates[frozenset(cycle)].append(face.index)
        for loop in face.loop_indices:
            edge_faces[mesh.loops[loop].edge_index].append(face.index)
        if selection.domain != "FACE" and set(cycle) & vertices:
            faces.add(face.index)
    if scope == "SELECTION_AND_NEIGHBORS":
        region_vertices = {i for f in faces for i in cycles[f]} | vertices
        faces |= {i for i, cycle in cycles.items() if set(cycle) & region_vertices}
    vertices |= {i for f in faces for i in cycles[f]}
    edges |= {mesh.loops[i].edge_index for f in faces for i in mesh.polygons[f].loop_indices}
    boundary = sorted(i for i in edges if len(edge_faces[i]) == 1)
    non_manifold = sorted(i for i in edges if len(edge_faces[i]) != 2)
    duplicate_faces = sorted(
        {i for group in duplicates.values() if len(group) > 1 for i in group if i in faces}
    )
    degenerate = sorted(i for i in faces if mesh.polygons[i].area <= tolerance * tolerance)
    directions: dict[tuple[int, int], list[tuple[int, int]]] = defaultdict(list)
    for cycle in cycles.values():
        for a, b in zip(cycle, (*cycle[1:], cycle[0]), strict=True):
            directions[tuple(sorted((a, b)))].append((a, b))
    inconsistent = sorted(
        i
        for i in edges
        if len(directions[tuple(sorted(mesh.edges[i].vertices))]) == 2
        and len(set(directions[tuple(sorted(mesh.edges[i].vertices))])) == 1
    )
    remaining = set(faces)
    components = 0
    while remaining:
        components += 1
        queue = [remaining.pop()]
        while queue:
            current = queue.pop()
            for loop in mesh.polygons[current].loop_indices:
                for neighbor in edge_faces[mesh.loops[loop].edge_index]:
                    if neighbor in remaining:
                        remaining.remove(neighbor)
                        queue.append(neighbor)
    mesh.calc_loop_triangles()
    triangles = [tuple(t.vertices) for t in mesh.loop_triangles]
    source_indices = [i for i, t in enumerate(mesh.loop_triangles) if t.polygon_index in faces]
    coords = [v.co.copy() for v in mesh.vertices]
    extent = (
        max(
            (
                max(coords[i][axis] for i in vertices) - min(coords[i][axis] for i in vertices)
                for axis in range(3)
            ),
            default=0.0,
        )
        if vertices
        else 0.0
    )
    contact_epsilon = coordinate_epsilon(coords, vertices, max(tolerance, extent * 1e-7))
    intersections = set()
    intersection_pairs = set()
    checked = 0
    complete = True
    if source_indices:
        full = BVHTree.FromPolygons(coords, triangles, all_triangles=True, epsilon=contact_epsilon)
        local = BVHTree.FromPolygons(
            coords,
            [triangles[i] for i in source_indices],
            all_triangles=True,
            epsilon=contact_epsilon,
        )
        pairs = local.overlap(full)
        if len(pairs) > 200_000:
            pairs = pairs[:200_000]
            complete = False
        for local_index, other in pairs:
            check_deadline()
            current = source_indices[local_index]
            if (
                current == other
                or mesh.loop_triangles[current].polygon_index
                == mesh.loop_triangles[other].polygon_index
            ):
                continue
            checked += 1
            if illegal_contact(triangles[current], triangles[other], coords, contact_epsilon):
                intersections.add(mesh.loop_triangles[current].polygon_index)
                intersection_pairs.add(
                    tuple(
                        sorted(
                            (
                                mesh.loop_triangles[current].polygon_index,
                                mesh.loop_triangles[other].polygon_index,
                            )
                        )
                    )
                )
    issues = {
        "boundary_edges": boundary,
        "non_manifold_edges": non_manifold,
        "duplicate_faces": duplicate_faces,
        "degenerate_faces": degenerate,
        "orientation_edges": inconsistent,
        "intersection_faces": sorted(intersections),
    }
    return {
        "scope": scope,
        "tolerance_local": tolerance,
        "area_threshold_local_squared": tolerance * tolerance,
        "contact_epsilon_local": contact_epsilon,
        "complete": complete,
        "passed": complete and not any(issues.values()),
        "issues": issues,
        "intersection_pairs": sorted(intersection_pairs),
        "connected_face_components": components,
        "denominators": {
            "vertices": len(vertices),
            "edges": len(edges),
            "faces": len(faces),
            "surrounding_faces": len(mesh.polygons),
            "triangle_pairs_checked": checked,
        },
        "history": "CURRENT_STATE_ONLY",
        "unchecked": [] if complete else ["remaining_intersection_candidates"],
    }
