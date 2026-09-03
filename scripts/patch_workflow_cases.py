"""A compact split-patch/fit workflow and batch failure gates over the real socket."""

from __future__ import annotations

import json

import live_smoke_013 as topology
import live_smoke_0131 as batches
import live_smoke_015 as base

from blender_research_mcp.errors import BridgeError
from blender_research_mcp.observation import capture_image


async def exercise(client, report, directory):
    baseline = await base.mesh(client, "Boundary Live")
    inputs, handles = [], {}
    specs = {
        "bottom_side": ("EDGE", [0]),
        "top_side": ("EDGE", [11]),
        "left": ("EDGE", list(range(12, 16))),
        "bottom": ("EDGE", [1, 2, 3]),
        "right": ("EDGE", [4, 5, 6, 7]),
        "top": ("EDGE", [8, 9, 10]),
        **{f"v{i}": ("VERTEX", [i]) for i in (0, 1, 4, 8, 11, 12)},
    }
    for alias, (domain, indices) in specs.items():
        selected = await topology.selection_query(
            client, baseline, domain, {"type": "indices", "indices": indices}
        )
        handles[alias] = selected["selection_id"]
        inputs.append(
            {
                "type": "selection",
                "alias": alias,
                "target_alias": "patch",
                "selection_id": selected["selection_id"],
            }
        )
    annotation = {
        "paths": [
            {"selection_id": handles[p], "start_vertex": handles[v]}
            for p, v in (("bottom", "v1"), ("right", "v4"), ("top", "v8"), ("left", "v12"))
        ],
        "problem_vertices": [handles["v1"]],
    }
    png, meta = await capture_image(
        client,
        object_name="Boundary Live",
        view="TOP",
        max_size=900,
        viewport_id=None,
        display_mode="SOLID",
        overlays="OFF",
        boundary_annotations=annotation,
    )
    (directory / "boundary-annotations.png").write_bytes(png)
    report["annotations"] = meta
    assert meta["boundary_annotations"]["paths"] == 4

    def path(edge, vertex):
        return {"selection_alias": edge, "start_vertex_alias": vertex}

    def edit(operation, **outputs):
        return {
            "type": "mesh_edit",
            "target_alias": "patch",
            "data_scope": "OBJECT",
            "operation": operation,
            **outputs,
        }

    steps = [
        edit(
            {"type": "create_edge", "vertex_aliases": ["v1", "v11"]},
            created_selection_aliases={"edge": "divider"},
        ),
        edit(
            {"type": "subdivide", "selection_alias": "divider", "cuts": 3},
            created_selection_aliases={"vertex": "strip_inner"},
        ),
        edit(
            {
                "type": "grid_fill",
                "boundary": {
                    "type": "FOUR_PATHS",
                    "paths": [
                        path("bottom_side", "v0"),
                        path("divider", "v1"),
                        path("top_side", "v11"),
                        path("left", "v12"),
                    ],
                },
            },
            created_selection_aliases={"face": "strip_faces"},
        ),
        edit(
            {
                "type": "grid_fill",
                "boundary": {
                    "type": "FOUR_PATHS",
                    "paths": [
                        path("bottom", "v1"),
                        path("right", "v4"),
                        path("top", "v8"),
                        path("divider", "v11"),
                    ],
                },
            },
            created_selection_aliases={"vertex": "center_inner", "face": "center_faces"},
        ),
        {
            "type": "selection_derive",
            "output_alias": "interior",
            "operation": {
                "type": "combine",
                "mode": "UNION",
                "selection_aliases": ["strip_inner", "center_inner"],
            },
        },
        {
            "type": "selection_derive",
            "output_alias": "patch_faces",
            "operation": {
                "type": "combine",
                "mode": "UNION",
                "selection_aliases": ["strip_faces", "center_faces"],
            },
        },
        {
            "type": "mesh_surface_prepare",
            "target_alias": "reference",
            "geometry": "BASE",
            "output_surface_alias": "plane",
        },
        edit(
            {
                "type": "project",
                "selection_alias": "interior",
                "surface_alias": "plane",
                "maximum_distance": 0.1,
                "maximum_displacement": 0.06,
                "on_miss": "ERROR",
            }
        ),
        {
            "type": "mesh_validate",
            "selection_alias": "patch_faces",
            "check": "DEGENERATE",
            "scope": "SELECTION_AND_NEIGHBORS",
            "output_alias": "quality",
            "assertions": [{"type": "count_at_most", "value": 0}],
        },
    ]
    reference = await base.mesh(client, "Patch Reference")
    tx = await base.begin(
        client, baseline["scene_generation"], "Split patch and fixed-boundary fit"
    )
    result = await base.mutate(
        client,
        "mesh.batch.execute",
        {
            "transaction_id": tx["transaction_id"],
            "targets": [
                batches.batch_target("patch", baseline),
                batches.batch_target("reference", reference),
            ],
            "inputs": inputs,
            "steps": steps,
            "on_error": "ROLLBACK_TRANSACTION",
        },
        tx["scene_generation"],
    )
    assert result["scene_generation"] == tx["scene_generation"] + 1
    vertices = await client.call(
        "mesh.inspect",
        {"object_name": "Boundary Live", "component": "vertices", "offset": 0, "limit": 64},
        read_only=True,
    )
    assert len(vertices["items"]) == 25
    assert all(abs(v["co"][2]) < 1e-8 for v in vertices["items"][:16])
    assert all(abs(v["co"][2] - 0.05) < 1e-7 for v in vertices["items"][16:])
    report["split_patch_fit"] = result
    await base.mutate(
        client,
        "transaction.rollback",
        {"transaction_id": tx["transaction_id"]},
        result["scene_generation"],
    )
    assert (await base.mesh(client, "Boundary Live"))["mesh_fingerprint"] == baseline[
        "mesh_fingerprint"
    ]

    # An input alias error is preflight-only and retains the earlier accepted edit.
    tx = await base.begin(client, baseline["scene_generation"], "Batch failure boundaries")
    selected = await topology.selection_query(
        client, baseline, "VERTEX", {"type": "indices", "indices": [0]}
    )
    before_batch = await base.mutate(
        client,
        "mesh.edit",
        topology.edit_params(
            tx["transaction_id"],
            baseline,
            {
                "type": "set_positions",
                "selection_id": selected["selection_id"],
                "mode": "OFFSET",
                "positions": [{"x": 0, "y": 0, "z": 0.01}],
            },
        ),
        tx["scene_generation"],
    )
    current = await base.mesh(client, "Boundary Live")
    bad = {
        "transaction_id": tx["transaction_id"],
        "targets": [batches.batch_target("patch", current)],
        "inputs": [],
        "steps": [edit({"type": "create_edge", "vertex_aliases": ["missing", "also_missing"]})],
    }
    try:
        await base.mutate(client, "mesh.batch.execute", bad, before_batch["scene_generation"])
    except BridgeError as exc:
        report["batch_preflight_error"] = exc.error.model_dump(mode="json")
    else:
        raise AssertionError("unknown aliases accepted")
    assert (await base.mesh(client, "Boundary Live"))["mesh_fingerprint"] == current[
        "mesh_fingerprint"
    ]
    live_vertex = await topology.selection_query(
        client, current, "VERTEX", {"type": "indices", "indices": [0]}
    )
    bad["inputs"] = [
        {
            "type": "selection",
            "alias": name,
            "target_alias": "patch",
            "selection_id": live_vertex["selection_id"],
        }
        for name in ("a", "b")
    ]
    bad["steps"] = [edit({"type": "create_edge", "vertex_aliases": ["a", "b"]})]
    try:
        await base.mutate(client, "mesh.batch.execute", bad, current["scene_generation"])
    except BridgeError as exc:
        assert exc.error.details["reason"] == "DUPLICATE_VERTEX", exc.error
        report["known_geometry_preflight"] = exc.error.model_dump(mode="json")
    else:
        raise AssertionError("known invalid geometry was not preflighted")
    assert (await base.mesh(client, "Boundary Live"))["mesh_fingerprint"] == current[
        "mesh_fingerprint"
    ]
    bad["inputs"] = []
    # A query-generated non-singleton vertex is only knowable at runtime.
    bad["steps"] = [
        {
            "type": "selection_query",
            "target_alias": "patch",
            "domain": "VERTEX",
            "output_alias": "many",
            "query": {"type": "all"},
        },
        edit({"type": "create_edge", "vertex_aliases": ["many", "many"]}),
    ]
    try:
        await base.mutate(client, "mesh.batch.execute", bad, current["scene_generation"])
    except BridgeError as exc:
        assert exc.error.details["rollback"]["status"] == "rolled_back", exc.error
        report["batch_runtime_error"] = exc.error.model_dump(mode="json")
    else:
        raise AssertionError("ambiguous runtime vertex accepted")
    assert (await base.mesh(client, "Boundary Live"))["mesh_fingerprint"] == baseline[
        "mesh_fingerprint"
    ]
    (directory / "integrated-workflow.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
