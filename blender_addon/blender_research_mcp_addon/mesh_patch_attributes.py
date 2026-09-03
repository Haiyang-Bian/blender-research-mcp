"""Deterministic fixed-boundary attribute creation, separate from topology lineage."""

from __future__ import annotations

import math
from typing import Any

from .execution_budget import check_deadline
from .mesh_patch_ops import fail


def harmonic(
    lattice: dict[int, tuple[int, int]], fixed: dict[int, tuple[float, ...]]
) -> tuple[dict[int, tuple[float, ...]], int]:
    if not fixed:
        fail("ATTRIBUTE_SOURCE_MISSING", "No fixed attribute source exists")
    width = len(next(iter(fixed.values())))
    if not width:
        return {i: () for i in lattice}, 0
    by_position = {pos: i for i, pos in lattice.items()}
    neighbors = {
        i: [
            by_position[p]
            for p in ((x - 1, y), (x + 1, y), (x, y - 1), (x, y + 1))
            if p in by_position
        ]
        for i, (x, y) in lattice.items()
    }
    average = tuple(sum(row[k] for row in fixed.values()) / len(fixed) for k in range(width))
    values = {i: fixed.get(i, average) for i in lattice}
    unknown = sorted(set(lattice) - set(fixed), key=lambda i: lattice[i])
    if not unknown:
        return values, 0
    for iteration in range(5000):
        check_deadline()
        residual = 0.0
        for index in unknown:
            adjacent = neighbors[index]
            if not adjacent:
                fail("ATTRIBUTE_SOURCE_MISSING", "An interior vertex has no attribute neighbors")
            row = tuple(sum(values[n][k] for n in adjacent) / len(adjacent) for k in range(width))
            residual = max(
                residual, max(abs(a - b) for a, b in zip(row, values[index], strict=True))
            )
            values[index] = row
        if residual <= 1e-8:
            return values, iteration + 1
    fail("ATTRIBUTE_NON_CONVERGENCE", "Fixed-boundary interpolation did not converge")
    raise AssertionError("unreachable")


