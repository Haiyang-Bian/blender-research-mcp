"""Run focused Blender 4.2 ComponentMap and bounded topology acceptance for 0.13.0."""

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
    print(f"[0.13 smoke] {name}", flush=True)


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
        raise RuntimeError(f"Could not build 0.13 fixture; see {log_path}")


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


async def inspect_mesh(client: BridgeClient, object_name: str) -> dict[str, Any]:
    return await client.call(
        "mesh.inspect",
        {"object_name": object_name, "component": "summary", "offset": 0, "limit": 256},
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


def exact_mesh_state(inspected: dict[str, Any]) -> dict[str, Any]:
    return {
        "mesh_identity": inspected["mesh"]["session_identity"],
        "mesh_revision_id": inspected["mesh_revision_id"],
        "mesh_fingerprint": inspected["mesh_fingerprint"],
        "topology_fingerprint": inspected["topology_fingerprint"],
        "counts": inspected["counts"],
        "users": inspected["mesh"]["users"],
        "user_objects": inspected["user_objects"],
        "uv_layers": inspected["mesh"]["uv_layers"],
        "color_attributes": inspected["mesh"]["color_attributes"],
    }


def persistent_mesh_state(inspected: dict[str, Any]) -> dict[str, Any]:
    return {
        "topology_fingerprint": inspected["topology_fingerprint"],
        "counts": inspected["counts"],
        "uv_layers": inspected["mesh"]["uv_layers"],
        "color_attributes": inspected["mesh"]["color_attributes"],
    }


async def selection_query(
    client: BridgeClient,
    inspected: dict[str, Any],
    domain: str,
    query: dict[str, Any],
) -> dict[str, Any]:
    return await client.call(
        "mesh.selection.query",
        {
            "object_name": inspected["object"]["name"],
            "expected_object_identity": inspected["object"]["session_identity"],
            "expected_mesh_identity": inspected["mesh"]["session_identity"],
            "expected_mesh_revision_id": inspected["mesh_revision_id"],
            "domain": domain,
            "query": query,
        },
        read_only=True,
    )


def edit_params(
    transaction_id: str,
    inspected: dict[str, Any],
    operation: dict[str, Any],
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
        "data_scope": "OBJECT",
        "operation": operation,
    }


async def expect_error(awaitable: Any, code: str) -> dict[str, Any]:
    try:
        await awaitable
    except BridgeError as exc:
        if exc.error.code != code:
            raise
        return exc.error.model_dump(mode="json")
    raise RuntimeError(f"Expected {code}")


async def run_operation(
    client: BridgeClient,
    object_name: str,
    domain: str,
    query: dict[str, Any],
    operation: dict[str, Any],
    finish_command: str,
    *,
    bind_selection: bool = True,
) -> dict[str, Any]:
    before = await inspect_mesh(client, object_name)
    selection = await selection_query(client, before, domain, query)
    if bind_selection:
        operation = {**operation, "selection_id": selection["selection_id"]}
    transaction = await mutate(
        client,
        "transaction.begin",
        {"label": f"0.13 {operation['type']}", "viewport_id": None},
        int(before["scene_generation"]),
    )
    edited = await mutate(
        client,
        "mesh.edit",
        edit_params(str(transaction["transaction_id"]), before, operation),
        int(transaction["scene_generation"]),
    )
    if not edited["changed"] or edited["component_map"] is None:
        raise RuntimeError(f"{operation['type']} did not return a ComponentMap")
    component_map_id = str(edited["component_map"]["component_map_id"])
    map_summary = await client.call(
        "mesh.component_map.inspect",
        {
            "component_map_id": component_map_id,
            "domain": "SUMMARY",
            "direction": "FORWARD",
            "offset": 0,
            "limit": 256,
        },
        read_only=True,
    )
    map_forward = await client.call(
        "mesh.component_map.inspect",
        {
            "component_map_id": component_map_id,
            "domain": domain,
            "direction": "FORWARD",
            "offset": 0,
            "limit": 256,
        },
        read_only=True,
    )
    map_reverse = await client.call(
        "mesh.component_map.inspect",
        {
            "component_map_id": component_map_id,
            "domain": domain,
            "direction": "REVERSE",
            "offset": 0,
            "limit": 256,
        },
        read_only=True,
    )
    remapped = await client.call(
        "mesh.selection.remap",
        {
            "selection_id": selection["selection_id"],
            "component_map_id": component_map_id,
            "mode": "ALL_MAPPED",
            "weight_merge": "MAX",
        },
        read_only=True,
    )
    finished = await mutate(
        client,
        finish_command,
        {"transaction_id": transaction["transaction_id"]},
        int(edited["scene_generation"]),
    )
    after = await inspect_mesh(client, object_name)
    stale = None
    if finish_command == "transaction.rollback":
        if before["mesh_fingerprint"] != after["mesh_fingerprint"]:
            raise RuntimeError(f"{operation['type']} rollback did not restore the Mesh")
        stale = await expect_error(
            client.call(
                "mesh.selection.remap",
                {
                    "selection_id": selection["selection_id"],
                    "component_map_id": component_map_id,
                    "mode": "ALL_MAPPED",
                    "weight_merge": "MAX",
                },
                read_only=True,
            ),
            "MESH_COMPONENT_MAP_STALE",
        )
    return {
        "before": before,
        "selection": selection,
        "edit": edited,
        "map": map_summary,
        "map_forward": map_forward,
        "map_reverse": map_reverse,
        "remapped": remapped,
        "finish": finished,
        "after": after,
        "stale_after_rollback": stale,
    }


async def check_continuous_revisions(
    client: BridgeClient,
    manager: ApplicationManager,
    report: dict[str, Any],
) -> None:
    stage("continuous ComponentMap remap, UI collaboration, commit, save, and reload")
    before = await inspect_mesh(client, "Topology Chain")
    source = await selection_query(client, before, "EDGE", {"type": "all"})
    transaction = await begin(
        client, int(before["scene_generation"]), "0.13 continuous revision chain"
    )
    first = await mutate(
        client,
        "mesh.edit",
        edit_params(
            str(transaction["transaction_id"]),
            before,
            {
                "type": "subdivide",
                "selection_id": source["selection_id"],
                "cuts": 1,
            },
        ),
        int(transaction["scene_generation"]),
    )
    context_touch = await client.call(
        "_test.context.touch",
        {
            "viewport_id": None,
            "active_object": "Topology Fill",
            "shading": "WIREFRAME",
            "show_overlays": False,
        },
        read_only=False,
    )
    current = await inspect_mesh(client, "Topology Chain")
    second = await mutate(
        client,
        "mesh.edit",
        edit_params(
            str(transaction["transaction_id"]),
            current,
            {
                "type": "subdivide",
                "selection_id": first["rebound_selection"]["selection_id"],
                "cuts": 1,
            },
        ),
        int(first["scene_generation"]),
    )
    first_map_stale = await expect_error(
        client.call(
            "mesh.selection.remap",
            {
                "selection_id": source["selection_id"],
                "component_map_id": first["component_map"]["component_map_id"],
                "mode": "ALL_MAPPED",
                "weight_merge": "MAX",
            },
            read_only=True,
        ),
        "MESH_COMPONENT_MAP_STALE",
    )
    committed = await finish(
        client,
        "transaction.commit",
        str(transaction["transaction_id"]),
        int(second["scene_generation"]),
    )
    persisted = await inspect_mesh(client, "Topology Chain")
    saved = await manager.project_save()
    reloaded_result = await manager.project_reload(
        save_current=False, use_scripts=False, load_ui=False
    )
    reloaded = await inspect_mesh(client, "Topology Chain")
    if persistent_mesh_state(persisted) != persistent_mesh_state(reloaded):
        raise RuntimeError("Committed revision chain did not survive save/reload")
    report["continuous_revisions"] = {
        "before": exact_mesh_state(before),
        "source_selection": source,
        "first": first,
        "context_touch": context_touch,
        "second": second,
        "first_map_stale": first_map_stale,
        "commit": committed,
        "persisted": exact_mesh_state(persisted),
        "save": saved,
        "reload": reloaded_result,
        "reloaded": exact_mesh_state(reloaded),
    }


async def check_disconnect_rollback(client: BridgeClient, report: dict[str, Any]) -> None:
    stage("disconnect rollback restores the baseline revision")
    before = await inspect_mesh(client, "Topology Disconnect")
    selection = await selection_query(client, before, "EDGE", {"type": "all"})
    transaction = await begin(
        client, int(before["scene_generation"]), "0.13 disconnect rollback"
    )
    edited = await mutate(
        client,
        "mesh.edit",
        edit_params(
            str(transaction["transaction_id"]),
            before,
            {
                "type": "subdivide",
                "selection_id": selection["selection_id"],
                "cuts": 1,
            },
        ),
        int(transaction["scene_generation"]),
    )
    await client.close()
    await asyncio.sleep(3.0)
    ping = await client.call("connection.ping", read_only=True)
    after = await inspect_mesh(client, "Topology Disconnect")
    if exact_mesh_state(after) != exact_mesh_state(before):
        raise RuntimeError("Disconnect did not restore the baseline topology revision")
    stale = await expect_error(
        client.call(
            "mesh.selection.remap",
            {
                "selection_id": selection["selection_id"],
                "component_map_id": edited["component_map"]["component_map_id"],
                "mode": "ALL_MAPPED",
                "weight_merge": "MAX",
            },
            read_only=True,
        ),
        "MESH_COMPONENT_MAP_STALE",
    )
    report["disconnect_rollback"] = {
        "before": exact_mesh_state(before),
        "edit": edited,
        "ping": ping,
        "after": exact_mesh_state(after),
        "map_stale": stale,
    }


async def check_conflict_and_native_save(
    client: BridgeClient,
    manager: ApplicationManager,
    report: dict[str, Any],
) -> None:
    stage("user Mesh conflict is preserved and accepted by a native save")
    before = await inspect_mesh(client, "Topology Conflict")
    selection = await selection_query(client, before, "EDGE", {"type": "all"})
    transaction = await begin(
        client, int(before["scene_generation"]), "0.13 conflict and native save"
    )
    edited = await mutate(
        client,
        "mesh.edit",
        edit_params(
            str(transaction["transaction_id"]),
            before,
            {
                "type": "subdivide",
                "selection_id": selection["selection_id"],
                "cuts": 1,
            },
        ),
        int(transaction["scene_generation"]),
    )
    touched = await client.call(
        "_test.mesh.touch",
        {"object_name": "Topology Conflict", "action": "coordinate"},
        read_only=False,
    )
    touched_ping = await client.call("connection.ping", read_only=True)
    conflict = await expect_error(
        finish(
            client,
            "transaction.rollback",
            str(transaction["transaction_id"]),
            int(touched_ping["scene_generation"]),
        ),
        "MESH_DATA_CONFLICT",
    )
    preserved = await inspect_mesh(client, "Topology Conflict")
    if preserved["mesh_fingerprint"] != touched["mesh_fingerprint"]:
        raise RuntimeError("Mesh conflict did not preserve the user's current data")
    saved = await client.call("_test.native_save", {}, read_only=False)
    ping = await client.call("connection.ping", read_only=True)
    accepted = await expect_error(
        finish(
            client,
            "transaction.rollback",
            str(transaction["transaction_id"]),
            int(ping["scene_generation"]),
        ),
        "TRANSACTION_ACCEPTED_BY_USER_SAVE",
    )
    await client.close()
    await asyncio.sleep(3.0)
    reconnect = await client.call("connection.ping", read_only=True)
    current = await inspect_mesh(client, "Topology Conflict")
    if current["mesh_fingerprint"] != preserved["mesh_fingerprint"]:
        raise RuntimeError("Native-saved topology was rolled back after disconnect")
    reload_result = await manager.project_reload(
        save_current=False, use_scripts=False, load_ui=False
    )
    reloaded = await inspect_mesh(client, "Topology Conflict")
    if persistent_mesh_state(reloaded) != persistent_mesh_state(current):
        raise RuntimeError("Native-saved topology did not survive reload")
    report["conflict_native_save"] = {
        "before": exact_mesh_state(before),
        "edit": edited,
        "touch": touched,
        "touch_ping": touched_ping,
        "conflict": conflict,
        "preserved": exact_mesh_state(preserved),
        "save": saved,
        "accepted_error": accepted,
        "reconnect": reconnect,
        "current": exact_mesh_state(current),
        "reload": reload_result,
        "reloaded": exact_mesh_state(reloaded),
    }


async def prepare_surface(
    client: BridgeClient,
    inspected: dict[str, Any],
    geometry: str = "EVALUATED",
) -> dict[str, Any]:
    return await client.call(
        "mesh.surface.prepare",
        {
            "object_name": inspected["object"]["name"],
            "expected_object_identity": inspected["object"]["session_identity"],
            "expected_mesh_revision_id": inspected["mesh_revision_id"],
            "geometry": geometry,
        },
        read_only=True,
    )


async def distance_query(
    client: BridgeClient,
    selection_id: str,
    surface_id: str,
    maximum_distance: float = 1_000_000,
) -> dict[str, Any]:
    return await client.call(
        "mesh.surface.query",
        {
            "selection_id": selection_id,
            "surface_id": surface_id,
            "mode": "CLOSEST_POINT",
            "maximum_distance": maximum_distance,
            "threshold": None,
            "sample_limit": 16,
        },
        read_only=True,
    )


async def validate_mesh(
    client: BridgeClient,
    selection_id: str,
    check: str,
    surface_id: str | None = None,
) -> dict[str, Any]:
    return await client.call(
        "mesh.validate",
        {
            "selection_id": selection_id,
            "check": check,
            "surface_id": surface_id,
            "tolerance": 1e-8 if check != "DEGENERATE" else 1e-12,
            "maximum_distance": 0.2,
            "threshold": None,
            "sample_limit": 16,
        },
        read_only=True,
    )


async def capture_view(
    client: BridgeClient,
    object_name: str,
    view: str,
) -> dict[str, Any]:
    return await client.call(
        "viewport.capture",
        {
            "object_name": object_name,
            "view": view,
            "max_size": 800,
            "viewport_id": None,
            "display_mode": "SOLID",
            "overlays": "OFF",
            "orbit": None,
        },
        read_only=True,
    )


async def check_real_proxy(
    client: BridgeClient,
    manager: ApplicationManager,
    source_path: Path,
    project_path: Path,
    report: dict[str, Any],
) -> None:
    stage("independent eye proxy topology and evaluated-surface fitting")
    opened = await manager.project_open(
        str(project_path), save_current=False, use_scripts=False, load_ui=True
    )
    body = await inspect_mesh(client, "绯雪_edit_mesh")
    target = await prepare_surface(client, body, "EVALUATED")
    candidates = []
    for name in (
        "Portrait_ID_V13_SubjectFX_Sclera_L",
        "Portrait_ID_V13_SubjectFX_Cornea_L",
    ):
        candidates.append(await inspect_mesh(client, name))
    source_proxy = next(
        item for item in candidates if int(item["counts"]["vertices"]) == 1986
    )
    source_object = await client.call(
        "object.inspect",
        {"object_name": source_proxy["object"]["name"]},
        read_only=True,
    )
    duplicate_transaction = await begin(
        client,
        int(source_proxy["scene_generation"]),
        "0.13 independent evaluated proxy",
    )
    duplicated = await mutate(
        client,
        "object.duplicate",
        {
            "transaction_id": duplicate_transaction["transaction_id"],
            "source_name": source_object["name"],
            "expected_source_identity": source_object["session_identity"],
            "name": "MCP 0.13 Eye Proxy",
            "linked_data": False,
            "collection_name": None,
            "expected_collection_identity": None,
            "transform": None,
        },
        int(duplicate_transaction["scene_generation"]),
    )
    duplicate_commit = await finish(
        client,
        "transaction.commit",
        str(duplicate_transaction["transaction_id"]),
        int(duplicated["scene_generation"]),
    )
    proxy = await inspect_mesh(client, "MCP 0.13 Eye Proxy")
    if proxy["mesh"]["session_identity"] == source_proxy["mesh"]["session_identity"]:
        raise RuntimeError("Eye proxy did not receive independent Mesh data")

    edges = await selection_query(client, proxy, "EDGE", {"type": "all"})
    vertices = await selection_query(client, proxy, "VERTEX", {"type": "all"})
    if int(edges["component_count"]) > 4096:
        edges = await selection_query(
            client,
            proxy,
            "EDGE",
            {"type": "topology", "kind": "BOUNDARY", "seed_indices": None},
        )
    if int(edges["component_count"]) == 0:
        raise RuntimeError("Independent eye proxy has no bounded edge selection to refine")
    topology_transaction = await begin(
        client, int(proxy["scene_generation"]), "0.13 proxy topology refinement"
    )
    topology = await mutate(
        client,
        "mesh.edit",
        edit_params(
            str(topology_transaction["transaction_id"]),
            proxy,
            {
                "type": "subdivide",
                "selection_id": edges["selection_id"],
                "cuts": 1,
                "smooth": 0.0,
            },
        ),
        int(topology_transaction["scene_generation"]),
    )
    remap_result = await client.call(
        "mesh.selection.remap",
        {
            "selection_id": vertices["selection_id"],
            "component_map_id": topology["component_map"]["component_map_id"],
            "mode": "ALL_MAPPED",
            "weight_merge": "MAX",
        },
        read_only=True,
    )
    remapped_vertices = remap_result["selection"]
    created_vertices = topology["created_selections"].get("VERTEX")
    fit_selection = remapped_vertices
    if created_vertices is not None:
        fit_selection = await client.call(
            "mesh.selection.derive",
            {
                "operation": {
                    "type": "combine",
                    "mode": "UNION",
                    "selection_ids": [
                        remapped_vertices["selection_id"],
                        created_vertices["selection_id"],
                    ],
                }
            },
            read_only=True,
        )
    topology_commit = await finish(
        client,
        "transaction.commit",
        str(topology_transaction["transaction_id"]),
        int(topology["scene_generation"]),
    )
    refined = await inspect_mesh(client, "MCP 0.13 Eye Proxy")
    baseline = await distance_query(
        client, fit_selection["selection_id"], target["surface_id"], 0.2
    )
    baseline_non_manifold = await validate_mesh(
        client, fit_selection["selection_id"], "NON_MANIFOLD"
    )
    baseline_degenerate = await validate_mesh(
        client, fit_selection["selection_id"], "DEGENERATE"
    )
    before_front = await capture_view(client, "MCP 0.13 Eye Proxy", "FRONT")
    before_right = await capture_view(client, "MCP 0.13 Eye Proxy", "RIGHT")

    fit_transaction = await begin(
        client, int(refined["scene_generation"]), "0.13 proxy evaluated fit"
    )
    fitted = await mutate(
        client,
        "mesh.edit",
        edit_params(
            str(fit_transaction["transaction_id"]),
            refined,
            {
                "type": "shrinkwrap",
                "selection_id": fit_selection["selection_id"],
                "surface_id": target["surface_id"],
                "iterations": 1,
                "factor": 0.75,
                "maximum_distance": 0.2,
                "offset": 0.0,
                "side": "ANY",
                "on_miss": "KEEP",
            },
        ),
        int(fit_transaction["scene_generation"]),
    )
    fitted_mesh = await inspect_mesh(client, "MCP 0.13 Eye Proxy")
    relaxed = await mutate(
        client,
        "mesh.edit",
        edit_params(
            str(fit_transaction["transaction_id"]),
            fitted_mesh,
            {
                "type": "relax",
                "selection_id": fitted["rebound_selection"]["selection_id"],
                "iterations": 1,
                "factor": 0.001,
                "preserve_boundary": True,
            },
        ),
        int(fitted["scene_generation"]),
    )
    final_selection = relaxed["rebound_selection"]
    after = await distance_query(
        client, final_selection["selection_id"], target["surface_id"], 0.2
    )
    non_manifold = await validate_mesh(
        client, final_selection["selection_id"], "NON_MANIFOLD"
    )
    degenerate = await validate_mesh(
        client, final_selection["selection_id"], "DEGENERATE"
    )
    penetration = await validate_mesh(
        client, final_selection["selection_id"], "PENETRATION", target["surface_id"]
    )
    after_front = await capture_view(client, "MCP 0.13 Eye Proxy", "FRONT")
    after_right = await capture_view(client, "MCP 0.13 Eye Proxy", "RIGHT")

    baseline_p95 = baseline["distances"]["p95"]
    after_p95 = after["distances"]["p95"]
    if baseline_p95 is None or after_p95 is None or after_p95 > baseline_p95 * 0.5:
        raise RuntimeError("Independent proxy p95 error did not improve by at least 50%")
    if (
        non_manifold["count"] > baseline_non_manifold["count"]
        or degenerate["count"] > baseline_degenerate["count"]
    ):
        raise RuntimeError("Independent proxy fit introduced invalid topology")
    world_bounds = source_object["world_bounds"]
    diagonal = sum(
        (
            max(float(point[axis]) for point in world_bounds)
            - min(float(point[axis]) for point in world_bounds)
        )
        ** 2
        for axis in range(3)
    ) ** 0.5
    signed_minimum = penetration["distances"]["signed_minimum"]
    maximum_penetration = (
        max(0.0, -float(signed_minimum))
        if penetration["sign_reliable"] and signed_minimum is not None
        else None
    )
    if maximum_penetration is not None and maximum_penetration > diagonal * 0.001:
        raise RuntimeError("Independent proxy penetration exceeded 0.1% of bounds")
    if (
        before_front["native_sha256"] == after_front["native_sha256"]
        or before_right["native_sha256"] == after_right["native_sha256"]
    ):
        raise RuntimeError("Independent proxy did not produce two-angle image differences")
    fit_commit = await finish(
        client,
        "transaction.commit",
        str(fit_transaction["transaction_id"]),
        int(relaxed["scene_generation"]),
    )
    persisted = await inspect_mesh(client, "MCP 0.13 Eye Proxy")
    saved = await manager.project_save()
    reload_result = await manager.project_reload(
        save_current=False, use_scripts=False, load_ui=True
    )
    reloaded = await inspect_mesh(client, "MCP 0.13 Eye Proxy")
    if persistent_mesh_state(persisted) != persistent_mesh_state(reloaded):
        raise RuntimeError("Independent proxy did not persist through save/reload")
    rebuilt_surface = await prepare_surface(
        client, await inspect_mesh(client, "绯雪_edit_mesh"), "EVALUATED"
    )
    rebuilt_selection = await selection_query(
        client, reloaded, "VERTEX", {"type": "all"}
    )
    reloaded_distance = await distance_query(
        client,
        rebuilt_selection["selection_id"],
        rebuilt_surface["surface_id"],
        0.2,
    )
    reloaded_p95 = reloaded_distance["distances"]["p95"]
    if reloaded_p95 is None or reloaded_p95 > after_p95 * 1.01 + 1e-8:
        raise RuntimeError("Independent proxy surface fit did not survive save/reload")
    report["real_proxy"] = {
        "source": str(source_path),
        "project": str(project_path),
        "open": opened,
        "body": exact_mesh_state(body),
        "surface": target,
        "source_proxy": exact_mesh_state(source_proxy),
        "duplicate": duplicated,
        "duplicate_commit": duplicate_commit,
        "proxy": exact_mesh_state(proxy),
        "edge_selection": edges,
        "vertex_selection": vertices,
        "topology": topology,
        "remap_result": remap_result,
        "remapped_vertices": remapped_vertices,
        "fit_selection": fit_selection,
        "topology_commit": topology_commit,
        "refined": exact_mesh_state(refined),
        "baseline": baseline,
        "baseline_non_manifold": baseline_non_manifold,
        "baseline_degenerate": baseline_degenerate,
        "fit": fitted,
        "relax": relaxed,
        "after": after,
        "p95_improvement_ratio": after_p95 / baseline_p95,
        "non_manifold": non_manifold,
        "degenerate": degenerate,
        "penetration": penetration,
        "maximum_penetration": maximum_penetration,
        "captures": {
            "front_before": before_front,
            "front_after": after_front,
            "right_before": before_right,
            "right_after": after_right,
        },
        "fit_commit": fit_commit,
        "persisted": exact_mesh_state(persisted),
        "save": saved,
        "reload": reload_result,
        "reloaded": exact_mesh_state(reloaded),
        "reloaded_distance": reloaded_distance,
    }


async def run(args: argparse.Namespace) -> dict[str, Any]:
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + f"-{uuid4().hex[:8]}"
    temporary_root = Path(tempfile.gettempdir()) / "blender-research-mcp-topology" / run_id
    temporary_root.mkdir(parents=True, exist_ok=False)
    artifact_directory = ROOT / "artifacts" / "live-smoke" / run_id
    artifact_directory.mkdir(parents=True, exist_ok=False)
    fixture_source = temporary_root / "topology-source.blend"
    fixture_project = temporary_root / "topology-project.blend"
    build_fixture(args.blender_executable, fixture_source, artifact_directory / "fixture.log")
    fixture_hash = sha256(fixture_source)
    shutil.copy2(fixture_source, fixture_project)
    real_source = args.real_project.resolve(strict=True)
    real_hash = sha256(real_source)
    real_project = temporary_root / "test-model.blend"
    shutil.copy2(real_source, real_project)

    report: dict[str, Any] = {
        "run_id": run_id,
        "started_at": datetime.now(UTC).isoformat(),
        "temporary_root": str(temporary_root),
        "artifact_directory": str(artifact_directory),
        "fixture_source": str(fixture_source),
        "fixture_source_sha256_before": fixture_hash,
        "fixture_project": str(fixture_project),
        "real_source": str(real_source),
        "real_source_sha256_before": real_hash,
        "real_project": str(real_project),
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
        stage("managed launch and fixture open")
        launch = await manager.launch()
        launched = True
        report["launch"] = launch
        application = launch["application"]
        if application["addon_version"] != PACKAGE_VERSION:
            raise RuntimeError("Managed launch did not load the 0.13.0 add-on")
        if not str(application["blender_version"]).startswith("4.2.23"):
            raise RuntimeError("Managed launch did not use Blender 4.2.23")
        ping_before = await client.call("connection.ping", read_only=True)
        capabilities = ping_before["capability_versions"]
        if int(capabilities.get("mesh_component_map", 0)) < 1:
            raise RuntimeError("Managed add-on did not advertise mesh_component_map: 1")
        if int(capabilities.get("mesh_topology", 0)) < 2:
            raise RuntimeError("Managed add-on did not advertise mesh_topology: 2")
        if int(capabilities.get("transactions", 0)) < 7:
            raise RuntimeError("Managed add-on did not advertise transactions: 7")
        report["ping_before"] = ping_before
        report["fixture_open"] = await manager.project_open(
            str(fixture_project), save_current=False, use_scripts=False, load_ui=False
        )
        cases = (
            ("Topology Subdivide", "EDGE", {"type": "all"}, {"type": "subdivide", "cuts": 2}),
            (
                "Topology Loop Cut",
                "EDGE",
                {"type": "indices", "indices": [0]},
                {"type": "loop_cut", "cuts": 1},
            ),
            (
                "Topology Bisect",
                "FACE",
                {"type": "all"},
                {
                    "type": "bisect",
                    "plane_origin": {"x": 0, "y": 0, "z": 0},
                    "plane_normal": {"x": 1, "y": 0, "z": 0},
                },
            ),
            ("Topology Split", "FACE", {"type": "indices", "indices": [0]}, {"type": "split"}),
            ("Topology Bridge", "EDGE", {"type": "all"}, {"type": "bridge"}),
            (
                "Topology Fill",
                "EDGE",
                {"type": "all"},
                {"type": "fill", "method": "NGON"},
            ),
            (
                "Topology Grid Fill",
                "EDGE",
                {"type": "indices", "indices": list(range(8))},
                {"type": "grid_fill", "use_interp_simple": True},
            ),
        )
        results = {}
        for object_name, domain, query, operation in cases:
            stage(f"{operation['type']} rollback")
            results[str(operation["type"])] = await run_operation(
                client, object_name, domain, query, operation, "transaction.rollback"
            )
        for object_name, domain, query, operation in (
            (
                "Topology Legacy Extrude",
                "FACE",
                {"type": "indices", "indices": [0]},
                {
                    "type": "extrude_faces",
                    "face_indices": [0],
                    "offset": {"x": 0, "y": 0, "z": 0.5},
                },
            ),
            (
                "Topology Legacy Merge",
                "VERTEX",
                {"type": "indices", "indices": [0, 1]},
                {
                    "type": "merge_vertices",
                    "vertex_indices": [0, 1],
                    "destination": "CENTER",
                },
            ),
        ):
            stage(f"legacy {operation['type']} ComponentMap rollback")
            results[str(operation["type"])] = await run_operation(
                client,
                object_name,
                domain,
                query,
                operation,
                "transaction.rollback",
                bind_selection=False,
            )
        report["operations"] = results
        await check_continuous_revisions(client, manager, report)
        await check_disconnect_rollback(client, report)
        await check_conflict_and_native_save(client, manager, report)
        await check_real_proxy(client, manager, real_source, real_project, report)
        ping_after = await client.call("connection.ping", read_only=True)
        if int(ping_after["heartbeat"]) <= int(ping_before["heartbeat"]):
            raise RuntimeError("Blender heartbeat did not advance")
        report["ping_after"] = ping_after
        report["fixture_source_sha256_after"] = sha256(fixture_source)
        report["real_source_sha256_after"] = sha256(real_source)
        report["fixture_source_unchanged"] = report["fixture_source_sha256_after"] == fixture_hash
        report["real_source_unchanged"] = report["real_source_sha256_after"] == real_hash
        if not report["fixture_source_unchanged"] or not report["real_source_unchanged"]:
            raise RuntimeError("A source fixture changed during 0.13 acceptance")
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
    parser.add_argument("--real-project", type=Path, required=True)
    parser.add_argument("--port", type=int, default=9890)
    parser.add_argument("--timeout", type=float, default=120.0)
    args = parser.parse_args()
    report = asyncio.run(run(args))
    report_path = Path(report["artifact_directory"]) / f"report-{PACKAGE_VERSION}.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(report_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
