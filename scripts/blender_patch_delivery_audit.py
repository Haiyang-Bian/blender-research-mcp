"""Read-only save/reload lineage audit; all asset data stays outside this repository."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "blender_addon"))
from blender_research_mcp_addon.mesh_surface_ops import (  # noqa: E402
    _uv_overlap_faces,
    _uv_polygon_area,
)


def digest(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def capture(path, target):
    bpy.ops.wm.open_mainfile(filepath=str(path), load_ui=False, use_scripts=False)
    bpy.context.view_layer.update()
    obj = bpy.data.objects[target]
    mesh = obj.data
    result = {
        "coordinates": [tuple(v.co) for v in mesh.vertices],
        "weights": [[(g.group, g.weight) for g in v.groups] for v in mesh.vertices],
        "groups": [(g.name, g.lock_weight) for g in obj.vertex_groups],
        "edges": [
            {"vertices": tuple(e.vertices), "seam": e.use_seam, "hide": e.hide} for e in mesh.edges
        ],
        "faces": [
            {
                "vertices": tuple(f.vertices),
                "material": f.material_index,
                "hide": f.hide,
                "smooth": f.use_smooth,
                "uv": {
                    layer.name: {
                        mesh.loops[i].vertex_index: (tuple(layer.data[i].uv), layer.data[i].pin_uv)
                        for i in f.loop_indices
                    }
                    for layer in mesh.uv_layers
                },
            }
            for f in mesh.polygons
        ],
        "binding": [
            (m.name, m.type, m.object.name if m.type == "ARMATURE" and m.object else None)
            for m in obj.modifiers
        ],
        "materials": [m.name if m else None for m in mesh.materials],
        "others": {},
    }
    for other in bpy.data.objects:
        if other.name == target:
            continue
        value = {
            "type": other.type,
            "matrix": [tuple(row) for row in other.matrix_world],
            "hidden": other.hide_viewport,
            "render_hidden": other.hide_render,
        }
        if other.type == "MESH":
            value["mesh"] = digest(
                {
                    "v": [tuple(v.co) for v in other.data.vertices],
                    "e": [tuple(e.vertices) for e in other.data.edges],
                    "f": [tuple(f.vertices) for f in other.data.polygons],
                }
            )
        result["others"][other.name] = digest(value)
    return result


def run(args):
    report = json.loads(args.evidence.read_text(encoding="utf-8"))
    lineage = report["commit_path"]["lineage"]["domains"]
    original = capture(args.source, args.target)
    final = capture(args.result, args.target)
    assert original["groups"] == final["groups"]
    assert original["binding"] == final["binding"]
    assert original["materials"] == final["materials"]
    assert original["others"] == final["others"]
    vertex_map = {}
    for row in lineage["VERTEX"]:
        assert row["relation"] == "SURVIVED" and len(row["target_indices"]) == 1
        a, b = row["source_index"], row["target_indices"][0]
        vertex_map[a] = b
        assert original["coordinates"][a] == final["coordinates"][b]
        assert original["weights"][a] == final["weights"][b]
    uv_corners = 0
    for row in lineage["FACE"]:
        before = original["faces"][row["source_index"]]
        observed = set()
        for target in row["target_indices"]:
            after = final["faces"][target]
            assert before["material"] == after["material"] and before["hide"] == after["hide"]
            for name, values in before["uv"].items():
                for index, value in values.items():
                    actual = after["uv"][name].get(vertex_map[index])
                    if actual is not None:
                        assert actual == value, (
                            row["source_index"],
                            target,
                            name,
                            index,
                            value,
                            actual,
                        )
                        observed.add((name, index))
        expected = {(name, index) for name, values in before["uv"].items() for index in values}
        assert observed == expected
        uv_corners += len(observed)
    for row in lineage["EDGE"]:
        before = original["edges"][row["source_index"]]
        for index in row["target_indices"]:
            assert before["seam"] == final["edges"][index]["seam"]
            assert before["hide"] == final["edges"][index]["hide"]
    output = {
        "status": "passed",
        "blender": bpy.app.version_string,
        "original_vertices_preserved": len(vertex_map),
        "original_weight_vectors_preserved": len(vertex_map),
        "original_uv_pin_corners_preserved": uv_corners,
        "other_objects_unchanged": len(original["others"]),
        "binding": final["binding"],
        "groups_and_locks_preserved": len(original["groups"]),
        "result": str(args.result),
    }
    mesh = bpy.data.objects[args.target].data
    authored = tuple(report["commit_path"]["authored_faces"])
    output["new_uv"] = {}
    for layer in mesh.uv_layers:
        uv_faces = [
            [tuple(layer.data[i].uv) for i in mesh.polygons[f].loop_indices] for f in authored
        ]
        areas = [_uv_polygon_area(points) for points in uv_faces]
        uv = [point for points in uv_faces for point in points]
        assert min(areas) > 1e-10
        assert all(1 - 1e-6 <= u <= 2 + 1e-6 and -1e-6 <= v <= 1 + 1e-6 for u, v in uv)
        output["new_uv"][layer.name] = {
            "faces": len(authored),
            "minimum_area": min(areas),
            "tile": [1, 0],
            "overlap_faces": list(_uv_overlap_faces(mesh, layer, authored)),
        }
        assert not output["new_uv"][layer.name]["overlap_faces"]
    args.report.write_text(json.dumps(output, indent=2), encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    for name in ("source", "result", "evidence", "report"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--target", required=True)
    run(parser.parse_args(sys.argv[sys.argv.index("--") + 1 :]))
