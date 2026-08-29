"""Dependency-free bounded summaries for evaluated mesh polygons."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

DETAIL_POLYGON_LIMIT = 250_000


def summarize_polygon_diagnostics(
    *,
    edge_count: int,
    material_slot_count: int,
    polygons: Iterable[tuple[Iterable[int], int, float]],
) -> dict[str, Any]:
    edge_usage = [0] * edge_count
    material_usage = [0] * material_slot_count
    unassigned_polygons = 0
    surface_area_local = 0.0
    for edge_indices, material_index, area in polygons:
        surface_area_local += float(area)
        if 0 <= material_index < material_slot_count:
            material_usage[material_index] += 1
        else:
            unassigned_polygons += 1
        for edge_index in edge_indices:
            if 0 <= edge_index < edge_count:
                edge_usage[edge_index] += 1
    return {
        "surface_area_local": surface_area_local,
        "edge_topology": {
            "loose": sum(count == 0 for count in edge_usage),
            "boundary": sum(count == 1 for count in edge_usage),
            "manifold": sum(count == 2 for count in edge_usage),
            "non_manifold": sum(count > 2 for count in edge_usage),
        },
        "material_polygon_counts": material_usage,
        "unassigned_polygon_count": unassigned_polygons,
    }
