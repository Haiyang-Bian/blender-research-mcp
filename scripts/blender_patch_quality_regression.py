"""Small analytic native gates for local contact and cumulative displacement."""

from __future__ import annotations

from array import array
from types import SimpleNamespace

import bpy
from blender_research_mcp_addon.capture_model import CaptureBook
from blender_research_mcp_addon.mesh_deform_ops import edit_mesh_deform
from blender_research_mcp_addon.mesh_local_quality import local_quality
from blender_research_mcp_addon.mesh_ops import (
    MeshOperationError,
    mesh_fingerprint,
    mesh_user_refs,
    session_identity,
)
from blender_research_mcp_addon.mesh_patch_quality import illegal_contact
from blender_research_mcp_addon.mesh_resource_model import MeshResourceBook
from blender_research_mcp_addon.transaction_model import Transaction
from mathutils import Vector


def run(report):
    from blender_patch_regression import vertex
    from blender_regression_0172 import edit, selection
    from blender_research_mcp_addon.execution_budget import deadline_after, execution_deadline

    mesh = bpy.data.meshes.new("Legacy closed bridge")
    mesh.from_pydata(
        [(x, y, z) for z in (0, 1) for x, y in ((0, 0), (1, 0), (1, 1), (0, 1))],
        [(base + i, base + (i + 1) % 4) for base in (0, 4) for i in range(4)],
        [],
    )
    obj = bpy.data.objects.new("Legacy closed bridge", mesh)
    bpy.context.scene.collection.objects.link(obj)
    book = MeshResourceBook()
    legacy = edit(
        Transaction("legacy", "legacy", {}, "", 0),
        book,
        obj,
        {
            "type": "bridge",
            "selection_id": selection(book, obj, list(range(8))).selection_id,
        },
    )
    assert len(obj.data.polygons) == 4 and len(obj.data.vertices) == 8
    report["cases"].append({"name": "legacy closed bridge", "result": legacy})

    # Legal adjacent triangles remain legal at centimetre scale far from zero;
    # sharing one vertex never excuses an overlapping triangle interior.
    for scale, offset in ((1, (0, 0, 0)), (0.002, (1.6, 0, 1.55)), (100, (300, -200, 500))):
        coords = [
            Vector(offset) + Vector(co) * scale
            for co in ((0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0), (0.8, 0.2, 0), (0.2, 0.8, 0))
        ]
        assert not illegal_contact((0, 1, 2), (0, 2, 3), coords, scale * 1e-6)
        assert illegal_contact((0, 1, 2), (0, 4, 5), coords, scale * 1e-6)
    mesh = bpy.data.meshes.new("Local scope analytic")
    mesh.from_pydata(
        [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0), (10, 0, 0), (11, 0, 0), (10, 1, 0)],
        [],
        [(0, 1, 2, 3), (4, 5, 6), (4, 5, 6)],
    )
    quality = local_quality(
        mesh, SimpleNamespace(domain="FACE", indices=(0,)), "SELECTION_AND_NEIGHBORS", 1e-10
    )
    assert quality["complete"] and quality["denominators"]["faces"] == 1
    assert not quality["issues"]["duplicate_faces"] and not quality["issues"]["intersection_faces"]
    report["cases"].append(
        {"name": "translated contacts and local denominators", "quality": quality}
    )

    # Two swaps return to the original positions. The path length is 2, so a
    # 1.5 limit must reject even though net displacement would be zero.
    mesh = bpy.data.meshes.new("Cumulative path length")
    mesh.from_pydata([(0, 0, 0), (1, 0, 0)], [(0, 1)], [])
    obj = bpy.data.objects.new("Cumulative path length", mesh)
    bpy.context.scene.collection.objects.link(obj)
    book = MeshResourceBook()
    first = book.selection(vertex(book, obj, 0))
    selected = book.add_selection(
        **{
            key: getattr(first, key)
            for key in (
                "object_name",
                "object_identity",
                "mesh_name",
                "mesh_identity",
                "mesh_revision_id",
                "mesh_fingerprint",
                "expected_users",
                "expected_user_objects",
                "domain",
                "weights",
                "source_query",
            )
        },
        indices=(0, 1),
    )
    tx = Transaction("cumulative", "cumulative", {}, "", 0)
    before = mesh_fingerprint(mesh)
    try:
        edit_mesh_deform(
            tx,
            book,
            CaptureBook(),
            {
                "transaction_id": tx.transaction_id,
                "object_name": obj.name,
                "expected_object_identity": session_identity("object", obj),
                "expected_mesh_identity": session_identity("mesh", mesh),
                "expected_mesh_users": 1,
                "expected_mesh_user_objects": [
                    {"object_name": name, "expected_object_identity": identity}
                    for name, identity in mesh_user_refs(mesh)
                ],
                "expected_mesh_fingerprint": before,
                "data_scope": "OBJECT",
                "operation": {
                    "type": "smooth",
                    "selection_id": selected.selection_id,
                    "factor": 1,
                    "iterations": 2,
                    "preserve_boundary": False,
                    "maximum_displacement": 1.5,
                },
            },
        )
    except MeshOperationError as exc:
        assert exc.details["reason"] == "DISPLACEMENT_LIMIT", exc.details
        assert mesh_fingerprint(obj.data) == before
        report["cases"].append(
            {"name": "cumulative limit rejects zero-net roundtrip", "evidence": exc.details}
        )
    else:
        raise AssertionError("net displacement was incorrectly used as the limit")
    from blender_research_mcp_addon.mesh_uv_ops import _run_uv_operator

    mesh = bpy.data.meshes.new("Local multi UV packing")
    mesh.from_pydata(
        [(x, y, 0) for x, y in ((0, 0), (1, 0), (1, 1), (0, 1), (2, 0), (3, 0), (3, 1), (2, 1))],
        [],
        [(0, 1, 2, 3), (4, 5, 6, 7)],
    )
    for name in ("A", "B", "C"):
        mesh.uv_layers.new(name=name, do_init=False)
        mesh.uv_layers[name].uv.foreach_set("vector", array("f", [0, 0, 1, 0, 1, 1, 0, 1] * 2))
        mesh.uv_layers[name].pin.foreach_set("value", array("b", [1, 0, 0, 0, 0, 0, 0, 0]))
    before = {layer.name: [tuple(item.uv) for item in layer.data] for layer in mesh.uv_layers}
    for name in ("A", "B", "C"):
        _run_uv_operator(
            mesh, name, (1,), {"type": "unwrap", "method": "ANGLE_BASED", "pin_policy": "RESPECT"}
        )
        _run_uv_operator(mesh, name, (1,), {"type": "pack", "tile_u": 1, "pinned_policy": "MOVE"})
        assert [tuple(item.uv) for item in mesh.uv_layers[name].data[:4]] == before[name][:4]
        assert all(1 <= item.uv.x <= 2 for item in mesh.uv_layers[name].data[4:])
        assert mesh.uv_layers[name].pin[0].value
    report["cases"].append(
        {"name": "three-layer local unwrap pack preserves old UV and pins", "passed": True}
    )
    for side, expired in ((65, False), (4, True)):
        coords = (
            [(i, 0, 0) for i in range(side + 1)]
            + [(side, i, 0) for i in range(1, side + 1)]
            + [(i, side, 0) for i in range(side - 1, -1, -1)]
            + [(0, i, 0) for i in range(side - 1, 0, -1)]
        )
        mesh = bpy.data.meshes.new("Patch budget")
        mesh.from_pydata(coords, [(i, (i + 1) % len(coords)) for i in range(len(coords))], [])
        obj = bpy.data.objects.new("Patch budget", mesh)
        bpy.context.scene.collection.objects.link(obj)
        book = MeshResourceBook()
        boundary = {
            "type": "CLOSED_LOOP",
            "selection_id": selection(book, obj, list(range(len(coords)))).selection_id,
            "corners": [vertex(book, obj, i * side) for i in range(4)],
        }
        tx = Transaction("budget", "budget", {}, "", 0)
        before = mesh_fingerprint(mesh)
        retained = (len(book._selections), len(book._component_maps))
        try:
            with execution_deadline(deadline_after(0 if expired else 30)):
                edit(tx, book, obj, {"type": "grid_fill", "boundary": boundary})
        except MeshOperationError as exc:
            assert exc.details["reason"] == (
                "EXECUTION_DEADLINE" if expired else "OUTPUT_BUDGET_EXCEEDED"
            )
            assert not tx.deltas and mesh_fingerprint(obj.data) == before
            assert retained == (len(book._selections), len(book._component_maps))
            report["cases"].append(
                {"name": "deadline" if expired else "output budget", "evidence": exc.details}
            )
        else:
            raise AssertionError("patch budget not enforced before writes")
