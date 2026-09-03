from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path

import pytest

from blender_research_mcp.errors import BridgeError, ErrorInfo, ErrorKind
from blender_research_mcp.mesh_errors import mesh_errors


def model():
    path = (
        Path(__file__).parents[1]
        / "blender_addon/blender_research_mcp_addon/mesh_boundary_model.py"
    )
    spec = importlib.util.spec_from_file_location("boundary_model_tests", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


m = model()


def rectangle(*, branches=False, shortcut=False, hidden=False):
    # 4 x 4 perimeter; 0..4 lower, 8..12 upper.
    edges = [(i, (i + 1) % 16) for i in range(16)]
    if branches:
        edges += [(2, 16), (2, 17)]
    if shortcut:
        edges += [(5, 15)]
    return m.BoundaryGraph(
        tuple(m.Edge(i, edge, hidden=hidden and i == 5) for i, edge in enumerate(edges))
    )


def inspect(graph, maximum=20_000):
    return m.analyze(graph, tuple([*range(4), *range(8, 12)]), m.SearchBudget(maximum))


def test_unique_rails_and_degree_four_do_not_depend_on_global_degree():
    for graph in (rectangle(), rectangle(branches=True)):
        result = inspect(graph)
        assert result["status"] == "READY"
        assert result["resolved"]["expected_faces"] == 16
        assert result["resolved"]["expected_inner_vertices"] == 9


def test_unique_disjoint_pair_survives_shortcut_and_budget_never_claims_unreachable():
    assert inspect(rectangle(shortcut=True))["status"] == "READY"
    graph = rectangle()
    ambiguous = m.BoundaryGraph(
        tuple(graph.edges.values())
        + (
            m.Edge(16, (5, 16)),
            m.Edge(17, (16, 7)),
        )
    )
    assert inspect(ambiguous)["status"] == "AMBIGUOUS"
    result = inspect(rectangle(), 1)
    assert result["status"] == "UNKNOWN"
    assert not result["coverage"]["complete"]


def test_hidden_rail_is_diagnosed_separately():
    result = inspect(rectangle(hidden=True))
    assert result["reason"] == "RAIL_HIDDEN"
    assert any(r["status"] == "HIDDEN" for p in result["pairings"] for r in p["rails"])


def test_closed_loop_requires_explicit_corners_and_two_loops_are_unsupported():
    graph = rectangle()
    result = m.analyze(graph, tuple(range(16)), m.SearchBudget())
    assert result["reason"] == "CORNERS_REQUIRED"
    graph = m.BoundaryGraph(
        tuple(
            m.Edge(i, (base + j, base + (j + 1) % 4))
            for base in (0, 4)
            for j in range(4)
            for i in [base + j]
        )
    )
    assert m.analyze(graph, tuple(range(8)), m.SearchBudget())["status"] == "UNSUPPORTED"


def test_explicit_sides_ignore_external_shortcuts_and_reject_overlap():
    paths = [list(range(5)), list(range(4, 9)), list(range(8, 13)), [12, 13, 14, 15, 0]]
    a = m.four_sides(rectangle(), paths)
    b = m.four_sides(rectangle(shortcut=True), paths)
    assert a == b
    with pytest.raises(m.BoundaryError):
        m.four_sides(rectangle(), [paths[0], paths[1], paths[2], list(reversed(paths[3]))])


def test_structured_error_preserves_details_and_unrelated_plain_text_error():
    @mesh_errors
    async def failed():
        raise BridgeError(
            ErrorInfo(
                kind=ErrorKind.VALIDATION,
                code="MESH_BOUNDARY_INVALID",
                message="No rail",
                details={"reason": "RAIL_MISSING"},
            )
        )

    result = asyncio.run(failed())
    assert result.isError
    assert result.structuredContent["error"]["details"]["reason"] == "RAIL_MISSING"
    assert result.content[0].text == "MESH_BOUNDARY_INVALID: No rail"

    @mesh_errors
    async def plain():
        raise RuntimeError("transport text")

    with pytest.raises(RuntimeError, match="transport text"):
        asyncio.run(plain())
