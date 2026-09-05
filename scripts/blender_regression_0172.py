"""Run isolated real-RNA regressions; optionally save their deterministic fixture.

Executed only by a separate Blender process, never through an arbitrary-Python MCP tool.
"""

from __future__ import annotations

import argparse
import json
import sys
from array import array
from pathlib import Path

import bmesh
import bpy
from mathutils import Matrix

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "blender_addon"))

from blender_research_mcp_addon import mesh_join_ops as join  # noqa: E402
from blender_research_mcp_addon import mesh_ops as mesh_ops  # noqa: E402
from blender_research_mcp_addon import mesh_topology_ops as topology  # noqa: E402
from blender_research_mcp_addon import mesh_weight_ops as weights  # noqa: E402
from blender_research_mcp_addon.mesh_resource_model import MeshResourceBook  # noqa: E402
from blender_research_mcp_addon.transaction_model import Transaction  # noqa: E402


def grid(name: str, size: int, collection: bpy.types.Collection) -> bpy.types.Object:
    mesh = bpy.data.meshes.new(name + " Mesh")
    bm = bmesh.new()
    bmesh.ops.create_grid(bm, x_segments=size, y_segments=size, size=2.0)
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new(name, mesh)
    collection.objects.link(obj)
    return obj


def decorate(obj: bpy.types.Object) -> None:
    mesh = obj.data
    for index in range(3):
        name = f"UV{index}"
        mesh.uv_layers.new(name=name, do_init=False)
        values = array(
            "f", (value for i in range(len(mesh.loops)) for value in (i % 4 / 4, index / 4))
        )
        mesh.uv_layers[name].uv.foreach_set("vector", values)
        pins = array("b", (int(i % 19 == index) for i in range(len(mesh.loops))))
        mesh.uv_layers[name].pin.foreach_set("value", pins)
    mesh.uv_layers.active_index = 1
    mesh.uv_layers[2].active_render = True
    mesh.uv_layers[1].active_clone = True
    color = mesh.color_attributes.new(name="Tint", type="BYTE_COLOR", domain="CORNER")
    color.data.foreach_set("color", array("f", [0.2, 0.4, 0.8, 1.0]) * len(color.data))
    for index in range(761):
        group = obj.vertex_groups.new(name=f"Bone{index:03}")
        if index < 2:
            group.add(list(range(len(mesh.vertices))), 0.5, "REPLACE")
    for edge in mesh.edges:
        if edge.index % 37 == 0:
            edge.use_seam = True
            edge.use_edge_sharp = True


def edge_endpoints(mesh: bpy.types.Mesh) -> tuple[tuple[int, int], ...]:
    return tuple(tuple(sorted(edge.vertices)) for edge in mesh.edges)


def boundary(mesh: bpy.types.Mesh) -> list[int]:
    counts = [0] * len(mesh.edges)
    for loop in mesh.loops:
        counts[loop.edge_index] += 1
    return [i for i, count in enumerate(counts) if count == 1]


def selection(book: MeshResourceBook, obj: bpy.types.Object, indices: list[int]):
    mesh = obj.data
    return book.add_selection(
        object_name=obj.name,
        object_identity=mesh_ops.session_identity("object", obj),
        mesh_name=mesh.name,
        mesh_identity=mesh_ops.session_identity("mesh", mesh),
        mesh_revision_id=mesh_ops.mesh_revision_id(mesh),
        mesh_fingerprint=mesh_ops.mesh_fingerprint(mesh),
        expected_users=int(mesh.users),
        expected_user_objects=mesh_ops.mesh_user_refs(mesh),
        domain="EDGE",
        indices=tuple(sorted(indices)),
        weights=None,
        source_query={"type": "regression"},
    )


