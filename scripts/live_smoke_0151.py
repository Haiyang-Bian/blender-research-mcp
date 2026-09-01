"""Run Blender 4.2 ComponentCatalog and cross-object assembly acceptance."""

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

import live_smoke_015 as base

from blender_research_mcp.client import BridgeClient
from blender_research_mcp.constants import MAX_DEADLINE_MS, PACKAGE_VERSION
from blender_research_mcp.errors import BridgeError
from blender_research_mcp.lifecycle import ApplicationManager
from blender_research_mcp.rendering import request_render_preview

ROOT = Path(__file__).resolve().parents[1]


def stage(name: str) -> None:
    print(f"[0.15.1 smoke] {name}", flush=True)


async def inspect_object(client: BridgeClient, name: str) -> dict[str, Any]:
    return await client.call("object.inspect", {"object_name": name}, read_only=True)


async def inspect_collection(client: BridgeClient, name: str) -> dict[str, Any]:
    return await client.call(
        "collection.inspect",
        {"collection_name": name, "offset": 0, "limit": 256},
        read_only=True,
    )


async def scene(client: BridgeClient) -> dict[str, Any]:
    return await client.call(
        "scene.inspect",
        {"kinds": ["objects", "collections"], "name_filter": None, "limit": 256},
        read_only=True,
    )


def scene_root_parent(value: dict[str, Any]) -> dict[str, Any]:
    root = value["scene_root"]
    return {
        "type": "SCENE_ROOT",
        "scene_name": root["scene_name"],
        "expected_scene_identity": root["scene_identity"],
        "expected_scene_structure_fingerprint": root["scene_structure_fingerprint"],
    }


def collection_parent(value: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "COLLECTION",
        "collection_name": value["name"],
        "expected_collection_identity": value["session_identity"],
        "expected_collection_structure_fingerprint": value["structure_fingerprint"],
    }


def batch_target(alias: str, value: dict[str, Any]) -> dict[str, Any]:
    return {"alias": alias, **base.extract_target(value)}


def armature_input(alias: str, value: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "armature",
        "alias": alias,
        "target": {
            "object_name": value["object_name"],
            "expected_object_identity": value["object_identity"],
            "expected_data_identity": value["data_identity"],
            "expected_bone_schema_fingerprint": value["bone_schema_fingerprint"],
        },
    }


def object_input(alias: str, value: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "alias": alias,
        "object_name": value["name"],
        "expected_object_identity": value["session_identity"],
        "expected_object_structure_fingerprint": value["structure_fingerprint"],
    }


