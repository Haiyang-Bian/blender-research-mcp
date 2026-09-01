"""Run Blender 4.2 materialize, disconnected extraction, and rig-binding acceptance."""

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
from blender_research_mcp.constants import MAX_DEADLINE_MS, PACKAGE_VERSION
from blender_research_mcp.errors import BridgeError
from blender_research_mcp.lifecycle import ApplicationManager
from blender_research_mcp.rendering import request_render_preview

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_BUILDER = ROOT / "scripts" / "create_modular_fixture.py"


def stage(name: str) -> None:
    print(f"[0.15 smoke] {name}", flush=True)


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
        raise RuntimeError("Could not build the 0.15 modular fixture")


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
            deadline_ms=MAX_DEADLINE_MS,
            expected_scene_generation=generation,
            idempotency_key=str(uuid4()),
            read_only=False,
        )
    except BridgeError as exc:
        print(json.dumps(exc.error.model_dump(mode="json"), indent=2), flush=True)
        raise


async def mesh(client: BridgeClient, name: str) -> dict[str, Any]:
    return await client.call(
        "mesh.inspect",
        {"object_name": name, "component": "summary", "offset": 0, "limit": 256},
        deadline_ms=MAX_DEADLINE_MS,
        read_only=True,
    )


async def weights(client: BridgeClient, name: str) -> dict[str, Any]:
    return await client.call(
        "mesh.weights.inspect",
        {
            "object_name": name,
            "group_name": None,
            "component": "SUMMARY",
            "offset": 0,
            "limit": 256,
        },
        deadline_ms=MAX_DEADLINE_MS,
        read_only=True,
    )


def materialize_source(value: dict[str, Any]) -> dict[str, Any]:
    return {
        "object_name": value["object"]["name"],
        "expected_object_identity": value["object"]["session_identity"],
        "expected_mesh_identity": value["mesh"]["session_identity"],
        "expected_mesh_revision_id": value["mesh_revision_id"],
    }


def extract_target(value: dict[str, Any]) -> dict[str, Any]:
    return {
        "object_name": value["object"]["name"],
        "expected_object_identity": value["object"]["session_identity"],
        "expected_mesh_identity": value["mesh"]["session_identity"],
        "expected_mesh_users": value["mesh"]["users"],
        "expected_mesh_user_objects": [
            {
                "object_name": item["object_name"],
                "expected_object_identity": item["session_identity"],
            }
            for item in value["user_objects"]
        ],
        "expected_mesh_fingerprint": value["mesh_fingerprint"],
    }


async def face_selection(
    client: BridgeClient,
    value: dict[str, Any],
    indices: list[int],
) -> dict[str, Any]:
    return await client.call(
        "mesh.selection.query",
        {
            **extract_target(value),
            "expected_mesh_revision_id": value["mesh_revision_id"],
            "domain": "FACE",
            "query": {"type": "indices", "indices": indices},
        },
        deadline_ms=MAX_DEADLINE_MS,
        read_only=True,
    )


async def material_face_selection(
    client: BridgeClient,
    value: dict[str, Any],
    slot_indices: list[int],
) -> dict[str, Any]:
    return await client.call(
        "mesh.selection.query",
        {
            **extract_target(value),
            "expected_mesh_revision_id": value["mesh_revision_id"],
            "domain": "FACE",
            "query": {"type": "material", "slot_indices": slot_indices},
        },
        deadline_ms=MAX_DEADLINE_MS,
        read_only=True,
    )


async def object_exists(client: BridgeClient, name: str) -> bool:
    result = await client.call(
        "scene.inspect",
        {"kinds": ["objects"], "name_filter": name, "limit": 20},
        read_only=True,
    )
    return any(item["name"] == name for item in result["objects"])


async def begin(client: BridgeClient, generation: int, label: str) -> dict[str, Any]:
    del generation
    ping = await client.call("connection.ping", read_only=True)
    return await mutate(
        client,
        "transaction.begin",
        {"label": label, "viewport_id": None},
        int(ping["scene_generation"]),
    )