def edit(transaction, book, obj, operation, scope="OBJECT"):
    mesh = obj.data
    return topology.edit_mesh_topology(
        transaction,
        book,
        {
            "transaction_id": transaction.transaction_id,
            "object_name": obj.name,
            "expected_object_identity": mesh_ops.session_identity("object", obj),
            "expected_mesh_identity": mesh_ops.session_identity("mesh", mesh),
            "expected_mesh_users": int(mesh.users),
            "expected_mesh_user_objects": [
                {"object_name": name, "expected_object_identity": identity}
                for name, identity in mesh_ops.mesh_user_refs(mesh)
            ],
            "expected_mesh_fingerprint": mesh_ops.mesh_fingerprint(mesh),
            "data_scope": scope,
            "operation": operation,
        },
    )


def merge_boundary(transaction, book, obj, scope):
    mesh = obj.data
    endpoints = list(mesh.edges[boundary(mesh)[0]].vertices)
    before_edges = edge_endpoints(mesh)
    result = mesh_ops.edit_mesh(
        transaction,
        {
            "transaction_id": transaction.transaction_id,
            "object_name": obj.name,
            "expected_object_identity": mesh_ops.session_identity("object", obj),
            "expected_mesh_identity": mesh_ops.session_identity("mesh", mesh),
            "expected_mesh_users": int(mesh.users),
            "expected_mesh_user_objects": [
                {"object_name": name, "expected_object_identity": identity}
                for name, identity in mesh_ops.mesh_user_refs(mesh)
            ],
            "expected_mesh_fingerprint": mesh_ops.mesh_fingerprint(mesh),
            "data_scope": scope,
            "operation": {
                "type": "merge_vertices",
                "vertex_indices": endpoints,
                "destination": "TARGET",
                "target_index": endpoints[0],
            },
        },
        resources=book,
    )
    record = book.component_map(result["component_map"]["component_map_id"])
    vertex_map = {row.source_index: row.target_indices[0] for row in record.relations["VERTEX"]}
    for row in record.relations["EDGE"]:
        if len(row.target_indices) == 1:
            assert set(obj.data.edges[row.target_indices[0]].vertices) == {
                vertex_map[v] for v in before_edges[row.source_index]
            }
    assert record.after_mesh_fingerprint == mesh_ops.mesh_fingerprint(obj.data)


def verify_edge_lineage(record, before_edges, mesh):
    vertices = {
        row.source_index: row.target_indices[0]
        for row in record.relations["VERTEX"]
        if row.relation == "SURVIVED"
    }
    checked = 0
    for row in record.relations["EDGE"]:
        a, b = before_edges[row.source_index]
        assert a in vertices and b in vertices, (
            a,
            b,
            len(vertices),
            {
                key: sum(r.relation == key for r in record.relations["VERTEX"])
                for key in ("SURVIVED", "SPLIT", "DERIVED")
            },
        )
        # An edge split must form exactly one chain between its original endpoints.
        degree = {}
        adjacency = {}
        for index in row.target_indices:
            endpoints = tuple(mesh.edges[index].vertices)
            for v, other in (endpoints, endpoints[::-1]):
                degree[v] = degree.get(v, 0) + 1
                adjacency.setdefault(v, set()).add(other)
        ends = {v for v, count in degree.items() if count == 1}
        assert ends == {vertices[a], vertices[b]}, (row, ends, (a, b))
        assert all(count in {1, 2} for count in degree.values())
        seen, pending = set(), [vertices[a]]
        while pending:
            value = pending.pop()
            if value not in seen:
                seen.add(value)
                pending.extend(adjacency[value] - seen)
        assert seen == set(degree), "Disconnected descendants are not an edge lineage"
        checked += 1
    assert checked == len(before_edges)
    assert record.after_mesh_fingerprint == mesh_ops.mesh_fingerprint(mesh)
    return checked


