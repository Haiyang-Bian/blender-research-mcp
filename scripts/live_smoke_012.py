"""Run Blender 4.2 SelectionSet and evaluated-surface acceptance for 0.12.0."""

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

from blender_research_mcp.client import BridgeClient
from blender_research_mcp.constants import PACKAGE_VERSION
from blender_research_mcp.errors import BridgeError
from blender_research_mcp.lifecycle import ApplicationManager
from blender_research_mcp.rendering import request_render_preview

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_BUILDER = ROOT / "scripts" / "create_surface_fixture.py"


def stage(name: str) -> None:
    print(f"[0.12 smoke] {name}", flush=True)


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
        raise RuntimeError(f"Could not build 0.12 fixture; see {log_path}")


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


async def expect_error(awaitable: Any, code: str) -> dict[str, Any]:
    try:
        await awaitable
    except BridgeError as exc:
        if exc.error.code != code:
            raise
        return exc.error.model_dump(mode="json")
    raise RuntimeError(f"Expected {code}")


def exact_mesh_state(inspected: dict[str, Any]) -> dict[str, Any]:
    return {
        "mesh_identity": inspected["mesh"]["session_identity"],
        "mesh_revision_id": inspected["mesh_revision_id"],
        "mesh_fingerprint": inspected["mesh_fingerprint"],
        "topology_fingerprint": inspected["topology_fingerprint"],
        "users": inspected["mesh"]["users"],
        "user_objects": inspected["user_objects"],
        "uv_layers": inspected["mesh"]["uv_layers"],
        "color_attributes": inspected["mesh"]["color_attributes"],
    }


