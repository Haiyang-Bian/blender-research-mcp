"""Run focused Blender 4.2 separation and declarative Mesh-batch acceptance."""

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

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_BUILDER = ROOT / "scripts" / "create_topology_fixture.py"


def stage(name: str) -> None:
    print(f"[0.13.1 smoke] {name}", flush=True)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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
        raise RuntimeError("Could not build separation probe fixture")


async def mutate(
    client: BridgeClient,
    command: str,
    params: dict[str, Any],
    generation: int,
) -> dict[str, Any]:
    try:
        return await client.call(
            command,
            params,
            expected_scene_generation=generation,
            idempotency_key=str(uuid4()),
            read_only=False,
        )
    except BridgeError as exc:
        print(json.dumps(exc.error.model_dump(mode="json"), indent=2), flush=True)
        raise


async def inspect(client: BridgeClient, name: str) -> dict[str, Any]:
    return await client.call(
        "mesh.inspect",
        {"object_name": name, "component": "summary", "offset": 0, "limit": 256},
        read_only=True,
    )


async def select_face(client: BridgeClient, mesh: dict[str, Any]) -> dict[str, Any]:
    return await selection_query(
        client, mesh, "FACE", {"type": "indices", "indices": [0]}
    )


async def selection_query(
    client: BridgeClient,
    mesh: dict[str, Any],
    domain: str,
    query: dict[str, Any],
) -> dict[str, Any]:
    return await client.call(
        "mesh.selection.query",
        {
            "object_name": mesh["object"]["name"],
            "expected_object_identity": mesh["object"]["session_identity"],
            "expected_mesh_identity": mesh["mesh"]["session_identity"],
            "expected_mesh_revision_id": mesh["mesh_revision_id"],
            "domain": domain,
            "query": query,
        },
        read_only=True,
    )


def exact_params(
    transaction_id: str,
    mesh: dict[str, Any],
    selection_id: str,
    new_name: str,
) -> dict[str, Any]:
    return {
        "transaction_id": transaction_id,
        "object_name": mesh["object"]["name"],
        "expected_object_identity": mesh["object"]["session_identity"],
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
        "selection_id": selection_id,
        "new_object_name": new_name,
        "collection_name": None,
        "expected_collection_identity": None,
    }


def batch_target(alias: str, mesh: dict[str, Any]) -> dict[str, Any]:
    return {
        "alias": alias,
        "object_name": mesh["object"]["name"],
        "expected_object_identity": mesh["object"]["session_identity"],
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
    }


def edit_params(
    transaction_id: str,
    mesh: dict[str, Any],
    operation: dict[str, Any],
) -> dict[str, Any]:
    target = batch_target("target", mesh)
    target.pop("alias")
    return {
        **target,
        "transaction_id": transaction_id,
        "data_scope": "OBJECT",
        "operation": operation,
    }