async def catalog(
    client: BridgeClient,
    selection_id: str,
    *,
    limit: int = 128,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    prepared = await client.call(
        "mesh.component_catalog.prepare",
        {
            "selection_id": selection_id,
            "include": ["COUNT", "AREA", "BOUNDS", "MATERIALS", "BOUNDARY_COUNT"],
        },
        read_only=True,
    )
    items: list[dict[str, Any]] = []
    offset = 0
    while True:
        page = await client.call(
            "mesh.component_catalog.inspect",
            {
                "component_catalog_id": prepared["component_catalog_id"],
                "offset": offset,
                "limit": limit,
            },
            read_only=True,
        )
        items.extend(page["items"])
        next_offset = page["pagination"]["next_offset"]
        if next_offset is None:
            break
        offset = int(next_offset)
    return prepared, items


def assembly_steps(
    *,
    components: list[str],
    prefix: str,
    shape_fingerprint: str,
    root_parent: dict[str, Any],
    fail_validation: bool = False,
    copy_weights: bool = True,
    synthetic_deform_group: str | None = None,
) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = [
        {
            "type": "component_catalog_select",
            "catalog_alias": "shells",
            "component_identities": components,
            "output_selection_alias": "chosen_faces",
        },
        {
            "type": "collection_create",
            "name": f"{prefix} Modules",
            "parent": root_parent,
            "output_collection_alias": "modules",
        },
        {
            "type": "collection_create",
            "name": f"{prefix} Extracted",
            "parent": {"type": "COLLECTION_ALIAS", "collection_alias": "modules"},
            "output_collection_alias": "extracted_collection",
        },
        {
            "type": "mesh_materialize",
            "source_target_alias": "source",
            "evaluation": {
                "type": "SHAPE_KEYS_CURRENT",
                "expected_shape_key_state_fingerprint": shape_fingerprint,
            },
            "new_object_name": f"{prefix} Working",
            "copy": {"materials": True, "uv": True, "weights": copy_weights},
            "output_target_alias": "working",
            "collection_alias": "modules",
            "map_alias": "materialization_map",
        },
        {
            "type": "mesh_extract",
            "target_alias": "working",
            "selection_alias": "chosen_faces",
            "new_target_alias": "module",
            "new_selection_alias": "module_faces",
            "source_map_alias": "working_source_map",
            "extracted_map_alias": "module_map",
            "new_object_name": f"{prefix} Module",
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
            "collection_alias": "extracted_collection",
        },
        {
            "type": "collection_link_object",
            "collection_alias": "modules",
            "object_alias": "module",
        },
        {
            "type": "collection_unlink_object",
            "collection_alias": "extracted_collection",
            "object_alias": "module",
        },
        {
            "type": "object_parent_set",
            "child_alias": "module",
            "parent_alias": "working",
            "transform_mode": "KEEP_LOCAL",
        },
        {
            "type": "object_parent_clear",
            "child_alias": "module",
            "expected_parent_alias": "working",
            "transform_mode": "KEEP_LOCAL",
        },
        {
            "type": "selection_derive",
            "output_alias": "module_vertices",
            "operation": {
                "type": "convert",
                "selection_alias": "module_faces",
                "domain": "VERTEX",
            },
        },
    ]
    if synthetic_deform_group is not None:
        steps.append(
            {
                "type": "weights_edit",
                "target_alias": "module",
                "data_scope": "OBJECT",
                "operation": {
                    "type": "group_create",
                    "group_name": synthetic_deform_group,
                },
            }
        )
    steps.append(
        {
            "type": "rig_bind",
            "mesh_target_alias": "module",
            "armature_alias": "rig",
            "modifier": {
                "name": f"{prefix} Armature",
                "expected_existing": None,
                "use_vertex_groups": True,
                "use_bone_envelopes": False,
                "preserve_volume": True,
                "use_multi_modifier": False,
                "vertex_group": None,
            },
            "parenting": "KEEP_WORLD",
            "group_scope": (
                {"type": "EXPLICIT", "group_names": [synthetic_deform_group]}
                if synthetic_deform_group is not None
                else {"type": "ALL_MATCHED"}
            ),
            "output_binding_alias": "binding",
        }
    )
    if fail_validation:
        steps.append(
            {
                "type": "mesh_validate",
                "selection_alias": "module_faces",
                "check": "NON_MANIFOLD",
                "output_alias": "forced_failure",
                "assertions": [{"type": "count_at_most", "value": 0}],
            }
        )
    else:
        steps.append(
            {
                "type": "mesh_validate",
                "selection_alias": "module_vertices",
                "check": (
                    "DEFORM_GROUP_MISMATCH"
                    if synthetic_deform_group is not None
                    else "WEIGHT_UNASSIGNED"
                ),
                "output_alias": "weights_valid",
                "assertions": [{"type": "count_at_most", "value": 0}],
            }
        )
    return steps


async def execute_batch(
    client: BridgeClient,
    *,
    transaction_id: str,
    target: dict[str, Any],
    catalog_id: str,
    armature: dict[str, Any],
    rig_object: dict[str, Any],
    steps: list[dict[str, Any]],
    generation: int,
    idempotency_key: str,
) -> dict[str, Any]:
    return await client.call(
        "mesh.batch.execute",
        {
            "transaction_id": transaction_id,
            "targets": [batch_target("source", target)],
            "inputs": [
                {
                    "type": "component_catalog",
                    "alias": "shells",
                    "component_catalog_id": catalog_id,
                    "target_alias": "source",
                },
                armature_input("rig", armature),
                object_input("rig_object", rig_object),
            ],
            "steps": steps,
            "on_error": "ROLLBACK_TRANSACTION",
        },
        deadline_ms=MAX_DEADLINE_MS,
        expected_scene_generation=generation,
        idempotency_key=idempotency_key,
        read_only=False,
    )


async def run(args: argparse.Namespace) -> dict[str, Any]:
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + f"-{uuid4().hex[:8]}"
    temporary = Path(tempfile.gettempdir()) / "blender-research-mcp-assembly" / run_id
    temporary.mkdir(parents=True, exist_ok=False)
    artifact_directory = ROOT / "artifacts" / "live-smoke" / run_id
    artifact_directory.mkdir(parents=True, exist_ok=False)
    source_path = temporary / "assembly-source.blend"
    project_path = temporary / "assembly-project.blend"
    base.build_fixture(args.blender_executable, source_path)
    source_hash = base.sha256(source_path)
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
        stage("managed launch, project open, and transaction-v11 handshake")
        report["launch"] = await manager.launch()
        launched = True
        report["project_open"] = await manager.project_open(
            str(project_path), save_current=False, use_scripts=False, load_ui=False
        )
        ping = await client.call("connection.ping", read_only=True)
        report["ping_before"] = ping
        for capability, minimum in {
            "transactions": 11,
            "mesh_component_catalog": 1,
            "collection_authoring": 1,
            "object_parenting": 1,
            "mesh_batch": 3,
            "mesh_materialization": 1,
            "mesh_extraction": 1,
            "rig_binding": 1,
        }.items():
            if int(ping["capability_versions"].get(capability, 0)) < minimum:
                raise RuntimeError(f"Missing capability {capability}:{minimum}")

        baseline = await base.mesh(client, "Modular Source")
        baseline_object = await inspect_object(client, "Modular Source")
        rig_object = await inspect_object(client, "Modular Rig")
        rig_report = await client.call(
            "rig.inspect",
            {
                "object_name": "Modular Source",
                "armature_object_name": "Modular Rig",
                "offset": 0,
                "limit": 256,
            },
            read_only=True,
        )
        armature = rig_report["armatures"][0]
        context_before = await client.call("context.get", read_only=True)
        root_scene = await scene(client)
        all_faces = await base.face_selection(client, baseline, [0, 1, 2])

        stage("deterministic catalog pagination, weighted selection, and stale detection")
        prepared, components = await catalog(client, all_faces["selection_id"], limit=2)
        assert prepared["component_count"] == 3
        assert [item["component_index"] for item in components] == [0, 1, 2]
        chosen_identities = [
            components[0]["component_identity"],
            components[2]["component_identity"],
        ]
        selected = await client.call(
            "mesh.component_catalog.select",
            {
                "component_catalog_id": prepared["component_catalog_id"],
                "component_identities": chosen_identities,
            },
            read_only=True,
        )
        assert selected["component_count"] == 2

        stale_begin = await base.begin(
            client, int(baseline["scene_generation"]), "0.15.1 catalog stale"
        )
        stale_work = await base.materialize(
            client,
            stale_begin["transaction_id"],
            baseline,
            {"type": "BASE"},
            "Catalog Stale Working",
            int(stale_begin["scene_generation"]),
        )
        stale_mesh = await base.mesh(client, "Catalog Stale Working")
        stale_faces = await base.face_selection(client, stale_mesh, [0, 1, 2])
        stale_catalog, _stale_items = await catalog(client, stale_faces["selection_id"])
        changed_mesh = await base.mutate(
            client,
            "mesh.edit",
            {
                "transaction_id": stale_begin["transaction_id"],
                **base.extract_target(stale_mesh),
                "data_scope": "OBJECT",
                "operation": {
                    "type": "face_settings",
                    "face_indices": [0],
                    "smooth": True,
                },
            },
            int(stale_work["scene_generation"]),
        )
        stale_error = None
        try:
            await client.call(
                "mesh.component_catalog.inspect",
                {
                    "component_catalog_id": stale_catalog["component_catalog_id"],
                    "offset": 0,
                    "limit": 128,
                },
                read_only=True,
            )
        except BridgeError as exc:
            stale_error = exc.error.model_dump(mode="json")
        assert stale_error is not None and stale_error["code"] == "MESH_COMPONENT_CATALOG_STALE"
        stale_rollback = await base.mutate(
            client,
            "transaction.rollback",
            {"transaction_id": stale_begin["transaction_id"]},
            int(changed_mesh["scene_generation"]),
        )
        report["catalog"] = {
            "prepared": prepared,
            "components": components,
            "selected": selected,
            "stale_error": stale_error,
            "rollback": stale_rollback,
        }

        stage("exact nested Collections and KEEP_WORLD parenting rollback")
        organization_begin = await base.begin(
            client, int(stale_rollback["scene_generation"]), "0.15.1 organization"
        )
        root_collection = await base.mutate(
            client,
            "collection.create",
            {
                "transaction_id": organization_begin["transaction_id"],
                "name": "Direct Modules",
                "parent": scene_root_parent(await scene(client)),
            },
            int(organization_begin["scene_generation"]),
        )
        nested_collection = await base.mutate(
            client,
            "collection.create",
            {
                "transaction_id": organization_begin["transaction_id"],
                "name": "Direct Nested",
                "parent": collection_parent(root_collection["collection"]),
            },
            int(root_collection["scene_generation"]),
        )
        source_object = await inspect_object(client, "Modular Source")
        root_evidence = await inspect_collection(client, "Direct Modules")
        linked_root = await base.mutate(
            client,
            "collection.link_object",
            {
                "transaction_id": organization_begin["transaction_id"],
                "collection_name": root_evidence["name"],
                "expected_collection_identity": root_evidence["session_identity"],
                "expected_collection_structure_fingerprint": root_evidence[
                    "structure_fingerprint"
                ],
                "object_name": source_object["name"],
                "expected_object_identity": source_object["session_identity"],
                "expected_object_collections_fingerprint": source_object[
                    "collections_fingerprint"
                ],
            },
            int(nested_collection["scene_generation"]),
        )
        source_object = await inspect_object(client, "Modular Source")
        nested_evidence = await inspect_collection(client, "Direct Nested")
        linked_nested = await base.mutate(
            client,
            "collection.link_object",
            {
                "transaction_id": organization_begin["transaction_id"],
                "collection_name": nested_evidence["name"],
                "expected_collection_identity": nested_evidence["session_identity"],
                "expected_collection_structure_fingerprint": nested_evidence[
                    "structure_fingerprint"
                ],
                "object_name": source_object["name"],
                "expected_object_identity": source_object["session_identity"],
                "expected_object_collections_fingerprint": source_object[
                    "collections_fingerprint"
                ],
            },
            int(linked_root["scene_generation"]),
        )
        source_object = await inspect_object(client, "Modular Source")
        root_evidence = await inspect_collection(client, "Direct Modules")
        unlinked_root = await base.mutate(
            client,
            "collection.unlink_object",
            {
                "transaction_id": organization_begin["transaction_id"],
                "collection_name": root_evidence["name"],
                "expected_collection_identity": root_evidence["session_identity"],
                "expected_collection_structure_fingerprint": root_evidence[
                    "structure_fingerprint"
                ],
                "object_name": source_object["name"],
                "expected_object_identity": source_object["session_identity"],
                "expected_object_collections_fingerprint": source_object[
                    "collections_fingerprint"
                ],
            },
            int(linked_nested["scene_generation"]),
        )
        child = await inspect_object(client, "Modular Source")
        parent = await inspect_object(client, "Modular Rig")
        parented = await base.mutate(
            client,
            "object.parent.set",
            {
                "transaction_id": organization_begin["transaction_id"],
                "child_name": child["name"],
                "expected_child_identity": child["session_identity"],
                "expected_child_structure_fingerprint": child["structure_fingerprint"],
                "parent_name": parent["name"],
                "expected_parent_identity": parent["session_identity"],
                "expected_parent_structure_fingerprint": parent["structure_fingerprint"],
                "transform_mode": "KEEP_WORLD",
            },
            int(unlinked_root["scene_generation"]),
        )
        organization_rollback = await base.mutate(
            client,
            "transaction.rollback",
            {"transaction_id": organization_begin["transaction_id"]},
            int(parented["scene_generation"]),
        )
        after_organization = await scene(client)
        restored_object = await inspect_object(client, "Modular Source")
        assert all(
            item["name"] not in {"Direct Modules", "Direct Nested"}
            for item in after_organization["collections"]
        )
        assert restored_object["parent"] == baseline_object["parent"]
        report["organization_rollback"] = {
            "root": root_collection,
            "nested": nested_collection,
            "link_root": linked_root,
            "link_nested": linked_nested,
            "unlink_root": unlinked_root,
            "parent": parented,
            "rollback": organization_rollback,
        }

        stage("batch v3 assembly, idempotent replay, manifest, and rollback")
        current = await base.mesh(client, "Modular Source")
        root_scene = await scene(client)
        batch_begin = await base.begin(
            client, int(current["scene_generation"]), "0.15.1 assembly replay"
        )
        batch_key = str(uuid4())
        batch_steps = assembly_steps(
            components=chosen_identities,
            prefix="Replay",
            shape_fingerprint=current["shape_key_state_fingerprint"],
            root_parent=scene_root_parent(root_scene),
        )
        assembled = await execute_batch(
            client,
            transaction_id=batch_begin["transaction_id"],
            target=current,
            catalog_id=prepared["component_catalog_id"],
            armature=armature,
            rig_object=rig_object,
            steps=batch_steps,
            generation=int(batch_begin["scene_generation"]),
            idempotency_key=batch_key,
        )
        counts_after = (await scene(client))["objects"]
        replayed = await execute_batch(
            client,
            transaction_id=batch_begin["transaction_id"],
            target=current,
            catalog_id=prepared["component_catalog_id"],
            armature=armature,
            rig_object=rig_object,
            steps=batch_steps,
            generation=int(batch_begin["scene_generation"]),
            idempotency_key=batch_key,
        )
        assert (
            assembled["assembly_manifest"]["content_sha256"]
            == replayed["assembly_manifest"]["content_sha256"]
        )
        assert len((await scene(client))["objects"]) == len(counts_after)
        assert assembled["aliases"]["chosen_faces"]["target_alias"] == "working"
        batch_rollback = await base.mutate(
            client,
            "transaction.rollback",
            {"transaction_id": batch_begin["transaction_id"]},
            int(assembled["scene_generation"]),
        )
        assert not await base.object_exists(client, "Replay Working")
        assert not await base.object_exists(client, "Replay Module")
        report["batch_replay"] = {
            "result": assembled,
            "replay": replayed,
            "rollback": batch_rollback,
        }

        stage("batch runtime assertion failure restores transaction begin")
        current = await base.mesh(client, "Modular Source")
        failure_begin = await base.begin(
            client, int(current["scene_generation"]), "0.15.1 forced batch rollback"
        )
        failure = None
        try:
            await execute_batch(
                client,
                transaction_id=failure_begin["transaction_id"],
                target=current,
                catalog_id=prepared["component_catalog_id"],
                armature=armature,
                rig_object=rig_object,
                steps=assembly_steps(
                    components=chosen_identities,
                    prefix="Failure",
                    shape_fingerprint=current["shape_key_state_fingerprint"],
                    root_parent=scene_root_parent(await scene(client)),
                    fail_validation=True,
                ),
                generation=int(failure_begin["scene_generation"]),
                idempotency_key=str(uuid4()),
            )
        except BridgeError as exc:
            failure = exc.error.model_dump(mode="json")
        assert failure is not None and failure["code"] == "MESH_BATCH_ASSERTION_FAILED"
        assert not await base.object_exists(client, "Failure Working")
        assert not await base.object_exists(client, "Failure Module")
        report["batch_failure"] = failure

        stage("disconnect rollback of new Collection structure")
        current = await base.mesh(client, "Modular Source")
        disconnect_begin = await base.begin(
            client, int(current["scene_generation"]), "0.15.1 disconnect"
        )
        disconnect_write = await base.mutate(
            client,
            "collection.create",
            {
                "transaction_id": disconnect_begin["transaction_id"],
                "name": "Disconnect Collection",
                "parent": scene_root_parent(await scene(client)),
            },
            int(disconnect_begin["scene_generation"]),
        )
        await client.close()
        await asyncio.sleep(3.0)
        assert all(
            item["name"] != "Disconnect Collection"
            for item in (await scene(client))["collections"]
        )
        report["disconnect"] = {"write": disconnect_write, "rolled_back": True}

        stage("native-save adoption and committed batch persistence")
        current = await base.mesh(client, "Modular Source")
        native_begin = await base.begin(
            client, int(current["scene_generation"]), "0.15.1 native save"
        )
        native_write = await base.mutate(
            client,
            "collection.create",
            {
                "transaction_id": native_begin["transaction_id"],
                "name": "Native Saved Assembly",
                "parent": scene_root_parent(await scene(client)),
            },
            int(native_begin["scene_generation"]),
        )
        native_save = await client.call("_test.native_save", {}, read_only=False)
        await inspect_collection(client, "Native Saved Assembly")
        report["native_save"] = {"write": native_write, "save": native_save}

        current = await base.mesh(client, "Modular Source")
        fresh_faces = await base.face_selection(client, current, [0, 1, 2])
        fresh_catalog, fresh_components = await catalog(client, fresh_faces["selection_id"])
        commit_begin = await base.begin(
            client, int(current["scene_generation"]), "0.15.1 committed assembly"
        )
        committed = await execute_batch(
            client,
            transaction_id=commit_begin["transaction_id"],
            target=current,
            catalog_id=fresh_catalog["component_catalog_id"],
            armature=armature,
            rig_object=await inspect_object(client, "Modular Rig"),
            steps=assembly_steps(
                components=[
                    fresh_components[0]["component_identity"],
                    fresh_components[2]["component_identity"],
                ],
                prefix="Committed",
                shape_fingerprint=current["shape_key_state_fingerprint"],
                root_parent=scene_root_parent(await scene(client)),
            ),
            generation=int(commit_begin["scene_generation"]),
            idempotency_key=str(uuid4()),
        )
        commit = await base.mutate(
            client,
            "transaction.commit",
            {"transaction_id": commit_begin["transaction_id"]},
            int(committed["scene_generation"]),
        )
        save = await manager.project_save()
        reload_result = await manager.project_reload(
            save_current=False, use_scripts=False, load_ui=False
        )
        persisted_collection = await inspect_collection(client, "Committed Modules")
        persisted_module = await base.mesh(client, "Committed Module")
        persisted_rig = await client.call(
            "rig.inspect",
            {
                "object_name": "Committed Module",
                "armature_object_name": "Modular Rig",
                "offset": 0,
                "limit": 256,
            },
            read_only=True,
        )
        camera = await inspect_object(client, "Modular Camera")
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
            expected_scene_generation=int(persisted_module["scene_generation"]),
            idempotency_key=str(uuid4()),
        )
        render_path = artifact_directory / "assembly-0.15.1.png"
        render_path.write_bytes(image)
        report["persistence"] = {
            "batch": committed,
            "commit": commit,
            "save": save,
            "reload": reload_result,
            "collection": persisted_collection,
            "module": persisted_module,
            "rig": persisted_rig,
            "render": render,
            "render_path": str(render_path),
            "render_sha256": hashlib.sha256(image).hexdigest(),
        }

        if args.character_project is not None:
            stage("real character hair catalog and batch rollback")
            character_source = args.character_project.resolve(strict=True)
            character_hash = base.sha256(character_source)
            character_project = temporary / "test-model.blend"
            shutil.copy2(character_source, character_project)
            character_open = await manager.project_open(
                str(character_project), save_current=False, use_scripts=False, load_ui=False
            )
            character_mesh = await base.mesh(client, "绯雪_edit_mesh")
            hair_slots = [
                int(item["slot_index"])
                for item in character_mesh["mesh"]["material_slots"]
                if "Hair" in str(item.get("material_name") or "")
            ]
            if not hair_slots:
                raise RuntimeError("Character fixture exposes no semantically named hair slots")
            hair_faces = await base.material_face_selection(client, character_mesh, hair_slots)
            hair_catalog, hair_components = await catalog(
                client, hair_faces["selection_id"], limit=256
            )
            if len(hair_components) < 2:
                raise RuntimeError("Hair material query no longer yields multiple components")
            selected_hair_components = hair_components[:8]
            character_rig = await client.call(
                "rig.inspect",
                {
                    "object_name": "绯雪_edit_mesh",
                    "armature_object_name": "绯雪_edit_arm",
                    "offset": 0,
                    "limit": 256,
                },
                read_only=True,
            )
            character_begin = await base.begin(
                client,
                int(character_mesh["scene_generation"]),
                "0.15.1 character catalog assembly",
            )
            character_batch = await execute_batch(
                client,
                transaction_id=character_begin["transaction_id"],
                target=character_mesh,
                catalog_id=hair_catalog["component_catalog_id"],
                armature=character_rig["armatures"][0],
                rig_object=await inspect_object(client, "绯雪_edit_arm"),
                steps=assembly_steps(
                    components=[
                        item["component_identity"] for item in selected_hair_components
                    ],
                    prefix="绯雪_0.15.1_头发",
                    shape_fingerprint=character_mesh["shape_key_state_fingerprint"],
                    root_parent=scene_root_parent(await scene(client)),
                    copy_weights=False,
                    synthetic_deform_group="頭",
                ),
                generation=int(character_begin["scene_generation"]),
                idempotency_key=str(uuid4()),
            )
            character_rollback = await base.mutate(
                client,
                "transaction.rollback",
                {"transaction_id": character_begin["transaction_id"]},
                int(character_batch["scene_generation"]),
            )
            restored_character = await base.mesh(client, "绯雪_edit_mesh")
            assert restored_character["mesh_fingerprint"] == character_mesh["mesh_fingerprint"]
            assert not await base.object_exists(client, "绯雪_0.15.1_头发 Working")
            assert not await base.object_exists(client, "绯雪_0.15.1_头发 Module")
            if base.sha256(character_source) != character_hash:
                raise RuntimeError("Source test-model.blend changed during the smoke")
            report["character"] = {
                "source": str(character_source),
                "source_sha256_before": character_hash,
                "source_sha256_after": base.sha256(character_source),
                "project": str(character_project),
                "project_open": character_open,
                "hair_material_slots": hair_slots,
                "hair_catalog": hair_catalog,
                "hair_component_count": len(hair_components),
                "selected_hair_component_count": len(selected_hair_components),
                "batch": character_batch,
                "rollback": character_rollback,
            }

        context_after = await client.call("context.get", read_only=True)
        report["context"] = {"before": context_before, "after": context_after}
        report["fixture_source_sha256_after"] = base.sha256(source_path)
        if report["fixture_source_sha256_after"] != source_hash:
            raise RuntimeError("Source fixture changed during the smoke")
        report["ping_after"] = await client.call("connection.ping", read_only=True)
        report["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 3)
        report["finished_at"] = datetime.now(UTC).isoformat()
        report_path = artifact_directory / "report-0.15.1.json"
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
    parser.add_argument("--port", type=int, default=9896)
    parser.add_argument("--timeout", type=float, default=90.0)
    parser.add_argument("--character-project", type=Path)
    return parser.parse_args()


def main() -> int:
    report = asyncio.run(run(parse_args()))
    print(json.dumps({"run_id": report["run_id"], "elapsed_ms": report["elapsed_ms"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
