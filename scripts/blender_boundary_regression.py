"""Synthetic boundary fixtures and assertions in an isolated Blender process."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "blender_addon"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from blender_regression_0172 import edit, selection  # noqa: E402
from blender_research_mcp_addon.mesh_boundary_ops import inspect_boundary  # noqa: E402
from blender_research_mcp_addon.mesh_ops import mesh_fingerprint  # noqa: E402
from blender_research_mcp_addon.mesh_resource_model import MeshResourceBook  # noqa: E402
from blender_research_mcp_addon.transaction_model import Transaction  # noqa: E402


def fixture(name, *, branches=False, shortcut=False, hidden=False):
    vertices = (
        [(i, 0, 0) for i in range(5)]
        + [(4, i, 0) for i in range(1, 5)]
        + [(i, 4, 0) for i in range(3, -1, -1)]
        + [(0, i, 0) for i in range(3, 0, -1)]
    )
    edges = [(i, (i + 1) % 16) for i in range(16)]
    if branches:
        vertices += [(5, 1, 0), (5, 2, 0)]
        edges += [(5, 16), (5, 17)]
    if shortcut:
        edges += [(5, 15)]
    mesh = bpy.data.meshes.new(name + " Mesh")
    mesh.from_pydata(vertices, edges, [])
    if hidden:
        mesh.edges[5].hide = True
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(obj)
    return obj


def run():
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--save", type=Path)
    args = parser.parse_args(sys.argv[sys.argv.index("--") + 1 :])
    book = MeshResourceBook()
    cases = []
    for name, flags, indices in (
        ("Boundary Basic", {}, list(range(4)) + list(range(8, 12))),
        ("Boundary Branch", {"branches": True}, list(range(4)) + list(range(8, 12))),
        ("Boundary Shortcut", {"shortcut": True}, list(range(4)) + list(range(8, 12))),
        ("Boundary Hidden", {"hidden": True}, list(range(4)) + list(range(8, 12))),
        ("Boundary Closed", {}, list(range(16))),
    ):
        obj = fixture(name, **flags)
        selected = selection(book, obj, indices)
        before = mesh_fingerprint(obj.data)
        report = inspect_boundary(book, {"selection_id": selected.selection_id})
        assert mesh_fingerprint(obj.data) == before
        if name in {"Boundary Basic", "Boundary Branch", "Boundary Shortcut"}:
            assert report["status"] == "READY", report
            tx = Transaction(name, name, {}, "", 0)
            result = edit(
                tx, book, obj, {"type": "grid_fill", "selection_id": selected.selection_id}
            )
            assert result["evidence"]["created_faces"] == 16
            assert len(obj.data.polygons) == 16
        else:
            assert report["status"] != "READY"
            result = None
        cases.append({"name": name, "inspection": report, "edit": result})
    # Keep a pristine wire fixture for the socket test.
    fixture("Boundary Live")
    if args.save:
        bpy.ops.wm.save_as_mainfile(filepath=str(args.save))
    args.report.write_text(
        json.dumps(
            {"blender": bpy.app.version_string, "cases": cases, "status": "passed"}, indent=2
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    run()
