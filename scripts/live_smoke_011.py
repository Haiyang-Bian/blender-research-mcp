"""Run the Blender 4.2 semantic base-Mesh acceptance for release 0.11.0."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import hashlib
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

from blender_research_mcp.client import BridgeClient
from blender_research_mcp.constants import PACKAGE_VERSION
from blender_research_mcp.errors import BridgeError
from blender_research_mcp.lifecycle import ApplicationManager
from blender_research_mcp.rendering import request_render_preview

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_BUILDER = ROOT / "scripts" / "create_mesh_fixture.py"


def stage(name: str) -> None:
    print(f"[0.11 smoke] {name}", flush=True)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_fixture(blender: Path, output: Path, log_path: Path) -> None:
    command = [
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
    ]
    with log_path.open("wb") as log:
        result = subprocess.run(  # noqa: S603 - fixed executable and repository script
            command,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            shell=False,
            check=False,
        )
    if result.returncode != 0 or not output.is_file():
        raise RuntimeError(f"Could not build Mesh fixture; see {log_path}")


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


async def inspect_mesh(
    client: BridgeClient,
    object_name: str,
    component: str = "summary",
    offset: int = 0,
    limit: int = 256,
) -> dict[str, Any]:
    return await client.call(
        "mesh.inspect",
        {
            "object_name": object_name,
            "component": component,
            "offset": offset,
            "limit": limit,
        },
        read_only=True,
    )


async def begin(client: BridgeClient, generation: int, label: str) -> dict[str, Any]:
    return await mutate(
        client,
        "transaction.begin",
        {"label": label, "viewport_id": None},
        generation,
    )


async def finish(
    client: BridgeClient,
    command: str,
    transaction_id: str,
    generation: int,
) -> dict[str, Any]:
    return await mutate(
        client,
        command,
        {"transaction_id": transaction_id},
        generation,
    )


def mesh_edit_params(
    transaction_id: str,
    inspected: dict[str, Any],
    operation: dict[str, Any],
    scope: str = "OBJECT",
) -> dict[str, Any]:
    return {
        "transaction_id": transaction_id,
        "object_name": inspected["object"]["name"],
        "expected_object_identity": inspected["object"]["session_identity"],
        "expected_mesh_identity": inspected["mesh"]["session_identity"],
        "expected_mesh_users": inspected["mesh"]["users"],
        "expected_mesh_user_objects": [
            {
                "object_name": item["object_name"],
                "expected_object_identity": item["session_identity"],
            }
            for item in inspected["user_objects"]
        ],
        "expected_mesh_fingerprint": inspected["mesh_fingerprint"],
        "data_scope": scope,
        "operation": operation,
    }


def protected_summary(inspected: dict[str, Any]) -> dict[str, Any]:
    return {
        "mesh_identity": inspected["mesh"]["session_identity"],
        "mesh_fingerprint": inspected["mesh_fingerprint"],
        "topology_fingerprint": inspected["topology_fingerprint"],
        "user_objects": inspected["user_objects"],
        "uv_layers": inspected["mesh"]["uv_layers"],
        "color_attributes": inspected["mesh"]["color_attributes"],
        "material_slots": inspected["mesh"]["material_slots"],
        "attributes": [
            attribute
            for attribute in inspected["mesh"]["attributes"]
            if attribute["protected"]
        ],
    }


def persistent_summary(inspected: dict[str, Any]) -> dict[str, Any]:
    protected = protected_summary(inspected)
    protected.pop("mesh_identity")
    protected.pop("mesh_fingerprint")
    protected["user_objects"] = [
        item["object_name"] for item in protected["user_objects"]
    ]
    protected["material_slots"] = [
        {
            "slot_index": item["slot_index"],
            "material_name": item["material_name"],
        }
        for item in protected["material_slots"]
    ]
    return protected


def require_exact(before: dict[str, Any], after: dict[str, Any], label: str) -> None:
    if protected_summary(before) != protected_summary(after):
        raise RuntimeError(f"{label} did not restore the exact protected Mesh state")


def context_identity(context: dict[str, Any]) -> dict[str, Any]:
    return {
        key: context.get(key)
        for key in ("mode", "active_object", "selected_objects", "workspace", "scene")
    }


async def check_inspection(client: BridgeClient, report: dict[str, Any]) -> None:
    stage("paged base-Mesh inspection and evaluated-geometry distinction")
    summary = await inspect_mesh(client, "Mesh Transform")
    first = await inspect_mesh(client, "Mesh Transform", "vertices", 0, 2)
    second = await inspect_mesh(client, "Mesh Transform", "vertices", 2, 2)
    geometry = await client.call(
        "object.geometry.inspect",
        {"object_name": "Mesh Transform"},
        read_only=True,
    )
    if not first["pagination"]["truncated"] or first["pagination"]["next_offset"] != 2:
        raise RuntimeError("Mesh vertex pagination did not report the next page")
    if [item["index"] for item in first["items"] + second["items"]] != [0, 1, 2, 3]:
        raise RuntimeError("Mesh vertex pages were not stable under one fingerprint")
    if int(geometry["counts"]["vertices"]) == int(summary["counts"]["vertices"]):
        raise RuntimeError("Fixture Modifier did not distinguish base and evaluated Mesh")
    report["inspection"] = {
        "summary": summary,
        "vertex_pages": [first, second],
        "evaluated_geometry": geometry,
    }


async def check_noop(client: BridgeClient, report: dict[str, Any]) -> None:
    stage("no-op generation and delta behavior")
    before = await inspect_mesh(client, "Mesh Transform")
    transaction = await begin(client, int(before["scene_generation"]), "0.11 Mesh no-op")
    changed = await mutate(
        client,
        "mesh.edit",
        mesh_edit_params(
            str(transaction["transaction_id"]),
            before,
            {
                "type": "transform",
                "target": {"type": "vertices", "indices": [0]},
                "translation": {"x": 0.0, "y": 0.0, "z": 0.0},
            },
        ),
        int(transaction["scene_generation"]),
    )
    if changed["changed"] or changed["delta_count"] != 0:
        raise RuntimeError("Mesh no-op recorded a delta")
    if int(changed["scene_generation"]) != int(transaction["scene_generation"]):
        raise RuntimeError("Mesh no-op advanced scene generation")
    rolled_back = await finish(
        client,
        "transaction.rollback",
        str(transaction["transaction_id"]),
        int(changed["scene_generation"]),
    )
    report["noop"] = {"edit": changed, "rollback": rolled_back}


async def check_conflict(
    client: BridgeClient,
    manager: ApplicationManager,
    action: str,
) -> dict[str, Any]:
    before = await inspect_mesh(client, "Mesh Conflict")
    transaction = await begin(
        client,
        int(before["scene_generation"]),
        f"0.11 {action} conflict",
    )
    changed = await mutate(
        client,
        "mesh.edit",
        mesh_edit_params(
            str(transaction["transaction_id"]),
            before,
            {
                "type": "transform",
                "target": {"type": "vertices", "indices": [0]},
                "translation": {"x": 0.0, "y": 0.0, "z": 0.2},
            },
        ),
        int(transaction["scene_generation"]),
    )
    hook_params: dict[str, Any] = {"action": action, "object_name": "Mesh Conflict"}
    if action == "shared_user":
        hook_params["name"] = "Mesh Conflict External User"
    hook = await client.call("_test.mesh.touch", hook_params, read_only=False)
    touched = await inspect_mesh(client, "Mesh Conflict")
    try:
        await finish(
            client,
            "transaction.rollback",
            str(transaction["transaction_id"]),
            int(touched["scene_generation"]),
        )
    except BridgeError as exc:
        if exc.error.code != "MESH_DATA_CONFLICT":
            raise
        error = exc.error.model_dump(mode="json")
    else:
        raise RuntimeError(f"Injected {action} Mesh conflict unexpectedly restored")
    preserved = await inspect_mesh(client, "Mesh Conflict")
    if action == "shared_user":
        names = {item["object_name"] for item in preserved["user_objects"]}
        if "Mesh Conflict External User" not in names:
            raise RuntimeError("Shared-user conflict did not preserve the external object")
    elif preserved["mesh_fingerprint"] != hook["mesh_fingerprint"]:
        raise RuntimeError(f"{action} conflict overwrote the injected Mesh state")
    reloaded = await manager.project_reload(save_current=False, use_scripts=False, load_ui=False)
    return {
        "action": action,
        "edit": changed,
        "hook": hook,
        "touched": protected_summary(touched),
        "error": error,
        "preserved": protected_summary(preserved),
        "reload": reloaded,
    }


async def check_conflicts(
    client: BridgeClient,
    manager: ApplicationManager,
    report: dict[str, Any],
) -> None:
    stage("coordinate, topology, and shared-user conflict preservation")
    report["conflicts"] = [
        await check_conflict(client, manager, action)
        for action in ("coordinate", "topology", "shared_user")
    ]


async def edit_once(
    client: BridgeClient,
    object_name: str,
    operation: dict[str, Any],
    label: str,
    finish_command: str,
    scope: str = "OBJECT",
) -> dict[str, Any]:
    before = await inspect_mesh(client, object_name)
    transaction = await begin(client, int(before["scene_generation"]), label)
    changed = await mutate(
        client,
        "mesh.edit",
        mesh_edit_params(str(transaction["transaction_id"]), before, operation, scope),
        int(transaction["scene_generation"]),
    )
    if not changed["changed"] or int(changed["scene_generation"]) != int(
        transaction["scene_generation"]
    ) + 1:
        raise RuntimeError(f"{label} did not produce one generation-scoped Mesh edit")
    finished = await finish(
        client,
        finish_command,
        str(transaction["transaction_id"]),
        int(changed["scene_generation"]),
    )
    after = await inspect_mesh(client, object_name)
    if finish_command == "transaction.rollback":
        require_exact(before, after, label)
    elif after["mesh_fingerprint"] != changed["after_mesh_fingerprint"]:
        raise RuntimeError(f"{label} commit did not retain the edited Mesh")
    return {
        "before": protected_summary(before),
        "edit": changed,
        "finish": finished,
        "after": protected_summary(after),
    }


OPERATIONS: tuple[tuple[str, str, dict[str, Any]], ...] = (
    (
        "Mesh Transform",
        "transform",
        {
            "type": "transform",
            "target": {"type": "faces", "indices": [0]},
            "translation": {"x": 0.0, "y": 0.0, "z": 0.25},
        },
    ),
    (
        "Mesh Extrude",
        "extrude_faces",
        {
            "type": "extrude_faces",
            "face_indices": [0],
            "offset": {"x": 0.0, "y": 0.0, "z": 0.45},
        },
    ),
    (
        "Mesh Inset",
        "inset_faces",
        {"type": "inset_faces", "face_indices": [0], "thickness": 0.16},
    ),
    (
        "Mesh Bevel",
        "bevel_edges",
        {"type": "bevel_edges", "edge_indices": [0], "width": 0.12, "segments": 2},
    ),
    (
        "Mesh Delete",
        "delete",
        {"type": "delete", "target": {"type": "faces", "indices": [0]}},
    ),
    (
        "Mesh Dissolve",
        "dissolve",
        {"type": "dissolve", "target": {"type": "edges", "indices": [0]}},
    ),
    (
        "Mesh Merge",
        "merge_vertices",
        {"type": "merge_vertices", "vertex_indices": [0, 1], "destination": "CENTER"},
    ),
    (
        "Mesh Face Settings",
        "face_settings",
        {
            "type": "face_settings",
            "face_indices": [0],
            "material_slot_index": 1,
            "smooth": True,
        },
    ),
    (
        "Mesh Normals",
        "normals",
        {"type": "normals", "mode": "FLIP", "face_indices": [0]},
    ),
)


async def check_operations(client: BridgeClient, report: dict[str, Any]) -> None:
    stage("all nine operations with exact rollback and commit")
    results: dict[str, Any] = {}
    for object_name, operation_name, operation in OPERATIONS:
        rollback_result = await edit_once(
            client,
            object_name,
            operation,
            f"0.11 {operation_name} rollback",
            "transaction.rollback",
        )
        commit_result = await edit_once(
            client,
            object_name,
            operation,
            f"0.11 {operation_name} commit",
            "transaction.commit",
        )
        results[operation_name] = {"rollback": rollback_result, "commit": commit_result}
    report["operations"] = results


async def check_shared_scopes(client: BridgeClient, report: dict[str, Any]) -> None:
    stage("OBJECT single-user and SHARED_DATA linked edits")
    object_before = await inspect_mesh(client, "Mesh Shared A")
    linked_before = await inspect_mesh(client, "Mesh Shared B")
    transaction = await begin(
        client,
        int(object_before["scene_generation"]),
        "0.11 OBJECT scope rollback",
    )
    object_edit = await mutate(
        client,
        "mesh.edit",
        mesh_edit_params(
            str(transaction["transaction_id"]),
            object_before,
            {
                "type": "transform",
                "target": {"type": "vertices", "indices": [0]},
                "translation": {"x": 0.0, "y": 0.0, "z": 0.3},
            },
            "OBJECT",
        ),
        int(transaction["scene_generation"]),
    )
    object_during = await inspect_mesh(client, "Mesh Shared A")
    linked_during = await inspect_mesh(client, "Mesh Shared B")
    if object_during["mesh"]["session_identity"] == linked_during["mesh"]["session_identity"]:
        raise RuntimeError("OBJECT scope did not create a target-only Mesh")
    object_rollback = await finish(
        client,
        "transaction.rollback",
        str(transaction["transaction_id"]),
        int(object_edit["scene_generation"]),
    )
    object_after = await inspect_mesh(client, "Mesh Shared A")
    linked_after = await inspect_mesh(client, "Mesh Shared B")
    require_exact(object_before, object_after, "OBJECT scope")
    require_exact(linked_before, linked_after, "OBJECT linked peer")

    shared_before = await inspect_mesh(client, "Mesh Shared A")
    transaction = await begin(
        client,
        int(shared_before["scene_generation"]),
        "0.11 SHARED_DATA rollback",
    )
    shared_edit = await mutate(
        client,
        "mesh.edit",
        mesh_edit_params(
            str(transaction["transaction_id"]),
            shared_before,
            {
                "type": "transform",
                "target": {"type": "vertices", "indices": [0]},
                "translation": {"x": 0.2, "y": 0.0, "z": 0.0},
            },
            "SHARED_DATA",
        ),
        int(transaction["scene_generation"]),
    )
    shared_peer = await inspect_mesh(client, "Mesh Shared B")
    if shared_peer["mesh_fingerprint"] != shared_edit["after_mesh_fingerprint"]:
        raise RuntimeError("SHARED_DATA edit was not visible to every object user")
    shared_rollback = await finish(
        client,
        "transaction.rollback",
        str(transaction["transaction_id"]),
        int(shared_edit["scene_generation"]),
    )
    shared_after = await inspect_mesh(client, "Mesh Shared A")
    require_exact(shared_before, shared_after, "SHARED_DATA scope")
    report["shared_scopes"] = {
        "object": {
            "edit": object_edit,
            "during": protected_summary(object_during),
            "rollback": object_rollback,
        },
        "shared": {
            "edit": shared_edit,
            "peer": protected_summary(shared_peer),
            "rollback": shared_rollback,
        },
    }


async def check_disconnect(client: BridgeClient, report: dict[str, Any]) -> None:
    stage("disconnect rollback with protected layers, sharing, and context")
    context_before = await client.call("context.get", read_only=True)
    before = await inspect_mesh(client, "Mesh Shared A")
    peer_before = await inspect_mesh(client, "Mesh Shared B")
    transaction = await begin(
        client,
        int(before["scene_generation"]),
        "0.11 disconnect OBJECT rollback",
    )
    changed = await mutate(
        client,
        "mesh.edit",
        mesh_edit_params(
            str(transaction["transaction_id"]),
            before,
            {
                "type": "extrude_faces",
                "face_indices": [0],
                "offset": {"x": 0.0, "y": 0.0, "z": 0.35},
            },
            "OBJECT",
        ),
        int(transaction["scene_generation"]),
    )
    await client.close()
    await asyncio.sleep(3.0)
    reconnected = await client.call("connection.ping", read_only=True)
    after = await inspect_mesh(client, "Mesh Shared A")
    peer_after = await inspect_mesh(client, "Mesh Shared B")
    context_after = await client.call("context.get", read_only=True)
    require_exact(before, after, "disconnect target")
    require_exact(peer_before, peer_after, "disconnect linked peer")
    if context_identity(context_before) != context_identity(context_after):
        raise RuntimeError("Disconnect Mesh rollback did not preserve user context")
    report["disconnect"] = {
        "edit": changed,
        "reconnected_instance": reconnected["instance_id"],
        "before": protected_summary(before),
        "after": protected_summary(after),
        "peer_after": protected_summary(peer_after),
        "context_before": context_before,
        "context_after": context_after,
    }


async def save_reload_render(
    client: BridgeClient,
    manager: ApplicationManager,
    artifact_directory: Path,
    report: dict[str, Any],
) -> None:
    stage("save, reload, re-inspect, and Eevee render")
    persisted_names = [item[0] for item in OPERATIONS]
    before_inspected = {name: await inspect_mesh(client, name) for name in persisted_names}
    before = {name: persistent_summary(item) for name, item in before_inspected.items()}
    saved = await manager.project_save()
    reloaded = await manager.project_reload(save_current=False, use_scripts=False, load_ui=False)
    after_inspected = {name: await inspect_mesh(client, name) for name in persisted_names}
    after = {name: persistent_summary(item) for name, item in after_inspected.items()}
    if before != after:
        raise RuntimeError("Committed Mesh fingerprints or protected layers changed after reload")
    camera = await client.call(
        "object.inspect",
        {"object_name": "Mesh Camera"},
        read_only=True,
    )
    scene = await inspect_mesh(client, "Mesh Transform")
    image, preview = await request_render_preview(
        client,
        {
            "camera_name": "Mesh Camera",
            "expected_camera_identity": camera["session_identity"],
            "width": 512,
            "height": 384,
            "samples": 24,
            "transparent": False,
        },
        expected_scene_generation=int(scene["scene_generation"]),
        idempotency_key=str(uuid4()),
    )
    path = artifact_directory / "semantic-mesh-final.png"
    path.write_bytes(image)
    if path.stat().st_size < 1024:
        raise RuntimeError("Eevee Mesh acceptance render is unexpectedly small")
    report["persistence"] = {
        "before": before,
        "before_session_evidence": {
            name: {
                "mesh_identity": item["mesh"]["session_identity"],
                "mesh_fingerprint": item["mesh_fingerprint"],
            }
            for name, item in before_inspected.items()
        },
        "save": saved,
        "reload": reloaded,
        "after": after,
        "after_session_evidence": {
            name: {
                "mesh_identity": item["mesh"]["session_identity"],
                "mesh_fingerprint": item["mesh_fingerprint"],
            }
            for name, item in after_inspected.items()
        },
        "preview": preview,
        "preview_path": str(path),
        "preview_sha256": sha256(path),
        "preview_bytes": path.stat().st_size,
    }


async def run(args: argparse.Namespace) -> dict[str, Any]:
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + f"-{uuid4().hex[:8]}"
    temporary_root = Path(tempfile.gettempdir()) / "blender-research-mcp-mesh" / run_id
    temporary_root.mkdir(parents=True, exist_ok=False)
    artifact_directory = ROOT / "artifacts" / "live-smoke" / run_id
    artifact_directory.mkdir(parents=True, exist_ok=False)
    source = temporary_root / "mesh-source.blend"
    project = temporary_root / "mesh-project.blend"
    fixture_log = artifact_directory / "fixture.log"
    build_fixture(args.blender_executable, source, fixture_log)
    source_hash_before = sha256(source)
    shutil.copy2(source, project)

    report: dict[str, Any] = {
        "run_id": run_id,
        "started_at": datetime.now(UTC).isoformat(),
        "temporary_root": str(temporary_root),
        "artifact_directory": str(artifact_directory),
        "source": str(source),
        "source_sha256_before": source_hash_before,
        "project": str(project),
        "port": args.port,
        "server_version": PACKAGE_VERSION,
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
        status_before = await manager.status()
        if status_before.get("running"):
            raise RuntimeError(f"Smoke port is already in use: {args.port}")
        report["status_before"] = status_before
        stage("managed launch")
        launch = await manager.launch()
        launched = True
        report["launch"] = launch
        application = launch["application"]
        if launch["status"] != "launched":
            raise RuntimeError("Cold managed launch did not return launched")
        if application["addon_version"] != PACKAGE_VERSION or not str(
            application["blender_version"]
        ).startswith("4.2.23"):
            raise RuntimeError("Managed launch did not load matching Blender 4.2.23")
        ping_before = await client.call("connection.ping", read_only=True)
        capabilities = ping_before["capability_versions"]
        if int(capabilities.get("mesh_topology", 0)) < 1:
            raise RuntimeError("Managed add-on did not advertise mesh_topology: 1")
        if int(capabilities.get("transactions", 0)) < 4:
            raise RuntimeError("Managed add-on did not advertise transactions: 4")
        report["ping_before"] = ping_before
        report["project_open"] = await manager.project_open(
            str(project), save_current=False, use_scripts=False, load_ui=False
        )
        context_before = await client.call("context.get", read_only=True)
        report["context_before"] = context_before

        await check_inspection(client, report)
        await check_noop(client, report)
        await check_conflicts(client, manager, report)
        await check_shared_scopes(client, report)
        await check_disconnect(client, report)
        await check_operations(client, report)
        await save_reload_render(client, manager, artifact_directory, report)

        context_after = await client.call("context.get", read_only=True)
        if context_identity(context_before) != context_identity(context_after):
            raise RuntimeError("Mesh acceptance did not preserve Blender user context")
        ping_after = await client.call("connection.ping", read_only=True)
        if int(ping_after["heartbeat"]) <= int(ping_before["heartbeat"]):
            raise RuntimeError("Blender UI heartbeat did not advance")
        report["context_after"] = context_after
        report["ping_after"] = ping_after
        report["source_sha256_after"] = sha256(source)
        report["source_unchanged"] = report["source_sha256_after"] == source_hash_before
        if not report["source_unchanged"]:
            raise RuntimeError("Source Mesh fixture changed during live acceptance")
        report["project_sha256_after"] = sha256(project)
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
    parser.add_argument("--port", type=int, default=9886)
    parser.add_argument("--timeout", type=float, default=90.0)
    args = parser.parse_args()
    try:
        report = asyncio.run(run(args))
    except BridgeError as exc:
        print(json.dumps(exc.error.model_dump(mode="json"), ensure_ascii=False, indent=2))
        raise
    report_path = Path(report["artifact_directory"]) / f"report-{PACKAGE_VERSION}.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(report_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
