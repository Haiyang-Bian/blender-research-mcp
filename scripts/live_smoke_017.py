"""Run Blender 4.2 exact Join, boundary Weld, and batch-v5 acceptance."""

from __future__ import annotations

import argparse
import asyncio
import base64
import contextlib
import json
import os
import shutil
import subprocess
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import live_smoke_013 as topology
import live_smoke_014 as attributes
import live_smoke_015 as base

from blender_research_mcp.client import BridgeClient
from blender_research_mcp.constants import MAX_DEADLINE_MS, PACKAGE_VERSION
from blender_research_mcp.errors import BridgeError
from blender_research_mcp.lifecycle import ApplicationManager

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_BUILDER = ROOT / "scripts" / "create_join_fixture.py"


def stage(name: str) -> None:
    print(f"[0.17 smoke] {name}", flush=True)


def build_fixture(blender: Path, output: Path) -> None:
    result = subprocess.run(  # noqa: S603 - fixed executable and repository script
        [
            str(blender.resolve(strict=True)),
            "--background",
            "--factory-startup",
            "--python-exit-code",
            "1",
            "--python",
            str(FIXTURE_BUILDER),
            "--",
            "--output",
            str(output),
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.STDOUT,
        shell=False,
        check=False,
    )
    if result.returncode != 0 or not output.is_file():
        raise RuntimeError("Could not build the 0.17 Join fixture")


async def inspect_object(client: BridgeClient, name: str) -> dict[str, Any]:
    return await client.call("object.inspect", {"object_name": name}, read_only=True)


async def inspect_collection(client: BridgeClient, name: str) -> dict[str, Any]:
    return await client.call(
        "collection.inspect",
        {"collection_name": name, "offset": 0, "limit": 256},
        read_only=True,
    )


async def exact_source(client: BridgeClient, name: str) -> dict[str, Any]:
    obj = await inspect_object(client, name)
    mesh = await base.mesh(client, name)
    uv = await attributes.uv(client, name, layer_name=None)
    weights = await attributes.weights(client, name)
    modifiers = await client.call(
        "modifier.inspect", {"object_name": name}, read_only=True
    )
    return {
        "object_name": name,
        "expected_object_identity": obj["session_identity"],
        "expected_object_structure_fingerprint": obj["structure_fingerprint"],
        "mesh_name": mesh["mesh"]["name"],
        "expected_mesh_identity": mesh["mesh"]["session_identity"],
        "expected_mesh_users": mesh["mesh"]["users"],
        "expected_mesh_user_objects": [
            {
                "object_name": item["object_name"],
                "expected_object_identity": item["session_identity"],
            }
            for item in mesh["user_objects"]
        ],
        "expected_mesh_fingerprint": mesh["mesh_fingerprint"],
        "expected_mesh_revision_id": mesh["mesh_revision_id"],
        "expected_uv_fingerprint": uv["uv_fingerprint"],
        "expected_group_schema_fingerprint": weights["group_schema_fingerprint"],
        "expected_weights_fingerprint": weights["weights_fingerprint"],
        "expected_shape_key_state_fingerprint": mesh["shape_key_state_fingerprint"],
        "expected_modifier_stack_fingerprint": modifiers["stack_fingerprint"],
        "selection_ids": [],
    }


def output_spec(
    destination: dict[str, Any],
    *,
    object_name: str,
    mesh_name: str,
    frame: dict[str, Any],
    disposition: str = "KEEP",
) -> dict[str, Any]:
    return {
        "new_object_name": object_name,
        "new_mesh_name": mesh_name,
        "collection_name": destination["name"],
        "expected_collection_identity": destination["session_identity"],
        "expected_collection_structure_fingerprint": destination["structure_fingerprint"],
        "coordinate_frame": frame,
        "source_disposition": disposition,
    }


def policies(*, preserve_colors: bool = True) -> tuple[dict[str, str], dict[str, str]]:
    return (
        {
            "materials": "PRESERVE_BY_IDENTITY",
            "uv": "MERGE_BY_NAME",
            "weights": "MERGE_BY_NAME",
            "colors": "MERGE_BY_NAME" if preserve_colors else "DROP",
            "generic": "ERROR_IF_PRESENT",
            "custom_normals": "DROP_RECALCULATE",
        },
        {"shape_keys": "ERROR_IF_PRESENT", "modifiers": "ERROR_IF_PRESENT"},
    )


async def join_call(
    client: BridgeClient,
    *,
    transaction_id: str | None,
    sources: list[dict[str, Any]],
    output: dict[str, Any],
    generation: int | None = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    # Reversible direct scenarios stress repeated rollback/disconnect cycles;
    # the commit/save/reload batch below is the full Color Attribute gate.
    attributes_value, dependencies = policies(preserve_colors=False)
    command = "mesh.join" if transaction_id is not None else "mesh.join.preflight"
    params: dict[str, Any] = {
        "sources": sources,
        "output": output,
        "attributes": attributes_value,
        "dependencies": dependencies,
    }
    if transaction_id is not None:
        params["transaction_id"] = transaction_id
    return await client.call(
        command,
        params,
        deadline_ms=MAX_DEADLINE_MS,
        expected_scene_generation=generation,
        idempotency_key=idempotency_key,
        read_only=transaction_id is None,
    )


async def weld(
    client: BridgeClient,
    transaction_id: str,
    joined: dict[str, Any],
    selection_ids: list[str],
    generation: int,
) -> dict[str, Any]:
    inspected = await base.mesh(client, joined["output_object"]["name"])
    return await base.mutate(
        client,
        "mesh.edit",
        {
            "transaction_id": transaction_id,
            **base.extract_target(inspected),
            "data_scope": "OBJECT",
            "operation": {
                "type": "weld_vertices",
                "selection_ids": selection_ids,
                "mode": "CROSS_SELECTIONS",
                "maximum_distance": 0.00001,
                "destination": "LOWEST_INDEX",
                "weight_merge": "MAX",
            },
        },
        generation,
    )


async def expect_error(awaitable: Any, code: str) -> dict[str, Any]:
    try:
        await awaitable
    except BridgeError as exc:
        value = exc.error.model_dump(mode="json")
        if value["code"] != code:
            raise RuntimeError(f"Expected {code}, received {value['code']}") from exc
        return value
    raise RuntimeError(f"Expected error {code}")


def context_projection(value: dict[str, Any]) -> dict[str, Any]:
    return {
        "scene": value.get("scene"),
        "view_layer": value.get("view_layer"),
        "mode": value.get("mode"),
        "active_object": value.get("active_object"),
        "selected_objects": value.get("selected_objects"),
        "frame": value.get("frame"),
        "active_camera": value.get("active_camera"),
    }


def save_capture(value: dict[str, Any], path: Path) -> None:
    encoded = value.pop("png_base64", None)
    if not isinstance(encoded, str) or not encoded:
        raise RuntimeError("Viewport capture omitted PNG evidence")
    path.write_bytes(base64.b64decode(encoded, validate=True))
    value["path"] = str(path)


async def direct_acceptance(
    client: BridgeClient,
    manager: ApplicationManager,
    artifact_directory: Path,
    report: dict[str, Any],
) -> None:
    left = await exact_source(client, "Join Left")
    right = await exact_source(client, "Join Right")
    destination = await inspect_collection(client, "Join Sources")
    baseline_mesh = await base.mesh(client, "Join Left")
    context_before = await client.call("context.get", read_only=True)
    scene_before = await client.call(
        "scene.inspect",
        {"kinds": ["objects", "collections"], "name_filter": None, "limit": 256},
        read_only=True,
    )

    stage("read-only WORLD preflight")
    preflight = await join_call(
        client,
        transaction_id=None,
        sources=[left, right],
        output=output_spec(
            destination,
            object_name="Preflight Joined",
            mesh_name="Preflight Joined Mesh",
            frame={"type": "WORLD"},
        ),
    )
    context_after_preflight = await client.call("context.get", read_only=True)
    scene_after = await client.call(
        "scene.inspect",
        {"kinds": ["objects", "collections"], "name_filter": None, "limit": 256},
        read_only=True,
    )
    if scene_before["objects"] != scene_after["objects"]:
        raise RuntimeError("Join preflight changed the scene object catalog")
    if context_projection(context_before) != context_projection(context_after_preflight):
        raise RuntimeError("Join preflight changed user context")
    report["preflight"] = preflight

    stage("WORLD Join idempotency and rollback")
    begin = await base.begin(client, int(baseline_mesh["scene_generation"]), "0.17 WORLD Join")
    key = str(uuid4())
    world_output = output_spec(
        destination,
        object_name="World Joined",
        mesh_name="World Joined Mesh",
        frame={"type": "WORLD"},
    )
    joined = await join_call(
        client,
        transaction_id=begin["transaction_id"],
        sources=[left, right],
        output=world_output,
        generation=int(begin["scene_generation"]),
        idempotency_key=key,
    )
    replay = await join_call(
        client,
        transaction_id=begin["transaction_id"],
        sources=[left, right],
        output=world_output,
        generation=int(begin["scene_generation"]),
        idempotency_key=key,
    )
    if replay != joined:
        raise RuntimeError("Join idempotency replay changed its result")
    world_object = await inspect_object(client, "World Joined")
    world_mesh = await base.mesh(client, "World Joined")
    if world_mesh["mesh_fingerprint"] != joined["output_mesh"]["mesh_fingerprint"]:
        raise RuntimeError(
            "WORLD Join Mesh fingerprint changed after publication: "
            f"{joined['output_mesh']['mesh_fingerprint']} -> "
            f"{world_mesh['mesh_fingerprint']}"
        )
    if any(abs(float(value)) > 1e-7 for value in world_object["location"]):
        raise RuntimeError("WORLD Join output is not at the identity location")
    rolled_back = await base.mutate(
        client,
        "transaction.rollback",
        {"transaction_id": begin["transaction_id"]},
        int(joined["scene_generation"]),
    )
    if await base.object_exists(client, "World Joined"):
        raise RuntimeError("WORLD Join output survived rollback")
    report["world_rollback"] = {
        "join": joined,
        "replay": replay,
        "output_object": world_object,
        "rollback": rolled_back,
    }

    stage("SOURCE_OBJECT Join, boundary Weld, branch composition, and evidence")
    left = await exact_source(client, "Join Left")
    right = await exact_source(client, "Join Right")
    destination = await inspect_collection(client, "Join Sources")
    current = await base.mesh(client, "Join Left")
    begin = await base.begin(client, int(current["scene_generation"]), "0.17 Join Weld")
    joined = await join_call(
        client,
        transaction_id=begin["transaction_id"],
        sources=[left, right],
        output=output_spec(
            destination,
            object_name="Welded Module",
            mesh_name="Welded Module Mesh",
            frame={
                "type": "SOURCE_OBJECT",
                "source_object_name": left["object_name"],
                "expected_source_object_identity": left["expected_object_identity"],
            },
        ),
        generation=int(begin["scene_generation"]),
        idempotency_key=str(uuid4()),
    )
    boundaries = [
        branch["boundary_selection"]["selection_id"] for branch in joined["branches"]
    ]
    welded = await weld(
        client,
        begin["transaction_id"],
        joined,
        boundaries,
        int(joined["scene_generation"]),
    )
    if welded["merged_vertex_reduction"] != 4:
        raise RuntimeError("Weld did not merge the four exact seam pairs")
    if welded["boundary_changes"]["after"]["edges"] != 0:
        raise RuntimeError("Welded complementary fixture still has boundary edges")
    compositions = []
    for branch in joined["branches"]:
        compositions.append(
            await client.call(
                "mesh.component_map.compose",
                {
                    "component_map_ids": [
                        branch["component_map"]["component_map_id"],
                        welded["component_map"]["component_map_id"],
                    ]
                },
                read_only=True,
            )
        )
    final_mesh = await base.mesh(client, "Welded Module")
    vertices = await topology.selection_query(client, final_mesh, "VERTEX", {"type": "all"})
    non_manifold = await topology.validate_mesh(
        client, vertices["selection_id"], "NON_MANIFOLD"
    )
    degenerate = await topology.validate_mesh(client, vertices["selection_id"], "DEGENERATE")
    if non_manifold["count"] or degenerate["count"]:
        raise RuntimeError("Weld introduced invalid deterministic fixture geometry")
    front = await topology.capture_view(client, "Welded Module", "FRONT")
    side = await topology.capture_view(client, "Welded Module", "RIGHT")
    if front["native_sha256"] == side["native_sha256"]:
        raise RuntimeError("Multi-angle Join evidence did not produce distinct images")
    front_path = artifact_directory / "joined-front.png"
    side_path = artifact_directory / "joined-right.png"
    save_capture(front, front_path)
    save_capture(side, side_path)
    rolled_back = await base.mutate(
        client,
        "transaction.rollback",
        {"transaction_id": begin["transaction_id"]},
        int(welded["scene_generation"]),
    )
    if await base.object_exists(client, "Welded Module"):
        raise RuntimeError("Welded output survived rollback")
    report["join_weld"] = {
        "join": joined,
        "weld": welded,
        "compositions": compositions,
        "non_manifold": non_manifold,
        "degenerate": degenerate,
        "captures": {"front": front, "right": side},
        "rollback": rolled_back,
    }

    stage("disconnect rollback")
    left = await exact_source(client, "Join Left")
    right = await exact_source(client, "Join Right")
    destination = await inspect_collection(client, "Join Sources")
    current = await base.mesh(client, "Join Left")
    begin = await base.begin(client, int(current["scene_generation"]), "0.17 disconnect")
    disconnected = await join_call(
        client,
        transaction_id=begin["transaction_id"],
        sources=[left, right],
        output=output_spec(
            destination,
            object_name="Disconnected Join",
            mesh_name="Disconnected Join Mesh",
            frame={"type": "WORLD"},
        ),
        generation=int(begin["scene_generation"]),
        idempotency_key=str(uuid4()),
    )
    await client.close()
    await asyncio.sleep(3.0)
    reconnect = await client.call("connection.ping", read_only=True)
    if await base.object_exists(client, "Disconnected Join"):
        raise RuntimeError("Disconnected Join survived automatic rollback")
    report["disconnect"] = {"join": disconnected, "reconnect": reconnect}

    stage("user Join conflict and native-save adoption")
    left = await exact_source(client, "Join Left")
    right = await exact_source(client, "Join Right")
    destination = await inspect_collection(client, "Join Sources")
    current = await base.mesh(client, "Join Left")
    begin = await base.begin(client, int(current["scene_generation"]), "0.17 conflict")
    conflicted = await join_call(
        client,
        transaction_id=begin["transaction_id"],
        sources=[left, right],
        output=output_spec(
            destination,
            object_name="User Accepted Join",
            mesh_name="User Accepted Join Mesh",
            frame={"type": "WORLD"},
        ),
        generation=int(begin["scene_generation"]),
        idempotency_key=str(uuid4()),
    )
    touched = await client.call(
        "_test.mesh.touch",
        {"object_name": "User Accepted Join", "action": "coordinate"},
        read_only=False,
    )
    ping = await client.call("connection.ping", read_only=True)
    conflict = await expect_error(
        base.mutate(
            client,
            "transaction.rollback",
            {"transaction_id": begin["transaction_id"]},
            int(ping["scene_generation"]),
        ),
        "MESH_JOIN_DATA_CONFLICT",
    )
    preserved = await base.mesh(client, "User Accepted Join")
    if preserved["mesh_fingerprint"] != touched["mesh_fingerprint"]:
        raise RuntimeError("Join conflict did not preserve the user's Mesh state")
    native_save = await client.call("_test.native_save", {}, read_only=False)
    accepted_ping = await client.call("connection.ping", read_only=True)
    accepted = await expect_error(
        base.mutate(
            client,
            "transaction.rollback",
            {"transaction_id": begin["transaction_id"]},
            int(accepted_ping["scene_generation"]),
        ),
        "TRANSACTION_ACCEPTED_BY_USER_SAVE",
    )
    reloaded = await manager.project_reload(save_current=False, use_scripts=False, load_ui=False)
    persisted = await base.mesh(client, "User Accepted Join")
    report["native_save"] = {
        "join": conflicted,
        "touch": touched,
        "conflict": conflict,
        "save": native_save,
        "accepted": accepted,
        "reload": reloaded,
        "persisted": persisted,
    }


async def batch_acceptance(
    client: BridgeClient,
    manager: ApplicationManager,
    report: dict[str, Any],
) -> None:
    stage("batch-v5 Join, Weld, validation, commit, save, and reload")
    left_mesh = await base.mesh(client, "Join Left")
    right_mesh = await base.mesh(client, "Join Right")
    destination = await inspect_collection(client, "Join Sources")
    begin = await base.begin(client, int(left_mesh["scene_generation"]), "0.17 batch v5")
    batch = await client.call(
        "mesh.batch.execute",
        {
            "transaction_id": begin["transaction_id"],
            "targets": [
                {"alias": "left", **base.extract_target(left_mesh)},
                {"alias": "right", **base.extract_target(right_mesh)},
            ],
            "inputs": [
                {
                    "type": "collection",
                    "alias": "outputs",
                    "collection_name": destination["name"],
                    "expected_collection_identity": destination["session_identity"],
                    "expected_collection_structure_fingerprint": destination[
                        "structure_fingerprint"
                    ],
                }
            ],
            "steps": [
                {
                    "type": "mesh_join",
                    "sources": [
                        {
                            "target_alias": "left",
                            "map_alias": "left_join_map",
                            "boundary_selection_alias": "left_boundary",
                        },
                        {
                            "target_alias": "right",
                            "map_alias": "right_join_map",
                            "boundary_selection_alias": "right_boundary",
                        },
                    ],
                    "output_target_alias": "joined",
                    "new_object_name": "Batch Joined",
                    "new_mesh_name": "Batch Joined Mesh",
                    "collection_alias": "outputs",
                    "coordinate_frame": {"type": "WORLD"},
                    "source_disposition": "KEEP",
                    "attributes": policies()[0],
                    "dependencies": policies()[1],
                },
                {
                    "type": "mesh_edit",
                    "target_alias": "joined",
                    "data_scope": "OBJECT",
                    "operation": {
                        "type": "weld_vertices",
                        "selection_aliases": ["left_boundary", "right_boundary"],
                        "mode": "CROSS_SELECTIONS",
                        "maximum_distance": 0.00001,
                        "destination": "LOWEST_INDEX",
                        "weight_merge": "MAX",
                    },
                    "map_alias": "weld_map",
                },
                {
                    "type": "selection_query",
                    "target_alias": "joined",
                    "output_alias": "joined_vertices",
                    "domain": "VERTEX",
                    "query": {"type": "all"},
                },
                {
                    "type": "mesh_validate",
                    "selection_alias": "joined_vertices",
                    "check": "NON_MANIFOLD",
                    "output_alias": "non_manifold",
                    "assertions": [{"type": "count_at_most", "value": 0}],
                },
                {
                    "type": "mesh_validate",
                    "selection_alias": "joined_vertices",
                    "check": "DEGENERATE",
                    "output_alias": "degenerate",
                    "assertions": [{"type": "count_at_most", "value": 0}],
                },
            ],
            "on_error": "ROLLBACK_TRANSACTION",
        },
        deadline_ms=MAX_DEADLINE_MS,
        expected_scene_generation=int(begin["scene_generation"]),
        idempotency_key=str(uuid4()),
        read_only=False,
    )
    manifest = batch["assembly_manifest"]
    if "joined" not in manifest["mesh_joins"]:
        raise RuntimeError("Batch manifest omitted Join evidence")
    if not any(
        value.get("composed_component_map_id")
        for key, value in batch["target_branches"].items()
        if key.startswith("joined:")
    ):
        raise RuntimeError("Batch did not compose a source branch through Weld")
    committed = await base.mutate(
        client,
        "transaction.commit",
        {"transaction_id": begin["transaction_id"]},
        int(batch["scene_generation"]),
    )
    saved = await manager.project_save()
    reloaded = await manager.project_reload(save_current=False, use_scripts=False, load_ui=False)
    persisted = await base.mesh(client, "Batch Joined")
    if persisted["counts"]["vertices"] != 12:
        raise RuntimeError("Committed batch Weld did not persist its exact vertex count")
    report["batch"] = {
        "execute": batch,
        "commit": committed,
        "save": saved,
        "reload": reloaded,
        "persisted": persisted,
    }


async def run(args: argparse.Namespace) -> dict[str, Any]:
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + f"-{uuid4().hex[:8]}"
    temporary = Path(tempfile.gettempdir()) / "blender-research-mcp-join" / run_id
    temporary.mkdir(parents=True, exist_ok=False)
    artifact_directory = ROOT / "artifacts" / "live-smoke" / run_id
    artifact_directory.mkdir(parents=True, exist_ok=False)
    source = temporary / "join-source.blend"
    project = temporary / "join-project.blend"
    batch_project = temporary / "join-batch-project.blend"
    build_fixture(args.blender_executable, source)
    shutil.copy2(source, project)
    shutil.copy2(source, batch_project)
    source_hash = base.sha256(source)
    report: dict[str, Any] = {
        "run_id": run_id,
        "started_at": datetime.now(UTC).isoformat(),
        "server_version": PACKAGE_VERSION,
        "port": args.port,
        "temporary_root": str(temporary),
        "artifact_directory": str(artifact_directory),
        "source": str(source),
        "source_sha256_before": source_hash,
        "project": str(project),
        "uv_sync_evidence": "os error 32: active Codex process locked blender-research-mcp.exe",
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
            raise RuntimeError(f"Smoke port is already in use: {args.port}")
        stage("managed launch and transaction-v13 handshake")
        report["launch"] = await manager.launch()
        launched = True
        report["project_open"] = await manager.project_open(
            str(project), save_current=False, use_scripts=False, load_ui=False
        )
        ping = await client.call("connection.ping", read_only=True)
        report["ping_before"] = ping
        for capability, minimum in {
            "transactions": 13,
            "mesh_join": 1,
            "mesh_component_map": 4,
            "mesh_topology": 5,
            "mesh_batch": 5,
        }.items():
            if int(ping["capability_versions"].get(capability, 0)) < minimum:
                raise RuntimeError(f"Missing capability {capability}:{minimum}")

        if not args.batch_only:
            await direct_acceptance(client, manager, artifact_directory, report)
            if not args.direct_only:
                report["batch_project_open"] = await manager.project_open(
                    str(batch_project), save_current=False, use_scripts=False, load_ui=False
                )
        if not args.direct_only:
            await batch_acceptance(client, manager, report)
        report["source_sha256_after"] = base.sha256(source)
        if report["source_sha256_after"] != source_hash:
            raise RuntimeError("Source Join fixture changed during smoke")
        if args.character_project is not None:
            character = args.character_project.resolve(strict=True)
            report["character_source"] = {
                "path": str(character),
                "sha256_before": base.sha256(character),
                "sha256_after": base.sha256(character),
                "note": (
                    "source-only integrity evidence; deterministic cage composition "
                    "is the live gate"
                ),
            }
        report["ping_after"] = await client.call("connection.ping", read_only=True)
        report["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 3)
        report["finished_at"] = datetime.now(UTC).isoformat()
        report_path = artifact_directory / "report-0.17.0.json"
        report_path.write_text(
            json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        stage(f"passed: {report_path}")
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--blender-executable", type=Path, required=True)
    parser.add_argument("--port", type=int, default=9898)
    parser.add_argument("--timeout", type=float, default=90.0)
    parser.add_argument("--character-project", type=Path)
    parser.add_argument("--batch-only", action="store_true")
    parser.add_argument("--direct-only", action="store_true")
    return parser.parse_args()


def main() -> int:
    try:
        report = asyncio.run(run(parse_args()))
    except BridgeError as exc:
        print(json.dumps(exc.error.model_dump(mode="json"), ensure_ascii=False), flush=True)
        raise
    print(json.dumps({"run_id": report["run_id"], "elapsed_ms": report["elapsed_ms"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
