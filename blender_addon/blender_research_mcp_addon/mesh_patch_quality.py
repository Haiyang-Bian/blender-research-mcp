"""Bounded triangle contact checks, including legal shared-edge and vertex contacts."""

from __future__ import annotations

from typing import Any

from mathutils import Vector
from mathutils.bvhtree import BVHTree
from mathutils.geometry import tessellate_polygon

from .execution_budget import check_deadline
from .mesh_patch_ops import fail


def _inside(p: Any, tri: list[Any], epsilon: float) -> bool:
    a, b, c = tri
    normal = (b - a).cross(c - a)
    return all(
        normal.dot((y - x).cross(p - x)) >= -epsilon * normal.length * (y - x).length
        for x, y in ((a, b), (b, c), (c, a))
    )


def _on_segment(p: Any, a: Any, b: Any, epsilon: float) -> bool:
    axis = b - a
    if axis.length <= epsilon:
        return (p - a).length <= epsilon
    t = (p - a).dot(axis) / axis.length_squared
    return (
        -epsilon / axis.length <= t <= 1 + epsilon / axis.length
        and (p - (a + t * axis)).length <= epsilon
    )


def segment_contacts(a: Any, b: Any, tri: list[Any], epsilon: float) -> list[Any]:
    normal = (tri[1] - tri[0]).cross(tri[2] - tri[0]).normalized()
    da, db = normal.dot(a - tri[0]), normal.dot(b - tri[0])
    if abs(da) <= epsilon and abs(db) <= epsilon:
        points = [p for p in (a, b) if _inside(p, tri, epsilon)]
        axis = b - a
        for c, d in zip(tri, (*tri[1:], tri[0]), strict=True):
            other = d - c
            denominator = axis.cross(other).dot(normal)
            if abs(denominator) <= epsilon * max(axis.length, other.length):
                points.extend(p for p in (c, d) if _on_segment(p, a, b, epsilon))
                continue
            t = (c - a).cross(other).dot(normal) / denominator
            u = (c - a).cross(axis).dot(normal) / denominator
            if 0 <= t <= 1 and 0 <= u <= 1:
                points.append(a + axis * t)
        return points
    if da * db > 0 and min(abs(da), abs(db)) > epsilon:
        return []
    if abs(da - db) < 1e-30:
        return []
    t = da / (da - db)
    if not 0 <= t <= 1:
        return []
    point = a + (b - a) * t
    return [point] if _inside(point, tri, epsilon) else []


def illegal_contact(
    a_ids: tuple[int, ...], b_ids: tuple[int, ...], coords: Any, epsilon: float
) -> bool:
    a, b = [coords[i] for i in a_ids], [coords[i] for i in b_ids]
    contacts = []
    for tri, target in ((a, b), (b, a)):
        for p, q in zip(tri, (*tri[1:], tri[0]), strict=True):
            contacts.extend(segment_contacts(p, q, target, epsilon))
    shared = [coords[i] for i in set(a_ids) & set(b_ids)]
    for point in contacts:
        if any((point - vertex).length <= epsilon for vertex in shared):
            continue
        if len(shared) == 2 and _on_segment(point, *shared, epsilon):
            continue
        return True
    return False


