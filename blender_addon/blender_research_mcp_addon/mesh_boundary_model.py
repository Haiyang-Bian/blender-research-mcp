"""Bounded, deterministic boundary graphs. No Blender or scene-state access."""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any


class BoundaryError(RuntimeError):
    def __init__(self, reason: str, message: str, **evidence: Any) -> None:
        super().__init__(message)
        self.reason = reason
        self.evidence = evidence


@dataclass
class SearchBudget:
    maximum_visits: int = 20_000
    deadline: float = field(default_factory=lambda: time.monotonic() + 2.0)
    visits: int = 0

    def check(self, count: int = 0) -> None:
        self.visits += count
        if self.visits > self.maximum_visits or time.monotonic() >= self.deadline:
            raise BoundaryError(
                "SEARCH_BUDGET_EXCEEDED",
                "Boundary search is incomplete; specify exact paths",
                visits=self.visits,
                maximum_visits=self.maximum_visits,
            )


@dataclass(frozen=True)
class Edge:
    index: int
    vertices: tuple[int, int]
    face_count: int = 0
    hidden: bool = False

    @property
    def usable(self) -> bool:
        return self.face_count <= 1


class BoundaryGraph:
    def __init__(self, edges: tuple[Edge, ...]) -> None:
        self.edges = {edge.index: edge for edge in edges}
        adjacency: dict[int, list[tuple[int, int]]] = {}
        for edge in edges:
            a, b = edge.vertices
            adjacency.setdefault(a, []).append((b, edge.index))
            adjacency.setdefault(b, []).append((a, edge.index))
        self.adjacency = {vertex: tuple(sorted(items)) for vertex, items in adjacency.items()}

    def components(self, selected: tuple[int, ...]) -> list[dict[str, Any]]:
        if len(selected) > 4096:
            raise BoundaryError("SEARCH_BUDGET_EXCEEDED", "Inspect at most 4096 selected edges")
        missing = sorted(set(selected) - self.edges.keys())
        if missing:
            raise BoundaryError("EVIDENCE_STALE", "Boundary edge no longer exists", edges=missing)
        remaining = set(selected)
        components = []
        while remaining:
            queue = [min(remaining)]
            group = set()
            while queue:
                edge_id = queue.pop()
                if edge_id not in remaining:
                    continue
                remaining.remove(edge_id)
                group.add(edge_id)
                for vertex in self.edges[edge_id].vertices:
                    queue.extend(index for _, index in self.adjacency[vertex] if index in remaining)
            degrees: dict[int, int] = {}
            for index in group:
                for vertex in self.edges[index].vertices:
                    degrees[vertex] = degrees.get(vertex, 0) + 1
            ends = sorted(vertex for vertex, degree in degrees.items() if degree == 1)
            kind = "BRANCHED"
            if all(degree == 2 for degree in degrees.values()):
                kind = "CLOSED_LOOP"
            elif len(ends) == 2 and all(degree in {1, 2} for degree in degrees.values()):
                kind = "OPEN_CHAIN"
            ordered = (
                self.order(tuple(sorted(group)), ends[0] if ends else min(degrees))
                if kind != "BRANCHED"
                else None
            )
            components.append(
                {
                    "kind": kind,
                    "edges": sorted(group),
                    "endpoints": ends,
                    "vertices": sorted(degrees),
                    "ordered_vertices": ordered,
                    "selected_degrees": [
                        {"vertex": v, "degree": degrees[v]} for v in sorted(degrees)
                    ],
                }
            )
        return components

    def order(self, selected: tuple[int, ...], start: int) -> list[int]:
        remaining = set(selected)
        vertices = [start]
        while remaining:
            choices = [(v, e) for v, e in self.adjacency.get(vertices[-1], ()) if e in remaining]
            if not choices or (len(choices) > 1 and len(vertices) > 1):
                raise BoundaryError("PATH_INVALID", "Edges do not form a single ordered path")
            vertex, edge = choices[0]
            remaining.remove(edge)
            vertices.append(vertex)
        return vertices

    def edge_path(self, vertices: list[int]) -> list[int]:
        result = []
        for a, b in zip(vertices, vertices[1:], strict=False):
            matches = [e for v, e in self.adjacency.get(a, ()) if v == b]
            if len(matches) != 1:
                raise BoundaryError("PATH_INVALID", "Path adjacency is missing or ambiguous")
            result.append(matches[0])
        return result

    def path(
        self,
        start: int,
        end: int,
        blocked_vertices: set[int],
        blocked_edges: set[int],
        budget: SearchBudget,
        *,
        include_hidden: bool = False,
    ) -> list[int] | None:
        queue = deque([start])
        previous: dict[int, int | None] = {start: None}
        while queue:
            vertex = queue.popleft()
            budget.check()
            if vertex == end:
                result = [end]
                while previous[result[-1]] is not None:
                    result.append(previous[result[-1]])  # type: ignore[arg-type]
                return list(reversed(result))
            for other, edge_id in self.adjacency.get(vertex, ()):
                budget.check(1)
                edge = self.edges[edge_id]
                if (
                    other in previous
                    or other in blocked_vertices
                    or edge_id in blocked_edges
                    or not edge.usable
                    or (edge.hidden and not include_hidden)
                ):
                    continue
                previous[other] = vertex
                queue.append(other)
        return None

    def candidates(
        self,
        start: int,
        end: int,
        blocked: set[int],
        excluded: set[int],
        budget: SearchBudget,
    ) -> dict[str, Any]:
        first = self.path(start, end, blocked, excluded, budget)
        if first is None:
            hidden = self.path(start, end, blocked, excluded, budget, include_hidden=True)
            return {
                "status": "HIDDEN" if hidden else "UNREACHABLE",
                "paths": [hidden] if hidden else [],
            }
        # Any different simple path must omit at least one edge of this path.
        # Edge-removal reachability proves uniqueness without enumerating simple paths.
        for edge_id in self.edge_path(first):
            alternative = self.path(start, end, blocked, excluded | {edge_id}, budget)
            if alternative is not None:
                return {"status": "AMBIGUOUS", "paths": [first, alternative]}
        return {"status": "UNIQUE", "paths": [first]}


