"""Validate linked-data transaction guards against a real Blender 4.2 session."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import hashlib
import json
import os
import shutil
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from blender_research_mcp.client import BridgeClient
from blender_research_mcp.constants import PACKAGE_VERSION
from blender_research_mcp.errors import BridgeError
from blender_research_mcp.lifecycle import ApplicationManager

ROOT = Path(__file__).resolve().parents[1]


def stage(message: str) -> None:
    print(f"[linked-data smoke] {message}", flush=True)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def transform() -> dict[str, dict[str, float]]:
    return {
        "location": {"x": 0.0, "y": 0.0, "z": 0.0},
        "rotation_euler_degrees": {"x": 0.0, "y": 0.0, "z": 0.0},
        "scale": {"x": 1.0, "y": 1.0, "z": 1.0},
    }


def cube_definition(name: str) -> dict[str, Any]:
    return {
        "type": "cube",
        "name": name,
        "collection_name": None,
        "expected_collection_identity": None,
        "transform": transform(),
        "size": 1.0,
    }


def material_definition(name: str) -> dict[str, Any]:
    return {
        "name": name,
        "base_color": {"type": "hex_srgb", "value": "#7096C4"},
        "metallic": 0.1,
        "roughness": 0.42,
        "ior": 1.5,
        "transmission": 0.0,
        "emission_color": {"type": "hex_srgb", "value": "#000000"},
        "emission_strength": 0.0,
        "alpha": 1.0,
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


async def inspect(client: BridgeClient, name: str) -> dict[str, Any]:
    return await client.call("object.inspect", {"object_name": name}, read_only=True)


async def begin(client: BridgeClient, generation: int, label: str) -> dict[str, Any]:
    return await mutate(
        client,
        "transaction.begin",
        {"label": label, "viewport_id": None},
        generation,
    )


async def create_material_and_assign(
    client: BridgeClient,
    transaction: dict[str, Any],
    source: dict[str, Any],
    name: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    created = await mutate(
        client,
        "material.create",
        {
            "transaction_id": transaction["transaction_id"],
            "definition": material_definition(name),
        },
        int(transaction["scene_generation"]),
    )
    material = created["material"]
    assigned = await mutate(
        client,
        "material.assign",
        {
            "transaction_id": transaction["transaction_id"],
            "object_name": source["name"],
            "expected_object_identity": source["session_identity"],
            "expected_data_identity": source["data"]["session_identity"],
            "expected_data_users": source["data"]["users"],
            "allow_shared_data": False,
            "mode": "append",
            "slot_index": None,
            "material_name": material["name"],
            "expected_material_identity": material["session_identity"],
            "expected_material_users": material["users"],
            "expected_slot_material_identity": None,
        },
        int(created["scene_generation"]),
    )
    return created, assigned


async def duplicate(
    client: BridgeClient,
    transaction_id: str,
    source: dict[str, Any],
    name: str,
    generation: int,
    *,
    linked: bool,
) -> dict[str, Any]:
    return await mutate(
        client,
        "object.duplicate",
        {
            "transaction_id": transaction_id,
            "source_name": source["name"],
            "expected_source_identity": source["session_identity"],
            "name": name,
            "linked_data": linked,
            "collection_name": None,
            "expected_collection_identity": None,
            "transform": None,
        },
        generation,
    )


async def rollback(
    client: BridgeClient,
    transaction_id: str,
    generation: int,
) -> dict[str, Any]:
    return await mutate(
        client,
        "transaction.rollback",
        {"transaction_id": transaction_id},
        generation,
    )


async def require_absent(client: BridgeClient, names: list[str]) -> None:
    scene = await client.call(
        "scene.inspect",
        {"kinds": ["objects"], "name_filter": None, "limit": 256},
        read_only=True,
    )
    present = {item["name"] for item in scene["objects"]}
    unexpected = sorted(set(names) & present)
    if unexpected:
        raise RuntimeError(f"rolled-back objects still exist: {unexpected}")


def context_identity(value: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value.get(key)
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


async def check_guarded_rollback(client: BridgeClient) -> dict[str, Any]:
    source = await inspect(client, "Cube")
    baseline_users = int(source["data"]["users"])
    transaction = await begin(client, int(source["scene_generation"]), "linked rollback")
    _material, assigned = await create_material_and_assign(
        client, transaction, source, "Linked Rollback Material"
    )
    first = await duplicate(
        client,
        transaction["transaction_id"],
        source,
        "Linked Rollback 1",
        int(assigned["scene_generation"]),
        linked=True,
    )
    second = await duplicate(
        client,
        transaction["transaction_id"],
        source,
        "Linked Rollback 2",
        int(first["scene_generation"]),
        linked=True,
    )
    during = await inspect(client, "Cube")
    if int(during["data"]["users"]) != baseline_users + 2:
        raise RuntimeError("linked duplicates did not advance Mesh users")
    result = await rollback(
        client, transaction["transaction_id"], int(second["scene_generation"])
    )
    after = await inspect(client, "Cube")
    await require_absent(client, ["Linked Rollback 1", "Linked Rollback 2"])
    if after["data"] != source["data"]:
        raise RuntimeError("guarded rollback did not restore exact Mesh identity/users")
    return {"before": source, "during": during, "after": after, "rollback": result}


async def check_unguarded_and_independent(client: BridgeClient) -> dict[str, Any]:
    source = await inspect(client, "Cube")
    transaction = await begin(client, int(source["scene_generation"]), "three linked")
    generation = int(transaction["scene_generation"])
    names = ["No Guard 1", "No Guard 2", "No Guard 3"]
    for name in names:
        created = await duplicate(
            client,
            transaction["transaction_id"],
            source,
            name,
            generation,
            linked=True,
        )
        generation = int(created["scene_generation"])
    during = await inspect(client, "Cube")
    linked_rollback = await rollback(client, transaction["transaction_id"], generation)
    await require_absent(client, names)
    linked_after = await inspect(client, "Cube")
    if linked_after["data"] != source["data"]:
        raise RuntimeError("three linked duplicates did not restore source data")

    independent_transaction = await begin(
        client, int(linked_after["scene_generation"]), "independent duplicate"
    )
    independent = await duplicate(
        client,
        independent_transaction["transaction_id"],
        linked_after,
        "Independent Duplicate",
        int(independent_transaction["scene_generation"]),
        linked=False,
    )
    if independent["object"]["data"]["session_identity"] == source["data"][
        "session_identity"
    ]:
        raise RuntimeError("independent duplicate unexpectedly shared Mesh data")
    independent_rollback = await rollback(
        client,
        independent_transaction["transaction_id"],
        int(independent["scene_generation"]),
    )
    await require_absent(client, ["Independent Duplicate"])
    return {
        "linked_during": during,
        "linked_rollback": linked_rollback,
        "linked_after": linked_after,
        "independent": independent,
        "independent_rollback": independent_rollback,
    }


async def check_lifecycle_commit(
    client: BridgeClient,
    manager: ApplicationManager,
) -> dict[str, Any]:
    source = await inspect(client, "Cube")
    transaction = await begin(client, int(source["scene_generation"]), "lifecycle commit")
    _material, assigned = await create_material_and_assign(
        client, transaction, source, "Linked Commit Material"
    )
    first = await duplicate(
        client,
        transaction["transaction_id"],
        source,
        "Linked Commit 1",
        int(assigned["scene_generation"]),
        linked=True,
    )
    second = await duplicate(
        client,
        transaction["transaction_id"],
        source,
        "Linked Commit 2",
        int(first["scene_generation"]),
        linked=True,
    )
    saved = await manager.project_save()
    if saved.get("transaction", {}).get("status") != "committed":
        raise RuntimeError("project.save did not commit the valid linked transaction")
    committed_names = ("Cube", "Linked Commit 1", "Linked Commit 2")
    before_reload = [await inspect(client, name) for name in committed_names]
    identities = {item["data"]["session_identity"] for item in before_reload}
    if len(identities) != 1 or {item["data"]["users"] for item in before_reload} != {3}:
        raise RuntimeError("committed linked duplicates do not share one three-user Mesh")
    reloaded = await manager.project_reload(save_current=False, use_scripts=False, load_ui=False)
    after_reload = [await inspect(client, name) for name in committed_names]
    if len({item["data"]["session_identity"] for item in after_reload}) != 1:
        raise RuntimeError("linked Mesh identity was not persistent after reload")
    if {item["data"]["users"] for item in after_reload} != {3}:
        raise RuntimeError("linked Mesh users were not persistent after reload")
    return {
        "second_duplicate": second,
        "save": saved,
        "before_reload": before_reload,
        "reload": reloaded,
        "after_reload": after_reload,
    }


async def check_external_conflict(client: BridgeClient) -> dict[str, Any]:
    scene = await client.call(
        "scene.inspect",
        {"kinds": ["objects"], "name_filter": None, "limit": 256},
        read_only=True,
    )
    transaction = await begin(client, int(scene["scene_generation"]), "external users conflict")
    created = await mutate(
        client,
        "object.create",
        {
            "transaction_id": transaction["transaction_id"],
            "definition": cube_definition("Conflict Source"),
        },
        int(transaction["scene_generation"]),
    )
    source = created["object"]
    _material, assigned = await create_material_and_assign(
        client, created, source, "Conflict Material"
    )
    first = await duplicate(
        client,
        transaction["transaction_id"],
        source,
        "Conflict Linked 1",
        int(assigned["scene_generation"]),
        linked=True,
    )
    second = await duplicate(
        client,
        transaction["transaction_id"],
        source,
        "Conflict Linked 2",
        int(first["scene_generation"]),
        linked=True,
    )
    touched = await client.call(
        "_test.structure.touch",
        {
            "object_name": "Conflict Source",
            "action": "linked_duplicate",
            "name": "External Linked User",
        },
        read_only=False,
    )
    failures: dict[str, Any] = {}
    for command in ("transaction.commit", "transaction.rollback"):
        try:
            await mutate(
                client,
                command,
                {"transaction_id": transaction["transaction_id"]},
                int(second["scene_generation"]),
            )
        except BridgeError as exc:
            if exc.error.code != "STRUCTURE_CONFLICT":
                raise
            failures[command] = exc.error.model_dump(mode="json")
        else:
            raise RuntimeError(f"{command} accepted a genuine external data user")
    return {"touch": touched, "failures": failures}


async def run(args: argparse.Namespace) -> dict[str, Any]:
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + f"-{uuid4().hex[:8]}"
    temporary_root = Path(tempfile.gettempdir()) / "blender-research-mcp-linked-data" / run_id
    temporary_root.mkdir(parents=True, exist_ok=False)
    artifact_directory = ROOT / "artifacts" / "live-smoke" / run_id
    artifact_directory.mkdir(parents=True, exist_ok=False)
    source = temporary_root / "linked-data-source.blend"
    project = temporary_root / "linked-data-project.blend"
    report: dict[str, Any] = {
        "run_id": run_id,
        "started_at": datetime.now(UTC).isoformat(),
        "temporary_root": str(temporary_root),
        "artifact_directory": str(artifact_directory),
        "source": str(source),
        "project": str(project),
        "port": args.port,
        "server_version": PACKAGE_VERSION,
    }
    previous_hooks = os.environ.get("BLENDER_RESEARCH_MCP_TEST_HOOKS")
    os.environ["BLENDER_RESEARCH_MCP_TEST_HOOKS"] = "1"
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
        stage("launch")
        launch = await manager.launch()
        launched = True
        report["launch"] = launch
        application = launch["application"]
        if application["addon_version"] != PACKAGE_VERSION:
            raise RuntimeError("managed add-on version does not match the server")
        ping_before = await client.call("connection.ping", read_only=True)
        if int(ping_before["capability_versions"].get("transactions", 0)) < 3:
            raise RuntimeError("structural transactions v3 are unavailable")
        report["ping_before"] = ping_before

        stage("save baseline and open working copy")
        report["baseline_save"] = await manager.project_save(str(source))
        source_hash_before = sha256(source)
        shutil.copy2(source, project)
        report["source_sha256_before"] = source_hash_before
        report["project_open"] = await manager.project_open(
            str(project), save_current=False, use_scripts=False, load_ui=False
        )
        context_before = await client.call("context.get", read_only=True)
        report["context_before"] = context_before

        stage("guarded linked rollback")
        report["guarded_rollback"] = await check_guarded_rollback(client)
        stage("unguarded linked and independent rollback")
        report["duplicate_modes"] = await check_unguarded_and_independent(client)
        stage("lifecycle save commit and reload")
        report["lifecycle_commit"] = await check_lifecycle_commit(client, manager)

        context_after_success = await client.call("context.get", read_only=True)
        if context_identity(context_before) != context_identity(context_after_success):
            raise RuntimeError(
                "successful linked-data workflows changed user context: "
                + json.dumps(
                    {
                        "before": context_identity(context_before),
                        "after": context_identity(context_after_success),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
            )
        report["context_after_success"] = context_after_success

        stage("genuine external user-count conflict")
        report["external_conflict"] = await check_external_conflict(client)
        context_after_conflict = await client.call("context.get", read_only=True)
        if context_identity(context_before) != context_identity(context_after_conflict):
            raise RuntimeError("external conflict probe changed user context")
        ping_after = await client.call("connection.ping", read_only=True)
        if int(ping_after["heartbeat"]) <= int(ping_before["heartbeat"]):
            raise RuntimeError("Blender UI heartbeat did not advance")
        report["context_after_conflict"] = context_after_conflict
        report["ping_after"] = ping_after
        report["source_sha256_after"] = sha256(source)
        report["source_unchanged"] = report["source_sha256_after"] == source_hash_before
        if not report["source_unchanged"]:
            raise RuntimeError("source fixture changed")
        report["project_sha256"] = sha256(project)
        report["status"] = "passed"
        report["completed_at"] = datetime.now(UTC).isoformat()
        report["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 3)
        return report
    finally:
        if launched:
            with contextlib.suppress(Exception):
                await manager.quit(save_current=False)
        await manager.close()
        if previous_hooks is None:
            os.environ.pop("BLENDER_RESEARCH_MCP_TEST_HOOKS", None)
        else:
            os.environ["BLENDER_RESEARCH_MCP_TEST_HOOKS"] = previous_hooks


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--blender-executable", type=Path, required=True)
    parser.add_argument("--port", type=int, default=9884)
    parser.add_argument("--timeout", type=float, default=90.0)
    args = parser.parse_args()
    report = asyncio.run(run(args))
    report_path = Path(report["artifact_directory"]) / f"report-{PACKAGE_VERSION}.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(report_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
