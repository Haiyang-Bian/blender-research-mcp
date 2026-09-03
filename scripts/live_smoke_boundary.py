"""Boundary regression over an isolated, visible managed Blender and real socket."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from uuid import uuid4

import live_smoke_0111 as collaboration
import live_smoke_013 as topology
import live_smoke_015 as base

from blender_research_mcp.client import BridgeClient
from blender_research_mcp.constants import PACKAGE_VERSION
from blender_research_mcp.lifecycle import ApplicationManager

ROOT = Path(__file__).resolve().parents[1]


async def run(args):
    directory = ROOT / "artifacts/live-smoke" / ("boundary-" + time.strftime("%Y%m%dT%H%M%S"))
    directory.mkdir(parents=True)
    temporary = Path(tempfile.mkdtemp(prefix="blender-boundary-"))
    fixture = temporary / "fixture.blend"
    with (directory / "fixture.log").open("wb") as log:
        result = subprocess.run(
            [
                str(args.blender_executable),
                "--background",
                "--factory-startup",
                "--python-exit-code",
                "1",
                "--python",
                str(ROOT / "scripts/blender_boundary_regression.py"),
                "--",
                "--report",
                str(directory / "rna.json"),
                "--save",
                str(fixture),
            ],
            stdout=log,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            timeout=120,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    if result.returncode:
        raise RuntimeError(f"Native fixture failed: {directory / 'fixture.log'}")
    working = temporary / "working.blend"
    shutil.copy2(fixture, working)
    client = BridgeClient(port=args.port)
    manager = ApplicationManager(
        client, blender_executable=str(args.blender_executable), launch_timeout=90
    )
    report = {
        "version": PACKAGE_VERSION,
        "source_sha_before": base.sha256(fixture),
        "status": "running",
    }
    launched = False
    try:
        await manager.launch()
        launched = True
        await manager.project_open(
            str(working), save_current=False, use_scripts=False, load_ui=False
        )
        report["ping_before"] = await client.call("connection.ping", read_only=True)
        assert report["ping_before"]["addon_version"] == PACKAGE_VERSION
        report["context_before"] = await client.call("context.get", read_only=True)
        report["cycles"] = []
        for disconnect in (False, True):
            before = await base.mesh(client, "Boundary Live")
            selected = await topology.selection_query(
                client,
                before,
                "EDGE",
                {
                    "type": "indices",
                    "indices": [*range(4), *range(8, 12)],
                },
            )
            inspected = await client.call(
                "mesh.boundary.inspect",
                {
                    "selection_id": selected["selection_id"],
                },
                read_only=True,
            )
            assert inspected["status"] == "READY"
            tx = await base.begin(client, before["scene_generation"], "Boundary live regression")
            params = topology.edit_params(
                tx["transaction_id"],
                before,
                {
                    "type": "grid_fill",
                    "selection_id": selected["selection_id"],
                },
            )
            generation = int(
                (await client.call("connection.ping", read_only=True))["scene_generation"]
            )
            key = str(uuid4())
            edited = await client.call(
                "mesh.edit",
                params,
                expected_scene_generation=generation,
                idempotency_key=key,
                read_only=False,
                deadline_ms=30_000,
            )
            replay = await client.call(
                "mesh.edit",
                params,
                expected_scene_generation=generation,
                idempotency_key=key,
                read_only=False,
                deadline_ms=30_000,
            )
            assert edited == replay
            if disconnect:
                await client.close()
                await asyncio.sleep(3)
                finish = await client.call("connection.ping", read_only=True)
            else:
                finish = await base.mutate(
                    client,
                    "transaction.rollback",
                    {
                        "transaction_id": tx["transaction_id"],
                    },
                    int((await client.call("connection.ping", read_only=True))["scene_generation"]),
                )
            after = await base.mesh(client, "Boundary Live")
            assert after["mesh_fingerprint"] == before["mesh_fingerprint"]
            report["cycles"].append(
                {
                    "disconnect": disconnect,
                    "inspection": inspected,
                    "edit": edited,
                    "finish": finish,
                }
            )
        report["context_after"] = await client.call("context.get", read_only=True)
        assert collaboration.ui_projection(report["context_before"]) == collaboration.ui_projection(
            report["context_after"]
        )
        report["ping_after"] = await client.call("connection.ping", read_only=True)
        assert report["ping_after"]["heartbeat"] > report["ping_before"]["heartbeat"]
        report["source_sha_after"] = base.sha256(fixture)
        assert report["source_sha_before"] == report["source_sha_after"]
        report["status"] = "passed"
    finally:
        if launched:
            with contextlib.suppress(Exception):
                await manager.quit(save_current=False)
        await manager.close()
        (directory / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(str(directory / "report.json"), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--blender-executable", type=Path, required=True)
    parser.add_argument("--port", type=int, default=9918)
    asyncio.run(run(parser.parse_args()))