def triangles(indices: tuple[int, ...], coords: Any, epsilon: float) -> list[tuple[int, ...]]:
    points = [coords[i] for i in indices]
    exact_corners = {tuple(point): index for point, index in zip(points, indices, strict=True)}
    if len(exact_corners) != len(indices):
        fail("DEGENERATE_FACE", "Face corners have coincident coordinates", vertices=indices)
    if len(set(indices)) != len(indices):
        fail("DUPLICATE_VERTEX", "Face cycle contains repeated vertices")
    if len(indices) == 4:
        for n in (0, 1):
            a, b, c, d = [points[(n + i) % 4] for i in range(4)]
            axis, other = b - a, d - c
            normal = axis.cross(other)
            if normal.length > epsilon * max(axis.length, other.length):
                t = (c - a).cross(other).dot(normal) / normal.length_squared
                u = (c - a).cross(axis).dot(normal) / normal.length_squared
                if 0 < t < 1 and 0 < u < 1 and (a + axis * t - c - other * u).length <= epsilon:
                    fail("SELF_INTERSECTION", "Opposite quad edges cross", vertices=indices)
    tessellation = tessellate_polygon([points])
    result = []
    normals = []
    for offsets in tessellation:
        # Blender 4.2 returns exact offsets into the supplied polygon corner list.
        ids = tuple(indices[i] for i in offsets)
        tri = [points[i] for i in offsets]
        normal = (tri[1] - tri[0]).cross(tri[2] - tri[0])
        if normal.length <= epsilon * max((tri[1] - tri[0]).length, (tri[2] - tri[0]).length):
            fail("DEGENERATE_FACE", "Candidate contains a zero-area triangle", vertices=indices)
        normals.append(normal.normalized())
        result.append(ids)
    if len(result) != len(indices) - 2:
        fail("DEGENERATE_FACE", "Candidate cannot be triangulated", vertices=indices)
    if len(normals) == 2 and normals[0].dot(normals[1]) <= 0:
        fail("SELF_INTERSECTION", "Quad triangulation is folded or self-crossing", vertices=indices)
    return result


