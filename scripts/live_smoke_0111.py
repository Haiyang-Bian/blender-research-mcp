"""Run Blender 4.2 collaborative UI and native-save acceptance for release 0.11.1."""

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
from blender_research_mcp.comparison import ComparisonRequest, run_lookdev_comparison
from blender_research_mcp.constants import PACKAGE_VERSION
from blender_research_mcp.errors import BridgeError
from blender_research_mcp.lifecycle import ApplicationManager
from blender_research_mcp.observation import settle_scene_generation

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_BUILDER = ROOT / "scripts" / "create_mesh_fixture.py"


def stage(name: str) -> None:
    print(f"[0.11.1 smoke] {name}", flush=True)


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
        raise RuntimeError(f"Could not build collaborative fixture; see {log_path}")


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
        deadline_ms=30_000,
    )


async def begin(client: BridgeClient, label: str) -> dict[str, Any]:
    ping = await settle_scene_generation(client)
    return await mutate(
        client,
        "transaction.begin",
        {"label": label, "viewport_id": None},
        int(ping["scene_generation"]),
    )


async def inspect_object(client: BridgeClient, name: str) -> dict[str, Any]:
    return await client.call("object.inspect", {"object_name": name}, read_only=True)


async def inspect_mesh(
    client: BridgeClient,
    name: str,
    component: str = "summary",
) -> dict[str, Any]:
    return await client.call(
        "mesh.inspect",
        {"object_name": name, "component": component, "offset": 0, "limit": 256},
        read_only=True,
    )


async def transform(
    client: BridgeClient,
    transaction_id: str,
    inspected: dict[str, Any],
    generation: int,
    *,
    location: dict[str, float] | None = None,
    scale: dict[str, float] | None = None,
) -> dict[str, Any]:
    params: dict[str, Any] = {
        "transaction_id": transaction_id,
        "object_name": inspected["name"],
        "expected_object_identity": inspected["session_identity"],
    }
    if location is not None:
        params["location"] = location
    if scale is not None:
        params["scale"] = scale
    return await mutate(client, "object.transform", params, generation)


def ui_projection(context: dict[str, Any]) -> dict[str, Any]:
    return {
        "workspace": context["workspace"],
        "viewport_id": context["viewport_id"],
        "active_object": context["active_object"],
        "selected_objects": context["selected_objects"],
        "view": context["view"],
    }


async def expect_error(awaitable: Any, code: str) -> dict[str, Any]:
    try:
        await awaitable
    except BridgeError as exc:
        if exc.error.code != code:
            raise
        return exc.error.model_dump(mode="json")
    raise RuntimeError(f"Expected {code}")


async def check_collaborative_ui(
    client: BridgeClient,
    manager: ApplicationManager,
    report: dict[str, Any],
) -> None:
    stage("collaborative UI commit and rollback")
    baseline = await inspect_object(client, "Mesh Transform")
    context_before = await client.call("context.get", read_only=True)
    transaction = await begin(client, "0.11.1 preserve user UI rollback")
    first = await transform(
        client,
        str(transaction["transaction_id"]),
        baseline,
        int(transaction["scene_generation"]),
        location={"x": float(baseline["location"][0]) + 0.8},
    )
    touched = await client.call(
        "_test.context.touch",
        {
            "viewport_id": context_before["viewport_id"],
            "active_object": "Mesh Extrude",
            "shading": "WIREFRAME",
            "show_overlays": False,
        },
        read_only=False,
    )
    context_touched = touched["context"]
    second = await transform(
        client,
        str(transaction["transaction_id"]),
        baseline,
        int(first["scene_generation"]),
        location={"y": float(baseline["location"][1]) + 0.45},
    )
    rollback = await mutate(
        client,
        "transaction.rollback",
        {"transaction_id": transaction["transaction_id"]},
        int(second["scene_generation"]),
    )
    restored = await inspect_object(client, "Mesh Transform")
    context_after = await client.call("context.get", read_only=True)
    if restored["location"] != baseline["location"]:
        raise RuntimeError("Collaborative rollback did not restore object data")
    if ui_projection(context_after) != ui_projection(context_touched):
        raise RuntimeError("Collaborative rollback rewound the user's UI")
    if ui_projection(context_after) == ui_projection(context_before):
        raise RuntimeError("UI touch did not create observable navigation/display/selection drift")
    if not rollback.get("user_ui_preserved") or not rollback.get("preserved_ui_changes"):
        raise RuntimeError("Rollback did not report preserved user UI paths")

    committed_before = await inspect_object(client, "Mesh Transform")
    commit_tx = await begin(client, "0.11.1 preserve user UI commit")
    committed_write = await transform(
        client,
        str(commit_tx["transaction_id"]),
        committed_before,
        int(commit_tx["scene_generation"]),
        location={"z": float(committed_before["location"][2]) + 0.2},
    )
    commit_touch = await client.call(
        "_test.context.touch",
        {
            "active_object": "Mesh Inset",
            "shading": "MATERIAL",
            "show_overlays": True,
        },
        read_only=False,
    )
    committed = await mutate(
        client,
        "transaction.commit",
        {"transaction_id": commit_tx["transaction_id"]},
        int(committed_write["scene_generation"]),
    )
    committed_after = await inspect_object(client, "Mesh Transform")
    context_committed = await client.call("context.get", read_only=True)
    committed_expected = float(committed_before["location"][2]) + 0.2
    if abs(float(committed_after["location"][2]) - committed_expected) > 1e-6:
        raise RuntimeError("Collaborative commit did not retain object data")
    if ui_projection(context_committed) != ui_projection(commit_touch["context"]):
        raise RuntimeError("Collaborative commit rewound the user's UI")

    ping_before_save = await client.call("connection.ping", read_only=True)
    revision_before = int(ping_before_save["user_intent_revision"])
    managed_save = await manager.project_save()
    managed_ping = await client.call("connection.ping", read_only=True)
    if int(managed_ping["user_intent_revision"]) != revision_before:
        raise RuntimeError("Managed project.save was misclassified as native save")
    report["collaborative_ui"] = {
        "context_before": context_before,
        "context_touched": context_touched,
        "rollback": rollback,
        "context_after_rollback": context_after,
        "commit": committed,
        "context_after_commit": context_committed,
        "managed_save": managed_save,
        "managed_save_ping": managed_ping,
    }