async def materialize(
    client: BridgeClient,
    transaction_id: str,
    source: dict[str, Any],
    evaluation: dict[str, Any],
    name: str,
    generation: int,
    *,
    copy_weights: bool = True,
) -> dict[str, Any]:
    return await mutate(
        client,
        "mesh.materialize",
        {
            "transaction_id": transaction_id,
            "source": materialize_source(source),
            "evaluation": evaluation,
            "new_object_name": name,
            "copy": {"materials": True, "uv": True, "weights": copy_weights},
            "collection_name": None,
            "expected_collection_identity": None,
        },
        generation,
    )


async def execute_fixture_chain(
    client: BridgeClient,
    transaction_id: str,
    source: dict[str, Any],
    shape_fingerprint: str,
    working_name: str,
    module_name: str,
    generation: int,
) -> dict[str, Any]:
    working = await materialize(
        client,
        transaction_id,
        source,
        {
            "type": "SHAPE_KEYS_CURRENT",
            "expected_shape_key_state_fingerprint": shape_fingerprint,
        },
        working_name,
        generation,
    )
    working_mesh = await mesh(client, working_name)
    selected = await face_selection(client, working_mesh, [0, 2])
    exact_extract = {
        **extract_target(working_mesh),
        "selection_id": selected["selection_id"],
        "new_object_name": module_name,
        "output_policy": {
            "parent": "CLEAR_KEEP_WORLD",
            "modifiers": "DROP",
            "material_slots": "COMPACT",
        },
        "source_attribute_policy": {
            "uv": "PRESERVE_INTERPOLATE",
            "weights": "PRESERVE_INTERPOLATE",
        },
        "extracted_attribute_policy": {
            "uv": "PRESERVE_INTERPOLATE",
            "weights": "PRESERVE_INTERPOLATE",
        },
        "collection_name": None,
        "expected_collection_identity": None,
    }
    preflight = await client.call(
        "mesh.extract.preflight", exact_extract, deadline_ms=MAX_DEADLINE_MS, read_only=True
    )
    extracted = await mutate(
        client,
        "mesh.extract",
        {"transaction_id": transaction_id, **exact_extract},
        int(working["scene_generation"]),
    )
    module_mesh = await mesh(client, module_name)
    module_weights = await weights(client, module_name)
    rig = await client.call(
        "rig.inspect",
        {
            "object_name": "Modular Source",
            "armature_object_name": "Modular Rig",
            "offset": 0,
            "limit": 256,
        },
        read_only=True,
    )
    armature = rig["armatures"][0]
    bound = await mutate(
        client,
        "rig.bind",
        {
            "transaction_id": transaction_id,
            "mesh_target": {
                "object_name": module_name,
                "expected_object_identity": module_mesh["object"]["session_identity"],
                "expected_mesh_identity": module_mesh["mesh"]["session_identity"],
                "expected_mesh_revision_id": module_mesh["mesh_revision_id"],
                "expected_group_schema_fingerprint": module_weights[
                    "group_schema_fingerprint"
                ],
                "expected_weights_fingerprint": module_weights["weights_fingerprint"],
            },
            "armature_target": {
                "object_name": armature["object_name"],
                "expected_object_identity": armature["object_identity"],
                "expected_data_identity": armature["data_identity"],
                "expected_bone_schema_fingerprint": armature["bone_schema_fingerprint"],
            },
            "modifier": {
                "name": f"{module_name} Armature",
                "expected_existing": None,
                "use_vertex_groups": True,
                "use_bone_envelopes": False,
                "preserve_volume": True,
                "use_multi_modifier": False,
                "vertex_group": None,
            },
            "parenting": "KEEP_WORLD",
            "group_scope": {"type": "ALL_MATCHED"},
        },
        int(extracted["scene_generation"]),
    )
    rebound = await mutate(
        client,
        "rig.bind",
        {
            "transaction_id": transaction_id,
            "mesh_target": {
                "object_name": module_name,
                "expected_object_identity": module_mesh["object"]["session_identity"],
                "expected_mesh_identity": module_mesh["mesh"]["session_identity"],
                "expected_mesh_revision_id": module_mesh["mesh_revision_id"],
                "expected_group_schema_fingerprint": module_weights[
                    "group_schema_fingerprint"
                ],
                "expected_weights_fingerprint": module_weights["weights_fingerprint"],
            },
            "armature_target": {
                "object_name": armature["object_name"],
                "expected_object_identity": armature["object_identity"],
                "expected_data_identity": armature["data_identity"],
                "expected_bone_schema_fingerprint": armature["bone_schema_fingerprint"],
            },
            "modifier": {
                "name": f"{module_name} Armature",
                "expected_existing": {
                    "name": bound["modifier"]["name"],
                    "expected_identity": bound["modifier"]["session_identity"],
                    "expected_stack_index": bound["modifier"]["stack_index"],
                    "expected_stack_fingerprint": bound["modifier_stack_fingerprint"],
                },
                "use_vertex_groups": True,
                "use_bone_envelopes": False,
                "preserve_volume": True,
                "use_multi_modifier": False,
                "vertex_group": None,
            },
            "parenting": "KEEP_WORLD",
            "group_scope": {"type": "ALL_MATCHED"},
        },
        int(bound["scene_generation"]),
    )
    if rebound["changed"] is not False:
        raise RuntimeError("Repeating an exact rig binding must be a no-op")
    return {
        "materialize": working,
        "preflight": preflight,
        "extract": extracted,
        "bind": bound,
        "repeat_bind": rebound,
        "scene_generation": int(rebound["scene_generation"]),
    }