def four_sides(
    graph: BoundaryGraph, paths: list[list[int]], *, allow_hidden: bool = False
) -> dict[str, Any]:
    if len(paths) != 4 or any(len(path) < 2 for path in paths):
        raise BoundaryError("PATH_INVALID", "Exactly four nonempty paths are required")
    if any(len(set(path)) != len(path) for path in paths):
        raise BoundaryError("PATH_INVALID", "Each side must be a simple open path")
    if any(paths[i][-1] != paths[(i + 1) % 4][0] for i in range(4)):
        raise BoundaryError("DIRECTION_MISMATCH", "Four paths must form one directed cycle")
    cycle = [vertex for path in paths for vertex in path[:-1]]
    if len(set(cycle)) != len(cycle):
        raise BoundaryError("RAILS_OVERLAP", "Sides intersect outside their adjacent corners")
    edges = [graph.edge_path(path) for path in paths]
    invalid = [i for side in edges for i in side if not graph.edges[i].usable]
    hidden = [i for side in edges for i in side if graph.edges[i].hidden]
    if invalid:
        raise BoundaryError(
            "BOUNDARY_UNUSABLE", "Patch sides must be wire or boundary edges", edges=invalid
        )
    if hidden and not allow_hidden:
        raise BoundaryError(
            "BOUNDARY_HIDDEN", "Explicit hidden components require allow_hidden", edges=hidden
        )
    counts = [len(side) for side in edges]
    if counts[0] != counts[2] or counts[1] != counts[3]:
        raise BoundaryError(
            "SEGMENT_MISMATCH", "Opposite side counts differ; subdivide explicitly", segments=counts
        )
    return {
        "paths": paths,
        "edges": edges,
        "corners": [path[0] for path in paths],
        "segments": counts,
        "expected_faces": counts[0] * counts[1],
        "expected_inner_vertices": (counts[0] - 1) * (counts[1] - 1),
    }