async def check_resources(client: BridgeClient, report: dict[str, Any]) -> None:
    stage("revision-bound semantic, screen, and derived selections")
    source = await inspect_mesh(client, "Fit Source")
    plane = await inspect_mesh(client, "Query Plane")
    queries = {
        "indices": await selection_query(
            client, source, "VERTEX", {"type": "indices", "indices": [0, 1, 2]}
        ),
        "all": await selection_query(client, source, "VERTEX", {"type": "all"}),
        "sphere": await selection_query(
            client,
            source,
            "VERTEX",
            {
                "type": "sphere",
                "center": {"x": 0.0, "y": 0.0, "z": 0.0},
                "radius": 2.2,
                "space": "LOCAL",
            },
        ),
        "box": await selection_query(
            client,
            source,
            "VERTEX",
            {
                "type": "box",
                "minimum": {"x": -3.0, "y": -3.0, "z": 0.0},
                "maximum": {"x": 3.0, "y": 3.0, "z": 3.0},
                "space": "LOCAL",
            },
        ),
        "plane": await selection_query(
            client,
            source,
            "VERTEX",
            {
                "type": "plane",
                "origin": {"x": 0.0, "y": 0.0, "z": 0.0},
                "normal": {"x": 0.0, "y": 0.0, "z": 1.0},
                "side": "POSITIVE",
            },
        ),
        "normal": await selection_query(
            client,
            source,
            "VERTEX",
            {
                "type": "normal",
                "direction": {"x": 0.0, "y": 0.0, "z": 1.0},
                "minimum_dot": 0.5,
            },
        ),
        "material": await selection_query(
            client,
            source,
            "FACE",
            {"type": "material", "slot_indices": [0]},
        ),
        "measure": await selection_query(
            client,
            source,
            "EDGE",
            {"type": "measure", "field": "EDGE_LENGTH", "minimum": 0.01},
        ),
        "boundary": await selection_query(
            client,
            plane,
            "EDGE",
            {"type": "topology", "kind": "BOUNDARY", "seed_indices": None},
        ),
        "connected": await selection_query(
            client,
            plane,
            "VERTEX",
            {"type": "topology", "kind": "CONNECTED", "seed_indices": [0]},
        ),
    }
    if queries["all"]["component_count"] != source["counts"]["vertices"]:
        raise RuntimeError("All-query did not bind every source vertex")
    if queries["boundary"]["component_count"] == 0:
        raise RuntimeError("Boundary query did not find the open grid border")

    combined = await client.call(
        "mesh.selection.derive",
        {
            "operation": {
                "type": "combine",
                "mode": "UNION",
                "selection_ids": [
                    queries["indices"]["selection_id"],
                    queries["sphere"]["selection_id"],
                ],
            }
        },
        read_only=True,
    )
    expanded = await client.call(
        "mesh.selection.derive",
        {
            "operation": {
                "type": "expand",
                "selection_id": queries["indices"]["selection_id"],
                "steps": 2,
            }
        },
        read_only=True,
    )
    falloff = await client.call(
        "mesh.selection.derive",
        {
            "operation": {
                "type": "falloff",
                "selection_id": queries["indices"]["selection_id"],
                "radius": 1.0,
                "profile": "SMOOTH",
                "space": "WORLD",
            }
        },
        read_only=True,
    )
    converted = await client.call(
        "mesh.selection.derive",
        {
            "operation": {
                "type": "convert",
                "selection_id": queries["material"]["selection_id"],
                "domain": "VERTEX",
                "mode": "ANY",
            }
        },
        read_only=True,
    )
    page = await client.call(
        "mesh.selection.inspect",
        {"selection_id": falloff["selection_id"], "offset": 0, "limit": 8},
        read_only=True,
    )
    if not page["weighted"] or not page["items"]:
        raise RuntimeError("Geodesic falloff did not retain bounded weights")
    released = await client.call(
        "mesh.selection.release",
        {"selection_id": expanded["selection_id"]},
        read_only=True,
    )
    missing = await expect_error(
        client.call(
            "mesh.selection.inspect",
            {"selection_id": expanded["selection_id"], "offset": 0, "limit": 8},
            read_only=True,
        ),
        "MESH_RESOURCE_NOT_FOUND",
    )

    capture = await client.call(
        "viewport.capture",
        {
            "object_name": "Fit Source",
            "view": "FRONT",
            "max_size": 512,
            "viewport_id": None,
            "display_mode": "SOLID",
            "overlays": "OFF",
            "orbit": None,
        },
        read_only=True,
    )
    screen = await selection_query(
        client,
        source,
        "VERTEX",
        {
            "type": "screen",
            "capture_id": capture["capture_id"],
            "shape": "BOX",
            "points": [{"x": 0.2, "y": 0.2}, {"x": 0.8, "y": 0.8}],
            "visibility": "VISIBLE_ONLY",
            "include_backface": False,
        },
    )
    screen_through = await selection_query(
        client,
        source,
        "VERTEX",
        {
            "type": "screen",
            "capture_id": capture["capture_id"],
            "shape": "BOX",
            "points": [{"x": 0.2, "y": 0.2}, {"x": 0.8, "y": 0.8}],
            "visibility": "THROUGH",
            "include_backface": False,
        },
    )
    center_raycast = await client.call(
        "viewport.raycast",
        {"capture_id": capture["capture_id"], "x": 0.5, "y": 0.5},
        read_only=True,
    )
    if screen["component_count"] == 0:
        raise RuntimeError(
            "Capture-bound visible screen query returned no vertices: "
            f"through={screen_through['component_count']}, ray={center_raycast}"
        )
    report["selection_resources"] = {
        "revision": source["mesh_revision_id"],
        "queries": queries,
        "combined": combined,
        "falloff": falloff,
        "converted": converted,
        "falloff_page": page,
        "released": released,
        "released_error": missing,
        "capture": capture,
        "screen": screen,
        "screen_through": screen_through,
        "center_raycast": center_raycast,
    }


async def check_surfaces(client: BridgeClient, report: dict[str, Any]) -> None:
    stage("BASE/EVALUATED SurfaceRefs, distance evidence, and staleness")
    target = await inspect_mesh(client, "Evaluated Target")
    source = await inspect_mesh(client, "Fit Source")
    selection = await selection_query(client, source, "VERTEX", {"type": "all"})
    base = await prepare_surface(client, target, "BASE")
    evaluated = await prepare_surface(client, target, "EVALUATED")
    if evaluated["triangle_count"] <= base["triangle_count"]:
        raise RuntimeError("Evaluated target did not include its Subdivision result")
    distances = await distance_query(
        client, selection["selection_id"], evaluated["surface_id"], 10.0
    )
    if distances["distances"]["count"] != selection["component_count"]:
        raise RuntimeError("Surface query did not return one distance per selected vertex")

    target_object = await client.call(
        "object.inspect", {"object_name": "Evaluated Target"}, read_only=True
    )
    transaction = await begin(client, int(target["scene_generation"]), "0.12 stale SurfaceRef")
    moved = await mutate(
        client,
        "object.transform",
        {
            "transaction_id": transaction["transaction_id"],
            "object_name": target_object["name"],
            "expected_object_identity": target_object["session_identity"],
            "location": {"x": float(target_object["location"][0]) + 0.1},
        },
        int(transaction["scene_generation"]),
    )
    stale = await expect_error(
        distance_query(client, selection["selection_id"], evaluated["surface_id"], 10.0),
        "MESH_RESOURCE_STALE",
    )
    rolled_back = await finish(
        client,
        "transaction.rollback",
        str(transaction["transaction_id"]),
        int(moved["scene_generation"]),
    )
    restored = await distance_query(
        client, selection["selection_id"], evaluated["surface_id"], 10.0
    )
    report["surface_resources"] = {
        "base": base,
        "evaluated": evaluated,
        "distances": distances,
        "stale_error": stale,
        "rollback": rolled_back,
        "restored": restored,
    }


