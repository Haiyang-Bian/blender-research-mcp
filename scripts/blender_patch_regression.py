"""Explicit patch RNA contracts on distributable synthetic geometry."""

from __future__ import annotations

import argparse
import faulthandler
import json
import sys
from array import array
from pathlib import Path

import bpy

faulthandler.enable()

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "blender_addon"))
from blender_boundary_regression import fixture  # noqa: E402
from blender_regression_0172 import edit, selection  # noqa: E402
from blender_research_mcp_addon import mesh_topology_ops as topology  # noqa: E402
from blender_research_mcp_addon.mesh_ops import (  # noqa: E402
    MeshOperationError,
    mesh_fingerprint,
    mesh_revision_id,
    mesh_user_refs,
    session_identity,
)
from blender_research_mcp_addon.mesh_resource_model import (  # noqa: E402
    MeshResourceBook,
    MeshResourceError,
)
from blender_research_mcp_addon.transaction_model import Transaction  # noqa: E402


def vertex(book, obj, index):
    mesh = obj.data
    return book.add_selection(
        object_name=obj.name,
        object_identity=session_identity("object", obj),
        mesh_name=mesh.name,
        mesh_identity=session_identity("mesh", mesh),
        mesh_revision_id=mesh_revision_id(mesh),
        mesh_fingerprint=mesh_fingerprint(mesh),
        expected_users=int(mesh.users),
        expected_user_objects=mesh_user_refs(mesh),
        domain="VERTEX",
        indices=(index,),
        weights=None,
        source_query={"type": "fixture"},
    ).selection_id


def boundary(book, obj):
    return {
        "type": "FOUR_PATHS",
        "paths": [
            {
                "selection_id": selection(book, obj, list(range(i, i + 4))).selection_id,
                "start_vertex": vertex(book, obj, i),
            }
            for i in (0, 4, 8, 12)
        ],
    }


def decorated_ring(name):
    print("ring: geometry", name, flush=True)
    obj = fixture(name)
    coordinates = [tuple(v.co) for v in obj.data.vertices]
    coordinates += [(2 + (x - 2) * 1.5, 2 + (y - 2) * 1.5, z) for x, y, z in coordinates]
    mesh = bpy.data.meshes.new(name + " Ring")
    mesh.from_pydata(
        coordinates,
        [(i, (i + 1) % 16) for i in range(16)],
        [(i, i + 16, (i + 1) % 16 + 16, (i + 1) % 16) for i in range(16)],
    )
    obj.data = mesh
    print("ring: uv", flush=True)
    for n in range(3):
        name = f"UV{n}"
        mesh.uv_layers.new(name=name, do_init=False)
        values = array(
            "f",
            (
                value
                for loop in mesh.loops
                for value in (
                    mesh.vertices[loop.vertex_index].co.x / 4 + n,
                    mesh.vertices[loop.vertex_index].co.y / 4,
                )
            ),
        )
        mesh.uv_layers[name].uv.foreach_set("vector", values)
        mesh.uv_layers[name].pin.foreach_set(
            "value", array("b", (int(i % 3 == 0) for i in range(len(mesh.loops))))
        )
    color = mesh.color_attributes.new(name="Tint", type="BYTE_COLOR", domain="CORNER")
    for item in color.data:
        item.color = (0.2, 0.4, 0.8, 1)
    for n in range(761):
        group = obj.vertex_groups.new(name=f"Bone{n:03}")
        if n < 2:
            group.add(list(range(32)), 0.5, "REPLACE")
        group.lock_weight = n == 0
    for edge in mesh.edges:
        edge.use_seam = edge.index % 3 == 0
    return obj


def attribute_snapshot(obj):
    mesh = obj.data
    return {
        "uv": [[(tuple(d.uv), d.pin_uv) for d in layer.data] for layer in mesh.uv_layers],
        "colors": [tuple(d.color) for d in mesh.color_attributes[0].data],
        "weights": [[(g.group, g.weight) for g in v.groups] for v in mesh.vertices],
        "groups": [
            (g.name, g.lock_weight, session_identity("vertex_group", g)) for g in obj.vertex_groups
        ],
        "seams": [e.use_seam for e in mesh.edges],
    }