def topology_regression(obj, scope="OBJECT", peer=None):
    baseline_mesh = obj.data
    baseline = mesh_ops.mesh_fingerprint(obj.data)
    schema = weights.group_schema_fingerprint(obj)
    baseline_weights = weights.weights_fingerprint(obj.data)
    book = MeshResourceBook()
    transaction = Transaction("topology-0172", None, {}, "", 0)
    checked = []
    for iteration in range(2):
        print("TOPOLOGY", scope, bool(peer), iteration, flush=True)
        merge_boundary(transaction, book, obj, scope)
        edges_before = edge_endpoints(obj.data)
        vertices_before = len(obj.data.vertices)
        chosen = selection(book, obj, boundary(obj.data))
        result = edit(
            transaction,
            book,
            obj,
            {
                "type": "subdivide",
                "selection_id": chosen.selection_id,
                "cuts": 1,
                "use_grid_fill": False,
            },
            scope,
        )
        record = book.component_map(result["component_map"]["component_map_id"])
        checked.append(verify_edge_lineage(record, edges_before, obj.data))
        print("LINEAGE OK", checked[-1], flush=True)
        assert len(record.created["VERTEX"]) == len(obj.data.vertices) - vertices_before
        assert result["components"]["created"]["vertices"]["count"] == len(record.created["VERTEX"])
        assert result["components"]["deleted"]["vertices"]["count"] == 0
        if peer is not None:
            assert (peer.data is obj.data) == (scope == "SHARED_DATA")
            if scope == "OBJECT":
                assert mesh_ops.mesh_fingerprint(peer.data) == baseline
        current = mesh_ops.mesh_fingerprint(obj.data)
        current_schema = weights.group_schema_fingerprint(obj)
        # Reject after snapshot/weight guards exist, but before writing geometry.
        interior = next(i for i in range(len(obj.data.edges)) if i not in boundary(obj.data))
        invalid = selection(book, obj, [interior])
        for operation in ("fill", "grid_fill"):
            print("REJECT", operation, flush=True)
            try:
                edit(
                    transaction,
                    book,
                    obj,
                    {
                        "type": operation,
                        "selection_id": invalid.selection_id,
                    },
                    scope,
                )
            except mesh_ops.MeshOperationError as exc:
                assert exc.code == "MESH_BOUNDARY_INVALID", (exc.code, exc.details)
            else:
                raise AssertionError("Invalid boundary accepted")
            assert mesh_ops.mesh_fingerprint(obj.data) == current
            assert weights.group_schema_fingerprint(obj) == current_schema
            weights.validate_weight_snapshot_guards(transaction)
        valid = selection(book, obj, boundary(obj.data))
        original_finish = topology.finish_topology_attributes

        def reject_after_write(*_args):
            raise mesh_ops.MeshOperationError("MESH_ATTRIBUTE_MIGRATION_FAILED", "injected")

        topology.finish_topology_attributes = reject_after_write
        try:
            try:
                edit(
                    transaction,
                    book,
                    obj,
                    {
                        "type": "subdivide",
                        "selection_id": valid.selection_id,
                        "cuts": 1,
                    },
                    scope,
                )
            except mesh_ops.MeshOperationError as exc:
                assert exc.code == "MESH_ATTRIBUTE_MIGRATION_FAILED", (exc.code, exc.details)
            else:
                raise AssertionError("Injected post-write failure was ignored")
        finally:
            topology.finish_topology_attributes = original_finish
        assert mesh_ops.mesh_fingerprint(obj.data) == current
        assert weights.group_schema_fingerprint(obj) == current_schema
        weights.validate_weight_snapshot_guards(transaction)
        assert record.after_mesh_fingerprint == mesh_ops.mesh_fingerprint(obj.data)
        assert not any("mcp_lineage" in item.name for item in obj.data.attributes)
    weights.validate_weight_snapshot_guards(transaction)
    group = obj.vertex_groups[0]
    group.lock_weight = True
    try:
        weights.validate_weight_snapshot_guards(transaction)
    except weights.MeshWeightOperationError as exc:
        assert exc.code == "MESH_WEIGHT_DATA_CONFLICT"
        assert group.lock_weight is True
    else:
        raise AssertionError("External Group edits must still conflict")
    group.lock_weight = False
    print("RESTORE MESH", flush=True)
    mesh_ops.restore_mesh_snapshots(transaction)
    print(
        "SCHEMA AFTER MESH",
        len(obj.vertex_groups),
        weights.group_schema_fingerprint(obj) == schema,
        flush=True,
    )
    print("RESTORE WEIGHTS", flush=True)
    weights.restore_weight_snapshots(transaction)
    assert obj.data is baseline_mesh
    assert mesh_ops.mesh_fingerprint(obj.data) == baseline
    assert weights.weights_fingerprint(obj.data) == baseline_weights
    assert weights.group_schema_fingerprint(obj) == schema
    bpy.context.view_layer.update()
    return {
        "scope": scope,
        "checked_edge_relations": checked,
        "restored": True,
        "mesh_fingerprint": baseline,
        "group_schema_fingerprint": schema,
    }


