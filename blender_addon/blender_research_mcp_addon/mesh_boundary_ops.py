"""Read-only boundary evidence and preflight adapters."""

from __future__ import annotations

from typing import Any

from .execution_budget import check_deadline, deadline_after
from .mesh_boundary_model import BoundaryError, BoundaryGraph, Edge, SearchBudget, analyze
from .mesh_ops import MeshOperationError
from .mesh_query_ops import validate_selection
from .mesh_resource_model import MeshResourceBook, SelectionRecord


def graph_from_mesh(mesh: Any) -> BoundaryGraph:
    counts = [0] * len(mesh.edges)
    for index, loop in enumerate(mesh.loops):
        if index % 2048 == 0:
            check_deadline()
        counts[loop.edge_index] += 1
    edges = []
    for index, edge in enumerate(mesh.edges):
        if index % 2048 == 0:
            check_deadline()
        a, b = edge.vertices
        edges.append(
            Edge(
                index,
                (int(a), int(b)),
                counts[index],
                bool(edge.hide or mesh.vertices[a].hide or mesh.vertices[b].hide),
            )
        )
    return BoundaryGraph(tuple(edges))


def graph_from_bmesh(bm: Any) -> BoundaryGraph:
    bm.verts.index_update()
    bm.edges.index_update()
    return BoundaryGraph(
        tuple(
            Edge(
                e.index,
                tuple(v.index for v in e.verts),
                len(e.link_faces),
                bool(e.hide or any(v.hide for v in e.verts)),
            )
            for e in bm.edges
        )
    )


def boundary_failure(
    exc: BoundaryError, *, report: dict[str, Any] | None = None
) -> MeshOperationError:
    return MeshOperationError(
        "MESH_BUDGET_EXCEEDED"
        if exc.reason == "SEARCH_BUDGET_EXCEEDED"
        else "MESH_BOUNDARY_INVALID",
        str(exc),
        details={
            "reason": exc.reason,
            "phase": "preflight",
            "writeback": False,
            "recovery": "NOT_NEEDED",
            **exc.evidence,
            **({"boundary": report} if report is not None else {}),
            "next_steps": ["Inspect the boundary and submit exact sides/corners"],
        },
    )


def auto_boundary(graph: BoundaryGraph, selected: tuple[int, ...]) -> dict[str, Any]:
    try:
        report = analyze(graph, selected, SearchBudget(deadline=deadline_after(2)))
        if report["status"] != "READY":
            raise boundary_failure(
                BoundaryError(
                    report["reason"],
                    "Grid fill boundary is not uniquely executable: " + report["reason"],
                ),
                report=report,
            )
        return report["resolved"]
    except BoundaryError as exc:
        raise boundary_failure(exc) from exc


def preflight_grid(mesh: Any, selection: SelectionRecord) -> dict[str, Any]:
    return auto_boundary(graph_from_mesh(mesh), selection.indices)


def inspect_boundary(book: MeshResourceBook, params: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "selection_id",
        "expected_mesh_fingerprint",
        "component",
        "offset",
        "limit",
        "maximum_visits",
    }
    if set(params) - allowed:
        raise MeshOperationError("MESH_OPERATION_INVALID", "Unknown boundary inspection fields")
    record = book.selection(str(params.get("selection_id", "")))
    obj, mesh = validate_selection(record)
    if record.domain != "EDGE":
        raise MeshOperationError(
            "MESH_OPERATION_INVALID", "Boundary inspection requires EDGE selection"
        )
    expected = params.get("expected_mesh_fingerprint")
    offset, limit = params.get("offset", 0), params.get("limit", 256)
    maximum = params.get("maximum_visits", 20_000)
    for value, low, high in ((offset, 0, 2_000_000), (limit, 1, 4096), (maximum, 1, 100_000)):
        if type(value) is not int or not low <= value <= high:
            raise MeshOperationError(
                "MESH_PAGINATION_INVALID", "Invalid boundary page/search budget"
            )
    if offset and expected is None:
        raise MeshOperationError(
            "MESH_RESOURCE_STALE", "Further pages require the original fingerprint"
        )
    if expected is not None and expected != record.mesh_fingerprint:
        raise MeshOperationError(
            "MESH_RESOURCE_STALE",
            "Boundary fingerprint changed",
            kind="conflict",
            details={"reason": "EVIDENCE_STALE"},
        )
    component = params.get("component", "SUMMARY")
    if component not in {"SUMMARY", "COMPONENTS", "VERTICES", "PAIRINGS"}:
        raise MeshOperationError(
            "MESH_OPERATION_INVALID", "Unsupported boundary inspection component"
        )
    try:
        report = analyze(
            graph_from_mesh(mesh), record.indices, SearchBudget(maximum, deadline_after(2))
        )
    except BoundaryError as exc:
        raise boundary_failure(exc) from exc
    table = {"COMPONENTS": "components", "VERTICES": "surrounding_degrees", "PAIRINGS": "pairings"}
    items = report.get(table.get(component, ""), [])
    if component != "SUMMARY":
        report = {k: v for k, v in report.items() if k not in table.values()}
        report.update(
            items=items[offset : offset + limit],
            total=len(items),
            offset=offset,
            truncated=offset + limit < len(items),
        )
    return {
        "selection": record.summary(),
        "object_name": obj.name,
        "mesh_fingerprint": record.mesh_fingerprint,
        "mesh_revision_id": record.mesh_revision_id,
        "component": component,
        **report,
    }