def attribute_cases(report):
    for failure in (None, "writeback", "publication"):
        print("attribute case", failure, flush=True)
        book = MeshResourceBook()
        obj = decorated_ring(f"Attributes {failure}")
        source = obj.data
        sibling = bpy.data.objects.new(f"Sibling {failure}", source)
        bpy.context.scene.collection.objects.link(sibling)
        before = mesh_fingerprint(source)
        attrs = attribute_snapshot(obj)
        print("attributes captured", flush=True)
        spec = boundary(book, obj)
        resources = set(book._selections)
        tx = Transaction(f"attrs-{failure}", "attributes", {}, "", 0)
        original_write = topology.write_bmesh_exact
        original_add = book.add_selection
        count = [0]

        def failing_write(bm, mesh, original_write=original_write):
            original_write(bm, mesh)
            raise MeshOperationError("MESH_EDIT_FAILED", "injected after native write")

        def failing_add(count=count, original_add=original_add, **kwargs):
            count[0] += 1
            if count[0] == 3:
                raise MeshResourceError(
                    "MESH_RESOURCE_BUDGET_EXCEEDED", "injected publication failure"
                )
            return original_add(**kwargs)

        try:
            if failure == "writeback":
                topology.write_bmesh_exact = failing_write
            elif failure == "publication":
                book.add_selection = failing_add
            print("patch edit start", flush=True)
            result = edit(tx, book, obj, {"type": "grid_fill", "boundary": spec})
            print("patch edit done", flush=True)
        except (MeshOperationError, MeshResourceError):
            if failure is None:
                raise
            assert mesh_fingerprint(obj.data) == before
            assert obj.data is source and sibling.data is source
            assert set(book._selections) == resources and not book._component_maps
            assert attribute_snapshot(obj) == attrs
            report["cases"].append({"failure": failure, "restored": True})
        else:
            assert failure is None
            after = attribute_snapshot(obj)
            assert all(a == b[: len(a)] for a, b in zip(attrs["uv"], after["uv"], strict=True))
            assert attrs["colors"] == after["colors"][: len(attrs["colors"])]
            assert attrs["weights"] == after["weights"][:32]
            # OBJECT scope deliberately copies the shared Mesh and its Group data.
            assert [row[:2] for row in attrs["groups"]] == [row[:2] for row in after["groups"]]
            assert attrs["seams"] == after["seams"][: len(attrs["seams"])]
            assert mesh_fingerprint(sibling.data) == before and obj.data is not source
            assert all(row == [(0, 0.5), (1, 0.5)] for row in after["weights"][32:])
            record = book.component_map(result["component_map"]["component_map_id"])
            for relation in record.relations["EDGE"]:
                a = source.edges[relation.source_index]
                b = obj.data.edges[relation.target_indices[0]]
                assert tuple(a.vertices) == tuple(b.vertices)
            for relation in record.relations["FACE"]:
                a = source.polygons[relation.source_index]
                b = obj.data.polygons[relation.target_indices[0]]
                assert tuple(a.vertices) == tuple(b.vertices)
            report["cases"].append(
                {"name": "761 groups, UV, pins, colors, seams, shared Mesh", "result": result}
            )
        finally:
            topology.write_bmesh_exact = original_write
            book.add_selection = original_add


def rejection_cases(report):
    for name, coordinates, cycle, reason in (
        ("Nonplanar", [(0, 0, 0), (1, 0, 0), (1, 1, 0.3), (0, 1, 0)], [0, 1, 2, 3], None),
        (
            "Bow tie",
            [(0, 0, 0), (1, 0, 0), (1, 1, 0), (0, 1, 0)],
            [0, 2, 1, 3],
            "SELF_INTERSECTION",
        ),
        ("Collinear", [(0, 0, 0), (1, 0, 0), (2, 0, 0)], [0, 1, 2], "DEGENERATE_FACE"),
    ):
        mesh = bpy.data.meshes.new(name)
        mesh.from_pydata(coordinates, [], [])
        obj = bpy.data.objects.new(name, mesh)
        bpy.context.scene.collection.objects.link(obj)
        book = MeshResourceBook()
        tx = Transaction(name, name, {}, "", 0)
        before = mesh_fingerprint(mesh)
        try:
            result = edit(
                tx,
                book,
                obj,
                {"type": "create_face", "vertices": [vertex(book, obj, i) for i in cycle]},
            )
        except MeshOperationError as exc:
            assert reason is not None and exc.details["reason"] == reason, exc.details
            assert mesh_fingerprint(mesh) == before and not tx.deltas
            report["cases"].append({"name": name, "rejected": exc.details})
        else:
            assert reason is None
            report["cases"].append({"name": name, "result": result})
    obj = decorated_ring("UV ambiguity")
    obj.data.uv_layers["UV0"].uv[0].vector.x += 2
    book = MeshResourceBook()
    spec = boundary(book, obj)
    tx = Transaction("uv", "uv", {}, "", 0)
    before = mesh_fingerprint(obj.data)
    try:
        edit(tx, book, obj, {"type": "grid_fill", "boundary": spec})
    except MeshOperationError as exc:
        assert exc.details["reason"] == "ATTRIBUTE_SOURCE_AMBIGUOUS", exc.details
        assert mesh_fingerprint(obj.data) == before and not tx.deltas
    else:
        raise AssertionError("ambiguous UV accepted")
    result = edit(
        tx,
        book,
        obj,
        {"type": "grid_fill", "boundary": spec, "uv_creation": {"UV0": "INDEPENDENT_ISLAND"}},
    )
    assert result["evidence"]["attribute_creation"]["uv"]["UV0"]["requires_unwrap_pack"]
    report["cases"].append({"name": "explicit UV island", "result": result})