def operation_for(
    name: str,
    selection: dict[str, Any],
    vertices: dict[str, Any],
    surface: dict[str, Any],
) -> dict[str, Any]:
    selection_id = selection["selection_id"]
    if name == "set_positions":
        return {
            "type": name,
            "selection_id": selection_id,
            "mode": "OFFSET",
            "space": "LOCAL",
            "positions": [{"x": 0.0, "y": 0.0, "z": 0.08}],
        }
    if name in {"smooth", "relax"}:
        return {
            "type": name,
            "selection_id": selection_id,
            "iterations": 2,
            "factor": 0.35,
            "preserve_boundary": True,
        }
    if name == "project":
        return {
            "type": name,
            "selection_id": selection_id,
            "surface_id": surface["surface_id"],
            "direction": "CLOSEST_POINT",
            "maximum_distance": 10.0,
            "offset": 0.03,
            "side": "ANY",
            "on_miss": "ERROR",
        }
    if name == "shrinkwrap":
        return {
            "type": name,
            "selection_id": selection_id,
            "surface_id": surface["surface_id"],
            "iterations": 2,
            "factor": 0.8,
            "maximum_distance": 10.0,
            "offset": 0.04,
            "side": "ANY",
            "on_miss": "ERROR",
        }
    if name == "inflate":
        return {"type": name, "selection_id": selection_id, "amount": 0.06}
    if name == "flatten":
        return {
            "type": name,
            "selection_id": selection_id,
            "plane": {"type": "BEST_FIT"},
            "factor": 0.5,
            "space": "LOCAL",
        }
    raise AssertionError(name)


async def run_deformation_once(
    client: BridgeClient,
    manager: ApplicationManager,
    name: str,
    finish_command: str,
    *,
    collaborative_ui: bool = False,
) -> dict[str, Any]:
    source = await inspect_mesh(client, "Fit Source")
    target = await inspect_mesh(client, "Evaluated Target")
    vertices = await inspect_mesh(client, "Fit Source", "vertices", 0, 1)
    query = (
        {"type": "indices", "indices": [0]}
        if name == "set_positions"
        else {
            "type": "plane",
            "origin": {"x": 0.0, "y": 0.0, "z": 0.0},
            "normal": {"x": 0.0, "y": 0.0, "z": 1.0},
            "side": "POSITIVE",
        }
        if name == "flatten"
        else {"type": "all"}
    )
    selection = await selection_query(client, source, "VERTEX", query)
    surface = await prepare_surface(client, target, "EVALUATED")
    baseline = await distance_query(
        client, selection["selection_id"], surface["surface_id"], 10.0
    )
    transaction = await begin(
        client, int(source["scene_generation"]), f"0.12 {name} {finish_command}"
    )
    context_before = None
    touched = None
    if collaborative_ui:
        context_before = await client.call("context.get", read_only=True)
        touched = await client.call(
            "_test.context.touch",
            {
                "viewport_id": context_before["viewport_id"],
                "active_object": "Query Plane",
                "shading": "WIREFRAME",
                "show_overlays": False,
            },
            read_only=False,
        )
    changed = await mutate(
        client,
        "mesh.edit",
        mesh_edit_params(
            str(transaction["transaction_id"]),
            source,
            operation_for(name, selection, vertices, surface),
        ),
        int(transaction["scene_generation"]),
    )
    if not changed["changed"]:
        raise RuntimeError(f"{name} unexpectedly produced a no-op")
    if changed["before_topology_fingerprint"] != changed["after_topology_fingerprint"]:
        raise RuntimeError(f"{name} changed topology")
    rebound = changed["rebound_selection"]
    after_distance = await distance_query(
        client, rebound["selection_id"], surface["surface_id"], 10.0
    )
    stale = await expect_error(
        client.call(
            "mesh.selection.inspect",
            {"selection_id": selection["selection_id"], "offset": 0, "limit": 4},
            read_only=True,
        ),
        "MESH_RESOURCE_STALE",
    )
    finished = await finish(
        client,
        finish_command,
        str(transaction["transaction_id"]),
        int(changed["scene_generation"]),
    )
    context_after = await client.call("context.get", read_only=True) if touched else None
    if touched and context_after["view"] != touched["context"]["view"]:
        raise RuntimeError("Mesh deformation rollback rewound collaborative UI")
    if finish_command == "transaction.rollback":
        restored = await client.call(
            "mesh.selection.inspect",
            {"selection_id": selection["selection_id"], "offset": 0, "limit": 4},
            read_only=True,
        )
    else:
        restored = await client.call(
            "mesh.selection.inspect",
            {"selection_id": rebound["selection_id"], "offset": 0, "limit": 4},
            read_only=True,
        )
    result = {
        "before": exact_mesh_state(source),
        "selection": selection,
        "surface": surface,
        "baseline_distance": baseline,
        "edit": changed,
        "stale_error": stale,
        "finish": finished,
        "valid_after_finish": restored,
        "after_distance": after_distance,
        "context_before": context_before,
        "context_touched": touched,
        "context_after": context_after,
    }
    if finish_command == "transaction.commit":
        result["reload"] = await manager.project_reload(
            save_current=False, use_scripts=False, load_ui=False
        )
    return result


