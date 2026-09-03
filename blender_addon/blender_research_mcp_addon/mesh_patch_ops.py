"""Explicit, bounded patch preparation; live writes use the shared topology writer."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any

import bmesh

from .execution_budget import check_deadline
from .mesh_boundary_model import BoundaryError, four_sides
from .mesh_boundary_ops import auto_boundary, boundary_failure, graph_from_mesh
from .mesh_ops import MeshOperationError, mesh_counts, mesh_fingerprint, mesh_revision_id
from .mesh_query_ops import validate_selection

MAX_PATCH_FACES = 4096


def fail(reason: str, message: str, **details: Any) -> None:
    raise MeshOperationError(
        "MESH_BOUNDARY_INVALID",
        message,
        details={
            "reason": reason,
            "phase": "preflight",
            "writeback": False,
            "recovery": "NOT_NEEDED",
            **details,
        },
    )


def is_patch(operation: dict[str, Any]) -> bool:
    return operation.get("type") in {"grid_fill", "create_edge", "create_face"} or (
        operation.get("type") == "bridge" and "paths" in operation
    )


def validate_patch(raw: dict[str, Any], material_count: int) -> dict[str, Any]:
    from .mesh_topology_ops import _boolean, _closed, _integer, _material_index

    kind = raw["type"]
    inputs = {
        "grid_fill": {"selection_id", "boundary"},
        "bridge": {"paths"},
        "create_edge": {"vertices"},
        "create_face": {"vertices"},
    }[kind]
    options = {"allow_hidden", "attribute_policy"}
    if kind != "create_edge":
        options |= {"material_slot_index", "smooth", "uv_creation"}
    if kind == "grid_fill":
        options.add("use_interp_simple")
    if kind == "bridge":
        options |= {"cuts", "twist_offset"}
        _integer(raw.get("cuts", 0), "cuts", 0, 32)
        if raw.get("twist_offset", 0) != 0:
            fail("DIRECTION_MISMATCH", "Path starts define bridge correspondence")
    _closed(raw, {"type"}, inputs | options)
    if len(set(raw) & inputs) != 1:
        fail("INPUT_INVALID", "Provide exactly one boundary input")
    _boolean(raw.get("allow_hidden", False), "allow_hidden")
    _boolean(raw.get("smooth", False), "smooth")
    _boolean(raw.get("use_interp_simple", False), "use_interp_simple")
    _material_index(raw, material_count)
    uv = raw.get("uv_creation", {})
    if (
        not isinstance(uv, dict)
        or len(uv) > 8
        or any(
            not isinstance(k, str)
            or not k
            or len(k) > 63
            or v not in {"BOUNDARY_INTERPOLATE", "INDEPENDENT_ISLAND"}
            for k, v in uv.items()
        )
    ):
        fail("ATTRIBUTE_POLICY_INVALID", "Invalid per-layer UV creation policy")
    if "selection_id" in raw and raw.get("allow_hidden", False):
        fail("INPUT_INVALID", "Hidden boundaries require explicit directed paths")
    return raw


@dataclass
class PatchPlan:
    selection: Any
    coords: dict[int, tuple[float, ...]] = field(default_factory=dict)
    faces: list[tuple[int, ...]] = field(default_factory=list)
    edges: list[tuple[int, int]] = field(default_factory=list)
    lattice: dict[int, tuple[int, int]] = field(default_factory=dict)
    boundary: dict[str, Any] = field(default_factory=dict)
    attributes: dict[str, Any] = field(default_factory=dict)
    evidence: dict[str, Any] = field(default_factory=dict)
    no_op: bool = False


def _resolve(book: Any, obj: Any, mesh: Any, value: Any, domain: str) -> Any:
    if not isinstance(value, str) or not value:
        fail("REFERENCE_INVALID", "Expected a SelectionSet ID")
    record = book.selection(value)
    target, data = validate_selection(record)
    if target is not obj or data is not mesh or record.domain != domain or not record.indices:
        fail("REFERENCE_INVALID", "Reference must target this Mesh and domain", domain=domain)
    if domain == "VERTEX" and len(record.indices) != 1:
        fail("REFERENCE_AMBIGUOUS", "An exact vertex reference must contain one vertex")
    return record


def _directed(book: Any, obj: Any, mesh: Any, graph: Any, value: Any) -> tuple[Any, list[int]]:
    if not isinstance(value, dict) or set(value) != {"selection_id", "start_vertex"}:
        fail("PATH_INVALID", "A directed path requires selection_id and start_vertex")
    selected = _resolve(book, obj, mesh, value["selection_id"], "EDGE")
    start = _resolve(book, obj, mesh, value["start_vertex"], "VERTEX").indices[0]
    components = graph.components(selected.indices)
    if len(components) != 1 or components[0]["kind"] != "OPEN_CHAIN":
        fail("PATH_INVALID", "A directed path must be one open chain", components=components)
    if start not in components[0]["endpoints"]:
        fail("PATH_INVALID", "Path start must be an endpoint", start=start)
    return selected, graph.order(selected.indices, start)


def _native_grid(mesh: Any, plan: PatchPlan, simple: bool) -> None:
    """Native identities establish boundary correspondence; graph distances label the grid."""
    bm = bmesh.new()
    try:
        paths = plan.boundary["paths"]
        original = {i: bm.verts.new(mesh.vertices[i].co) for path in paths for i in path[:-1]}
        native_to_source = {v: i for i, v in original.items()}
        for path in paths:
            for a, b in zip(path, path[1:], strict=False):
                bm.edges.new((original[a], original[b]))
        saved = {v: tuple(v.co) for v in original.values()}
        selected = [
            bm.edges.get((original[a], original[b]))
            for n in (0, 2)
            for a, b in zip(paths[n], paths[n][1:], strict=False)
        ]
        check_deadline()
        bmesh.ops.grid_fill(bm, edges=selected, use_interp_simple=simple)
        if len(bm.faces) != plan.boundary["expected_faces"] or any(
            len(face.verts) != 4 for face in bm.faces
        ):
            fail("NATIVE_GRID_REJECTED", "Native fill did not produce the requested quad lattice")
        if any(not v.is_valid or tuple(v.co) != co for v, co in saved.items()):
            fail("NATIVE_BOUNDARY_CHANGED", "Native fill changed a confirmed boundary vertex")
        for path in paths:
            if any(
                bm.edges.get((original[a], original[b])) is None
                for a, b in zip(path, path[1:], strict=False)
            ):
                fail("NATIVE_BOUNDARY_CHANGED", "Native fill replaced a boundary edge")

        def distance(side: list[int]) -> dict[Any, int]:
            values = {original[i]: 0 for i in side}
            queue = deque(values)
            while queue:
                check_deadline()
                vertex = queue.popleft()
                for edge in vertex.link_edges:
                    other = edge.other_vert(vertex)
                    if other not in values:
                        values[other] = values[vertex] + 1
                        queue.append(other)
            return values

        x, y = distance(paths[3]), distance(paths[0])
        width, height = plan.boundary["segments"][:2]
        positions = {(x[v], y[v]): v for v in bm.verts}
        if set(positions) != {(i, j) for i in range(width + 1) for j in range(height + 1)}:
            fail("NATIVE_GRID_REJECTED", "Native output is not a rectangular topological grid")
        for pos, vertex in sorted(positions.items()):
            if vertex not in native_to_source:
                index = len(mesh.vertices) + len(plan.coords)
                native_to_source[vertex] = index
                plan.coords[index] = tuple(vertex.co)
            plan.lattice[native_to_source[vertex]] = pos
        # Validate native face cells, then orient them by the requested cyclic sides.
        native_cells = {frozenset(native_to_source[v] for v in face.verts) for face in bm.faces}
        for j in range(height):
            for i in range(width):
                face = tuple(
                    native_to_source[positions[p]]
                    for p in ((i, j), (i + 1, j), (i + 1, j + 1), (i, j + 1))
                )
                if frozenset(face) not in native_cells:
                    fail("NATIVE_GRID_REJECTED", "Native grid face adjacency is unexpected")
                plan.faces.append(face)
    finally:
        bm.free()


def prepare_patch(book: Any, obj: Any, mesh: Any, operation: dict[str, Any]) -> PatchPlan:
    graph = graph_from_mesh(mesh)
    kind = operation["type"]
    allow_hidden = operation.get("allow_hidden", False)
    try:
        if kind in {"create_edge", "create_face"}:
            refs = operation["vertices"]
            expected = (2,) if kind == "create_edge" else (3, 4)
            if not isinstance(refs, (list, tuple)) or len(refs) not in expected:
                fail("INPUT_INVALID", "Invalid number of exact vertex references")
            records = [_resolve(book, obj, mesh, value, "VERTEX") for value in refs]
            vertices = [record.indices[0] for record in records]
            if len(set(vertices)) != len(vertices):
                fail("DUPLICATE_VERTEX", "Exact vertices must be distinct")
            if not allow_hidden and any(mesh.vertices[i].hide for i in vertices):
                fail("BOUNDARY_HIDDEN", "Explicit hidden vertices require allow_hidden")
            plan = PatchPlan(records[0])
            plan.boundary = {"paths": [vertices], "vertices": vertices}
            if kind == "create_edge":
                pair = tuple(vertices)
                if any(set(edge.vertices) == set(pair) for edge in mesh.edges):
                    plan.no_op = True
                    return plan
                plan.edges = [pair]
            else:
                plan.faces = [tuple(vertices)]
                plan.lattice = dict(zip(vertices, ((0, 0), (1, 0), (1, 1), (0, 1)), strict=False))
        elif kind == "bridge":
            if not isinstance(operation["paths"], (list, tuple)) or len(operation["paths"]) != 2:
                fail("PATH_INVALID", "Open bridge requires two directed paths")
            resolved = [_directed(book, obj, mesh, graph, path) for path in operation["paths"]]
            a, b = [item[1] for item in resolved]
            if set(a) & set(b):
                fail("RAILS_OVERLAP", "Bridge paths must be disjoint")
            if len(a) != len(b):
                fail(
                    "SEGMENT_MISMATCH",
                    "Bridge paths must have equal segment counts",
                    segments=[len(a) - 1, len(b) - 1],
                    difference=len(a) - len(b),
                )
            for path in (a, b):
                for edge_id in graph.edge_path(path):
                    edge = graph.edges[edge_id]
                    if not edge.usable or (edge.hidden and not allow_hidden):
                        fail("BOUNDARY_UNUSABLE", "Bridge path is internal or hidden", edge=edge_id)
            plan = PatchPlan(resolved[0][0])
            rows = [a]
            height = operation.get("cuts", 0) + 1
            for j in range(1, height):
                row = []
                for va, vb in zip(a, b, strict=True):
                    index = len(mesh.vertices) + len(plan.coords)
                    plan.coords[index] = tuple(
                        mesh.vertices[va].co.lerp(mesh.vertices[vb].co, j / height)
                    )
                    row.append(index)
                rows.append(row)
            rows.append(b)
            for j, row in enumerate(rows):
                for i, vertex in enumerate(row):
                    plan.lattice[vertex] = (i, j)
            for j in range(height):
                for i in range(len(a) - 1):
                    plan.faces.append(
                        (rows[j][i], rows[j][i + 1], rows[j + 1][i + 1], rows[j + 1][i])
                    )
            plan.boundary = {
                "paths": [a, b],
                "segments": [len(a) - 1, height],
                "end_boundaries": [[row[0] for row in rows], [row[-1] for row in rows]],
            }
        else:
            if "selection_id" in operation:
                selected = _resolve(book, obj, mesh, operation["selection_id"], "EDGE")
                boundary = auto_boundary(graph, selected.indices)
            else:
                raw = operation["boundary"]
                if not isinstance(raw, dict):
                    fail("INPUT_INVALID", "boundary must be a closed object")
                if raw.get("type") == "FOUR_PATHS" and set(raw) == {"type", "paths"}:
                    if not isinstance(raw["paths"], (list, tuple)) or len(raw["paths"]) != 4:
                        fail("PATH_INVALID", "Four directed paths are required")
                    resolved = [_directed(book, obj, mesh, graph, path) for path in raw["paths"]]
                    selected = resolved[0][0]
                    paths = [item[1] for item in resolved]
                elif raw.get("type") == "CLOSED_LOOP" and set(raw) == {
                    "type",
                    "selection_id",
                    "corners",
                }:
                    selected = _resolve(book, obj, mesh, raw["selection_id"], "EDGE")
                    if not isinstance(raw["corners"], (list, tuple)) or len(raw["corners"]) != 4:
                        fail("CORNERS_REQUIRED", "Exactly four cyclic corners are required")
                    corners = [
                        _resolve(book, obj, mesh, value, "VERTEX").indices[0]
                        for value in raw["corners"]
                    ]
                    components = graph.components(selected.indices)
                    if len(components) != 1 or components[0]["kind"] != "CLOSED_LOOP":
                        fail("PATH_INVALID", "Expected one closed loop")
                    cycle = graph.order(selected.indices, corners[0])[:-1]
                    if len(set(corners)) != 4 or not set(corners) <= set(cycle):
                        fail("CORNERS_REQUIRED", "Corners must be distinct vertices of the loop")
                    offsets = [cycle.index(i) for i in corners]
                    if offsets != sorted(offsets):
                        cycle = [cycle[0], *reversed(cycle[1:])]
                        offsets = [cycle.index(i) for i in corners]
                    if offsets != sorted(offsets):
                        fail("DIRECTION_MISMATCH", "Corners are not cyclically ordered")
                    cycle.append(cycle[0])
                    paths = [
                        cycle[offsets[i] : (offsets[i + 1] if i < 3 else len(cycle) - 1) + 1]
                        for i in range(4)
                    ]
                else:
                    fail("INPUT_INVALID", "Unsupported explicit boundary shape")
                boundary = four_sides(graph, paths, allow_hidden=allow_hidden)
            plan = PatchPlan(selected, boundary=boundary)
            if boundary["expected_faces"] > MAX_PATCH_FACES:
                fail("OUTPUT_BUDGET_EXCEEDED", "Patch exceeds 4096 faces")
            _native_grid(mesh, plan, operation.get("use_interp_simple", False))
    except BoundaryError as exc:
        raise boundary_failure(exc) from exc
    if len(plan.faces) > MAX_PATCH_FACES:
        fail("OUTPUT_BUDGET_EXCEEDED", "Patch exceeds 4096 faces")
    from .mesh_patch_attributes import prepare_attributes
    from .mesh_patch_quality import check_candidate

    check_candidate(mesh, plan)
    plan.attributes = prepare_attributes(obj, mesh, plan, operation)
    # No resource insertion or eviction before the complete candidate is known.
    from .mesh_resource_model import MAX_SELECTION_COMPONENTS, MAX_SELECTIONS

    new_edges = {
        tuple(sorted((a, b)))
        for face in plan.faces
        for a, b in zip(face, (*face[1:], face[0]), strict=True)
    } | set(plan.edges)
    counts = mesh_counts(mesh)
    if (
        counts["vertices"] + len(plan.coords) > 500_000
        or counts["edges"] + len(new_edges) > 1_000_000
        or counts["faces"] + len(plan.faces) > 500_000
        or counts["loops"] + sum(map(len, plan.faces)) > 2_000_000
        or len(book._selections) + 4 > MAX_SELECTIONS
        or sum(len(s.indices) for s in book._selections.values())
        + len(plan.coords)
        + len(new_edges)
        + len(plan.faces)
        + len(plan.selection.indices)
        > MAX_SELECTION_COMPONENTS
        or len(book._component_maps) >= 128
        or sum(m.relation_count for m in book._component_maps.values())
        + counts["vertices"]
        + counts["edges"]
        + counts["faces"]
        > 8_000_000
    ):
        fail("OUTPUT_BUDGET_EXCEEDED", "Release unused resources or use a smaller patch")
    plan.evidence = {
        "boundary": plan.boundary,
        "created_faces": len(plan.faces),
        "created_vertices": len(plan.coords),
        "quality": plan.evidence,
        "attribute_creation": plan.attributes["evidence"],
    }
    return plan


def apply_patch_plan(bm: Any, plan: PatchPlan, operation: dict[str, Any]) -> dict[str, Any]:
    from .mesh_patch_attributes import apply_attributes

    bm.verts.ensure_lookup_table()
    vertices = {
        i: bm.verts[i]
        for i in set(plan.lattice) | {i for e in plan.edges for i in e}
        if i < len(bm.verts)
    }
    for index, co in plan.coords.items():
        vertices[index] = bm.verts.new(co)
    for a, b in plan.edges:
        bm.edges.new((vertices[a], vertices[b]))
    faces = []
    for indices in plan.faces:
        check_deadline()
        face = bm.faces.new([vertices[i] for i in indices])
        face.material_index = plan.attributes["material"]
        face.smooth = operation.get("smooth", False)
        faces.append(face)
    apply_attributes(bm, plan, vertices, faces)
    return plan.evidence


def no_op_result(transaction: Any, obj: Any, plan: PatchPlan, data_scope: str) -> dict[str, Any]:
    fingerprint = mesh_fingerprint(obj.data)
    return {
        "transaction_id": transaction.transaction_id,
        "operation": "create_edge",
        "changed": False,
        "data_scope": data_scope,
        "component_map": None,
        "before_mesh_revision_id": mesh_revision_id(obj.data),
        "after_mesh_revision_id": mesh_revision_id(obj.data),
        "before_mesh_fingerprint": fingerprint,
        "after_mesh_fingerprint": fingerprint,
        "rebound_selection": plan.selection.summary(),
        "created_selections": {},
        "delta": {"type": "mesh_edit", "recorded": False},
        "evidence": {"reason": "EDGE_ALREADY_EXISTS"},
    }
