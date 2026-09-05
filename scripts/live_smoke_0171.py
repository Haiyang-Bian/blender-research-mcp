"""Validate transaction-owned Collection rollback and bounded UV inspection."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import shutil
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import live_smoke_015 as base

from blender_research_mcp.client import BridgeClient
from blender_research_mcp.constants import MAX_DEADLINE_MS, PACKAGE_VERSION
from blender_research_mcp.errors import BridgeError
from blender_research_mcp.lifecycle import ApplicationManager

ROOT = Path(__file__).resolve().parents[1]


def stage(name: str) -> None:
    print(f"[0.17.1 smoke] {name}", flush=True)


async def scene(client: BridgeClient) -> dict[str, Any]:
    return await client.call(
        "scene.inspect",
        {"kinds": ["objects", "collections"], "name_filter": None, "limit": 256},
        deadline_ms=MAX_DEADLINE_MS,
        read_only=True,
    )


async def inspect_collection(client: BridgeClient, name: str) -> dict[str, Any]:
    return await client.call(
        "collection.inspect",
        {"collection_name": name, "offset": 0, "limit": 256},
        deadline_ms=MAX_DEADLINE_MS,
        read_only=True,
    )


def root_parent(value: dict[str, Any]) -> dict[str, Any]:
    root = value["scene_root"]
    return {
        "type": "SCENE_ROOT",
        "scene_name": root["scene_name"],
        "expected_scene_identity": root["scene_identity"],
        "expected_scene_structure_fingerprint": root["scene_structure_fingerprint"],
    }


def collection_parent(value: dict[str, Any]) -> dict[str, Any]:
    collection = value.get("collection", value)
    return {
        "type": "COLLECTION",
        "collection_name": collection["name"],
        "expected_collection_identity": collection["session_identity"],
        "expected_collection_structure_fingerprint": collection["structure_fingerprint"],
    }


async def create_collection(
    client: BridgeClient,
    transaction_id: str,
    generation: int,
    name: str,
    parent: dict[str, Any],
) -> dict[str, Any]:
    return await base.mutate(
        client,
        "collection.create",
        {"transaction_id": transaction_id, "name": name, "parent": parent},
        generation,
    )


async def materialize_into(
    client: BridgeClient,
    transaction_id: str,
    generation: int,
    source: dict[str, Any],
    collection: dict[str, Any],
    output_name: str,
) -> dict[str, Any]:
    shape_fingerprint = source.get("shape_key_state_fingerprint")
    evaluation = (
        {
            "type": "SHAPE_KEYS_CURRENT",
            "expected_shape_key_state_fingerprint": shape_fingerprint,
        }
        if isinstance(shape_fingerprint, str) and shape_fingerprint
        else {"type": "BASE"}
    )
    return await base.mutate(
        client,
        "mesh.materialize",
        {
            "transaction_id": transaction_id,
            "source": base.materialize_source(source),
            "evaluation": evaluation,
            "new_object_name": output_name,
            "copy": {"materials": True, "uv": False, "weights": True},
            "collection_name": collection["name"],
            "expected_collection_identity": collection["session_identity"],
        },
        generation,
    )


async def uv_inspection(client: BridgeClient, object_name: str) -> dict[str, Any]:
    results: dict[str, Any] = {}
    for component in ("SUMMARY", "LOOPS"):
        started = time.perf_counter()
        value = await client.call(
            "mesh.uv.inspect",
            {
                "object_name": object_name,
                "layer_name": None,
                "component": component,
                "offset": 0,
                "limit": 32,
            },
            deadline_ms=MAX_DEADLINE_MS,
            read_only=True,
        )
        results[component.lower()] = {
            "elapsed_ms": round((time.perf_counter() - started) * 1000, 3),
            "component": value["component"],
            "pagination": value["pagination"],
            "counts": value["counts"],
            "warnings": value["warnings"],
            "mesh_fingerprint": value["mesh_fingerprint"],
            "uv_fingerprint": value["uv_fingerprint"],
        }
    return results


async def rollback_case(
    client: BridgeClient,
    *,
    source_name: str,
    prefix: str,
    inspect_large_uv: bool,
) -> dict[str, Any]:
    before = await base.mesh(client, source_name)
    transaction = await base.begin(
        client, int(before["scene_generation"]), f"{prefix} collection rollback"
    )
    root = await create_collection(
        client,
        transaction["transaction_id"],
        int(transaction["scene_generation"]),
        f"{prefix} Root",
        root_parent(await scene(client)),
    )
    body = await create_collection(
        client,
        transaction["transaction_id"],
        int(root["scene_generation"]),
        f"{prefix} Body",
        collection_parent(root),
    )
    current_root = await inspect_collection(client, root["collection"]["name"])
    hair = await create_collection(
        client,
        transaction["transaction_id"],
        int(body["scene_generation"]),
        f"{prefix} Hair",
        collection_parent(current_root),
    )
    materialized = await materialize_into(
        client,
        transaction["transaction_id"],
        int(hair["scene_generation"]),
        before,
        body["collection"],
        f"{prefix} Body Object",
    )
    uv = await uv_inspection(client, source_name) if inspect_large_uv else None
    rollback = await base.mutate(
        client,
        "transaction.rollback",
        {"transaction_id": transaction["transaction_id"]},
        int(materialized["scene_generation"]),
    )
    after_scene = await scene(client)
    remaining = {
        item["name"]
        for key in ("objects", "collections")
        for item in after_scene.get(key, [])
        if item["name"].startswith(prefix)
    }
    if remaining:
        raise RuntimeError(f"Rollback left transaction-owned resources: {sorted(remaining)}")
    after = await base.mesh(client, source_name)
    if before["mesh_fingerprint"] != after["mesh_fingerprint"]:
        raise RuntimeError("Rollback changed the source Mesh fingerprint")
    return {
        "transaction": transaction,
        "collections": [root, body, hair],
        "materialize": materialized,
        "uv": uv,
        "rollback": rollback,
        "source_fingerprint_before": before["mesh_fingerprint"],
        "source_fingerprint_after": after["mesh_fingerprint"],
    }


async def disconnect_case(client: BridgeClient) -> dict[str, Any]:
    before = await base.mesh(client, "Modular Source")
    transaction = await base.begin(
        client, int(before["scene_generation"]), "0.17.1 disconnect rollback"
    )
    owned = await create_collection(
        client,
        transaction["transaction_id"],
        int(transaction["scene_generation"]),
        "Disconnect Owned",
        root_parent(await scene(client)),
    )
    materialized = await materialize_into(
        client,
        transaction["transaction_id"],
        int(owned["scene_generation"]),
        before,
        owned["collection"],
        "Disconnect Output",
    )
    await client.close()
    await asyncio.sleep(3.0)
    ping = await client.call("connection.ping", read_only=True)
    after_scene = await scene(client)
    names = {
        item["name"]
        for key in ("objects", "collections")
        for item in after_scene.get(key, [])
    }
    if {"Disconnect Owned", "Disconnect Output"}.intersection(names):
        raise RuntimeError("Disconnect rollback left transaction-owned resources")
    return {"write": materialized, "ping": ping, "status": "rolled_back_disconnect"}


async def run(args: argparse.Namespace) -> dict[str, Any]:
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + f"-{uuid4().hex[:8]}"
    temporary = Path(tempfile.gettempdir()) / "blender-research-mcp-0171" / run_id
    temporary.mkdir(parents=True, exist_ok=False)
    artifacts = ROOT / "artifacts" / "live-smoke" / run_id
    artifacts.mkdir(parents=True, exist_ok=False)
    fixture_source = temporary / "fixture-source.blend"
    fixture_project = temporary / "fixture-project.blend"
    base.build_fixture(args.blender_executable, fixture_source)
    fixture_hash = base.sha256(fixture_source)
    shutil.copy2(fixture_source, fixture_project)
    report: dict[str, Any] = {
        "run_id": run_id,
        "started_at": datetime.now(UTC).isoformat(),
        "server_version": PACKAGE_VERSION,
        "port": args.port,
        "fixture_source": str(fixture_source),
        "fixture_source_sha256_before": fixture_hash,
        "character_source": str(args.character_project) if args.character_project else None,
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
            raise RuntimeError(f"Smoke port is already in use: {args.port}")
        stage("managed launch and fixture rollback")
        report["launch"] = await manager.launch()
        launched = True
        report["fixture_open"] = await manager.project_open(
            str(fixture_project), save_current=False, use_scripts=False, load_ui=False
        )
        report["ping"] = await client.call("connection.ping", read_only=True)
        if report["ping"]["addon_version"] != PACKAGE_VERSION:
            raise RuntimeError("Managed Blender did not load the current add-on version")
        report["fixture_rollback"] = await rollback_case(
            client,
            source_name="Modular Source",
            prefix="Fixture Owned",
            inspect_large_uv=False,
        )
        stage("disconnect auto-rollback")
        report["disconnect_rollback"] = await disconnect_case(client)

        if args.character_project is not None:
            character_source = args.character_project.resolve(strict=True)
            character_hash = base.sha256(character_source)
            character_copy = temporary / "test-model.blend"
            shutil.copy2(character_source, character_copy)
            stage("large character UV inspection and manual rollback")
            report["character_open"] = await manager.project_open(
                str(character_copy), save_current=False, use_scripts=False, load_ui=False
            )
            report["character_rollback"] = await rollback_case(
                client,
                source_name="绯雪_edit_mesh",
                prefix="Character Owned",
                inspect_large_uv=True,
            )
            report["character_source_sha256_before"] = character_hash
            report["character_source_sha256_after"] = base.sha256(character_source)
            if report["character_source_sha256_after"] != character_hash:
                raise RuntimeError("Source test-model.blend changed during the smoke")

        report["fixture_source_sha256_after"] = base.sha256(fixture_source)
        if report["fixture_source_sha256_after"] != fixture_hash:
            raise RuntimeError("Fixture source changed during the smoke")
        report["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 3)
        report["finished_at"] = datetime.now(UTC).isoformat()
        report_path = artifacts / "report-0.17.1.json"
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        stage(f"passed: {report_path}")
        return {**report, "report_path": str(report_path)}
    finally:
        if launched:
            with contextlib.suppress(Exception):
                await manager.quit(save_current=False)
        await manager.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--blender-executable", type=Path, required=True)
    parser.add_argument("--port", type=int, default=9899)
    parser.add_argument("--timeout", type=float, default=90.0)
    parser.add_argument("--character-project", type=Path)
    return parser.parse_args()


def main() -> int:
    try:
        report = asyncio.run(run(parse_args()))
    except BridgeError as exc:
        print(json.dumps(exc.error.model_dump(mode="json"), ensure_ascii=False), flush=True)
        raise
    print(json.dumps({"run_id": report["run_id"], "report_path": report["report_path"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