async def check_deformations(
    client: BridgeClient,
    manager: ApplicationManager,
    report: dict[str, Any],
) -> None:
    stage("seven topology-preserving deformations with rollback and commit")
    results: dict[str, Any] = {}
    for index, name in enumerate(
        ("set_positions", "smooth", "relax", "project", "shrinkwrap", "inflate", "flatten")
    ):
        rollback = await run_deformation_once(
            client,
            manager,
            name,
            "transaction.rollback",
            collaborative_ui=index == 3,
        )
        commit = await run_deformation_once(
            client, manager, name, "transaction.commit"
        )
        results[name] = {"rollback": rollback, "commit": commit}
    report["deformations"] = results


async def check_shared_and_disconnect(client: BridgeClient, report: dict[str, Any]) -> None:
    stage("OBJECT/SHARED_DATA scopes and disconnect rollback")
    before = await inspect_mesh(client, "Fit Shared A")
    peer_before = await inspect_mesh(client, "Fit Shared B")
    selection = await selection_query(client, before, "VERTEX", {"type": "all"})
    transaction = await begin(client, int(before["scene_generation"]), "0.12 OBJECT scope")
    changed = await mutate(
        client,
        "mesh.edit",
        mesh_edit_params(
            str(transaction["transaction_id"]),
            before,
            {"type": "inflate", "selection_id": selection["selection_id"], "amount": 0.05},
            "OBJECT",
        ),
        int(transaction["scene_generation"]),
    )
    during = await inspect_mesh(client, "Fit Shared A")
    peer_during = await inspect_mesh(client, "Fit Shared B")
    if during["mesh"]["session_identity"] == peer_during["mesh"]["session_identity"]:
        raise RuntimeError("OBJECT deformation did not single-user the target")
    rollback = await finish(
        client,
        "transaction.rollback",
        str(transaction["transaction_id"]),
        int(changed["scene_generation"]),
    )
    if exact_mesh_state(await inspect_mesh(client, "Fit Shared A")) != exact_mesh_state(before):
        raise RuntimeError("OBJECT deformation did not restore shared data")

    shared = await inspect_mesh(client, "Fit Shared A")
    shared_selection = await selection_query(client, shared, "VERTEX", {"type": "all"})
    transaction = await begin(
        client, int(shared["scene_generation"]), "0.12 SHARED_DATA scope"
    )
    shared_edit = await mutate(
        client,
        "mesh.edit",
        mesh_edit_params(
            str(transaction["transaction_id"]),
            shared,
            {
                "type": "inflate",
                "selection_id": shared_selection["selection_id"],
                "amount": 0.04,
            },
            "SHARED_DATA",
        ),
        int(transaction["scene_generation"]),
    )
    peer_changed = await inspect_mesh(client, "Fit Shared B")
    if peer_changed["mesh_fingerprint"] != shared_edit["after_mesh_fingerprint"]:
        raise RuntimeError("SHARED_DATA deformation was not visible to its peer")
    shared_rollback = await finish(
        client,
        "transaction.rollback",
        str(transaction["transaction_id"]),
        int(shared_edit["scene_generation"]),
    )

    disconnect_before = await inspect_mesh(client, "Fit Shared A")
    disconnect_selection = await selection_query(
        client, disconnect_before, "VERTEX", {"type": "all"}
    )
    transaction = await begin(
        client, int(disconnect_before["scene_generation"]), "0.12 disconnect rollback"
    )
    disconnect_edit = await mutate(
        client,
        "mesh.edit",
        mesh_edit_params(
            str(transaction["transaction_id"]),
            disconnect_before,
            {
                "type": "inflate",
                "selection_id": disconnect_selection["selection_id"],
                "amount": 0.03,
            },
            "OBJECT",
        ),
        int(transaction["scene_generation"]),
    )
    await client.close()
    await asyncio.sleep(3.0)
    ping = await client.call("connection.ping", read_only=True)
    disconnect_after = await inspect_mesh(client, "Fit Shared A")
    if exact_mesh_state(disconnect_after) != exact_mesh_state(disconnect_before):
        raise RuntimeError("Disconnect did not restore SelectionSet deformation")
    report["shared_disconnect"] = {
        "object": {"edit": changed, "during": during, "rollback": rollback},
        "shared": {"edit": shared_edit, "peer": peer_changed, "rollback": shared_rollback},
        "disconnect": {
            "edit": disconnect_edit,
            "ping": ping,
            "before": exact_mesh_state(disconnect_before),
            "after": exact_mesh_state(disconnect_after),
            "peer_before": exact_mesh_state(peer_before),
        },
    }