def mesh_edit_params(transaction_id: str, inspected: dict[str, Any]) -> dict[str, Any]:
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
        "data_scope": "OBJECT",
        "operation": {
            "type": "transform",
            "target": {"type": "vertices", "indices": [0]},
            "translation": {"x": 0.0, "y": 0.0, "z": 0.35},
        },
    }


def persistent_mesh_summary(inspected: dict[str, Any]) -> dict[str, Any]:
    return {
        "counts": inspected["counts"],
        "topology_fingerprint": inspected["topology_fingerprint"],
        "uv_layers": inspected["mesh"]["uv_layers"],
        "color_attributes": inspected["mesh"]["color_attributes"],
        "material_slots": [
            {
                "slot_index": item["slot_index"],
                "material_name": item["material_name"],
            }
            for item in inspected["mesh"]["material_slots"]
        ],
        "attributes": inspected["mesh"]["attributes"],
    }


async def check_native_save_adoption(
    client: BridgeClient,
    manager: ApplicationManager,
    report: dict[str, Any],
) -> None:
    stage("native save adopts deferred structure and Mesh snapshot")
    delete_object = await inspect_object(client, "Mesh Delete")
    modifier_stack = await client.call(
        "modifier.inspect", {"object_name": "Mesh Transform"}, read_only=True
    )
    modifier = next(
        item
        for item in modifier_stack["modifiers"]
        if item["name"] == "Evaluation Bevel"
    )
    mesh_before = await inspect_mesh(client, "Mesh Bevel")
    vertices_before = await inspect_mesh(client, "Mesh Bevel", "vertices")
    transaction = await begin(client, "0.11.1 native save adopts mixed transaction")
    generation = int(transaction["scene_generation"])
    deleted = await mutate(
        client,
        "object.delete",
        {
            "transaction_id": transaction["transaction_id"],
            "object_name": delete_object["name"],
            "expected_object_identity": delete_object["session_identity"],
        },
        generation,
    )
    modifier_deleted = await mutate(
        client,
        "modifier.delete",
        {
            "transaction_id": transaction["transaction_id"],
            "object_name": modifier_stack["object_name"],
            "expected_object_identity": modifier_stack["object_identity"],
            "modifier_name": modifier["name"],
            "expected_modifier_identity": modifier["session_identity"],
            "expected_modifier_type": modifier["type"],
            "expected_stack_index": modifier["stack_index"],
            "expected_stack_fingerprint": modifier_stack["stack_fingerprint"],
        },
        int(deleted["scene_generation"]),
    )
    mesh_changed = await mutate(
        client,
        "mesh.edit",
        mesh_edit_params(str(transaction["transaction_id"]), mesh_before),
        int(modifier_deleted["scene_generation"]),
    )
    ping_before_save = await client.call("connection.ping", read_only=True)
    revision_before = int(ping_before_save["user_intent_revision"])
    saved = await client.call("_test.native_save", {}, read_only=False)
    ping = await client.call("connection.ping", read_only=True)
    if int(ping["user_intent_revision"]) != revision_before + 1:
        raise RuntimeError("Native save did not advance user intent revision")
    action = ping["last_user_action"]
    if action["status"] != "succeeded" or action["kind"] != "native_save":
        raise RuntimeError("Native save did not record a successful operation")
    inspected_now = await inspect_object(client, "Mesh Transform")
    write_error = await expect_error(
        transform(
            client,
            str(transaction["transaction_id"]),
            inspected_now,
            int(ping["scene_generation"]),
            scale={"x": 1.1},
        ),
        "TRANSACTION_ACCEPTED_BY_USER_SAVE",
    )
    rollback_error = await expect_error(
        mutate(
            client,
            "transaction.rollback",
            {"transaction_id": transaction["transaction_id"]},
            int(ping["scene_generation"]),
        ),
        "TRANSACTION_ACCEPTED_BY_USER_SAVE",
    )

    await client.close()
    await asyncio.sleep(3.0)
    reconnect_ping = await client.call("connection.ping", read_only=True)
    missing_before_reload = await expect_error(
        inspect_object(client, "Mesh Delete"), "OBJECT_NOT_FOUND"
    )
    stack_before_reload = await client.call(
        "modifier.inspect", {"object_name": "Mesh Transform"}, read_only=True
    )
    mesh_before_reload = await inspect_mesh(client, "Mesh Bevel")
    vertices_before_reload = await inspect_mesh(client, "Mesh Bevel", "vertices")
    if any(item["name"] == "Evaluation Bevel" for item in stack_before_reload["modifiers"]):
        raise RuntimeError(
            "Native save did not finalize the pending Modifier delete: "
            + json.dumps(
                {
                    "last_user_action": ping["last_user_action"],
                    "stack": stack_before_reload,
                },
                ensure_ascii=False,
            )
        )
    if mesh_before_reload["mesh_fingerprint"] != mesh_changed["after_mesh_fingerprint"]:
        raise RuntimeError("Disconnect rolled back the native-save-adopted Mesh")
    if vertices_before_reload["items"][0]["co"] == vertices_before["items"][0]["co"]:
        raise RuntimeError("Native-save-adopted Mesh vertex did not change")

    reload_result = await manager.project_reload(
        save_current=False, use_scripts=False, load_ui=False
    )
    missing_after_reload = await expect_error(
        inspect_object(client, "Mesh Delete"), "OBJECT_NOT_FOUND"
    )
    stack_after_reload = await client.call(
        "modifier.inspect", {"object_name": "Mesh Transform"}, read_only=True
    )
    mesh_after_reload = await inspect_mesh(client, "Mesh Bevel")
    vertices_after_reload = await inspect_mesh(client, "Mesh Bevel", "vertices")
    if any(item["name"] == "Evaluation Bevel" for item in stack_after_reload["modifiers"]):
        raise RuntimeError("Reload restored a Modifier deleted by native save")
    if persistent_mesh_summary(mesh_after_reload) != persistent_mesh_summary(mesh_before_reload):
        raise RuntimeError("Reload did not retain the native-save-adopted Mesh structure")
    for before_value, after_value in zip(
        vertices_before_reload["items"][0]["co"],
        vertices_after_reload["items"][0]["co"],
        strict=True,
    ):
        if abs(float(before_value) - float(after_value)) > 1e-6:
            raise RuntimeError("Reload did not retain the native-save-adopted Mesh vertex")
    report["native_save_adoption"] = {
        "operator": saved,
        "ping": ping,
        "write_error": write_error,
        "rollback_error": rollback_error,
        "reconnect_ping": reconnect_ping,
        "missing_before_reload": missing_before_reload,
        "stack_before_reload": stack_before_reload,
        "mesh_before_reload": mesh_before_reload,
        "vertices_before_reload": vertices_before_reload,
        "reload": reload_result,
        "missing_after_reload": missing_after_reload,
        "stack_after_reload": stack_after_reload,
        "mesh_after_reload": mesh_after_reload,
        "vertices_after_reload": vertices_after_reload,
    }