def run(args):
    report = {"blender": bpy.app.version_string, "cases": []}
    for closed in (False, True):
        book = MeshResourceBook()
        obj = fixture("Explicit Closed" if closed else "Explicit Four")
        before = [tuple(v.co) for v in obj.data.vertices]
        if closed:
            spec = {
                "type": "CLOSED_LOOP",
                "selection_id": selection(book, obj, list(range(16))).selection_id,
                "corners": [vertex(book, obj, i) for i in (0, 4, 8, 12)],
            }
        else:
            spec = boundary(book, obj)
        result = edit(
            Transaction("patch", "patch", {}, "", 0),
            book,
            obj,
            {"type": "grid_fill", "boundary": spec},
        )
        assert len(obj.data.polygons) == 16 and len(obj.data.vertices) == 25
        assert [tuple(v.co) for v in obj.data.vertices[:16]] == before
        record = book.component_map(result["component_map"]["component_map_id"])
        assert record.created["VERTEX"] == tuple(range(16, 25))
        assert all(
            r.relation == "SURVIVED" and r.target_indices == (r.source_index,)
            for r in record.relations["VERTEX"]
        )
        report["cases"].append({"name": obj.name, "result": result})
    book = MeshResourceBook()
    obj = fixture("Exact Face")
    tx = Transaction("exact", "exact", {}, "", 0)
    edge_refs = [vertex(book, obj, i) for i in (0, 1)]
    before = mesh_fingerprint(obj.data)
    result = edit(tx, book, obj, {"type": "create_edge", "vertices": edge_refs})
    assert not result["changed"] and mesh_fingerprint(obj.data) == before
    assert not tx.deltas and not book._component_maps
    result = edit(
        tx,
        book,
        obj,
        {"type": "create_face", "vertices": [vertex(book, obj, i) for i in (0, 1, 15)]},
    )
    assert len(obj.data.polygons) == 1
    before = mesh_fingerprint(obj.data)
    try:
        edit(
            tx,
            book,
            obj,
            {"type": "create_face", "vertices": [vertex(book, obj, i) for i in (0, 1, 15)]},
        )
    except MeshOperationError as exc:
        assert exc.details["reason"] == "DUPLICATE_FACE", exc.details
    else:
        raise AssertionError("duplicate face accepted")
    assert mesh_fingerprint(obj.data) == before
    report["cases"].append({"name": obj.name, "result": result})
    # Parallel disconnected chains, with an explicit endpoint correspondence.
    mesh = bpy.data.meshes.new("Bridge Mesh")
    mesh.from_pydata(
        [(i, j, 0) for j in (0, 2) for i in range(5)],
        [(i, i + 1) for i in (0, 1, 2, 3, 5, 6, 7, 8)],
        [],
    )
    obj = bpy.data.objects.new("Open Bridge", mesh)
    bpy.context.scene.collection.objects.link(obj)
    book = MeshResourceBook()
    paths = [
        {
            "selection_id": selection(book, obj, list(range(i, i + 4))).selection_id,
            "start_vertex": vertex(book, obj, v),
        }
        for i, v in ((0, 0), (4, 5))
    ]
    result = edit(
        Transaction("bridge", "bridge", {}, "", 0),
        book,
        obj,
        {"type": "bridge", "paths": paths, "cuts": 2},
    )
    assert len(obj.data.polygons) == 12 and len(obj.data.vertices) == 20
    report["cases"].append({"name": obj.name, "result": result})
    attribute_cases(report)
    rejection_cases(report)
    from blender_patch_quality_regression import run as quality_cases

    quality_cases(report)
    fixture("Boundary Live")
    mesh = bpy.data.meshes.new("Patch Reference Mesh")
    mesh.from_pydata(
        [(-1, -1, 0.05), (5, -1, 0.05), (5, 5, 0.05), (-1, 5, 0.05)], [], [(0, 1, 2, 3)]
    )
    reference = bpy.data.objects.new("Patch Reference", mesh)
    bpy.context.scene.collection.objects.link(reference)
    report["status"] = "passed"
    args.report.write_text(json.dumps(report, indent=2), encoding="utf-8")
    if args.save:
        bpy.ops.wm.save_as_mainfile(filepath=str(args.save))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--save", type=Path)
    run(parser.parse_args(sys.argv[sys.argv.index("--") + 1 :]))