async def run(args: argparse.Namespace) -> dict[str, Any]:
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + f"-{uuid4().hex[:8]}"
    temporary = Path(tempfile.gettempdir()) / "blender-research-mcp-modular" / run_id
    temporary.mkdir(parents=True, exist_ok=False)
    artifact_directory = ROOT / "artifacts" / "live-smoke" / run_id
    artifact_directory.mkdir(parents=True, exist_ok=False)
    source_path = temporary / "modular-source.blend"
    project_path = temporary / "modular-project.blend"
    build_fixture(args.blender_executable, source_path)
    source_hash = sha256(source_path)
    shutil.copy2(source_path, project_path)
    report: dict[str, Any] = {
        "run_id": run_id,
        "started_at": datetime.now(UTC).isoformat(),
        "server_version": PACKAGE_VERSION,
        "port": args.port,
        "temporary_root": str(temporary),
        "artifact_directory": str(artifact_directory),
        "fixture_source": str(source_path),
        "fixture_source_sha256_before": source_hash,
        "fixture_project": str(project_path),
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
        stage("managed launch, open, and capability handshake")
        report["launch"] = await manager.launch()
        launched = True
        report["project_open"] = await manager.project_open(
            str(project_path), save_current=False, use_scripts=False, load_ui=False
        )
        ping = await client.call("connection.ping", read_only=True)
        report["ping_before"] = ping
        for capability, minimum in {
            "transactions": 10,
            "mesh_component_map": 3,
            "mesh_materialization": 1,
            "mesh_extraction": 1,
            "rig_binding": 1,
        }.items():
            if int(ping["capability_versions"].get(capability, 0)) < minimum:
                raise RuntimeError(f"Missing capability {capability}:{minimum}")

        baseline = await mesh(client, "Modular Source")
        baseline_weights = await weights(client, "Modular Source")
        report["baseline_weights"] = baseline_weights
        shape_fingerprint = baseline["shape_key_state_fingerprint"]

        stage("BASE, SHAPE_KEYS_CURRENT, and FINAL_EVALUATED rollback")
        mode_results = {}
        for label, evaluation, copy_weights in (
            ("base", {"type": "BASE"}, True),
            (
                "shape",
                {
                    "type": "SHAPE_KEYS_CURRENT",
                    "expected_shape_key_state_fingerprint": shape_fingerprint,
                },
                True,
            ),
        ):
            transaction = await begin(client, int(baseline["scene_generation"]), f"0.15 {label}")
            result = await materialize(
                client,
                transaction["transaction_id"],
                baseline,
                evaluation,
                f"Materialized {label}",
                int(transaction["scene_generation"]),
                copy_weights=copy_weights,
            )
            output = await mesh(client, f"Materialized {label}")
            assert output["mesh"]["shape_keys"] is False
            assert result["component_map"]["map_kind"] == "MATERIALIZATION"
            assert result["output_object"]["collections"]
            rollback = await mutate(
                client,
                "transaction.rollback",
                {"transaction_id": transaction["transaction_id"]},
                int(result["scene_generation"]),
            )
            assert not await object_exists(client, f"Materialized {label}")
            mode_results[label] = {"result": result, "output": output, "rollback": rollback}

        surface = await client.call(
            "mesh.surface.prepare",
            {
                "object_name": baseline["object"]["name"],
                "expected_object_identity": baseline["object"]["session_identity"],
                "expected_mesh_revision_id": baseline["mesh_revision_id"],
                "geometry": "EVALUATED",
            },
            deadline_ms=MAX_DEADLINE_MS,
            read_only=True,
        )
        final_begin = await begin(client, int(surface["scene_generation"]), "0.15 final")
        final = await materialize(
            client,
            final_begin["transaction_id"],
            baseline,
            {"type": "FINAL_EVALUATED", "surface_id": surface["surface_id"]},
            "Materialized final",
            int(final_begin["scene_generation"]),
            copy_weights=False,
        )
        assert final["evaluation"]["armature"] is True
        assert final["component_map"] is None
        assert final["topology_identical"] is False
        final_rollback = await mutate(
            client,
            "transaction.rollback",
            {"transaction_id": final_begin["transaction_id"]},
            int(final["scene_generation"]),
        )
        mode_results["final"] = {"surface": surface, "result": final}
        report["evaluation_modes"] = mode_results

        stage("materialize, disconnected extract, bind, and whole-chain rollback")
        chain_begin = await begin(
            client,
            int(final_rollback["scene_generation"]),
            "0.15 modular chain rollback",
        )
        working = await materialize(
            client,
            chain_begin["transaction_id"],
            baseline,
            {
                "type": "SHAPE_KEYS_CURRENT",
                "expected_shape_key_state_fingerprint": shape_fingerprint,
            },
            "Working Mesh",
            int(chain_begin["scene_generation"]),
        )
        working_mesh = await mesh(client, "Working Mesh")
        selected = await face_selection(client, working_mesh, [0, 2])
        common_extract = {
            **extract_target(working_mesh),
            "selection_id": selected["selection_id"],
            "new_object_name": "Extracted Module",
            "output_policy": {
                "parent": "CLEAR_KEEP_WORLD",
                "modifiers": "DROP",
                "material_slots": "COMPACT",
            },
            "source_attribute_policy": {
                "uv": "PRESERVE_INTERPOLATE",
                "weights": "PRESERVE_INTERPOLATE",
            },
            "extracted_attribute_policy": {
                "uv": "PRESERVE_INTERPOLATE",
                "weights": "PRESERVE_INTERPOLATE",
            },
            "collection_name": None,
            "expected_collection_identity": None,
        }
        preflight = await client.call(
            "mesh.extract.preflight", common_extract, deadline_ms=MAX_DEADLINE_MS, read_only=True
        )
        assert preflight["connected_components"] == 2
        extracted = await mutate(
            client,
            "mesh.extract",
            {"transaction_id": chain_begin["transaction_id"], **common_extract},
            int(working["scene_generation"]),
        )
        assert extracted["connected_components"] == 2
        assert extracted["source_counts"]["faces"] == 1
        assert extracted["extracted_counts"]["faces"] == 2
        extracted_mesh = await mesh(client, "Extracted Module")
        extracted_weights = await weights(client, "Extracted Module")
        rig = await client.call(
            "rig.inspect",
            {
                "object_name": "Modular Source",
                "armature_object_name": "Modular Rig",
                "offset": 0,
                "limit": 256,
            },
            read_only=True,
        )
        armature = rig["armatures"][0]
        bound = await mutate(
            client,
            "rig.bind",
            {
                "transaction_id": chain_begin["transaction_id"],
                "mesh_target": {
                    "object_name": "Extracted Module",
                    "expected_object_identity": extracted_mesh["object"]["session_identity"],
                    "expected_mesh_identity": extracted_mesh["mesh"]["session_identity"],
                    "expected_mesh_revision_id": extracted_mesh["mesh_revision_id"],
                    "expected_group_schema_fingerprint": extracted_weights[
                        "group_schema_fingerprint"
                    ],
                    "expected_weights_fingerprint": extracted_weights["weights_fingerprint"],
                },
                "armature_target": {
                    "object_name": armature["object_name"],
                    "expected_object_identity": armature["object_identity"],
                    "expected_data_identity": armature["data_identity"],
                    "expected_bone_schema_fingerprint": armature[
                        "bone_schema_fingerprint"
                    ],
                },
                "modifier": {
                    "name": "Module Armature",
                    "expected_existing": None,
                    "use_vertex_groups": True,
                    "use_bone_envelopes": False,
                    "preserve_volume": True,
                    "use_multi_modifier": False,
                    "vertex_group": None,
                },
                "parenting": "KEEP_WORLD",
                "group_scope": {"type": "ALL_MATCHED"},
            },
            int(extracted["scene_generation"]),
        )
        rollback = await mutate(
            client,
            "transaction.rollback",
            {"transaction_id": chain_begin["transaction_id"]},
            int(bound["scene_generation"]),
        )
        assert not await object_exists(client, "Working Mesh")
        assert not await object_exists(client, "Extracted Module")
        restored_source = await mesh(client, "Modular Source")
        assert restored_source["mesh_fingerprint"] == baseline["mesh_fingerprint"]
        assert restored_source["shape_key_state_fingerprint"] == shape_fingerprint
        report["chain_rollback"] = {
            "preflight": preflight,
            "materialize": working,
            "extract": extracted,
            "bind": bound,
            "rollback": rollback,
        }

        stage("disconnect rollback")
        disconnect_begin = await begin(
            client, int(rollback["scene_generation"]), "0.15 disconnect rollback"
        )
        disconnect_write = await execute_fixture_chain(
            client,
            disconnect_begin["transaction_id"],
            baseline,
            shape_fingerprint,
            "Disconnect Working",
            "Disconnect Module",
            int(disconnect_begin["scene_generation"]),
        )
        await client.close()
        await asyncio.sleep(3.0)
        assert not await object_exists(client, "Disconnect Working")
        assert not await object_exists(client, "Disconnect Module")
        report["disconnect"] = {"write": disconnect_write, "rolled_back": True}

        stage("native-save adoption")
        current = await mesh(client, "Modular Source")
        native_begin = await begin(client, int(current["scene_generation"]), "0.15 native save")
        native_write = await execute_fixture_chain(
            client,
            native_begin["transaction_id"],
            current,
            current["shape_key_state_fingerprint"],
            "Native Saved Working",
            "Native Saved Module",
            int(native_begin["scene_generation"]),
        )
        native_save = await client.call("_test.native_save", {}, read_only=False)
        assert await object_exists(client, "Native Saved Working")
        assert await object_exists(client, "Native Saved Module")
        report["native_save"] = {"write": native_write, "save": native_save}

        stage("commit, save, reload, and render evidence")
        current = await mesh(client, "Modular Source")
        commit_begin = await begin(client, int(current["scene_generation"]), "0.15 commit")
        committed = await execute_fixture_chain(
            client,
            commit_begin["transaction_id"],
            current,
            current["shape_key_state_fingerprint"],
            "Committed Working Mesh",
            "Committed Extracted Module",
            int(commit_begin["scene_generation"]),
        )
        commit = await mutate(
            client,
            "transaction.commit",
            {"transaction_id": commit_begin["transaction_id"]},
            int(committed["scene_generation"]),
        )
        save = await manager.project_save()
        reload_result = await manager.project_reload(
            save_current=False,
            use_scripts=False,
            load_ui=False,
        )
        persisted = await mesh(client, "Committed Working Mesh")
        persisted_module = await mesh(client, "Committed Extracted Module")
        persisted_rig = await client.call(
            "rig.inspect",
            {
                "object_name": "Committed Extracted Module",
                "armature_object_name": "Modular Rig",
                "offset": 0,
                "limit": 256,
            },
            read_only=True,
        )
        assert len(persisted_rig["armature_modifiers"]) == 1
        camera = await client.call(
            "object.inspect", {"object_name": "Modular Camera"}, read_only=True
        )
        image, render = await request_render_preview(
            client,
            {
                "camera_name": "Modular Camera",
                "expected_camera_identity": camera["session_identity"],
                "width": 384,
                "height": 384,
                "samples": 8,
                "transparent": False,
            },
            expected_scene_generation=int(persisted["scene_generation"]),
            idempotency_key=str(uuid4()),
        )
        render_path = artifact_directory / "modular-0.15.png"
        render_path.write_bytes(image)
        report["persistence"] = {
            "commit": commit,
            "save": save,
            "reload": reload_result,
            "persisted": persisted,
            "persisted_module": persisted_module,
            "persisted_rig": persisted_rig,
            "render": render,
            "render_path": str(render_path),
            "render_sha256": hashlib.sha256(image).hexdigest(),
        }

        if args.character_project is not None:
            stage("real Shape-Key character materialize, hair extract, and rig rollback")
            character_source = args.character_project.resolve(strict=True)
            character_hash = sha256(character_source)
            character_project = temporary / "test-model.blend"
            shutil.copy2(character_source, character_project)
            character_open = await manager.project_open(
                str(character_project),
                save_current=False,
                use_scripts=False,
                load_ui=False,
            )
            character_baseline = await mesh(client, "绯雪_edit_mesh")
            if not character_baseline["mesh"]["shape_keys"]:
                raise RuntimeError("Character fixture no longer contains Shape Keys")
            character_rig = await client.call(
                "rig.inspect",
                {
                    "object_name": "绯雪_edit_mesh",
                    "armature_object_name": "绯雪_edit_arm",
                    "offset": 0,
                    "limit": 256,
                },
                deadline_ms=MAX_DEADLINE_MS,
                read_only=True,
            )
            transaction = await begin(
                client,
                int(character_baseline["scene_generation"]),
                "0.15 character module rollback",
            )
            character_working = await materialize(
                client,
                transaction["transaction_id"],
                character_baseline,
                {
                    "type": "SHAPE_KEYS_CURRENT",
                    "expected_shape_key_state_fingerprint": character_baseline[
                        "shape_key_state_fingerprint"
                    ],
                },
                "绯雪_0.15_工作副本",
                int(transaction["scene_generation"]),
            )
            working_mesh = await mesh(client, "绯雪_0.15_工作副本")
            hair_slots = [
                int(item["slot_index"])
                for item in working_mesh["mesh"]["material_slots"]
                if "Hair" in str(item.get("material_name") or "")
            ]
            if not hair_slots:
                raise RuntimeError("Character fixture exposes no semantically named hair slots")
            hair_selection = await material_face_selection(client, working_mesh, hair_slots)
            character_extract = {
                **extract_target(working_mesh),
                "selection_id": hair_selection["selection_id"],
                "new_object_name": "绯雪_0.15_头发模块",
                "output_policy": {
                    "parent": "CLEAR_KEEP_WORLD",
                    "modifiers": "DROP",
                    "material_slots": "COMPACT",
                },
                "source_attribute_policy": {
                    "uv": "PRESERVE_INTERPOLATE",
                    "weights": "PRESERVE_INTERPOLATE",
                },
                "extracted_attribute_policy": {
                    "uv": "PRESERVE_INTERPOLATE",
                    "weights": "PRESERVE_INTERPOLATE",
                },
                "collection_name": None,
                "expected_collection_identity": None,
            }
            character_preflight = await client.call(
                "mesh.extract.preflight",
                character_extract,
                deadline_ms=MAX_DEADLINE_MS,
                read_only=True,
            )
            if int(character_preflight["connected_components"]) < 2:
                raise RuntimeError("Hair material query no longer yields multiple components")
            character_extracted = await mutate(
                client,
                "mesh.extract",
                {"transaction_id": transaction["transaction_id"], **character_extract},
                int(character_working["scene_generation"]),
            )
            module_mesh = await mesh(client, "绯雪_0.15_头发模块")
            module_weights = await weights(client, "绯雪_0.15_头发模块")
            armature = character_rig["armatures"][0]
            character_bound = await mutate(
                client,
                "rig.bind",
                {
                    "transaction_id": transaction["transaction_id"],
                    "mesh_target": {
                        "object_name": "绯雪_0.15_头发模块",
                        "expected_object_identity": module_mesh["object"]["session_identity"],
                        "expected_mesh_identity": module_mesh["mesh"]["session_identity"],
                        "expected_mesh_revision_id": module_mesh["mesh_revision_id"],
                        "expected_group_schema_fingerprint": module_weights[
                            "group_schema_fingerprint"
                        ],
                        "expected_weights_fingerprint": module_weights[
                            "weights_fingerprint"
                        ],
                    },
                    "armature_target": {
                        "object_name": armature["object_name"],
                        "expected_object_identity": armature["object_identity"],
                        "expected_data_identity": armature["data_identity"],
                        "expected_bone_schema_fingerprint": armature[
                            "bone_schema_fingerprint"
                        ],
                    },
                    "modifier": {
                        "name": "绯雪_0.15_头发绑定",
                        "expected_existing": None,
                        "use_vertex_groups": True,
                        "use_bone_envelopes": False,
                        "preserve_volume": True,
                        "use_multi_modifier": False,
                        "vertex_group": None,
                    },
                    "parenting": "KEEP_WORLD",
                    "group_scope": {"type": "ALL_MATCHED"},
                },
                int(character_extracted["scene_generation"]),
            )
            character_rollback = await mutate(
                client,
                "transaction.rollback",
                {"transaction_id": transaction["transaction_id"]},
                int(character_bound["scene_generation"]),
            )
            restored_character = await mesh(client, "绯雪_edit_mesh")
            if (
                restored_character["mesh_fingerprint"]
                != character_baseline["mesh_fingerprint"]
                or restored_character["shape_key_state_fingerprint"]
                != character_baseline["shape_key_state_fingerprint"]
            ):
                raise RuntimeError("Character source changed after chain rollback")
            if await object_exists(client, "绯雪_0.15_工作副本") or await object_exists(
                client, "绯雪_0.15_头发模块"
            ):
                raise RuntimeError("Character rollback left materialized objects behind")
            character_hash_after = sha256(character_source)
            if character_hash_after != character_hash:
                raise RuntimeError("Source test-model.blend changed during the smoke")
            report["character"] = {
                "source": str(character_source),
                "source_sha256_before": character_hash,
                "source_sha256_after": character_hash_after,
                "project": str(character_project),
                "project_open": character_open,
                "baseline": character_baseline,
                "rig": character_rig,
                "materialize": character_working,
                "hair_material_slots": hair_slots,
                "hair_selection": hair_selection,
                "preflight": character_preflight,
                "extract": character_extracted,
                "bind": character_bound,
                "rollback": character_rollback,
                "restored": restored_character,
            }

        report["fixture_source_sha256_after"] = sha256(source_path)
        if report["fixture_source_sha256_after"] != source_hash:
            raise RuntimeError("Source fixture changed during the smoke")
        report["ping_after"] = await client.call("connection.ping", read_only=True)
        report["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 3)
        report["finished_at"] = datetime.now(UTC).isoformat()
        report_path = artifact_directory / "report-0.15.0.json"
        report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
        stage(f"passed: {report_path}")
        return report
    finally:
        if launched:
            with contextlib.suppress(Exception):
                await manager.quit(save_current=False)
        if previous_hooks is None:
            os.environ.pop("BLENDER_RESEARCH_MCP_TEST_HOOKS", None)
        else:
            os.environ["BLENDER_RESEARCH_MCP_TEST_HOOKS"] = previous_hooks


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--blender-executable", type=Path, required=True)
    parser.add_argument("--port", type=int, default=9895)
    parser.add_argument("--timeout", type=float, default=90.0)
    parser.add_argument("--character-project", type=Path)
    return parser.parse_args()


def main() -> int:
    report = asyncio.run(run(parse_args()))
    print(json.dumps({"run_id": report["run_id"], "elapsed_ms": report["elapsed_ms"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