async def check_comparison_save_barrier(
    client: BridgeClient,
    manager: ApplicationManager,
    report: dict[str, Any],
) -> None:
    stage("comparison stops on native save")
    inspected = await inspect_object(client, "Mesh Extrude")
    baseline = float(inspected["scale"][0])
    request = ComparisonRequest.model_validate(
        {
            "target": {
                "type": "object_scale_axis",
                "object_name": inspected["name"],
                "expected_object_identity": inspected["session_identity"],
                "axis": "x",
            },
            "candidates": [
                {"label": "saved-current", "value": baseline + 0.18},
                {"label": "must-not-run", "value": baseline + 0.36},
            ],
            "capture": {
                "object_name": inspected["name"],
                "view": "FRONT",
                "max_size": 384,
                "display_mode": "SOLID",
                "overlays": "OFF",
            },
        }
    )
    hook_calls: list[dict[str, Any]] = []

    async def phase_hook(
        phase: str,
        label: str | None,
        details: dict[str, Any],
    ) -> None:
        hook_calls.append({"phase": phase, "label": label, "details": details})
        if phase == "after_write" and label == "saved-current":
            hook_calls.append(
                {
                    "phase": "native_save",
                    "label": label,
                    "details": await client.call("_test.native_save", {}, read_only=False),
                }
            )

    try:
        await run_lookdev_comparison(client, request, _phase_hook=phase_hook)
    except BridgeError as exc:
        if exc.error.code != "COMPARISON_ACCEPTED_BY_USER_SAVE":
            raise
        error = exc.error.model_dump(mode="json")
    else:
        raise RuntimeError("Comparison continued after native save")
    if any(call["label"] == "must-not-run" for call in hook_calls):
        raise RuntimeError("Comparison executed a candidate after native save")
    current = await inspect_object(client, "Mesh Extrude")
    expected = baseline + 0.18
    if abs(float(current["scale"][0]) - expected) > 1e-6:
        raise RuntimeError("Comparison cleanup rolled back the native-saved candidate")
    reload_result = await manager.project_reload(
        save_current=False, use_scripts=False, load_ui=False
    )
    reloaded = await inspect_object(client, "Mesh Extrude")
    if abs(float(reloaded["scale"][0]) - expected) > 1e-6:
        raise RuntimeError("Reload did not retain the native-saved comparison candidate")
    report["comparison_save_barrier"] = {
        "error": error,
        "hook_calls": hook_calls,
        "current": current,
        "reload": reload_result,
        "reloaded": reloaded,
    }


