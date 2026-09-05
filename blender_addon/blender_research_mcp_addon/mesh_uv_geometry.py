"""Positive-area UV triangle overlap; edge and point contact is legal."""

from __future__ import annotations

from typing import Any


def triangle_overlap_area(first: Any, second: Any) -> float:
    origin = first[0]
    polygon = [(float(p[0] - origin[0]), float(p[1] - origin[1])) for p in first]
    clip = [(float(p[0] - origin[0]), float(p[1] - origin[1])) for p in second]

    def cross(a, b, c):
        return (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])

    direction = 1 if cross(*clip) >= 0 else -1
    for a, b in zip(clip, (*clip[1:], clip[0]), strict=True):
        if not polygon:
            return 0.0
        output = []
        previous = polygon[-1]
        old = direction * cross(a, b, previous)
        for current in polygon:
            new = direction * cross(a, b, current)
            if (old >= 0) != (new >= 0):
                ratio = old / (old - new)
                output.append(
                    tuple(x + ratio * (y - x) for x, y in zip(previous, current, strict=True))
                )
            if new >= 0:
                output.append(current)
            previous, old = current, new
        polygon = output
    if len(polygon) < 3:
        return 0.0
    return (
        abs(
            sum(
                a[0] * b[1] - b[0] * a[1]
                for a, b in zip(polygon, (*polygon[1:], polygon[0]), strict=True)
            )
        )
        * 0.5
    )