async def run(args: argparse.Namespace) -> dict[str, Any]:
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + f"-{uuid4().hex[:8]}"
    temporary = Path(tempfile.gettempdir()) / "blender-research-mcp-batch" / run_id
    temporary.mkdir(parents=True, exist_ok=False)
    artifact_directory = ROOT / "artifacts" / "live-smoke" / run_id
    artifact_directory.mkdir(parents=True, exist_ok=False)
    source = temporary / "fixture-source.blend"
    project = temporary / "fixture-project.blend"
    build_fixture(args.blender_executable, source)
    source_hash = sha256(source)
    shutil.copy2(source, project)
    report: dict[str, Any] = {
        "run_id": run_id,
        "started_at": datetime.now(UTC).isoformat(),
        "temporary_root": str(temporary),
        "artifact_directory": str(artifact_directory),
        "fixture_source": str(source),
        "fixture_source_sha256_before": source_hash,
        "fixture_project": str(project),
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
        stage("managed launch and exact capability handshake")
        report["launch"] = await manager.launch()
        launched = True
        report["project_open"] = await manager.project_open(
            str(project), save_current=False, use_scripts=False, load_ui=False
        )
        ping = await client.call("connection.ping", read_only=True)
        report["ping_before"] = ping
        caps = ping["capability_versions"]
        required = {
            "mesh_component_map": 2,
            "mesh_topology": 3,
            "mesh_separation": 1,
            "mesh_batch": 1,
            "transactions": 8,
        }
        for capability, version in required.items():
            if int(caps.get(capability, 0)) < version:
                raise RuntimeError(
                    f"Managed add-on did not advertise {capability}: {version}"
                )

        stage("connected FACE separation and explicit rollback")
        baseline = await inspect(client, "Topology Split")
        selection = await select_face(client, baseline)
        begin = await mutate(
            client,
            "transaction.begin",
            {"label": "0.13.1 separation rollback", "viewport_id": None},
            int(selection["scene_generation"]),
        )
        separated = await mutate(
            client,
            "mesh.separate",
            exact_params(
                begin["transaction_id"],
                baseline,
                selection["selection_id"],
                "Separated Face",
            ),
            int(begin["scene_generation"]),
        )
        assert separated["source_counts"]["faces"] == baseline["counts"]["faces"] - 1
        assert separated["separated_counts"]["faces"] == 1
        assert separated["source_component_map"]["branch_role"] == "SOURCE"
        assert separated["separated_component_map"]["branch_role"] == "SEPARATED"
        assert separated["source_component_map"]["separation_id"] == separated[
            "separated_component_map"
        ]["separation_id"]
        rollback = await mutate(
            client,
            "transaction.rollback",
            {"transaction_id": begin["transaction_id"]},
            int(separated["scene_generation"]),
        )
        restored = await inspect(client, "Topology Split")
        assert rollback["status"] == "rolled_back"
        assert restored["mesh_fingerprint"] == baseline["mesh_fingerprint"]
        missing = await client.call(
            "scene.inspect",
            {"kinds": ["objects"], "name_filter": "Separated Face", "limit": 10},
            read_only=True,
        )
        assert missing["objects"] == []
        report["separation_rollback"] = {
            "baseline": baseline,
            "selection": selection,
            "separated": separated,
            "rollback": rollback,
            "restored": restored,
        }

        stage("declarative separation, topology, remap, validation, and disconnect")
        batch_begin = await mutate(
            client,
            "transaction.begin",
            {"label": "0.13.1 declarative batch", "viewport_id": None},
            int(restored["scene_generation"]),
        )
        batch_result = await mutate(
            client,
            "mesh.batch.execute",
            {
                "transaction_id": batch_begin["transaction_id"],
                "targets": [batch_target("source", restored)],
                "inputs": [],
                "steps": [
                    {
                        "type": "selection_query",
                        "target_alias": "source",
                        "output_alias": "face",
                        "domain": "FACE",
                        "query": {"type": "indices", "indices": [0]},
                    },
                    {
                        "type": "mesh_separate",
                        "target_alias": "source",
                        "selection_alias": "face",
                        "new_target_alias": "patch",
                        "new_selection_alias": "patch_faces",
                        "source_map_alias": "source_map",
                        "separated_map_alias": "patch_map",
                        "new_object_name": "Batch Patch",
                    },
                    {
                        "type": "selection_query",
                        "target_alias": "patch",
                        "output_alias": "patch_edges",
                        "domain": "EDGE",
                        "query": {"type": "all"},
                    },
                    {
                        "type": "mesh_edit",
                        "target_alias": "patch",
                        "data_scope": "OBJECT",
                        "operation": {
                            "type": "subdivide",
                            "selection_alias": "patch_edges",
                            "cuts": 1,
                        },
                        "map_alias": "subdivide_map",
                    },
                    {
                        "type": "mesh_validate",
                        "selection_alias": "patch_faces",
                        "check": "DEGENERATE",
                        "output_alias": "degenerate",
                        "assertions": [{"type": "count_at_most", "value": 0}],
                    },
                ],
                "on_error": "ROLLBACK_TRANSACTION",
            },
            int(batch_begin["scene_generation"]),
        )
        assert batch_result["scene_generation"] == int(batch_begin["scene_generation"]) + 1
        assert len(batch_result["step_reports"]) == 5
        assert batch_result["aliases"]["patch"]["kind"] == "target"
        assert batch_result["aliases"]["patch_faces"]["kind"] == "selection"
        assert batch_result["target_branches"]["patch"]["composed_component_map_id"]
        await client.close()
        await asyncio.sleep(3.0)
        batch_restored = await inspect(client, "Topology Split")
        assert batch_restored["mesh_fingerprint"] == restored["mesh_fingerprint"]
        batch_status = await client.call("project.status", read_only=True)
        assert batch_status["active_transaction"] is None
        missing_batch_patch = await client.call(
            "scene.inspect",
            {"kinds": ["objects"], "name_filter": "Batch Patch", "limit": 10},
            read_only=True,
        )
        assert missing_batch_patch["objects"] == []
        report["batch_disconnect_rollback"] = {
            "begin": batch_begin,
            "result": batch_result,
            "project_status": batch_status,
            "restored": batch_restored,
        }

        stage("runtime assertion failure restores the transaction begin baseline")
        failure_baseline = batch_restored
        failure_begin = await mutate(
            client,
            "transaction.begin",
            {"label": "0.13.1 batch whole transaction rollback", "viewport_id": None},
            int(failure_baseline["scene_generation"]),
        )
        before_batch_write = await mutate(
            client,
            "mesh.edit",
            {
                **batch_target("source", failure_baseline),
                "transaction_id": failure_begin["transaction_id"],
                "data_scope": "OBJECT",
                "operation": {
                    "type": "transform",
                    "target": {"type": "vertices", "indices": [0]},
                    "translation": {"x": 0.0, "y": 0.0, "z": 0.125},
                },
            },
            int(failure_begin["scene_generation"]),
        )
        written = await inspect(client, "Topology Split")
        assert written["mesh_fingerprint"] != failure_baseline["mesh_fingerprint"]
        failure_error = None
        try:
            await mutate(
                client,
                "mesh.batch.execute",
                {
                    "transaction_id": failure_begin["transaction_id"],
                    "targets": [batch_target("source", written)],
                    "inputs": [],
                    "steps": [
                        {
                            "type": "selection_query",
                            "target_alias": "source",
                            "output_alias": "faces",
                            "domain": "FACE",
                            "query": {"type": "all"},
                        },
                        {
                            "type": "mesh_validate",
                            "selection_alias": "faces",
                            "check": "ORIENTATION",
                            "output_alias": "orientation",
                            "assertions": [{"type": "count_at_most", "value": 0}],
                        },
                    ],
                    "on_error": "ROLLBACK_TRANSACTION",
                },
                int(before_batch_write["scene_generation"]),
            )
        except BridgeError as exc:
            assert exc.error.code == "MESH_BATCH_ASSERTION_FAILED"
            assert exc.error.details["rollback"]["status"] == "rolled_back"
            failure_error = exc.error.model_dump(mode="json")
        else:
            raise AssertionError("Expected the batch assertion to fail")
        failed_restored = await inspect(client, "Topology Split")
        assert failed_restored["mesh_fingerprint"] == failure_baseline["mesh_fingerprint"]
        report["batch_assertion_rollback"] = {
            "before_batch_write": before_batch_write,
            "error": failure_error,
            "restored": failed_restored,
        }

        stage("shared Mesh target becomes single-user while its peer remains unchanged")
        duplicate_begin = await mutate(
            client,
            "transaction.begin",
            {"label": "0.13.1 shared peer", "viewport_id": None},
            int(failed_restored["scene_generation"]),
        )
        duplicate = await mutate(
            client,
            "object.duplicate",
            {
                "transaction_id": duplicate_begin["transaction_id"],
                "source_name": "Topology Split",
                "expected_source_identity": restored["object"]["session_identity"],
                "name": "Topology Split Peer",
                "linked_data": True,
                "collection_name": None,
                "expected_collection_identity": None,
                "transform": None,
            },
            int(duplicate_begin["scene_generation"]),
        )
        await mutate(
            client,
            "transaction.commit",
            {"transaction_id": duplicate_begin["transaction_id"]},
            int(duplicate["scene_generation"]),
        )
        shared = await inspect(client, "Topology Split")
        peer = await inspect(client, "Topology Split Peer")
        assert shared["mesh"]["users"] == 2
        shared_selection = await select_face(client, shared)
        shared_begin = await mutate(
            client,
            "transaction.begin",
            {"label": "0.13.1 shared separation", "viewport_id": None},
            int(shared_selection["scene_generation"]),
        )
        shared_result = await mutate(
            client,
            "mesh.separate",
            exact_params(
                shared_begin["transaction_id"],
                shared,
                shared_selection["selection_id"],
                "Shared Separated Face",
            ),
            int(shared_begin["scene_generation"]),
        )
        live_peer = await inspect(client, "Topology Split Peer")
        assert live_peer["mesh"]["session_identity"] == peer["mesh"]["session_identity"]
        assert live_peer["mesh_fingerprint"] == peer["mesh_fingerprint"]
        assert shared_result["source_mesh"]["session_identity"] != shared[
            "mesh"
        ]["session_identity"]
        await mutate(
            client,
            "transaction.rollback",
            {"transaction_id": shared_begin["transaction_id"]},
            int(shared_result["scene_generation"]),
        )
        shared_restored = await inspect(client, "Topology Split")
        assert shared_restored["mesh"]["session_identity"] == shared["mesh"][
            "session_identity"
        ]
        assert shared_restored["mesh_fingerprint"] == shared["mesh_fingerprint"]
        report["shared_separation"] = {
            "source_before": shared,
            "peer_before": peer,
            "result": shared_result,
            "peer_during": live_peer,
            "source_restored": shared_restored,
        }

        stage("cross-transaction ComponentMap composition")
        chain_baseline = await inspect(client, "Topology Chain")
        chain_selection = await selection_query(
            client, chain_baseline, "EDGE", {"type": "all"}
        )
        first_begin = await mutate(
            client,
            "transaction.begin",
            {"label": "0.13.1 composed map step 1", "viewport_id": None},
            int(chain_selection["scene_generation"]),
        )
        first_edit = await mutate(
            client,
            "mesh.edit",
            edit_params(
                first_begin["transaction_id"],
                chain_baseline,
                {
                    "type": "subdivide",
                    "selection_id": chain_selection["selection_id"],
                    "cuts": 1,
                },
            ),
            int(first_begin["scene_generation"]),
        )
        first_commit = await mutate(
            client,
            "transaction.commit",
            {"transaction_id": first_begin["transaction_id"]},
            int(first_edit["scene_generation"]),
        )
        chain_middle = await inspect(client, "Topology Chain")
        second_begin = await mutate(
            client,
            "transaction.begin",
            {"label": "0.13.1 composed map step 2", "viewport_id": None},
            int(chain_middle["scene_generation"]),
        )
        second_edit = await mutate(
            client,
            "mesh.edit",
            edit_params(
                second_begin["transaction_id"],
                chain_middle,
                {
                    "type": "subdivide",
                    "selection_id": first_edit["rebound_selection"]["selection_id"],
                    "cuts": 1,
                },
            ),
            int(second_begin["scene_generation"]),
        )
        second_commit = await mutate(
            client,
            "transaction.commit",
            {"transaction_id": second_begin["transaction_id"]},
            int(second_edit["scene_generation"]),
        )
        composed = await client.call(
            "mesh.component_map.compose",
            {
                "component_map_ids": [
                    first_edit["component_map"]["component_map_id"],
                    second_edit["component_map"]["component_map_id"],
                ]
            },
            read_only=True,
        )
        assert composed["component_map"]["map_kind"] == "COMPOSED"
        assert composed["component_map"]["step_count"] == 2
        assert len(composed["component_map"]["transaction_ids"]) == 2
        report["cross_transaction_composition"] = {
            "baseline": chain_baseline,
            "first_edit": first_edit,
            "first_commit": first_commit,
            "second_edit": second_edit,
            "second_commit": second_commit,
            "composed": composed,
        }

        stage("successful batch adopted by native save and persisted through reload")
        saved_baseline = await inspect(client, "Topology Bisect")
        saved_begin = await mutate(
            client,
            "transaction.begin",
            {"label": "0.13.1 native saved batch", "viewport_id": None},
            int(saved_baseline["scene_generation"]),
        )
        saved_batch = await mutate(
            client,
            "mesh.batch.execute",
            {
                "transaction_id": saved_begin["transaction_id"],
                "targets": [batch_target("source", saved_baseline)],
                "inputs": [],
                "steps": [
                    {
                        "type": "selection_query",
                        "target_alias": "source",
                        "output_alias": "face",
                        "domain": "FACE",
                        "query": {"type": "indices", "indices": [0]},
                    },
                    {
                        "type": "mesh_separate",
                        "target_alias": "source",
                        "selection_alias": "face",
                        "new_target_alias": "patch",
                        "new_selection_alias": "patch_faces",
                        "source_map_alias": "source_map",
                        "separated_map_alias": "patch_map",
                        "new_object_name": "Batch Saved Patch",
                    },
                ],
                "on_error": "ROLLBACK_TRANSACTION",
            },
            int(saved_begin["scene_generation"]),
        )
        native_save = await client.call(
            "_test.native_save", {"path": str(project)}, read_only=True
        )
        assert native_save["last_user_action"]["status"] == "succeeded"
        reload_result = await manager.project_reload(
            save_current=False, use_scripts=False, load_ui=False
        )
        persisted_patch = await inspect(client, "Batch Saved Patch")
        assert persisted_patch["counts"]["faces"] == 1
        report["native_save_persistence"] = {
            "begin": saved_begin,
            "batch": saved_batch,
            "native_save": native_save,
            "reload": reload_result,
            "persisted_patch": persisted_patch,
        }

        ping_after = await client.call("connection.ping", read_only=True)
        if int(ping_after["heartbeat"]) <= int(ping["heartbeat"]):
            raise RuntimeError("Blender heartbeat did not advance")
        report["ping_after"] = ping_after
        report["fixture_source_sha256_after"] = sha256(source)
        report["fixture_source_unchanged"] = (
            report["fixture_source_sha256_after"] == source_hash
        )
        if not report["fixture_source_unchanged"]:
            raise RuntimeError("The source topology fixture changed during acceptance")
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
    parser.add_argument("--port", type=int, default=9891)
    parser.add_argument("--timeout", type=float, default=120.0)
    report = asyncio.run(run(parser.parse_args()))
    report_path = Path(report["artifact_directory"]) / f"report-{PACKAGE_VERSION}.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(report_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