def join_regression(sources, collection):
    fingerprints = [mesh_ops.mesh_fingerprint(s["mesh"]) for s in sources]
    schemas = {
        "materials": join._material_schema(sources, "PRESERVE_BY_IDENTITY"),
        "uv": join._uv_schema(sources, "MERGE_BY_NAME"),
        "weights": join._group_schema(sources, "MERGE_BY_NAME"),
        "colors": join._color_schema(sources, "MERGE_BY_NAME"),
    }
    results = []
    for iteration in range(6):
        print("JOIN", iteration, flush=True)
        obj, mesh, offsets = join._build_output(
            {
                "sources": sources,
                "schemas": schemas,
                "output": {
                    "raw": {"new_object_name": "Join Output", "new_mesh_name": "Join Output Mesh"},
                    "collection": collection,
                    "matrix_world": Matrix.Identity(4),
                    "inverse_matrix_world": Matrix.Identity(4),
                },
            }
        )
        bpy.context.view_layer.update()
        assert len(obj.vertex_groups) == 761
        assert len(mesh.uv_layers) == 3
        for source, offset in zip(sources, offsets, strict=True):
            original = source["mesh"]
            for layer in original.uv_layers:
                for i, item in enumerate(layer.data):
                    target = mesh.uv_layers[layer.name].data[offset["loop"] + i]
                    assert tuple(item.uv) == tuple(target.uv)
                    assert item.pin_uv == target.pin_uv
            for edge in original.edges:
                assert set(mesh.edges[offset["edge_map"][edge.index]].vertices) == {
                    v + offset["vertex"] for v in edge.vertices
                }
            for face in original.polygons:
                material = (
                    original.materials[face.material_index]
                    if face.material_index < len(original.materials)
                    else None
                )
                target_face = mesh.polygons[face.index + offset["face"]]
                assert mesh.materials[target_face.material_index] == material
        results.append(mesh_ops.mesh_fingerprint(mesh))
        assert fingerprints == [mesh_ops.mesh_fingerprint(s["mesh"]) for s in sources]
        bpy.data.objects.remove(obj, do_unlink=True)
        bpy.data.meshes.remove(mesh)
        bpy.context.view_layer.update()
    return {"iterations": len(results), "fingerprints": results, "sources_preserved": True}


def main():
    args = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    options = parser.parse_args(args)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    collection = bpy.data.collections.new("Regression Sources")
    bpy.context.scene.collection.children.link(collection)
    mesh = grid("Topology Grid", 65, collection)
    decorate(mesh)
    report = {"blender_version": bpy.app.version_string, "topology": []}
    report["topology"].append(topology_regression(mesh))
    peer = mesh.copy()
    peer.name = "Topology Peer"
    collection.objects.link(peer)
    report["topology"].append(topology_regression(mesh, peer=peer))
    report["topology"].append(topology_regression(mesh, "SHARED_DATA", peer))
    left = grid("Join Detailed", 30, collection)
    right = grid("Join Slotless", 30, collection)
    decorate(left)
    left.data.materials.append(bpy.data.materials.new("Skin"))
    sources = [{"object": obj, "mesh": obj.data} for obj in (left, right)]
    report["join_slotless"] = join_regression(sources, collection)
    right.data.materials.append(None)
    report["join_empty_slot"] = join_regression(sources, collection)
    right.data.materials.clear()
    bpy.context.view_layer.update()
    options.output.parent.mkdir(parents=True, exist_ok=True)
    assert "FINISHED" in bpy.ops.wm.save_as_mainfile(filepath=str(options.output))
    options.report.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"status": "passed", "report": str(options.report)}), flush=True)


if __name__ == "__main__":
    main()