def check_candidate(mesh: Any, plan: Any) -> None:
    coords = {v.index: v.co.copy() for v in mesh.vertices}
    coords.update({i: Vector(co) for i, co in plan.coords.items()})
    used = {i for face in plan.faces for i in face} | {i for edge in plan.edges for i in edge}
    span = (
        max(((coords[a] - coords[b]).length for a in used for b in used), default=1)
        if len(used) < 64
        else max(
            max(coords[i][axis] for i in used) - min(coords[i][axis] for i in used)
            for axis in range(3)
        )
    )
    epsilon = max(span * 1e-7, 1e-10)
    old_cycles = {frozenset(face.vertices) for face in mesh.polygons}
    for edge in plan.edges:
        if any(set(edge) <= set(face.vertices) for face in mesh.polygons):
            fail("IMPLICIT_CUT_REQUIRED", "New edge would cut an existing face", vertices=edge)
        a, b = [coords[i] for i in edge]
        for old in mesh.edges:
            check_deadline()
            c, d = [coords[i] for i in old.vertices]
            axis, other = b - a, d - c
            cross = axis.cross(other)
            if cross.length > epsilon * max(axis.length, other.length):
                t = (c - a).cross(other).dot(cross) / cross.length_squared
                u = (c - a).cross(axis).dot(cross) / cross.length_squared
                if 0 < t < 1 and 0 < u < 1 and (a + t * axis - c - u * other).length <= epsilon:
                    fail(
                        "IMPLICIT_CUT_REQUIRED", "New edge crosses an existing edge", edge=old.index
                    )
            elif any(
                _on_segment(p, a, b, epsilon)
                and (p - a).length > epsilon
                and (p - b).length > epsilon
                for p in (c, d)
            ):
                fail("IMPLICIT_CUT_REQUIRED", "New edge overlaps an existing edge", edge=old.index)
    seen = set()
    for face in plan.faces:
        key = frozenset(face)
        if key in old_cycles or key in seen:
            fail("DUPLICATE_FACE", "Candidate face already exists", vertices=face)
        seen.add(key)
    edge_uses: dict[tuple[int, int], list[tuple[int, int]]] = {}
    old_uses: dict[tuple[int, int], list[tuple[int, int]]] = {}
    for cycle in (tuple(face.vertices) for face in mesh.polygons):
        for a, b in zip(cycle, (*cycle[1:], cycle[0]), strict=True):
            old_uses.setdefault(tuple(sorted((a, b))), []).append((a, b))
    flips = set()
    for cycle in plan.faces:
        for a, b in zip(cycle, (*cycle[1:], cycle[0]), strict=True):
            key = tuple(sorted((a, b)))
            edge_uses.setdefault(key, []).append((a, b))
            old = old_uses.get(key, [])
            if old:
                flips.add(old[0] == (a, b))
    if len(flips) > 1:
        fail("ORIENTATION_CONFLICT", "Boundary faces require contradictory patch orientation")
    if flips == {True}:
        plan.faces = [tuple(reversed(face)) for face in plan.faces]
    for key, values in edge_uses.items():
        if len(values) + len(old_uses.get(key, [])) > 2:
            fail("NON_MANIFOLD", "Candidate would create a non-manifold edge", vertices=key)
    new_triangles = [tri for face in plan.faces for tri in triangles(face, coords, epsilon)]
    mesh.calc_loop_triangles()
    old_triangles = [tuple(tri.vertices) for tri in mesh.loop_triangles]
    all_triangles = new_triangles + old_triangles
    points = [coords[i] for i in range(len(coords))]
    if new_triangles:
        tree = BVHTree.FromPolygons(points, all_triangles, all_triangles=True, epsilon=epsilon)
        patch_tree = BVHTree.FromPolygons(
            points, new_triangles, all_triangles=True, epsilon=epsilon
        )
        for vertex in mesh.vertices:
            if vertex.index not in used:
                check_deadline()
                _point, _normal, index, distance = patch_tree.find_nearest(vertex.co, epsilon)
                if index is not None and distance <= epsilon:
                    fail(
                        "IMPLICIT_CUT_REQUIRED",
                        "An unrelated vertex lies on the candidate patch",
                        vertex=vertex.index,
                    )
        candidates = patch_tree.overlap(tree)
        if len(candidates) > 200_000:
            fail("QUALITY_BUDGET_EXCEEDED", "Too many intersection candidates")
        checked = 0
        for a, b in candidates:
            check_deadline()
            if b < len(new_triangles) and b <= a:
                continue
            if illegal_contact(new_triangles[a], all_triangles[b], coords, epsilon):
                fail(
                    "SELF_INTERSECTION",
                    "Candidate intersects surrounding or new geometry",
                    candidate_triangle=a,
                    other_triangle=b,
                )
            checked += 1
    else:
        checked = 0
    # Wire edges are not represented in a triangle BVH. A patch must not silently
    # embed an unconnected wire or vertex: that would require an implicit cut.
    wire_edges = [
        tuple(e.vertices) for e in mesh.edges if tuple(sorted(e.vertices)) not in old_uses
    ]
    for a, b in wire_edges:
        for face in plan.faces:
            if a in face and b in face:
                cycle_edges = {
                    frozenset((x, y)) for x, y in zip(face, (*face[1:], face[0]), strict=True)
                }
                if frozenset((a, b)) not in cycle_edges:
                    fail(
                        "IMPLICIT_CUT_REQUIRED",
                        "A wire would become an implicit face diagonal",
                        vertices=[a, b],
                    )
    extra_edges = plan.edges
    pair_count = len(wire_edges) * len(new_triangles) + len(extra_edges) * len(old_triangles)
    if pair_count > 500_000:
        fail("QUALITY_BUDGET_EXCEEDED", "Wire intersection coverage exceeds the bounded budget")
    for edges, targets in (
        (wire_edges, new_triangles),
        (extra_edges, old_triangles + new_triangles),
    ):
        for edge in edges:
            check_deadline()
            a, b = [coords[i] for i in edge]
            if (a - b).length <= epsilon:
                fail("DEGENERATE_EDGE", "Edge endpoints coincide", vertices=edge)
            for tri in targets:
                if set(edge) <= set(tri):
                    continue
                shared = set(edge) & set(tri)
                for p in segment_contacts(a, b, [coords[i] for i in tri], epsilon):
                    if not any((p - coords[i]).length <= epsilon for i in shared):
                        fail(
                            "IMPLICIT_CUT_REQUIRED",
                            "An edge crosses a face without shared topology",
                            edge=edge,
                            triangle=tri,
                        )
    plan.evidence = {
        "complete": True,
        "candidate_faces": len(plan.faces),
        "candidate_triangles": len(new_triangles),
        "surrounding_faces": len(mesh.polygons),
        "intersection_pairs_checked": checked,
        "epsilon_local": epsilon,
        "new_non_manifold": 0,
        "new_degenerate": 0,
        "new_intersections": 0,
    }
