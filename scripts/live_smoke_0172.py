"""Exercise rejected topology recovery and multi-layer Join over a real MCP socket."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from uuid import uuid4

import live_smoke_0111 as collaboration
import live_smoke_013 as topology
import live_smoke_014 as attributes
import live_smoke_015 as base
import live_smoke_017 as join

from blender_research_mcp.client import BridgeClient
from blender_research_mcp.constants import MAX_DEADLINE_MS, PACKAGE_VERSION
from blender_research_mcp.lifecycle import ApplicationManager

ROOT = Path(__file__).resolve().parents[1]


def stage(value):
    print(f"[0.17.2 regression] {value}", flush=True)


async def fingerprint(client, name):
    mesh = await base.mesh(client, name)
    weight = await attributes.weights(client, name)
    uv = await attributes.uv(client, name, layer_name=None)
    return {
        "mesh_fingerprint": mesh["mesh_fingerprint"],
        "topology_fingerprint": mesh["topology_fingerprint"],
        "weights_fingerprint": weight["weights_fingerprint"],
        "group_schema_fingerprint": weight["group_schema_fingerprint"],
        "uv_fingerprint": uv["uv_fingerprint"],
        "counts": mesh["counts"],
    }


async def topo_case(client, disconnect=False):
    name = "Topology Grid"
    before = await fingerprint(client, name)
    tx = await base.begin(
        client,
        int((await base.mesh(client, name))["scene_generation"]),
        "0.17.2 rejected topology recovery",
    )
    steps = []
    for _iteration in range(2):
        mesh = await base.mesh(client, name)
        selected = await topology.selection_query(
            client, mesh, "EDGE", {"type": "topology", "kind": "BOUNDARY", "seed_indices": None}
        )
        edited = await base.mutate(
            client,
            "mesh.edit",
            topology.edit_params(
                tx["transaction_id"],
                mesh,
                {
                    "type": "subdivide",
                    "selection_id": selected["selection_id"],
                    "cuts": 1,
                    "use_grid_fill": False,
                },
            ),
            int(mesh["scene_generation"]),
        )
        mesh = await base.mesh(client, name)
        page = await client.call(
            "mesh.inspect",
            {
                "object_name": name,
                "component": "edges",
                "offset": 0,
                "limit": 256,
            },
            read_only=True,
        )
        internal = next(item["index"] for item in page["items"] if not item["is_boundary"])
        invalid = await topology.selection_query(
            client, mesh, "EDGE", {"type": "indices", "indices": [internal]}
        )
        after_write = await fingerprint(client, name)
        errors = []
        for operation in ("fill", "grid_fill"):
            error = await topology.expect_error(
                base.mutate(
                    client,
                    "mesh.edit",
                    topology.edit_params(
                        tx["transaction_id"],
                        mesh,
                        {
                            "type": operation,
                            "selection_id": invalid["selection_id"],
                        },
                    ),
                    int(mesh["scene_generation"]),
                ),
                "MESH_BOUNDARY_INVALID",
            )
            assert await fingerprint(client, name) == after_write
            errors.append(error)
        steps.append({"edit": edited, "errors": errors})
    if disconnect:
        await client.close()
        await asyncio.sleep(3)
        finish = await client.call("connection.ping", read_only=True)
    else:
        finish = await base.mutate(
            client,
            "transaction.rollback",
            {"transaction_id": tx["transaction_id"]},
            int(mesh["scene_generation"]),
        )
    after = await fingerprint(client, name)
    assert after == before, (before, after)
    return {
        "before": before,
        "steps": steps,
        "finish": finish,
        "after": after,
        "disconnect": disconnect,
    }


async def join_case(client, manager, finish):
    names = ["Join Detailed", "Join Slotless"]
    before = {name: await fingerprint(client, name) for name in names}
    sources = [await join.exact_source(client, name) for name in names]
    collection = await join.inspect_collection(client, "Regression Sources")
    output_name = f"Joined {finish}"
    attr, dep = join.policies(preserve_colors=True)
    params = {
        "sources": sources,
        "attributes": attr,
        "dependencies": dep,
        "output": join.output_spec(
            collection,
            object_name=output_name,
            mesh_name=output_name + " Mesh",
            frame={"type": "WORLD"},
        ),
    }
    preflight = await client.call(
        "mesh.join.preflight", params, read_only=True, deadline_ms=MAX_DEADLINE_MS
    )
    tx = await base.begin(
        client,
        int((await base.mesh(client, names[0]))["scene_generation"]),
        f"0.17.2 Join {finish}",
    )
    params["transaction_id"] = tx["transaction_id"]
    key = str(uuid4())

    async def request():
        return await client.call(
            "mesh.join",
            params,
            expected_scene_generation=int(tx["scene_generation"]),
            idempotency_key=key,
            read_only=False,
            deadline_ms=MAX_DEADLINE_MS,
        )

    result = await request()
    assert await request() == result
    output = await fingerprint(client, output_name)
    assert {name: await fingerprint(client, name) for name in names} == before
    evidence = {
        "preflight": preflight,
        "join": result,
        "idempotent": True,
        "output": output,
        "sources_before": before,
    }
    if finish == "disconnect":
        await client.close()
        await asyncio.sleep(3)
        evidence["finish"] = await client.call("connection.ping", read_only=True)
    elif finish == "native_save":
        evidence["finish"] = await client.call("_test.native_save", {}, read_only=False)
        evidence["accepted"] = await topology.expect_error(
            base.mutate(
                client,
                "transaction.rollback",
                {"transaction_id": tx["transaction_id"]},
                int(result["scene_generation"]),
            ),
            "TRANSACTION_ACCEPTED_BY_USER_SAVE",
        )
    else:
        evidence["finish"] = await base.mutate(
            client,
            f"transaction.{finish}",
            {"transaction_id": tx["transaction_id"]},
            int(result["scene_generation"]),
        )
    if finish in {"commit", "native_save"}:
        if finish == "commit":
            evidence["save"] = await manager.project_save()
        evidence["reload"] = await manager.project_reload(use_scripts=False, load_ui=False)
        reloaded = await fingerprint(client, output_name)
        # Session identities change on file load; topology, UV values and weights do not.
        for field in ("topology_fingerprint", "uv_fingerprint", "weights_fingerprint", "counts"):
            assert output[field] == reloaded[field], (field, output, reloaded)
        evidence["reloaded"] = reloaded
    else:
        scene = await client.call(
            "scene.inspect",
            {
                "kinds": ["objects"],
                "name_filter": output_name,
                "limit": 256,
            },
            read_only=True,
        )
        assert not scene["objects"]
        assert {name: await fingerprint(client, name) for name in names} == before
    return evidence


async def run(args):
    run_id = time.strftime("%Y%m%dT%H%M%S") + "-" + uuid4().hex[:8]
    directory = ROOT / "artifacts" / "live-smoke" / run_id
    directory.mkdir(parents=True)
    temporary = Path(tempfile.mkdtemp(prefix="blender-0172-"))
    fixture = temporary / "source.blend"
    stage("build fixture and real-RNA regression matrix")
    with (directory / "fixture.log").open("wb") as log:
        process = subprocess.run(
            [
                str(args.blender_executable),
                "--background",
                "--factory-startup",
                "--python-exit-code",
                "1",
                "--python",
                str(ROOT / "scripts/blender_regression_0172.py"),
                "--",
                "--output",
                str(fixture),
                "--report",
                str(directory / "rna.json"),
            ],
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            check=False,
            timeout=120,
        )
    if process.returncode:
        raise RuntimeError(f"RNA regressions failed: {directory / 'fixture.log'}")
    source_hash = base.sha256(fixture)
    project = temporary / "working.blend"
    shutil.copy2(fixture, project)
    previous = os.environ.get("BLENDER_RESEARCH_MCP_TEST_HOOKS")
    os.environ["BLENDER_RESEARCH_MCP_TEST_HOOKS"] = "1"
    client = BridgeClient(port=args.port)
    manager = ApplicationManager(
        client, blender_executable=str(args.blender_executable), launch_timeout=90
    )
    launched = False
    report = {
        "run_id": run_id,
        "server_version": PACKAGE_VERSION,
        "port": args.port,
        "source_file": str(fixture),
        "source_sha256_before": source_hash,
    }
    try:
        assert not (await manager.status()).get("running"), "Isolated test port is busy"
        report["launch"] = await manager.launch()
        launched = True
        await manager.project_open(
            str(project), save_current=False, use_scripts=False, load_ui=False
        )
        report["ping_before"] = await client.call("connection.ping", read_only=True)
        assert report["ping_before"]["addon_version"] == PACKAGE_VERSION
        report["context_before"] = await client.call("context.get", read_only=True)
        stage("reject -> continue -> rollback / disconnect")
        report["topology"] = [await topo_case(client), await topo_case(client, disconnect=True)]
        stage("multi-UV/761-Group Join rollback / disconnect / commit / native save")
        report["join"] = {}
        for finish in ("rollback", "disconnect", "commit", "native_save"):
            stage("Join " + finish)
            report["join"][finish] = await join_case(client, manager, finish)
        report["context_after"] = await client.call("context.get", read_only=True)
        assert collaboration.ui_projection(report["context_before"]) == collaboration.ui_projection(
            report["context_after"]
        )
        report["ping_after"] = await client.call("connection.ping", read_only=True)
        assert report["ping_after"]["heartbeat"] > report["ping_before"]["heartbeat"]
        report["source_sha256_after"] = base.sha256(fixture)
        assert source_hash == report["source_sha256_after"]
        report["status"] = "passed"
    finally:
        if launched:
            with contextlib.suppress(Exception):
                await manager.quit(save_current=False)
        await manager.close()
        if previous is None:
            os.environ.pop("BLENDER_RESEARCH_MCP_TEST_HOOKS", None)
        else:
            os.environ["BLENDER_RESEARCH_MCP_TEST_HOOKS"] = previous
        (directory / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    stage(f"passed: {directory / 'report.json'}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--blender-executable", type=Path, required=True)
    parser.add_argument("--port", type=int, default=9917)
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