async def run(args: argparse.Namespace) -> dict[str, Any]:
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + f"-{uuid4().hex[:8]}"
    temporary_root = Path(tempfile.gettempdir()) / "blender-research-mcp-collab" / run_id
    temporary_root.mkdir(parents=True, exist_ok=False)
    artifact_directory = ROOT / "artifacts" / "live-smoke" / run_id
    artifact_directory.mkdir(parents=True, exist_ok=False)
    source = temporary_root / "collaborative-source.blend"
    project = temporary_root / "collaborative-project.blend"
    build_fixture(args.blender_executable, source, artifact_directory / "fixture.log")
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
        if (await manager.status()).get("running"):
            raise RuntimeError(f"Smoke port is already in use: {args.port}")
        launch = await manager.launch()
        launched = True
        report["launch"] = launch
        application = launch["application"]
        if application["addon_version"] != PACKAGE_VERSION:
            raise RuntimeError("Managed launch did not load the 0.11.1 add-on")
        if not str(application["blender_version"]).startswith("4.2.23"):
            raise RuntimeError("Managed launch did not use Blender 4.2.23")
        ping_before = await client.call("connection.ping", read_only=True)
        if int(ping_before["capability_versions"].get("transactions", 0)) < 5:
            raise RuntimeError("Managed add-on did not advertise transactions: 5")
        report["ping_before"] = ping_before
        report["project_open"] = await manager.project_open(
            str(project), save_current=False, use_scripts=False, load_ui=False
        )
        await check_collaborative_ui(client, manager, report)
        await check_native_save_adoption(client, manager, report)
        await check_comparison_save_barrier(client, manager, report)
        ping_after = await client.call("connection.ping", read_only=True)
        if int(ping_after["heartbeat"]) <= int(ping_before["heartbeat"]):
            raise RuntimeError("Blender UI heartbeat did not advance")
        report["ping_after"] = ping_after
        report["source_sha256_after"] = sha256(source)
        report["source_unchanged"] = report["source_sha256_after"] == source_hash_before
        if not report["source_unchanged"]:
            raise RuntimeError("Source collaborative fixture changed")
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
    parser.add_argument("--port", type=int, default=9887)
    parser.add_argument("--timeout", type=float, default=90.0)
    args = parser.parse_args()
    report = asyncio.run(run(args))
    report_path = Path(report["artifact_directory"]) / f"report-{PACKAGE_VERSION}.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(report_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
