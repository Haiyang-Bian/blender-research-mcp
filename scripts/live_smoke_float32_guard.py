"""Validate Blender float32 transaction guards against a real managed session."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import struct
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from blender_research_mcp.client import BridgeClient
from blender_research_mcp.constants import PACKAGE_VERSION
from blender_research_mcp.lifecycle import ApplicationManager

ROOT = Path(__file__).resolve().parents[1]


def float32_equal(left: float, right: float) -> bool:
    return struct.pack("<f", left) == struct.pack("<f", right)


def context_identity(context: dict[str, Any]) -> dict[str, Any]:
    return {
        key: context[key]
        for key in (
            "scene",
            "view_layer",
            "workspace",
            "mode",
            "active_object",
            "selected_objects",
            "frame_current",
        )
    }


async def mutate(
    client: BridgeClient,
    command: str,
    params: dict[str, Any],
    generation: int,
) -> dict[str, Any]:
    return await client.call(
        command,
        params,
        expected_scene_generation=generation,
        idempotency_key=str(uuid4()),
        read_only=False,
    )


async def run(args: argparse.Namespace) -> dict[str, Any]:
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + f"-{uuid4().hex[:8]}"
    artifact_directory = ROOT / "artifacts" / "live-smoke" / run_id
    artifact_directory.mkdir(parents=True, exist_ok=False)
    report: dict[str, Any] = {
        "run_id": run_id,
        "started_at": datetime.now(UTC).isoformat(),
        "artifact_directory": str(artifact_directory),
        "port": args.port,
        "server_version": PACKAGE_VERSION,
    }
    client = BridgeClient(port=args.port)
    manager = ApplicationManager(
        client,
        blender_executable=str(args.blender_executable),
        launch_timeout=args.timeout,
    )
    launched = False
    started = time.perf_counter()
    try:
        if (await manager.status()).get("running"):
            raise RuntimeError(f"smoke port is already in use: {args.port}")
        launch = await manager.launch()
        launched = True
        report["launch"] = launch
        if launch["application"]["addon_version"] != PACKAGE_VERSION:
            raise RuntimeError("managed add-on version does not match the server")
        ping_before = await client.call("connection.ping", read_only=True)
        context_before = await client.call("context.get", read_only=True)
        camera_before = await client.call(
            "object.inspect", {"object_name": "Camera"}, read_only=True
        )
        light_before = await client.call(
            "object.inspect", {"object_name": "Light"}, read_only=True
        )
        transaction = await mutate(
            client,
            "transaction.begin",
            {"label": "smoke:float32-guard", "viewport_id": None},
            int(ping_before["scene_generation"]),
        )
        generation = int(transaction["scene_generation"])
        camera_write = await mutate(
            client,
            "object.set",
            {
                "transaction_id": transaction["transaction_id"],
                "object_name": "Camera",
                "expected_object_identity": camera_before["session_identity"],
                "patches": [
                    {
                        "type": "transform",
                        "location": {"z": 6.2},
                    }
                ],
            },
            generation,
        )
        generation = int(camera_write["scene_generation"])
        light_write = await mutate(
            client,
            "object.set",
            {
                "transaction_id": transaction["transaction_id"],
                "object_name": "Light",
                "expected_object_identity": light_before["session_identity"],
                "patches": [
                    {
                        "type": "light",
                        "expected_data_identity": light_before["data"]["session_identity"],
                        "expected_data_users": light_before["data"]["users"],
                        "expected_light_type": light_before["data"]["settings"]["light_type"],
                        "allow_shared_data": False,
                        "energy": 900.0,
                    }
                ],
            },
            generation,
        )
        generation = int(light_write["scene_generation"])
        rollback = await mutate(
            client,
            "transaction.rollback",
            {"transaction_id": transaction["transaction_id"]},
            generation,
        )
        camera_after = await client.call(
            "object.inspect", {"object_name": "Camera"}, read_only=True
        )
        light_after = await client.call(
            "object.inspect", {"object_name": "Light"}, read_only=True
        )
        context_after = await client.call("context.get", read_only=True)
        ping_after = await client.call("connection.ping", read_only=True)
        if not float32_equal(camera_before["location"][2], camera_after["location"][2]):
            raise RuntimeError("Camera location did not restore")
        if not float32_equal(
            light_before["data"]["settings"]["energy"],
            light_after["data"]["settings"]["energy"],
        ):
            raise RuntimeError("Light energy did not restore")
        if context_identity(context_before) != context_identity(context_after):
            raise RuntimeError("User context changed during the float32 guard smoke")
        if int(ping_after["heartbeat"]) <= int(ping_before["heartbeat"]):
            raise RuntimeError("Blender heartbeat did not advance")
        report.update(
            {
                "ping_before": ping_before,
                "ping_after": ping_after,
                "context_before": context_before,
                "context_after": context_after,
                "camera_write": camera_write,
                "camera_blender_readback_z": camera_write["object"]["location"][2],
                "light_write": light_write,
                "rollback": rollback,
                "restored": True,
                "status": "passed",
                "completed_at": datetime.now(UTC).isoformat(),
                "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
            }
        )
        return report
    finally:
        if launched:
            with contextlib.suppress(Exception):
                await manager.quit(save_current=False)
        await manager.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--blender-executable", type=Path, required=True)
    parser.add_argument("--port", type=int, default=9885)
    parser.add_argument("--timeout", type=float, default=90.0)
    args = parser.parse_args()
    report = asyncio.run(run(args))
    report_path = Path(report["artifact_directory"]) / f"report-{PACKAGE_VERSION}.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(report_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