def analyze(
    graph: BoundaryGraph, selected: tuple[int, ...], budget: SearchBudget
) -> dict[str, Any]:
    components = graph.components(selected)
    vertices = sorted({v for edge_id in selected for v in graph.edges[edge_id].vertices})
    report: dict[str, Any] = {
        "components": components,
        "component_count": len(components),
        "pairings": [],
        "selected_edges": len(selected),
        "selected_vertices": len(vertices),
        "invalid_edges": [i for i in selected if not graph.edges[i].usable],
        "hidden_edges": [i for i in selected if graph.edges[i].hidden],
        "surrounding_degrees": [
            {
                "vertex": v,
                "visible_boundary_degree": sum(
                    graph.edges[e].usable and not graph.edges[e].hidden
                    for _, e in graph.adjacency[v]
                ),
                "boundary_degree_including_hidden": sum(
                    graph.edges[e].usable for _, e in graph.adjacency[v]
                ),
            }
            for v in vertices
        ],
        "status": "UNSUPPORTED",
        "reason": "INPUT_COMPONENTS_UNSUPPORTED",
        "resolved": None,
        "coverage": {"complete": True, "maximum_visits": budget.maximum_visits, "visits": 0},
    }
    if report["invalid_edges"] or report["hidden_edges"]:
        report["reason"] = "BOUNDARY_UNUSABLE" if report["invalid_edges"] else "BOUNDARY_HIDDEN"
        return report
    if len(components) == 1 and components[0]["kind"] == "CLOSED_LOOP":
        report["reason"] = "CORNERS_REQUIRED"
        report["next_steps"] = ["Specify four corners for this closed loop"]
        return report
    if len(components) != 2 or any(item["kind"] != "OPEN_CHAIN" for item in components):
        return report
    a, b = (item["ordered_vertices"] for item in components)
    valid = []
    try:
        for target in (b, list(reversed(b))):
            pairing: dict[str, Any] = {
                "endpoints": [[a[0], target[0]], [a[-1], target[-1]]],
                "rails": [],
            }
            report["pairings"].append(pairing)
            for start, end in pairing["endpoints"]:
                blocked = set(a + target) - {start, end}
                pairing["rails"].append(
                    graph.candidates(start, end, blocked, set(selected), budget)
                )
            rails = pairing["rails"]
            if all(rail["status"] == "UNIQUE" for rail in rails):
                paths = [
                    a,
                    rails[1]["paths"][0],
                    list(reversed(target)),
                    list(reversed(rails[0]["paths"][0])),
                ]
                try:
                    resolved = four_sides(graph, paths)
                    pairing["status"] = "VALID"
                    valid.append(resolved)
                except BoundaryError as exc:
                    pairing["status"] = exc.reason
                    pairing["evidence"] = exc.evidence
            elif any(rail["status"] in {"UNREACHABLE", "HIDDEN"} for rail in rails):
                pairing["status"] = (
                    "RAIL_HIDDEN" if any(r["status"] == "HIDDEN" for r in rails) else "RAIL_MISSING"
                )
            else:
                pairing["status"] = "AMBIGUOUS"
        ambiguous = any(p["status"] == "AMBIGUOUS" for p in report["pairings"])
        if len(valid) == 1 and not ambiguous:
            report.update(status="READY", reason=None, resolved=valid[0])
        elif len(valid) > 1 or ambiguous:
            report.update(status="AMBIGUOUS", reason="PAIRING_AMBIGUOUS")
        else:
            report["reason"] = next(
                (p["status"] for p in report["pairings"] if p["status"] != "RAIL_MISSING"),
                "RAIL_MISSING",
            )
    except BoundaryError as exc:
        report.update(status="UNKNOWN", reason=exc.reason)
        report["coverage"]["complete"] = False
        report["unexamined"] = ["Remaining paths and pairings; use explicit sides"]
    report["coverage"]["visits"] = budget.visits
    return report