async def check_native_save(
    client: BridgeClient,
    manager: ApplicationManager,
    report: dict[str, Any],
) -> None:
    stage("native-save adoption and save/reload revision reconstruction")
    before = await inspect_mesh(client, "Fit Source")
    selection = await selection_query(client, before, "VERTEX", {"type": "all"})
    transaction = await begin(client, int(before["scene_generation"]), "0.12 native save")
    changed = await mutate(
        client,
        "mesh.edit",
        mesh_edit_params(
            str(transaction["transaction_id"]),
            before,
            {"type": "inflate", "selection_id": selection["selection_id"], "amount": 0.025},
        ),
        int(transaction["scene_generation"]),
    )
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
    current = await inspect_mesh(client, "Fit Source")
    if current["mesh_fingerprint"] != changed["after_mesh_fingerprint"]:
        raise RuntimeError("Native save was rolled back after disconnect")
    reload_result = await manager.project_reload(
        save_current=False, use_scripts=False, load_ui=False
    )
    reloaded = await inspect_mesh(client, "Fit Source")
    if reloaded["topology_fingerprint"] != current["topology_fingerprint"]:
        raise RuntimeError("Reload did not retain native-saved deformation topology")
    rebuilt = await selection_query(client, reloaded, "VERTEX", {"type": "all"})
    report["native_save"] = {
        "edit": changed,
        "save": saved,
        "ping": ping,
        "accepted_error": accepted,
        "reconnect": reconnect,
        "current": exact_mesh_state(current),
        "reload": reload_result,
        "reloaded": exact_mesh_state(reloaded),
        "rebuilt_selection": rebuilt,
    }


async def render_evidence(
    client: BridgeClient,
    artifact_directory: Path,
    report: dict[str, Any],
) -> None:
    stage("reviewed Eevee render after persisted deformation")
    camera = await client.call("object.inspect", {"object_name": "Fit Camera"}, read_only=True)
    source = await inspect_mesh(client, "Fit Source")
    image, evidence = await request_render_preview(
        client,
        {
            "camera_name": "Fit Camera",
            "expected_camera_identity": camera["session_identity"],
            "width": 512,
            "height": 384,
            "samples": 24,
            "transparent": False,
        },
        expected_scene_generation=int(source["scene_generation"]),
        idempotency_key=str(uuid4()),
    )
    path = artifact_directory / "selection-surface-fitting.png"
    path.write_bytes(image)
    if path.stat().st_size < 1024:
        raise RuntimeError("0.12 Eevee evidence render is unexpectedly small")
    report["render"] = {
        "evidence": evidence,
        "path": str(path),
        "sha256": sha256(path),
        "bytes": path.stat().st_size,
    }


