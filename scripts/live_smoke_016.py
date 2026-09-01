"""Run Blender 4.2 controlled Library and template-coverage acceptance."""

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

import live_smoke_013 as topology
import live_smoke_014 as attributes
import live_smoke_015 as base

from blender_research_mcp.client import BridgeClient
from blender_research_mcp.constants import MAX_DEADLINE_MS, PACKAGE_VERSION
from blender_research_mcp.errors import BridgeError
from blender_research_mcp.library_assets import (
    inspect_local_library_file,
    library_entry_identity,
)
from blender_research_mcp.lifecycle import ApplicationManager
from blender_research_mcp.rendering import request_render_preview

ROOT = Path(__file__).resolve().parents[1]
LIBRARY_BUILDER = ROOT / "scripts" / "create_library_fixture.py"


def stage(name: str) -> None:
    print(f"[0.16 smoke] {name}", flush=True)


def build_library(blender: Path, output: Path) -> None:
    result = subprocess.run(  # noqa: S603 - fixed executable and repository script
        [
            str(blender.resolve(strict=True)),
            "--background",
            "--factory-startup",
            "--python-exit-code",
            "1",
            "--python",
            str(LIBRARY_BUILDER),
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
        raise RuntimeError("Could not build the 0.16 Library fixture")


def source_payload(evidence: dict[str, object]) -> dict[str, object]:
    return {
        "path": evidence["path"],
        "expected_file_sha256": evidence["file_sha256"],
        "expected_size_bytes": evidence["size_bytes"],
        "expected_modified_ns": evidence["modified_ns"],
    }


def entry(evidence: dict[str, object], kind: str, name: str) -> dict[str, str]:
    digest = str(evidence["file_sha256"])
    return {
        "type": kind,
        "name": name,
        "expected_entry_identity": library_entry_identity(digest, kind, name),
    }


async def scene(client: BridgeClient) -> dict[str, Any]:
    return await client.call(
        "scene.inspect",
        {"kinds": ["objects", "collections"], "name_filter": None, "limit": 256},
        read_only=True,
    )


async def collection(client: BridgeClient, name: str) -> dict[str, Any]:
    return await client.call(
        "collection.inspect",
        {"collection_name": name, "offset": 0, "limit": 256},
        read_only=True,
    )


async def inspect_object(client: BridgeClient, name: str) -> dict[str, Any]:
    return await client.call("object.inspect", {"object_name": name}, read_only=True)


async def inspect_vertices(client: BridgeClient, name: str) -> dict[str, Any]:
    return await client.call(
        "mesh.inspect",
        {"object_name": name, "component": "vertices", "offset": 0, "limit": 256},
        deadline_ms=MAX_DEADLINE_MS,
        read_only=True,
    )


def scene_parent(value: dict[str, Any]) -> dict[str, Any]:
    root = value["scene_root"]
    return {
        "type": "SCENE_ROOT",
        "scene_name": root["scene_name"],
        "expected_scene_identity": root["scene_identity"],
        "expected_scene_structure_fingerprint": root["scene_structure_fingerprint"],
    }


def collection_evidence(value: dict[str, Any]) -> dict[str, Any]:
    return {
        "collection_name": value["name"],
        "expected_collection_identity": value["session_identity"],
        "expected_collection_structure_fingerprint": value["structure_fingerprint"],
    }


def batch_collection_input(alias: str, value: dict[str, Any]) -> dict[str, Any]:
    return {"type": "collection", "alias": alias, **collection_evidence(value)}


async def append(
    client: BridgeClient,
    transaction_id: str,
    evidence: dict[str, object],
    entry_value: dict[str, str],
    output: dict[str, Any],
    generation: int,
    *,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    return await client.call(
        "library.append",
        {
            "transaction_id": transaction_id,
            "source": source_payload(evidence),
            "entry": entry_value,
            "output": output,
        },
        deadline_ms=MAX_DEADLINE_MS,
        expected_scene_generation=generation,
        idempotency_key=idempotency_key or str(uuid4()),
        read_only=False,
    )


async def inspect_library(
    client: BridgeClient, evidence: dict[str, object]
) -> dict[str, Any]:
    return await client.call(
        "library.inspect",
        {
            "source": source_payload(evidence),
            "blend_header": evidence["blend_header"],
            "kinds": ["OBJECT", "COLLECTION", "MESH"],
            "name_filter": None,
            "offset": 0,
            "limit": 256,
        },
        deadline_ms=MAX_DEADLINE_MS,
        read_only=True,
    )


async def create_destination(
    client: BridgeClient, generation: int
) -> tuple[dict[str, Any], dict[str, Any]]:
    transaction = await base.begin(client, generation, "0.16 Library destination")
    created = await base.mutate(
        client,
        "collection.create",
        {
            "transaction_id": transaction["transaction_id"],
            "name": "Library Outputs",
            "parent": scene_parent(await scene(client)),
        },
        int(transaction["scene_generation"]),
    )
    committed = await base.mutate(
        client,
        "transaction.commit",
        {"transaction_id": transaction["transaction_id"]},
        int(created["scene_generation"]),
    )
    return await collection(client, "Library Outputs"), committed


async def execute_template_batch(
    client: BridgeClient,
    *,
    transaction_id: str,
    baseline: dict[str, Any],
    destination: dict[str, Any],
    library: dict[str, object],
    generation: int,
    collection_name: str,
    prefix: str,
    idempotency_key: str,
) -> dict[str, Any]:
    digest = str(library["file_sha256"])
    return await client.call(
        "mesh.batch.execute",
        {
            "transaction_id": transaction_id,
            "targets": [{"alias": "source", **base.extract_target(baseline)}],
            "inputs": [
                batch_collection_input("outputs", destination),
                {
                    "type": "library",
                    "alias": "templates",
                    "path": library["path"],
                    "expected_file_sha256": digest,
                    "expected_size_bytes": library["size_bytes"],
                    "expected_modified_ns": library["modified_ns"],
                },
            ],
            "steps": [
                {
                    "type": "library_append",
                    "library_alias": "templates",
                    "entry": entry(library, "COLLECTION", "Template Assembly"),
                    "output": {
                        "type": "COLLECTION",
                        "new_collection_name": collection_name,
                        "parent": {"type": "COLLECTION_ALIAS", "collection_alias": "outputs"},
                    },
                    "output_root_alias": "assembly",
                    "root_alias_kind": "COLLECTION",
                    "exports": [
                        {
                            "source_object_name": "Template Head",
                            "expected_entry_identity": library_entry_identity(
                                digest, "OBJECT", "Template Head"
                            ),
                            "output_alias": "head",
                            "alias_kind": "MESH_TARGET",
                        },
                        {
                            "source_object_name": "Template Rig",
                            "expected_entry_identity": library_entry_identity(
                                digest, "OBJECT", "Template Rig"
                            ),
                            "output_alias": "rig",
                            "alias_kind": "ARMATURE",
                        },
                    ],
                },
                {
                    "type": "object_set",
                    "object_alias": "head",
                    "patches": [
                        {
                            "type": "transform",
                            "location": {"x": 3.0, "y": 0.0, "z": 0.0},
                            "rotation_euler_degrees": {"z": 12.0},
                            "scale": {"x": 1.1, "y": 1.1, "z": 1.1},
                        }
                    ],
                },
                {
                    "type": "mesh_surface_prepare",
                    "target_alias": "head",
                    "geometry": "EVALUATED",
                    "output_surface_alias": "head_surface",
                },
                {
                    "type": "selection_query",
                    "target_alias": "head",
                    "output_alias": "head_vertices",
                    "domain": "VERTEX",
                    "query": {"type": "all"},
                },
                {
                    "type": "mesh_validate",
                    "selection_alias": "head_vertices",
                    "check": "NON_MANIFOLD",
                    "output_alias": "manifold",
                    "assertions": [{"type": "count_at_most", "value": 0}],
                },
                {
                    "type": "rig_bind",
                    "mesh_target_alias": "head",
                    "armature_alias": "rig",
                    "modifier": {"name": f"{prefix} Armature", "expected_existing": None},
                    "parenting": "KEEP_WORLD",
                    "group_scope": {"type": "ALL_MATCHED"},
                    "output_binding_alias": "binding",
                },
            ],
            "on_error": "ROLLBACK_TRANSACTION",
        },
        deadline_ms=MAX_DEADLINE_MS,
        expected_scene_generation=generation,
        idempotency_key=idempotency_key,
        read_only=False,
    )


async def check_character_coverage(
    client: BridgeClient,
    manager: ApplicationManager,
    *,
    source_path: Path,
    temporary: Path,
    library: dict[str, object],
    report: dict[str, Any],
) -> None:
    stage("real character Library template fit, weight transfer, and binding")
    source = source_path.resolve(strict=True)
    source_hash = base.sha256(source)
    project = temporary / "test-model.blend"
    shutil.copy2(source, project)
    opened = await manager.project_open(
        str(project), save_current=False, use_scripts=False, load_ui=False
    )
    body = await base.mesh(client, "绯雪_edit_mesh")
    body_object = await inspect_object(client, "绯雪_edit_mesh")
    surface = await topology.prepare_surface(client, body, "EVALUATED")
    source_weights = await attributes.weights(client, "绯雪_edit_mesh")
    if not any(item["name"] == "頭" for item in source_weights["groups"]):
        raise RuntimeError("Character fixture no longer exposes the semantically named 頭 group")
    rig = await client.call(
        "rig.inspect",
        {
            "object_name": "绯雪_edit_mesh",
            "armature_object_name": "绯雪_edit_arm",
            "offset": 0,
            "limit": 256,
        },
        read_only=True,
    )
    armature = next(
        item for item in rig["armatures"] if item["object_name"] == "绯雪_edit_arm"
    )
    destination, destination_commit = await create_destination(
        client, int(body["scene_generation"])
    )

    bounds = body_object["world_bounds"]
    minimum = [min(float(point[axis]) for point in bounds) for axis in range(3)]
    maximum = [max(float(point[axis]) for point in bounds) for axis in range(3)]
    diagonal = sum((maximum[axis] - minimum[axis]) ** 2 for axis in range(3)) ** 0.5
    # A compact four-corner anchor patch keeps the planar cage face outside the
    # curved character surface while still reserving the upper half as an
    # untouched hidden-shape prior.
    half_extent = diagonal * 0.0012
    scale = half_extent / 0.7
    center = {
        "x": (minimum[0] + maximum[0]) * 0.5,
        "y": (minimum[1] + maximum[1]) * 0.5,
        "z": maximum[2] + half_extent + diagonal * 0.02,
    }
    offset = diagonal * 0.00002

    transaction = await base.begin(
        client,
        int(destination["scene_generation"]),
        "0.16 character template coverage",
    )
    appended = await append(
        client,
        transaction["transaction_id"],
        library,
        entry(library, "OBJECT", "Template Head"),
        {
            "type": "OBJECT",
            "new_object_name": "MCP 0.16 Coverage Head",
            "collection": collection_evidence(destination),
        },
        int(transaction["scene_generation"]),
    )
    proxy_object = await inspect_object(client, "MCP 0.16 Coverage Head")
    aligned = await base.mutate(
        client,
        "object.set",
        {
            "transaction_id": transaction["transaction_id"],
            "object_name": proxy_object["name"],
            "expected_object_identity": proxy_object["session_identity"],
            "patches": [
                {
                    "type": "transform",
                    "location": center,
                    "rotation_euler_degrees": {"x": 0.0, "y": 0.0, "z": 0.0},
                    "scale": {"x": scale, "y": scale, "z": scale},
                }
            ],
        },
        int(appended["scene_generation"]),
    )
    proxy = await base.mesh(client, "MCP 0.16 Coverage Head")
    plane = {
        "type": "plane",
        "origin": {"x": 0.0, "y": 0.0, "z": 0.0},
        "normal": {"x": 0.0, "y": 0.0, "z": 1.0},
        "space": "LOCAL",
    }
    visible = await topology.selection_query(
        client, proxy, "VERTEX", {**plane, "side": "NEGATIVE"}
    )
    hidden = await topology.selection_query(
        client, proxy, "VERTEX", {**plane, "side": "POSITIVE"}
    )
    if int(visible["component_count"]) == 0 or int(hidden["component_count"]) == 0:
        raise RuntimeError("Template plane queries did not split visible and hidden regions")
    baseline = await topology.distance_query(
        client, visible["selection_id"], surface["surface_id"], diagonal
    )
    vertices_before = await inspect_vertices(client, "MCP 0.16 Coverage Head")
    before_capture = await topology.capture_view(client, "MCP 0.16 Coverage Head", "FRONT")
    baseline_all = await topology.selection_query(client, proxy, "VERTEX", {"type": "all"})
    baseline_non_manifold = await topology.validate_mesh(
        client, baseline_all["selection_id"], "NON_MANIFOLD"
    )
    baseline_degenerate = await topology.validate_mesh(
        client, baseline_all["selection_id"], "DEGENERATE"
    )

    fitted = await base.mutate(
        client,
        "mesh.edit",
        topology.edit_params(
            transaction["transaction_id"],
            proxy,
            {
                "type": "shrinkwrap",
                "selection_id": visible["selection_id"],
                "surface_id": surface["surface_id"],
                "iterations": 1,
                "factor": 1.0,
                "maximum_distance": diagonal,
                "offset": offset,
                "side": "ANY",
                "on_miss": "ERROR",
            },
        ),
        int(aligned["scene_generation"]),
    )
    fitted_mesh = await base.mesh(client, "MCP 0.16 Coverage Head")
    after_distance = await topology.distance_query(
        client,
        fitted["rebound_selection"]["selection_id"],
        surface["surface_id"],
        diagonal,
    )
    hidden_after = await topology.selection_query(
        client, fitted_mesh, "VERTEX", {**plane, "side": "POSITIVE"}
    )
    vertices_after = await inspect_vertices(client, "MCP 0.16 Coverage Head")
    before_positions = {int(item["index"]): item["co"] for item in vertices_before["items"]}
    after_positions = {int(item["index"]): item["co"] for item in vertices_after["items"]}
    hidden_indices = {
        index for index, coordinate in before_positions.items() if float(coordinate[2]) > 0
    }
    hidden_displacement = max(
        (
            sum(
                (float(after_positions[index][axis]) - float(before_positions[index][axis])) ** 2
                for axis in range(3)
            )
            ** 0.5
            for index in hidden_indices
        ),
        default=0.0,
    )
    all_after = await topology.selection_query(client, fitted_mesh, "VERTEX", {"type": "all"})
    non_manifold = await topology.validate_mesh(
        client, all_after["selection_id"], "NON_MANIFOLD"
    )
    degenerate = await topology.validate_mesh(
        client, all_after["selection_id"], "DEGENERATE"
    )
    self_intersection = await topology.validate_mesh(
        client, all_after["selection_id"], "SELF_INTERSECTION"
    )
    target_intersection = await topology.validate_mesh(
        client,
        all_after["selection_id"],
        "TARGET_INTERSECTION",
        surface["surface_id"],
    )
    penetration = await topology.validate_mesh(
        client, all_after["selection_id"], "PENETRATION", surface["surface_id"]
    )

    target_weights = await attributes.weights(client, "MCP 0.16 Coverage Head")
    transferred = await base.mutate(
        client,
        "mesh.attribute.transfer",
        {
            "transaction_id": transaction["transaction_id"],
            "source": attributes.attribute_exact(body, source_weights),
            "target": attributes.attribute_exact(fitted_mesh, target_weights),
            "transfer": {
                "type": "WEIGHTS",
                "groups": [
                    {
                        "source": attributes.group_ref(source_weights, "頭"),
                        "target_group_name": "頭",
                    }
                ],
                "target_selection_id": all_after["selection_id"],
                "mapping": "NEAREST_SURFACE",
                "source_geometry": "BASE",
                "maximum_distance": diagonal,
                "on_miss": "ERROR",
            },
        },
        int(fitted["scene_generation"]),
    )
    bound_mesh = await base.mesh(client, "MCP 0.16 Coverage Head")
    bound_weights = await attributes.weights(client, "MCP 0.16 Coverage Head")
    bound = await base.mutate(
        client,
        "rig.bind",
        {
            "transaction_id": transaction["transaction_id"],
            "mesh_target": {
                "object_name": bound_mesh["object"]["name"],
                "expected_object_identity": bound_mesh["object"]["session_identity"],
                "expected_mesh_identity": bound_mesh["mesh"]["session_identity"],
                "expected_mesh_revision_id": bound_mesh["mesh_revision_id"],
                "expected_group_schema_fingerprint": bound_weights[
                    "group_schema_fingerprint"
                ],
                "expected_weights_fingerprint": bound_weights["weights_fingerprint"],
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
                "name": "MCP Character Armature",
                "expected_existing": None,
                "use_vertex_groups": True,
                "use_bone_envelopes": False,
                "preserve_volume": True,
                "use_multi_modifier": False,
                "vertex_group": None,
            },
            "parenting": "KEEP_WORLD",
            "group_scope": {"type": "EXPLICIT", "group_names": ["頭"]},
        },
        int(transferred["scene_generation"]),
    )
    after_capture = await topology.capture_view(client, "MCP 0.16 Coverage Head", "FRONT")

    baseline_p95 = float(baseline["distances"]["p95"])
    fitted_p95 = float(after_distance["distances"]["p95"])
    fitted_maximum = float(after_distance["distances"]["maximum"])
    template_diagonal = half_extent * 2.0 * (3.0**0.5)
    stage(
        "character metrics "
        + json.dumps(
            {
                "baseline_p95": baseline_p95,
                "fitted_p95": fitted_p95,
                "fitted_maximum": fitted_maximum,
                "non_manifold": non_manifold["count"],
                "baseline_non_manifold": baseline_non_manifold["count"],
                "degenerate": degenerate["count"],
                "baseline_degenerate": baseline_degenerate["count"],
                "self_intersection": self_intersection["count"],
                "target_intersection": target_intersection["count"],
                "hidden_displacement": hidden_displacement,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    if fitted_p95 > baseline_p95 * 0.5:
        raise RuntimeError("Character coverage p95 did not improve by at least 50%")
    if fitted_p95 > template_diagonal * 0.005 or fitted_maximum > template_diagonal * 0.01:
        raise RuntimeError("Character coverage seam distance exceeded the bounded template scale")
    if hidden_displacement > diagonal * 0.1:
        raise RuntimeError("Hidden template prior moved more than 10% of character bounds")
    if (
        int(non_manifold["count"]) > int(baseline_non_manifold["count"])
        or int(degenerate["count"]) > int(baseline_degenerate["count"])
        or int(self_intersection["count"]) > 0
        or int(target_intersection["count"]) > 0
    ):
        raise RuntimeError("Character coverage introduced invalid or intersecting geometry")
    signed_minimum = penetration["distances"]["signed_minimum"]
    maximum_penetration = (
        max(0.0, -float(signed_minimum))
        if penetration["sign_reliable"] and signed_minimum is not None
        else None
    )
    if maximum_penetration is not None and maximum_penetration > diagonal * 0.001:
        raise RuntimeError("Character coverage penetration exceeded 0.1% of bounds")
    if before_capture["native_sha256"] == after_capture["native_sha256"]:
        raise RuntimeError("Character coverage produced no visible image difference")

    committed = await base.mutate(
        client,
        "transaction.commit",
        {"transaction_id": transaction["transaction_id"]},
        int(bound["scene_generation"]),
    )
    saved = await manager.project_save()
    reloaded = await manager.project_reload(
        save_current=False, use_scripts=False, load_ui=False
    )
    persisted = await base.mesh(client, "MCP 0.16 Coverage Head")
    persisted_rig = await client.call(
        "rig.inspect",
        {
            "object_name": "MCP 0.16 Coverage Head",
            "armature_object_name": "绯雪_edit_arm",
            "offset": 0,
            "limit": 256,
        },
        read_only=True,
    )
    if base.sha256(source) != source_hash:
        raise RuntimeError("Source test-model.blend changed during Library coverage smoke")
    report["character"] = {
        "source": str(source),
        "source_sha256_before": source_hash,
        "source_sha256_after": base.sha256(source),
        "project": str(project),
        "open": opened,
        "destination_commit": destination_commit,
        "body_bounds_diagonal": diagonal,
        "surface": surface,
        "append": appended,
        "align": aligned,
        "visible_selection": visible,
        "hidden_selection": hidden,
        "hidden_selection_after": hidden_after,
        "baseline_distance": baseline,
        "fit": fitted,
        "fitted_distance": after_distance,
        "p95_improvement_ratio": fitted_p95 / baseline_p95,
        "seam_limits": {
            "p95": template_diagonal * 0.005,
            "maximum": template_diagonal * 0.01,
        },
        "hidden_maximum_displacement": hidden_displacement,
        "hidden_limit": diagonal * 0.1,
        "non_manifold": non_manifold,
        "degenerate": degenerate,
        "self_intersection": self_intersection,
        "target_intersection": target_intersection,
        "penetration": penetration,
        "maximum_penetration": maximum_penetration,
        "weight_transfer": transferred,
        "binding": bound,
        "captures": {"before": before_capture, "after": after_capture},
        "commit": committed,
        "save": saved,
        "reload": reloaded,
        "persisted": persisted,
        "persisted_rig": persisted_rig,
    }


async def run(args: argparse.Namespace) -> dict[str, Any]:
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + f"-{uuid4().hex[:8]}"
    temporary = Path(tempfile.gettempdir()) / "blender-research-mcp-library" / run_id
    temporary.mkdir(parents=True, exist_ok=False)
    artifact_directory = ROOT / "artifacts" / "live-smoke" / run_id
    artifact_directory.mkdir(parents=True, exist_ok=False)
    fixture_source = temporary / "scene-source.blend"
    fixture_project = temporary / "scene-project.blend"
    library_path = temporary / "template-library.blend"
    base.build_fixture(args.blender_executable, fixture_source)
    build_library(args.blender_executable, library_path)
    shutil.copy2(fixture_source, fixture_project)
    fixture_hash = base.sha256(fixture_source)
    library_hash = base.sha256(library_path)
    library = inspect_local_library_file(str(library_path))
    report: dict[str, Any] = {
        "run_id": run_id,
        "started_at": datetime.now(UTC).isoformat(),
        "server_version": PACKAGE_VERSION,
        "port": args.port,
        "temporary_root": str(temporary),
        "artifact_directory": str(artifact_directory),
        "fixture_source": str(fixture_source),
        "fixture_source_sha256_before": fixture_hash,
        "library_source": str(library_path),
        "library_source_sha256_before": library_hash,
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
        stage("managed launch, project open, and transaction-v12 handshake")
        report["launch"] = await manager.launch()
        launched = True
        report["project_open"] = await manager.project_open(
            str(fixture_project), save_current=False, use_scripts=False, load_ui=False
        )
        ping = await client.call("connection.ping", read_only=True)
        report["ping_before"] = ping
        for capability, minimum in {
            "transactions": 12,
            "library_inspection": 1,
            "library_append": 1,
            "mesh_batch": 4,
        }.items():
            if int(ping["capability_versions"].get(capability, 0)) < minimum:
                raise RuntimeError(f"Missing capability {capability}:{minimum}")

        baseline = await base.mesh(client, "Modular Source")
        context_before = await client.call("context.get", read_only=True)
        destination, destination_commit = await create_destination(
            client, int(baseline["scene_generation"])
        )
        baseline = await base.mesh(client, "Modular Source")

        stage("read-only Library catalog and stable entry identities")
        counts_before = await scene(client)
        catalog = await inspect_library(client, library)
        counts_after = await scene(client)
        assert counts_before["objects"] == counts_after["objects"]
        assert counts_before["collections"] == counts_after["collections"]
        catalog_keys = [(item["type"], item["name"]) for item in catalog["items"]]
        assert catalog_keys == sorted(catalog_keys)
        assert ("OBJECT", "Template Head") in catalog_keys
        assert ("COLLECTION", "Template Assembly") in catalog_keys
        assert ("MESH", "Loose Template Mesh") in catalog_keys
        report["catalog"] = catalog
        report["destination_commit"] = destination_commit

        stage("Object append idempotency and explicit rollback")
        transaction = await base.begin(
            client, int(baseline["scene_generation"]), "0.16 Object append rollback"
        )
        key = str(uuid4())
        object_write = await append(
            client,
            transaction["transaction_id"],
            library,
            entry(library, "OBJECT", "Template Head"),
            {
                "type": "OBJECT",
                "new_object_name": "Rollback Head",
                "collection": collection_evidence(destination),
            },
            int(transaction["scene_generation"]),
            idempotency_key=key,
        )
        replay = await append(
            client,
            transaction["transaction_id"],
            library,
            entry(library, "OBJECT", "Template Head"),
            {
                "type": "OBJECT",
                "new_object_name": "Rollback Head",
                "collection": collection_evidence(destination),
            },
            int(transaction["scene_generation"]),
            idempotency_key=key,
        )
        assert replay == object_write
        rollback = await base.mutate(
            client,
            "transaction.rollback",
            {"transaction_id": transaction["transaction_id"]},
            int(object_write["scene_generation"]),
        )
        assert not await base.object_exists(client, "Rollback Head")
        report["object_append"] = {"write": object_write, "replay": replay, "rollback": rollback}

        stage("unsupported dependency rejection leaves no closure")
        current = await base.mesh(client, "Modular Source")
        rejected_begin = await base.begin(
            client, int(current["scene_generation"]), "0.16 rejected append"
        )
        rejected = None
        try:
            await append(
                client,
                rejected_begin["transaction_id"],
                library,
                entry(library, "OBJECT", "Unsupported Constrained"),
                {
                    "type": "OBJECT",
                    "new_object_name": "Rejected Template",
                    "collection": collection_evidence(destination),
                },
                int(rejected_begin["scene_generation"]),
            )
        except BridgeError as exc:
            rejected = exc.error.model_dump(mode="json")
        assert rejected is not None and rejected["code"] == "LIBRARY_DEPENDENCY_UNSUPPORTED"
        await base.mutate(
            client,
            "transaction.rollback",
            {"transaction_id": rejected_begin["transaction_id"]},
            int(rejected_begin["scene_generation"]),
        )
        assert not await base.object_exists(client, "Rejected Template")
        report["unsupported"] = rejected

        stage("Mesh root commit and Collection disconnect rollback")
        current = await base.mesh(client, "Modular Source")
        mesh_begin = await base.begin(client, int(current["scene_generation"]), "0.16 Mesh append")
        mesh_write = await append(
            client,
            mesh_begin["transaction_id"],
            library,
            entry(library, "MESH", "Loose Template Mesh"),
            {
                "type": "MESH",
                "new_mesh_name": "Committed Loose Mesh",
                "new_object_name": "Committed Loose Object",
                "collection": collection_evidence(destination),
            },
            int(mesh_begin["scene_generation"]),
        )
        mesh_commit = await base.mutate(
            client,
            "transaction.commit",
            {"transaction_id": mesh_begin["transaction_id"]},
            int(mesh_write["scene_generation"]),
        )
        current = await base.mesh(client, "Modular Source")
        disconnect_begin = await base.begin(
            client, int(current["scene_generation"]), "0.16 Collection disconnect"
        )
        disconnect_write = await append(
            client,
            disconnect_begin["transaction_id"],
            library,
            entry(library, "COLLECTION", "Template Assembly"),
            {
                "type": "COLLECTION",
                "new_collection_name": "Disconnected Assembly",
                "parent": scene_parent(await scene(client)),
            },
            int(disconnect_begin["scene_generation"]),
        )
        await client.close()
        await asyncio.sleep(3.0)
        assert all(
            item["name"] != "Disconnected Assembly"
            for item in (await scene(client))["collections"]
        )
        report["mesh_append"] = {"write": mesh_write, "commit": mesh_commit}
        report["disconnect"] = {"write": disconnect_write, "rolled_back": True}

        stage("batch-v4 append, align, dynamic surface, validate, bind, rollback")
        current = await base.mesh(client, "Modular Source")
        destination = await collection(client, "Library Outputs")
        batch_begin = await base.begin(
            client, int(current["scene_generation"]), "0.16 template batch rollback"
        )
        batch = await execute_template_batch(
            client,
            transaction_id=batch_begin["transaction_id"],
            baseline=current,
            destination=destination,
            library=library,
            generation=int(batch_begin["scene_generation"]),
            collection_name="Rollback Template Assembly",
            prefix="Rollback Template",
            idempotency_key=str(uuid4()),
        )
        assert batch["assembly_manifest"]["libraries"]["templates"]["file_sha256"] == library_hash
        assert batch["assembly_manifest"]["surface_refs"]["head_surface"]["triangle_count"] > 0
        batch_rollback = await base.mutate(
            client,
            "transaction.rollback",
            {"transaction_id": batch_begin["transaction_id"]},
            int(batch["scene_generation"]),
        )
        assert not await base.object_exists(client, "Template Head")
        report["batch_rollback"] = {"batch": batch, "rollback": batch_rollback}

        stage("committed batch persistence, native-save adoption, reload, and render")
        current = await base.mesh(client, "Modular Source")
        destination = await collection(client, "Library Outputs")
        commit_begin = await base.begin(
            client, int(current["scene_generation"]), "0.16 committed template batch"
        )
        committed_batch = await execute_template_batch(
            client,
            transaction_id=commit_begin["transaction_id"],
            baseline=current,
            destination=destination,
            library=library,
            generation=int(commit_begin["scene_generation"]),
            collection_name="Committed Template Assembly",
            prefix="Committed Template",
            idempotency_key=str(uuid4()),
        )
        committed = await base.mutate(
            client,
            "transaction.commit",
            {"transaction_id": commit_begin["transaction_id"]},
            int(committed_batch["scene_generation"]),
        )
        current = await base.mesh(client, "Modular Source")
        native_begin = await base.begin(
            client, int(current["scene_generation"]), "0.16 native-save append"
        )
        destination = await collection(client, "Library Outputs")
        native_write = await append(
            client,
            native_begin["transaction_id"],
            library,
            entry(library, "OBJECT", "Loose Template Carrier"),
            {
                "type": "OBJECT",
                "new_object_name": "Native Saved Template",
                "collection": collection_evidence(destination),
            },
            int(native_begin["scene_generation"]),
        )
        native_save = await client.call("_test.native_save", {}, read_only=False)
        await asyncio.sleep(0.2)
        report["native_save"] = {"write": native_write, "save": native_save}
        save = await manager.project_save()
        reload_result = await manager.project_reload(
            save_current=False, use_scripts=False, load_ui=False
        )
        persisted_head = await base.mesh(client, "Template Head")
        persisted_native = await base.mesh(client, "Native Saved Template")
        persisted_collection = await collection(client, "Committed Template Assembly")
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
            expected_scene_generation=int(persisted_head["scene_generation"]),
            idempotency_key=str(uuid4()),
        )
        render_path = artifact_directory / "library-template-0.16.0.png"
        render_path.write_bytes(image)
        report["persistence"] = {
            "batch": committed_batch,
            "commit": committed,
            "save": save,
            "reload": reload_result,
            "head": persisted_head,
            "native": persisted_native,
            "collection": persisted_collection,
            "render": render,
            "render_path": str(render_path),
            "render_sha256": hashlib.sha256(image).hexdigest(),
        }

        if args.character_project is not None:
            await check_character_coverage(
                client,
                manager,
                source_path=args.character_project,
                temporary=temporary,
                library=library,
                report=report,
            )

        context_after = await client.call("context.get", read_only=True)
        report["context"] = {"before": context_before, "after": context_after}
        report["fixture_source_sha256_after"] = base.sha256(fixture_source)
        report["library_source_sha256_after"] = base.sha256(library_path)
        if report["fixture_source_sha256_after"] != fixture_hash:
            raise RuntimeError("Scene source fixture changed during smoke")
        if report["library_source_sha256_after"] != library_hash:
            raise RuntimeError("Library source fixture changed during smoke")
        report["ping_after"] = await client.call("connection.ping", read_only=True)
        report["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 3)
        report["finished_at"] = datetime.now(UTC).isoformat()
        report_path = artifact_directory / "report-0.16.0.json"
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
    parser.add_argument("--port", type=int, default=9897)
    parser.add_argument("--timeout", type=float, default=90.0)
    parser.add_argument("--character-project", type=Path)
    return parser.parse_args()


def main() -> int:
    try:
        report = asyncio.run(run(parse_args()))
    except BridgeError as exc:
        print(
            json.dumps(exc.error.model_dump(mode="json"), ensure_ascii=False),
            flush=True,
        )
        raise
    print(json.dumps({"run_id": report["run_id"], "elapsed_ms": report["elapsed_ms"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
