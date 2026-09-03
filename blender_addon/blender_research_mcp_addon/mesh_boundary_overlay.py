"""Call-local pixel annotations from exact resource geometry; no scene helpers."""

from __future__ import annotations

import math
from typing import Any

from mathutils import Vector

from .execution_budget import check_deadline
from .mesh_boundary_ops import graph_from_mesh
from .mesh_patch_ops import _directed, _resolve, fail

COLORS = ((255, 96, 80), (80, 220, 140), (80, 160, 255), (255, 205, 65))
DIGITS = {
    "1": ("010", "110", "010", "010", "111"),
    "2": ("110", "001", "010", "100", "111"),
    "3": ("110", "001", "010", "001", "110"),
    "4": ("101", "101", "111", "001", "001"),
}


def prepare_overlay(book: Any, obj: Any, raw: Any) -> dict[str, Any] | None:
    if raw is None:
        return None
    if not isinstance(raw, dict) or set(raw) - {"paths", "problem_vertices"}:
        fail("ANNOTATION_INVALID", "Boundary annotations accept paths and problem_vertices")
    paths, problems = raw.get("paths", []), raw.get("problem_vertices", [])
    if (
        not isinstance(paths, (list, tuple))
        or len(paths) > 4
        or not isinstance(problems, (list, tuple))
        or len(problems) > 64
    ):
        fail("ANNOTATION_INVALID", "Annotations accept up to four paths and 64 problem vertices")
    if obj is None or obj.type != "MESH":
        fail("ANNOTATION_INVALID", "Boundary annotations require a Mesh")
    graph = graph_from_mesh(obj.data)
    resolved = [_directed(book, obj, obj.data, graph, path)[1] for path in paths]
    if sum(map(len, resolved)) > 4096:
        fail("ANNOTATION_BUDGET_EXCEEDED", "Annotations exceed 4096 path vertices")
    return {
        "paths": [
            [tuple(obj.matrix_world @ obj.data.vertices[i].co) for i in path] for path in resolved
        ],
        "problems": [
            tuple(
                obj.matrix_world
                @ obj.data.vertices[_resolve(book, obj, obj.data, ref, "VERTEX").indices[0]].co
            )
            for ref in problems
        ],
        "vertex_indices": resolved,
    }


def paint_overlay(
    rgba: bytes, width: int, height: int, matrix: Any, overlay: Any
) -> tuple[bytes, dict[str, Any]]:
    data = bytearray(rgba)

    def project(co: Any) -> tuple[float, float] | None:
        clip = matrix @ Vector((*co, 1))
        if clip.w <= 0 or not all(math.isfinite(v) for v in clip):
            return None
        x, y = clip.x / clip.w, clip.y / clip.w
        if abs(x) > 3 or abs(y) > 3:
            return None
        return (x + 1) * width / 2, (y + 1) * height / 2

    def pixel(x: int, y: int, color: Any) -> None:
        if 0 <= x < width and 0 <= y < height:
            offset = 4 * (y * width + x)
            data[offset : offset + 4] = bytes((*color, 255))

    def line(a: Any, b: Any, color: Any) -> None:
        if a is None or b is None:
            return
        check_deadline()
        dx, dy = b[0] - a[0], b[1] - a[1]
        steps = max(1, math.ceil(max(abs(dx), abs(dy))))
        for n in range(steps + 1):
            x, y = round(a[0] + dx * n / steps), round(a[1] + dy * n / steps)
            for ox, oy in ((0, 0), (1, 0), (0, 1)):
                pixel(x + ox, y + oy, color)

    for index, path in enumerate(overlay["paths"]):
        points = [project(co) for co in path]
        color = COLORS[index]
        for a, b in zip(points, points[1:], strict=False):
            line(a, b, color)
        for point in (points[0], points[-1]):
            if point is not None:
                x, y = map(round, point)
                for ox in range(-3, 4):
                    for oy in range(-3, 4):
                        pixel(x + ox, y + oy, color)
        if points[0] is not None:
            x, y = map(round, points[0])
            for row, bits in enumerate(DIGITS[str(index + 1)]):
                for column, bit in enumerate(bits):
                    if bit == "1":
                        for ox in range(2):
                            for oy in range(2):
                                pixel(x + 6 + column * 2 + ox, y + 8 - row * 2 + oy, color)
        middle = max(0, (len(points) - 2) // 2)
        a, b = points[middle : middle + 2]
        if a is not None and b is not None:
            dx, dy = b[0] - a[0], b[1] - a[1]
            length = math.hypot(dx, dy)
            if length > 1:
                dx, dy = dx / length, dy / length
                tip = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
                for sign in (-1, 1):
                    line(
                        tip,
                        (tip[0] - 10 * dx + sign * 5 * dy, tip[1] - 10 * dy - sign * 5 * dx),
                        color,
                    )
    for co in overlay["problems"]:
        point = project(co)
        if point is not None:
            x, y = point
            line((x - 6, y - 6), (x + 6, y + 6), (255, 40, 220))
            line((x - 6, y + 6), (x + 6, y - 6), (255, 40, 220))
    return bytes(data), {
        "mode": "PROJECTED_XRAY",
        "paths": len(overlay["paths"]),
        "problem_vertices": len(overlay["problems"]),
        "path_vertex_indices": overlay["vertex_indices"],
        "legend": ["1 red", "2 green", "3 blue", "4 yellow", "problems magenta"],
    }
