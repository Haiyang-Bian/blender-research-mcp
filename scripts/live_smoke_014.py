"""Run focused Blender 4.2 UV and skin-weight authoring acceptance."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import hashlib
import json
import math
import os
import shutil
import subprocess
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from PIL import Image, ImageChops, ImageStat

from blender_research_mcp.client import BridgeClient
from blender_research_mcp.constants import MAX_DEADLINE_MS, PACKAGE_VERSION
from blender_research_mcp.errors import BridgeError
from blender_research_mcp.lifecycle import ApplicationManager
from blender_research_mcp.rendering import request_render_preview

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_BUILDER = ROOT / "scripts" / "create_attribute_fixture.py"


def stage(name: str) -> None:
    print(f"[0.14 smoke] {name}", flush=True)


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
        raise RuntimeError("Could not build the 0.14 attribute fixture")


async def call(
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


async def uv(
    client: BridgeClient,
    name: str,
    layer_name: str | None = "UVMap",
    component: str = "SUMMARY",
    offset: int = 0,
    limit: int = 256,
) -> dict[str, Any]:
    return await client.call(
        "mesh.uv.inspect",
        {
            "object_name": name,
            "layer_name": layer_name,
            "component": component,
            "offset": offset,
            "limit": limit,
        },
        deadline_ms=MAX_DEADLINE_MS,
        read_only=True,
    )


async def weights(
    client: BridgeClient,
    name: str,
    component: str = "SUMMARY",
    offset: int = 0,
    limit: int = 256,
) -> dict[str, Any]:
    return await client.call(
        "mesh.weights.inspect",
        {
            "object_name": name,
            "group_name": None,
            "component": component,
            "offset": offset,
            "limit": limit,
        },
        deadline_ms=MAX_DEADLINE_MS,
        read_only=True,
    )


async def geometry(client: BridgeClient, name: str) -> dict[str, Any]:
    return await client.call(
        "object.geometry.inspect",
        {"object_name": name},
        deadline_ms=MAX_DEADLINE_MS,
        read_only=True,
    )


async def render_checker(
    client: BridgeClient,
    artifacts: Path,
    label: str,
    generation: int,
) -> tuple[bytes, dict[str, Any]]:
    camera = await client.call(
        "object.inspect",
        {"object_name": "Attribute Camera"},
        read_only=True,
    )
    image, evidence = await request_render_preview(
        client,
        {
            "camera_name": "Attribute Camera",
            "expected_camera_identity": camera["session_identity"],
            "width": 384,
            "height": 384,
            "samples": 8,
            "transparent": False,
        },
        expected_scene_generation=generation,
        idempotency_key=str(uuid4()),
    )
    (artifacts / f"uv-checker-{label}.png").write_bytes(image)
    return image, evidence


def exact(value: dict[str, Any]) -> dict[str, Any]:
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


def attribute_exact(
    value: dict[str, Any], weight_evidence: dict[str, Any], scope: str = "OBJECT"
) -> dict[str, Any]:
    return {
        **exact(value),
        "expected_group_schema_fingerprint": weight_evidence["group_schema_fingerprint"],
        "expected_weights_fingerprint": weight_evidence["weights_fingerprint"],
        "data_scope": scope,
    }


async def selection(client: BridgeClient, value: dict[str, Any], domain: str) -> dict[str, Any]:
    return await client.call(
        "mesh.selection.query",
        {
            **exact(value),
            "expected_mesh_revision_id": value["mesh_revision_id"],
            "domain": domain,
            "query": {"type": "all"},
        },
        deadline_ms=MAX_DEADLINE_MS,
        read_only=True,
    )


async def indexed_selection(
    client: BridgeClient,
    value: dict[str, Any],
    domain: str,
    indices: list[int],
) -> dict[str, Any]:
    return await client.call(
        "mesh.selection.query",
        {
            **exact(value),
            "expected_mesh_revision_id": value["mesh_revision_id"],
            "domain": domain,
            "query": {"type": "indices", "indices": indices},
        },
        deadline_ms=MAX_DEADLINE_MS,
        read_only=True,
    )


def layer_ref(value: dict[str, Any]) -> dict[str, str]:
    return {
        "layer_name": value["layer"]["name"],
        "expected_layer_identity": value["layer"]["session_identity"],
    }


def group_ref(value: dict[str, Any], name: str) -> dict[str, str]:
    group = next(item for item in value["groups"] if item["name"] == name)
    return {"group_name": name, "expected_group_identity": group["session_identity"]}


async def run(args: argparse.Namespace) -> dict[str, Any]:
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + f"-{uuid4().hex[:8]}"
    temporary = Path(tempfile.gettempdir()) / "blender-research-mcp-attributes" / run_id
    temporary.mkdir(parents=True, exist_ok=False)
    artifacts = ROOT / "artifacts" / "live-smoke" / run_id
    artifacts.mkdir(parents=True, exist_ok=False)
    source = temporary / "attribute-source.blend"
    project = temporary / "attribute-project.blend"
    build_fixture(args.blender_executable, source)
    source_hash = sha256(source)
    shutil.copy2(source, project)
    report: dict[str, Any] = {
        "run_id": run_id,
        "started_at": datetime.now(UTC).isoformat(),
        "artifact_directory": str(artifacts),
        "temporary_root": str(temporary),
        "fixture_source": str(source),
        "fixture_source_sha256_before": source_hash,
        "fixture_project": str(project),
        "server_version": PACKAGE_VERSION,
        "port": args.port,
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
        stage("managed launch and capability handshake")
        report["launch"] = await manager.launch()
        launched = True
        report["project_open"] = await manager.project_open(
            str(project), save_current=False, use_scripts=False, load_ui=False
        )
        ping = await client.call("connection.ping", read_only=True)
        report["ping_before"] = ping
        required = {
            "transactions": 9,
            "mesh_uv": 1,
            "mesh_weights": 1,
            "mesh_attribute_transfer": 1,
            "mesh_validation": 2,
            "mesh_topology": 4,
            "mesh_separation": 2,
            "mesh_batch": 2,
        }
        for name, version in required.items():
            if int(ping["capability_versions"].get(name, 0)) < version:
                raise RuntimeError(f"Missing capability {name}:{version}")

        stage("official unwrap and pack restore exact UV baseline")
        baseline = await mesh(client, "Attribute Source")
        baseline_uv = await uv(client, "Attribute Source")
        faces = await selection(client, baseline, "FACE")
        checker_before, checker_before_evidence = await render_checker(
            client, artifacts, "before", int(faces["scene_generation"])
        )
        begin = await call(
            client,
            "transaction.begin",
            {"label": "0.14 unwrap pack", "viewport_id": None},
            int(faces["scene_generation"]),
        )
        unwrapped = await call(
            client,
            "mesh.uv.edit",
            {
                **exact(baseline),
                "transaction_id": begin["transaction_id"],
                "expected_uv_fingerprint": baseline_uv["uv_fingerprint"],
                "data_scope": "OBJECT",
                "operation": {
                    "type": "unwrap",
                    "layer": layer_ref(baseline_uv),
                    "selection_id": faces["selection_id"],
                    "method": "ANGLE_BASED",
                    "pin_policy": "RESPECT",
                },
            },
            int(begin["scene_generation"]),
        )
        after_unwrap = await mesh(client, "Attribute Source")
        after_unwrap_uv = await uv(client, "Attribute Source")
        faces_after = await selection(client, after_unwrap, "FACE")
        packed = await call(
            client,
            "mesh.uv.edit",
            {
                **exact(after_unwrap),
                "transaction_id": begin["transaction_id"],
                "expected_uv_fingerprint": after_unwrap_uv["uv_fingerprint"],
                "data_scope": "OBJECT",
                "operation": {
                    "type": "pack",
                    "layer": layer_ref(after_unwrap_uv),
                    "selection_id": faces_after["selection_id"],
                    "tile_u": 1,
                    "pinned_policy": "MOVE",
                },
            },
            int(unwrapped["scene_generation"]),
        )
        after_pack = await mesh(client, "Attribute Source")
        after_pack_uv = await uv(client, "Attribute Source")
        faces_after_pack = await selection(client, after_pack, "FACE")
        transformed = await call(
            client,
            "mesh.uv.edit",
            {
                **exact(after_pack),
                "transaction_id": begin["transaction_id"],
                "expected_uv_fingerprint": after_pack_uv["uv_fingerprint"],
                "data_scope": "OBJECT",
                "operation": {
                    "type": "transform",
                    "layer": layer_ref(after_pack_uv),
                    "selection_id": faces_after_pack["selection_id"],
                    "scope": "ISLANDS",
                    "translation": [0.125, 0.0],
                    "scale": [0.8, 0.8],
                },
            },
            int(packed["scene_generation"]),
        )
        checker_after, checker_after_evidence = await render_checker(
            client, artifacts, "after", int(transformed["scene_generation"])
        )
        with (
            Image.open(artifacts / "uv-checker-before.png") as before_image,
            Image.open(artifacts / "uv-checker-after.png") as after_image,
        ):
            checker_difference = ImageChops.difference(
                before_image.convert("RGB"), after_image.convert("RGB")
            )
            checker_extrema = checker_difference.getextrema()
            checker_mean = ImageStat.Stat(checker_difference).mean
        checker_maximum = max(channel[1] for channel in checker_extrema)
        if checker_maximum <= 0 or max(checker_mean) <= 0.0 or checker_before == checker_after:
            raise RuntimeError("UV checker evidence did not change after the reviewed edit")
        rollback = await call(
            client,
            "transaction.rollback",
            {"transaction_id": begin["transaction_id"]},
            int(transformed["scene_generation"]),
        )
        restored_uv = await uv(client, "Attribute Source")
        assert restored_uv["uv_fingerprint"] == baseline_uv["uv_fingerprint"]
        report["uv_unwrap_pack_rollback"] = {
            "unwrapped": unwrapped,
            "packed": packed,
            "transformed": transformed,
            "rollback": rollback,
            "restored_uv_fingerprint": restored_uv["uv_fingerprint"],
            "checker": {
                "before": checker_before_evidence,
                "after": checker_after_evidence,
                "maximum_channel_difference": checker_maximum,
                "mean_absolute_channel_difference": checker_mean,
            },
        }

        stage("weight edit and exact rollback")
        current = await mesh(client, "Attribute Source")
        current_weights = await weights(client, "Attribute Source")
        geometry_before = await geometry(client, "Attribute Source")
        vertices = await selection(client, current, "VERTEX")
        begin = await call(
            client,
            "transaction.begin",
            {"label": "0.14 weights", "viewport_id": None},
            int(vertices["scene_generation"]),
        )
        edited_weights = await call(
            client,
            "mesh.weights.edit",
            {
                **attribute_exact(current, current_weights),
                "transaction_id": begin["transaction_id"],
                "operation": {
                    "type": "set",
                    "group": group_ref(current_weights, "Bone.L"),
                    "selection_id": vertices["selection_id"],
                    "value": 0.25,
                },
            },
            int(begin["scene_generation"]),
        )
        geometry_during = await geometry(client, "Attribute Source")
        if geometry_before["world_bounds"] == geometry_during["world_bounds"]:
            raise RuntimeError("Weight edit did not change Armature-evaluated geometry")
        rollback = await call(
            client,
            "transaction.rollback",
            {"transaction_id": begin["transaction_id"]},
            int(edited_weights["scene_generation"]),
        )
        restored_weights = await weights(client, "Attribute Source")
        assert restored_weights["weights_fingerprint"] == current_weights["weights_fingerprint"]
        report["weight_rollback"] = {
            "edited": edited_weights,
            "rollback": rollback,
            "restored_weights_fingerprint": restored_weights["weights_fingerprint"],
            "evaluated_geometry_before": geometry_before,
            "evaluated_geometry_during": geometry_during,
        }

        stage("topology and nearest attribute transfer rollback")
        source_mesh = await mesh(client, "Attribute Source")
        source_uv = await uv(client, "Attribute Source")
        source_weights = await weights(client, "Attribute Source")
        target_mesh = await mesh(client, "Attribute Target")
        target_uv = await uv(client, "Attribute Target")
        target_weights = await weights(client, "Attribute Target")
        target_faces = await selection(client, target_mesh, "FACE")
        begin = await call(
            client,
            "transaction.begin",
            {"label": "0.14 attribute transfer", "viewport_id": None},
            int(target_faces["scene_generation"]),
        )
        uv_transfer = await call(
            client,
            "mesh.attribute.transfer",
            {
                "transaction_id": begin["transaction_id"],
                "source": attribute_exact(source_mesh, source_weights),
                "target": attribute_exact(target_mesh, target_weights),
                "transfer": {
                    "type": "UV",
                    "source_layer": layer_ref(source_uv),
                    "target_layer_name": "UVMap",
                    "expected_target_layer_identity": target_uv["layer"]["session_identity"],
                    "target_selection_id": target_faces["selection_id"],
                    "mapping": "TOPOLOGY",
                    "maximum_distance": 10.0,
                },
            },
            int(begin["scene_generation"]),
        )
        target_after_uv = await mesh(client, "Attribute Target")
        target_after_weights = await weights(client, "Attribute Target")
        target_vertices = await selection(client, target_after_uv, "VERTEX")
        weight_transfer = await call(
            client,
            "mesh.attribute.transfer",
            {
                "transaction_id": begin["transaction_id"],
                "source": attribute_exact(source_mesh, source_weights),
                "target": attribute_exact(target_after_uv, target_after_weights),
                "transfer": {
                    "type": "WEIGHTS",
                    "groups": [
                        {
                            "source": group_ref(source_weights, "Bone.L"),
                            "target_group_name": "Bone.L",
                        }
                    ],
                    "target_selection_id": target_vertices["selection_id"],
                    "mapping": "NEAREST_SURFACE",
                    "maximum_distance": 10.0,
                },
            },
            int(uv_transfer["scene_generation"]),
        )
        rollback = await call(
            client,
            "transaction.rollback",
            {"transaction_id": begin["transaction_id"]},
            int(weight_transfer["scene_generation"]),
        )
        assert (await uv(client, "Attribute Target"))["uv_fingerprint"] == target_uv[
            "uv_fingerprint"
        ]
        assert (await weights(client, "Attribute Target"))["weights_fingerprint"] == target_weights[
            "weights_fingerprint"
        ]
        report["attribute_transfer_rollback"] = {
            "uv": uv_transfer,
            "weights": weight_transfer,
            "rollback": rollback,
        }

        stage("attribute-aware topology and batch disconnect rollback")
        target_mesh = await mesh(client, "Attribute Target")
        target_uv = await uv(client, "Attribute Target")
        target_weights = await weights(client, "Attribute Target")
        target_edges = await selection(client, target_mesh, "EDGE")
        begin = await call(
            client,
            "transaction.begin",
            {"label": "0.14 topology preserve", "viewport_id": None},
            int(target_edges["scene_generation"]),
        )
        topology = await call(
            client,
            "mesh.edit",
            {
                **exact(target_mesh),
                "transaction_id": begin["transaction_id"],
                "data_scope": "OBJECT",
                "operation": {
                    "type": "subdivide",
                    "selection_id": target_edges["selection_id"],
                    "cuts": 1,
                    "attribute_policy": {
                        "uv": "PRESERVE_INTERPOLATE",
                        "weights": "PRESERVE_INTERPOLATE",
                    },
                },
            },
            int(begin["scene_generation"]),
        )
        assert topology["attribute_effects"]["migration"]["uv"] == "PRESERVE_INTERPOLATE"
        assert topology["attribute_effects"]["migration"]["weights"] == "PRESERVE_INTERPOLATE"
        await call(
            client,
            "transaction.rollback",
            {"transaction_id": begin["transaction_id"]},
            int(topology["scene_generation"]),
        )

        batch_mesh = await mesh(client, "Attribute Target")
        batch_uv = await uv(client, "Attribute Target")
        batch_weights = await weights(client, "Attribute Target")
        begin = await call(
            client,
            "transaction.begin",
            {"label": "0.14 attribute batch", "viewport_id": None},
            int(batch_weights["scene_generation"]),
        )
        batch = await call(
            client,
            "mesh.batch.execute",
            {
                "transaction_id": begin["transaction_id"],
                "targets": [{"alias": "target", **exact(batch_mesh)}],
                "inputs": [],
                "steps": [
                    {
                        "type": "selection_query",
                        "target_alias": "target",
                        "output_alias": "faces",
                        "domain": "FACE",
                        "query": {"type": "all"},
                    },
                    {
                        "type": "selection_query",
                        "target_alias": "target",
                        "output_alias": "vertices",
                        "domain": "VERTEX",
                        "query": {"type": "all"},
                    },
                    {
                        "type": "uv_edit",
                        "target_alias": "target",
                        "data_scope": "OBJECT",
                        "operation": {
                            "type": "transform",
                            "layer": layer_ref(batch_uv),
                            "selection_alias": "faces",
                            "scope": "ISLANDS",
                            "translation": [0.125, 0.0],
                        },
                    },
                    {
                        "type": "weights_edit",
                        "target_alias": "target",
                        "data_scope": "OBJECT",
                        "operation": {
                            "type": "set",
                            "group": group_ref(batch_weights, "Bone.L"),
                            "selection_alias": "vertices",
                            "value": 0.4,
                        },
                    },
                    {
                        "type": "mesh_validate",
                        "selection_alias": "vertices",
                        "check": "WEIGHT_INFLUENCE_LIMIT",
                        "output_alias": "influences",
                        "maximum_influences": 4,
                        "assertions": [{"type": "count_at_most", "value": 0}],
                    },
                ],
                "on_error": "ROLLBACK_TRANSACTION",
            },
            int(begin["scene_generation"]),
        )
        assert batch["scene_generation"] == int(begin["scene_generation"]) + 1
        await client.close()
        await asyncio.sleep(3.0)
        assert (await uv(client, "Attribute Target"))["uv_fingerprint"] == target_uv[
            "uv_fingerprint"
        ]
        assert (await weights(client, "Attribute Target"))["weights_fingerprint"] == target_weights[
            "weights_fingerprint"
        ]
        report["topology_and_batch"] = {"topology": topology, "batch": batch}

        stage("Shape-Key attribute write and native-save adoption")
        shape_mesh = await mesh(client, "Attribute ShapeKey")
        assert shape_mesh["writable_domains"]["geometry"] is False
        assert shape_mesh["writable_domains"]["uv"] is True
        shape_weights = await weights(client, "Attribute ShapeKey")
        shape_vertices = await selection(client, shape_mesh, "VERTEX")
        begin = await call(
            client,
            "transaction.begin",
            {"label": "0.14 native save", "viewport_id": None},
            int(shape_vertices["scene_generation"]),
        )
        created = await call(
            client,
            "mesh.weights.edit",
            {
                **attribute_exact(shape_mesh, shape_weights),
                "transaction_id": begin["transaction_id"],
                "operation": {"type": "group_create", "group_name": "Committed"},
            },
            int(begin["scene_generation"]),
        )
        native_save = await client.call("_test.native_save", {"path": str(project)}, read_only=True)
        assert native_save["last_user_action"]["status"] == "succeeded"
        reloaded = await manager.project_reload(
            save_current=False, use_scripts=False, load_ui=False
        )
        persisted = await weights(client, "Attribute ShapeKey")
        assert any(item["name"] == "Committed" for item in persisted["groups"])
        report["shape_key_native_save"] = {
            "created": created,
            "native_save": native_save,
            "reload": reloaded,
            "persisted_group_schema": persisted["group_schema_fingerprint"],
        }

        if args.character_project is not None:
            stage("real Shape-Key character UV/weight rollback and persisted commit")
            character_source = args.character_project.resolve(strict=True)
            character_source_hash = sha256(character_source)
            character_project = temporary / "test-model.blend"
            shutil.copy2(character_source, character_project)
            report["character_source"] = str(character_source)
            report["character_source_sha256_before"] = character_source_hash
            report["character_project"] = str(character_project)
            report["character_project_open"] = await manager.project_open(
                str(character_project),
                save_current=False,
                use_scripts=False,
                load_ui=False,
            )
            character_name = "绯雪_edit_mesh"
            character_mesh = await mesh(client, character_name)
            if character_mesh["writable_domains"]["geometry"] is not False:
                raise RuntimeError("Character Shape-Key Mesh unexpectedly permits topology edits")
            if not character_mesh["writable_domains"]["uv"]:
                raise RuntimeError("Character Shape-Key Mesh does not permit UV edits")
            if not character_mesh["writable_domains"]["weights"]:
                raise RuntimeError("Character Shape-Key Mesh does not permit weight edits")
            character_uv = await uv(client, character_name, None, component="LOOPS", limit=1)
            character_weights = await weights(
                client, character_name, component="VERTICES", limit=256
            )
            baseline_character_uv = character_uv["uv_fingerprint"]
            baseline_character_weights = character_weights["weights_fingerprint"]
            uv_corner = character_uv["items"][0] if character_uv["items"] else None
            weighted_vertex = next(
                (
                    item
                    for item in character_weights["items"]
                    if any(
                        assignment["group_index"] < len(character_weights["groups"])
                        and not character_weights["groups"][assignment["group_index"]][
                            "lock_weight"
                        ]
                        for assignment in item["weights"]
                    )
                ),
                None,
            )
            if weighted_vertex is None:
                raise RuntimeError("No editable weighted character vertex was found")
            assignment = next(
                item
                for item in weighted_vertex["weights"]
                if item["group_index"] < len(character_weights["groups"])
                and not character_weights["groups"][item["group_index"]]["lock_weight"]
            )
            group_name = str(assignment["group_name"])
            baseline_vertex_weight = float(assignment["weight"])
            begin = await call(
                client,
                "transaction.begin",
                {"label": "0.14 character UV", "viewport_id": None},
                int(character_mesh["scene_generation"]),
            )
            character_operations: dict[str, Any] = {}
            if character_uv.get("layer") is not None and uv_corner is not None:
                changed_uv = await call(
                    client,
                    "mesh.uv.edit",
                    {
                        **exact(character_mesh),
                        "transaction_id": begin["transaction_id"],
                        "expected_uv_fingerprint": character_uv["uv_fingerprint"],
                        "data_scope": "OBJECT",
                        "operation": {
                            "type": "coordinate_set",
                            "layer": layer_ref(character_uv),
                            "mode": "OFFSET",
                            "corners": [
                                {
                                    "loop_index": uv_corner["loop_index"],
                                    "face_index": uv_corner["face_index"],
                                    "corner_index": uv_corner["corner_index"],
                                    "vertex_index": uv_corner["vertex_index"],
                                    "uv": [0.001, 0.0],
                                }
                            ],
                        },
                    },
                    int(begin["scene_generation"]),
                )
                character_operations["uv"] = changed_uv
                uv_rollback_generation = int(changed_uv["scene_generation"])
            else:
                uv_rollback_generation = int(begin["scene_generation"])
            uv_rollback = await call(
                client,
                "transaction.rollback",
                {"transaction_id": begin["transaction_id"]},
                uv_rollback_generation,
            )
            restored_character_uv = await uv(client, character_name, None)
            if restored_character_uv["uv_fingerprint"] != baseline_character_uv:
                raise RuntimeError("Character UV rollback did not restore the baseline")
            character_mesh = await mesh(client, character_name)
            character_weights = await weights(
                client,
                character_name,
                component="VERTICES",
                offset=int(weighted_vertex["vertex_index"]),
                limit=1,
            )
            if character_weights["weights_fingerprint"] != baseline_character_weights:
                raise RuntimeError(
                    "Character UV rollback changed weights: "
                    f"expected {baseline_character_weights}, "
                    f"got {character_weights['weights_fingerprint']}"
                )
            begin = await call(
                client,
                "transaction.begin",
                {"label": "0.14 character weights", "viewport_id": None},
                int(character_mesh["scene_generation"]),
            )
            weighted_selection = await indexed_selection(
                client,
                character_mesh,
                "VERTEX",
                [int(weighted_vertex["vertex_index"])],
            )
            rollback_weight = 0.25 if not math.isclose(baseline_vertex_weight, 0.25) else 0.5
            changed_weight = await call(
                client,
                "mesh.weights.edit",
                {
                    **attribute_exact(character_mesh, character_weights),
                    "transaction_id": begin["transaction_id"],
                    "operation": {
                        "type": "set",
                        "group": group_ref(character_weights, group_name),
                        "selection_id": weighted_selection["selection_id"],
                        "value": rollback_weight,
                    },
                },
                int(begin["scene_generation"]),
            )
            character_operations["weights"] = changed_weight
            weight_rollback = await call(
                client,
                "transaction.rollback",
                {"transaction_id": begin["transaction_id"]},
                int(changed_weight["scene_generation"]),
            )
            restored_character_weights = await weights(client, character_name)
            if restored_character_weights["weights_fingerprint"] != baseline_character_weights:
                raise RuntimeError(
                    "Character weight rollback did not restore the baseline: "
                    f"expected {baseline_character_weights}, "
                    f"got {restored_character_weights['weights_fingerprint']}"
                )

            character_mesh = await mesh(client, character_name)
            character_weights = await weights(
                client,
                character_name,
                component="VERTICES",
                offset=int(weighted_vertex["vertex_index"]),
                limit=1,
            )
            begin = await call(
                client,
                "transaction.begin",
                {"label": "0.14 character persisted weight", "viewport_id": None},
                int(character_mesh["scene_generation"]),
            )
            committed_selection = await indexed_selection(
                client,
                character_mesh,
                "VERTEX",
                [int(weighted_vertex["vertex_index"])],
            )
            committed_weight_value = 0.75 if not math.isclose(baseline_vertex_weight, 0.75) else 0.6
            committed_weight = await call(
                client,
                "mesh.weights.edit",
                {
                    **attribute_exact(character_mesh, character_weights),
                    "transaction_id": begin["transaction_id"],
                    "operation": {
                        "type": "set",
                        "group": group_ref(character_weights, group_name),
                        "selection_id": committed_selection["selection_id"],
                        "value": committed_weight_value,
                    },
                },
                int(begin["scene_generation"]),
            )
            committed = await call(
                client,
                "transaction.commit",
                {"transaction_id": begin["transaction_id"]},
                int(committed_weight["scene_generation"]),
            )
            character_save = await client.call(
                "_test.native_save",
                {"path": str(character_project)},
                read_only=True,
            )
            if character_save["last_user_action"]["status"] != "succeeded":
                raise RuntimeError("Character native save did not succeed")
            character_reload = await manager.project_reload(
                save_current=False,
                use_scripts=False,
                load_ui=False,
            )
            persisted_character_weights = await weights(client, character_name)
            if (
                persisted_character_weights["weights_fingerprint"]
                != committed_weight["after_weights_fingerprint"]
            ):
                raise RuntimeError("Committed character weight did not survive reload")
            report["character_shape_key_attributes"] = {
                "mesh": character_mesh,
                "operations": character_operations,
                "uv_rollback": uv_rollback,
                "weight_rollback": weight_rollback,
                "committed_weight": committed_weight,
                "commit": committed,
                "native_save": character_save,
                "reload": character_reload,
                "persisted_weights_fingerprint": persisted_character_weights["weights_fingerprint"],
            }
            report["character_source_sha256_after"] = sha256(character_source)
            report["character_source_unchanged"] = (
                report["character_source_sha256_after"] == character_source_hash
            )
            if not report["character_source_unchanged"]:
                raise RuntimeError("Source test-model.blend changed during acceptance")

        ping_after = await client.call("connection.ping", read_only=True)
        assert int(ping_after["heartbeat"]) > int(ping["heartbeat"])
        report["ping_after"] = ping_after
        report["fixture_source_sha256_after"] = sha256(source)
        report["fixture_source_unchanged"] = report["fixture_source_sha256_after"] == source_hash
        assert report["fixture_source_unchanged"]
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
    parser.add_argument("--port", type=int, default=9892)
    parser.add_argument("--timeout", type=float, default=120.0)
    parser.add_argument("--character-project", type=Path)
    report = asyncio.run(run(parser.parse_args()))
    report_path = Path(report["artifact_directory"]) / f"report-{PACKAGE_VERSION}.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(report_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