def prepare_attributes(obj: Any, mesh: Any, plan: Any, operation: dict[str, Any]) -> dict[str, Any]:
    from .mesh_ops import prepare_topology_attributes

    policy = prepare_topology_attributes(obj, mesh, operation)["policy"]
    boundary = sorted({i for path in plan.boundary.get("paths", ()) for i in path})
    boundary_edges = {
        tuple(sorted((a, b)))
        for path in plan.boundary.get("paths", ())
        for a, b in zip(path, path[1:], strict=False)
    }
    if operation["type"] == "create_face":
        cycle = plan.faces[0]
        boundary_edges = {
            tuple(sorted((a, b))) for a, b in zip(cycle, (*cycle[1:], cycle[0]), strict=True)
        }
    adjacent = [
        p
        for p in mesh.polygons
        if any(
            tuple(
                sorted(
                    (
                        mesh.loops[i].vertex_index,
                        mesh.loops[p.loop_start + (n + 1) % p.loop_total].vertex_index,
                    )
                )
            )
            in boundary_edges
            for n, i in enumerate(p.loop_indices)
        )
    ]
    sources = {i: [] for i in boundary}
    for face in adjacent:
        for loop in face.loop_indices:
            index = mesh.loops[loop].vertex_index
            if index in sources:
                sources[index].append(loop)
    material = operation.get("material_slot_index")
    if material is None:
        candidates = {p.material_index for p in adjacent}
        if len(candidates) > 1 or (not candidates and len(mesh.materials) > 1):
            fail("MATERIAL_SOURCE_AMBIGUOUS", "Specify material_slot_index for this patch")
        material = next(iter(candidates), 0)
    result: dict[str, Any] = {
        "material": material,
        "uv": {},
        "weights": {},
        "colors": {},
        "evidence": {
            "algorithm": "FIXED_BOUNDARY_HARMONIC",
            "uv": {},
            "weights": {},
            "boundary_sources": boundary,
        },
    }
    if not plan.faces:
        return result
    uv_policy = operation.get("uv_creation", {})
    unknown_layers = set(uv_policy) - {layer.name for layer in mesh.uv_layers}
    if unknown_layers:
        fail(
            "ATTRIBUTE_POLICY_INVALID",
            "UV creation names unknown layers",
            layers=sorted(unknown_layers),
        )

    def unique(rows: list[tuple[float, ...]], vertex: int, name: str) -> tuple[float, ...]:
        if not rows or any(
            len(row) != len(rows[0])
            or any(
                not math.isfinite(b) or abs(a - b) > 1e-6 for a, b in zip(rows[0], row, strict=True)
            )
            for row in rows
        ):
            fail(
                "ATTRIBUTE_SOURCE_AMBIGUOUS",
                "Boundary attribute source is missing or incompatible",
                vertex=vertex,
                attribute=name,
                next_steps=["Choose an independent UV island or resolve the boundary attributes"],
            )
        return rows[0]

    if policy["uv"] == "PRESERVE_INTERPOLATE":
        for layer in mesh.uv_layers:
            check_deadline()
            mode = uv_policy.get(layer.name, "BOUNDARY_INTERPOLATE")
            if mode == "INDEPENDENT_ISLAND":
                width = max(pos[0] for pos in plan.lattice.values()) or 1
                height = max(pos[1] for pos in plan.lattice.values()) or 1
                values = {i: (x / width, y / height) for i, (x, y) in plan.lattice.items()}
                iterations = 0
            else:
                fixed = {
                    i: unique([tuple(layer.data[j].uv) for j in sources[i]], i, layer.name)
                    for i in boundary
                }
                values, iterations = harmonic(plan.lattice, fixed)
            result["uv"][layer.name] = values
            result["evidence"]["uv"][layer.name] = {
                "mode": mode,
                "iterations": iterations,
                "new_pin": False,
                "requires_unwrap_pack": mode == "INDEPENDENT_ISLAND",
                "seam_boundary_edges": sorted(boundary_edges)
                if mode == "INDEPENDENT_ISLAND"
                else [],
            }
    if policy["weights"] == "PRESERVE_INTERPOLATE" and plan.coords:
        groups = sorted({g.group for i in boundary for g in mesh.vertices[i].groups})
        fixed = {}
        for i in boundary:
            weights = {g.group: float(g.weight) for g in mesh.vertices[i].groups}
            fixed[i] = tuple(weights.get(g, 0.0) for g in groups)
        values, iterations = harmonic(plan.lattice, fixed)
        result["weights"] = {
            i: {g: value for g, value in zip(groups, values[i], strict=True) if value > 0}
            for i in plan.coords
        }
        if any(
            not math.isfinite(w) or not 0 <= w <= 1
            for row in result["weights"].values()
            for w in row.values()
        ):
            fail("ATTRIBUTE_INVALID", "Weight interpolation produced invalid values")
        result["evidence"]["weights"] = {
            "iterations": iterations,
            "group_count": len(obj.vertex_groups),
            "unweighted_created_vertices": [i for i, row in result["weights"].items() if not row],
            "maximum_influences": max((len(row) for row in result["weights"].values()), default=0),
            "pruned": False,
        }
    for layer in mesh.color_attributes:
        fixed = {
            i: unique([tuple(layer.data[j].color) for j in sources[i]], i, layer.name)
            if layer.domain == "CORNER"
            else tuple(layer.data[i].color)
            for i in boundary
        }
        values, _iterations = harmonic(plan.lattice, fixed)
        result["colors"][layer.name] = {
            "domain": layer.domain,
            "type": layer.data_type,
            "values": values,
        }
    return result


def apply_attributes(bm: Any, plan: Any, vertices: Any, faces: Any) -> None:
    attrs = plan.attributes
    if not faces:
        return
    deform = bm.verts.layers.deform.active
    if attrs["weights"] and deform is None:
        deform = bm.verts.layers.deform.new()
    for index, values in attrs["weights"].items():
        for group, weight in values.items():
            vertices[index][deform][group] = weight
    for name, data in attrs["colors"].items():
        if data["domain"] == "POINT":
            collection = (
                bm.verts.layers.float_color
                if data["type"] == "FLOAT_COLOR"
                else bm.verts.layers.color
            )
            layer = collection.get(name)
            for index in plan.coords:
                vertices[index][layer] = data["values"][index]
    for indices, face in zip(plan.faces, faces, strict=True):
        check_deadline()
        for index, loop in zip(indices, face.loops, strict=True):
            for name, values in attrs["uv"].items():
                data = loop[bm.loops.layers.uv.get(name)]
                data.uv = values[index]
                data.pin_uv = False
            for name, data in attrs["colors"].items():
                if data["domain"] == "CORNER":
                    collection = (
                        bm.loops.layers.float_color
                        if data["type"] == "FLOAT_COLOR"
                        else bm.loops.layers.color
                    )
                    loop[collection.get(name)] = data["values"][index]