async def check_real_scene(
    client: BridgeClient,
    manager: ApplicationManager,
    source_path: Path,
    project_path: Path,
    report: dict[str, Any],
) -> None:
    stage("real test-model evaluated eye-proxy fit on a temporary copy")
    report["real_project_open"] = await manager.project_open(
        str(project_path), save_current=False, use_scripts=False, load_ui=True
    )
    body = await inspect_mesh(client, "绯雪_edit_mesh")
    target = await prepare_surface(client, body, "EVALUATED")
    candidates = []
    for name in (
        "Portrait_ID_V13_SubjectFX_Sclera_L",
        "Portrait_ID_V13_SubjectFX_Cornea_L",
    ):
        inspected = await inspect_mesh(client, name)
        candidates.append(inspected)
    proxy = next(item for item in candidates if int(item["counts"]["vertices"]) == 1986)
    proxy_object = await client.call(
        "object.inspect", {"object_name": proxy["object"]["name"]}, read_only=True
    )
    world_bounds = proxy_object["world_bounds"]
    bounds_min = [min(float(point[axis]) for point in world_bounds) for axis in range(3)]
    bounds_max = [max(float(point[axis]) for point in world_bounds) for axis in range(3)]
    bounds_diagonal = math.sqrt(
        sum((bounds_max[axis] - bounds_min[axis]) ** 2 for axis in range(3))
    )
    selection = await selection_query(client, proxy, "VERTEX", {"type": "all"})
    boundary_edges = await selection_query(
        client,
        proxy,
        "EDGE",
        {"type": "topology", "kind": "BOUNDARY", "seed_indices": None},
    )
    baseline = await distance_query(
        client, selection["selection_id"], target["surface_id"], 0.2
    )
    baseline_non_manifold = await client.call(
        "mesh.validate",
        {
            "selection_id": selection["selection_id"],
            "check": "NON_MANIFOLD",
            "surface_id": None,
            "tolerance": 1e-8,
            "maximum_distance": 0.2,
            "threshold": None,
            "sample_limit": 16,
        },
        read_only=True,
    )
    baseline_degenerate = await client.call(
        "mesh.validate",
        {
            "selection_id": selection["selection_id"],
            "check": "DEGENERATE",
            "surface_id": None,
            "tolerance": 1e-12,
            "maximum_distance": 0.2,
            "threshold": None,
            "sample_limit": 16,
        },
        read_only=True,
    )
    baseline_target_intersection = await client.call(
        "mesh.validate",
        {
            "selection_id": selection["selection_id"],
            "check": "TARGET_INTERSECTION",
            "surface_id": target["surface_id"],
            "tolerance": 1e-8,
            "maximum_distance": 0.2,
            "threshold": None,
            "sample_limit": 16,
        },
        read_only=True,
    )
    before_capture = await client.call(
        "viewport.capture",
        {
            "object_name": proxy["object"]["name"],
            "view": "FRONT",
            "max_size": 800,
            "viewport_id": None,
            "display_mode": "SOLID",
            "overlays": "OFF",
            "orbit": None,
        },
        read_only=True,
    )
    before_capture_right = await client.call(
        "viewport.capture",
        {
            "object_name": proxy["object"]["name"],
            "view": "RIGHT",
            "max_size": 800,
            "viewport_id": None,
            "display_mode": "SOLID",
            "overlays": "OFF",
            "orbit": None,
        },
        read_only=True,
    )
    transaction = await begin(
        client, int(proxy["scene_generation"]), "0.12 real evaluated surface fit"
    )
    try:
        fitted = await mutate(
            client,
            "mesh.edit",
            mesh_edit_params(
                str(transaction["transaction_id"]),
                proxy,
                {
                    "type": "shrinkwrap",
                    "selection_id": selection["selection_id"],
                    "surface_id": target["surface_id"],
                    "iterations": 1,
                    "factor": 0.75,
                    "maximum_distance": 0.2,
                    "offset": 0.0,
                    "side": "ANY",
                    "on_miss": "KEEP",
                },
            ),
            int(transaction["scene_generation"]),
        )
    except BridgeError as exc:
        raise RuntimeError(
            "Real proxy deformation failed: "
            + json.dumps(exc.error.model_dump(mode="json"), ensure_ascii=False)
        ) from exc
    fitted_mesh = await inspect_mesh(client, proxy["object"]["name"])
    try:
        relaxed = await mutate(
            client,
            "mesh.edit",
            mesh_edit_params(
                str(transaction["transaction_id"]),
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
    except BridgeError as exc:
        raise RuntimeError(
            "Real proxy relaxation failed: "
            + json.dumps(exc.error.model_dump(mode="json"), ensure_ascii=False)
        ) from exc
    rebound = relaxed["rebound_selection"]
    after = await distance_query(client, rebound["selection_id"], target["surface_id"], 0.2)
    non_manifold = await client.call(
        "mesh.validate",
        {
            "selection_id": rebound["selection_id"],
            "check": "NON_MANIFOLD",
            "surface_id": None,
            "tolerance": 1e-8,
            "maximum_distance": 0.2,
            "threshold": None,
            "sample_limit": 16,
        },
        read_only=True,
    )
    degenerate = await client.call(
        "mesh.validate",
        {
            "selection_id": rebound["selection_id"],
            "check": "DEGENERATE",
            "surface_id": None,
            "tolerance": 1e-12,
            "maximum_distance": 0.2,
            "threshold": None,
            "sample_limit": 16,
        },
        read_only=True,
    )
    penetration = await client.call(
        "mesh.validate",
        {
            "selection_id": rebound["selection_id"],
            "check": "PENETRATION",
            "surface_id": target["surface_id"],
            "tolerance": 1e-8,
            "maximum_distance": 0.2,
            "threshold": None,
            "sample_limit": 16,
        },
        read_only=True,
    )
    target_intersection = await client.call(
        "mesh.validate",
        {
            "selection_id": rebound["selection_id"],
            "check": "TARGET_INTERSECTION",
            "surface_id": target["surface_id"],
            "tolerance": 1e-8,
            "maximum_distance": 0.2,
            "threshold": None,
            "sample_limit": 16,
        },
        read_only=True,
    )
    after_capture = await client.call(
        "viewport.capture",
        {
            "object_name": proxy["object"]["name"],
            "view": "FRONT",
            "max_size": 800,
            "viewport_id": None,
            "display_mode": "SOLID",
            "overlays": "OFF",
            "orbit": None,
        },
        read_only=True,
    )
    after_capture_right = await client.call(
        "viewport.capture",
        {
            "object_name": proxy["object"]["name"],
            "view": "RIGHT",
            "max_size": 800,
            "viewport_id": None,
            "display_mode": "SOLID",
            "overlays": "OFF",
            "orbit": None,
        },
        read_only=True,
    )
    baseline_p95 = baseline["distances"]["p95"]
    after_p95 = after["distances"]["p95"]
    if baseline_p95 is None or after_p95 is None or after_p95 > baseline_p95 * 0.5:
        raise RuntimeError("Real proxy p95 surface error did not improve by at least 50%")
    if (
        non_manifold["count"] > baseline_non_manifold["count"]
        or degenerate["count"] > baseline_degenerate["count"]
    ):
        raise RuntimeError(
            "Real proxy fit introduced invalid topology evidence: "
            f"non_manifold={baseline_non_manifold['count']}->{non_manifold['count']}, "
            f"degenerate={baseline_degenerate['count']}->{degenerate['count']}"
        )
    signed_minimum = penetration["distances"]["signed_minimum"]
    maximum_penetration = (
        max(0.0, -float(signed_minimum))
        if penetration["sign_reliable"] and signed_minimum is not None
        else None
    )
    if maximum_penetration is not None and maximum_penetration > bounds_diagonal * 0.001:
        raise RuntimeError("Real proxy maximum penetration exceeded 0.1% of its bounds")
    if (
        before_capture["native_sha256"] == after_capture["native_sha256"]
        or before_capture_right["native_sha256"]
        == after_capture_right["native_sha256"]
    ):
        raise RuntimeError("Real proxy fit did not produce two-angle image evidence")
    rollback = await finish(
        client,
        "transaction.rollback",
        str(transaction["transaction_id"]),
        int(relaxed["scene_generation"]),
    )
    restored = await inspect_mesh(client, proxy["object"]["name"])
    if exact_mesh_state(restored) != exact_mesh_state(proxy):
        raise RuntimeError("Real proxy rollback did not restore the temporary project")
    report["real_scene"] = {
        "source": str(source_path),
        "project": str(project_path),
        "body": exact_mesh_state(body),
        "surface": target,
        "proxy": exact_mesh_state(proxy),
        "selection": selection,
        "boundary_edges": boundary_edges,
        "proxy_object": proxy_object,
        "proxy_bounds_diagonal": bounds_diagonal,
        "baseline": baseline,
        "baseline_non_manifold": baseline_non_manifold,
        "baseline_degenerate": baseline_degenerate,
        "baseline_target_intersection": baseline_target_intersection,
        "fit": fitted,
        "relax": relaxed,
        "after": after,
        "p95_improvement_ratio": after_p95 / baseline_p95,
        "non_manifold": non_manifold,
        "degenerate": degenerate,
        "penetration": penetration,
        "maximum_penetration": maximum_penetration,
        "penetration_fallback": (
            None
            if penetration["sign_reliable"]
            else "signed_depth_unavailable_for_open_or_inconsistently_oriented_surface"
        ),
        "target_intersection": target_intersection,
        "target_intersection_delta": (
            target_intersection["count"] - baseline_target_intersection["count"]
        ),
        "captures": {
            "front_before": before_capture,
            "front_after": after_capture,
            "right_before": before_capture_right,
            "right_after": after_capture_right,
        },
        "rollback": rollback,
        "restored": exact_mesh_state(restored),
    }


async def run(args: argparse.Namespace) -> dict[str, Any]:
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + f"-{uuid4().hex[:8]}"
    temporary_root = Path(tempfile.gettempdir()) / "blender-research-mcp-surface" / run_id
    temporary_root.mkdir(parents=True, exist_ok=False)
    artifact_directory = ROOT / "artifacts" / "live-smoke" / run_id
    artifact_directory.mkdir(parents=True, exist_ok=False)
    fixture_source = temporary_root / "surface-source.blend"
    fixture_project = temporary_root / "surface-project.blend"
    build_fixture(
        args.blender_executable, fixture_source, artifact_directory / "fixture.log"
    )
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
        stage("managed launch")
        launch = await manager.launch()
        launched = True
        report["launch"] = launch
        application = launch["application"]
        if application["addon_version"] != PACKAGE_VERSION:
            raise RuntimeError("Managed launch did not load the 0.12.0 add-on")
        if not str(application["blender_version"]).startswith("4.2.23"):
            raise RuntimeError("Managed launch did not use Blender 4.2.23")
        ping_before = await client.call("connection.ping", read_only=True)
        capabilities = ping_before["capability_versions"]
        for capability in (
            "mesh_selection",
            "mesh_surface_query",
            "mesh_deformation",
            "mesh_validation",
        ):
            if int(capabilities.get(capability, 0)) < 1:
                raise RuntimeError(f"Managed add-on did not advertise {capability}: 1")
        if int(capabilities.get("transactions", 0)) < 6:
            raise RuntimeError("Managed add-on did not advertise transactions: 6")
        report["ping_before"] = ping_before
        report["fixture_open"] = await manager.project_open(
            str(fixture_project), save_current=False, use_scripts=False, load_ui=False
        )
        report["context_before"] = await client.call("context.get", read_only=True)
        await check_resources(client, report)
        await check_surfaces(client, report)
        await check_deformations(client, manager, report)
        await check_shared_and_disconnect(client, report)
        await check_native_save(client, manager, report)
        await render_evidence(client, artifact_directory, report)
        await check_real_scene(client, manager, real_source, real_project, report)
        ping_after = await client.call("connection.ping", read_only=True)
        if int(ping_after["heartbeat"]) <= int(ping_before["heartbeat"]):
            raise RuntimeError("Blender heartbeat did not advance")
        report["ping_after"] = ping_after
        report["fixture_source_sha256_after"] = sha256(fixture_source)
        report["real_source_sha256_after"] = sha256(real_source)
        report["fixture_source_unchanged"] = (
            report["fixture_source_sha256_after"] == fixture_hash
        )
        report["real_source_unchanged"] = report["real_source_sha256_after"] == real_hash
        if not report["fixture_source_unchanged"] or not report["real_source_unchanged"]:
            raise RuntimeError("A source fixture changed during 0.12 acceptance")
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
    parser.add_argument("--port", type=int, default=9888)
    parser.add_argument("--timeout", type=float, default=120.0)
    args = parser.parse_args()
    report = asyncio.run(run(args))
    report_path = Path(report["artifact_directory"]) / f"report-{PACKAGE_VERSION}.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(report_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
