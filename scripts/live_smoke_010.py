"""Run the Blender 4.2 bounded Modifier-authoring acceptance for release 0.10.0."""

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
from blender_research_mcp.rendering import request_render_preview

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_BUILDER = ROOT / "scripts" / "create_modifier_fixture.py"


def stage(name: str) -> None:
    print(f"[0.10 smoke] {name}", flush=True)


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
        raise RuntimeError(f"Could not build Modifier fixture; see {log_path}")


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


async def begin(client: BridgeClient, generation: int, label: str) -> dict[str, Any]:
    return await mutate(
        client,
        "transaction.begin",
        {"label": label, "viewport_id": None},
        generation,
    )


async def inspect_stack(client: BridgeClient, object_name: str) -> dict[str, Any]:
    return await client.call(
        "modifier.inspect",
        {"object_name": object_name},
        read_only=True,
    )


async def inspect_object(client: BridgeClient, object_name: str) -> dict[str, Any]:
    return await client.call(
        "object.inspect",
        {"object_name": object_name},
        read_only=True,
    )


async def inspect_geometry(client: BridgeClient, object_name: str) -> dict[str, Any]:
    return await client.call(
        "object.geometry.inspect",
        {"object_name": object_name},
        read_only=True,
    )


def exact_operand(inspected: dict[str, Any]) -> dict[str, str]:
    return {
        "object_name": str(inspected["name"]),
        "expected_object_identity": str(inspected["session_identity"]),
    }


def create_params(
    transaction_id: str,
    inspected: dict[str, Any],
    definition: dict[str, Any],
) -> dict[str, Any]:
    return {
        "transaction_id": transaction_id,
        "object_name": inspected["object_name"],
        "expected_object_identity": inspected["object_identity"],
        "expected_stack_fingerprint": inspected["stack_fingerprint"],
        "definition": definition,
    }


def modifier_item(inspected: dict[str, Any], name: str) -> dict[str, Any]:
    return next(item for item in inspected["modifiers"] if item["name"] == name)


def target_params(
    transaction_id: str,
    inspected: dict[str, Any],
    item: dict[str, Any],
) -> dict[str, Any]:
    return {
        "transaction_id": transaction_id,
        "object_name": inspected["object_name"],
        "expected_object_identity": inspected["object_identity"],
        "modifier_name": item["name"],
        "expected_modifier_identity": item["session_identity"],
        "expected_modifier_type": item["type"],
        "expected_stack_index": item["stack_index"],
        "expected_stack_fingerprint": inspected["stack_fingerprint"],
    }


def require_fingerprint(before: dict[str, Any], after: dict[str, Any], label: str) -> None:
    if before["stack_fingerprint"] != after["stack_fingerprint"]:
        raise RuntimeError(f"{label} did not restore the complete Modifier stack")


async def rollback(
    client: BridgeClient,
    transaction_id: str,
    generation: int,
) -> dict[str, Any]:
    return await mutate(
        client,
        "transaction.rollback",
        {"transaction_id": transaction_id},
        generation,
    )


async def create_then_rollback(
    client: BridgeClient,
    object_name: str,
    definition: dict[str, Any],
) -> dict[str, Any]:
    before = await inspect_stack(client, object_name)
    geometry_before = await inspect_geometry(client, object_name)
    transaction = await begin(
        client,
        int(before["scene_generation"]),
        f"0.10 create {definition['type']}",
    )
    created = await mutate(
        client,
        "modifier.create",
        create_params(str(transaction["transaction_id"]), before, definition),
        int(transaction["scene_generation"]),
    )
    geometry_during = await inspect_geometry(client, object_name)
    if geometry_before["counts"] == geometry_during["counts"]:
        raise RuntimeError(f"{definition['type']} did not change evaluated geometry counts")
    rolled_back = await rollback(
        client,
        str(transaction["transaction_id"]),
        int(created["scene_generation"]),
    )
    after = await inspect_stack(client, object_name)
    require_fingerprint(before, after, f"{definition['type']} create rollback")
    return {
        "before": before,
        "created": created,
        "geometry_before": geometry_before,
        "geometry_during": geometry_during,
        "rollback": rolled_back,
        "after": after,
    }


async def check_four_creates(client: BridgeClient, report: dict[str, Any]) -> None:
    stage("four typed creates and evaluated geometry rollback")
    cutter = await inspect_object(client, "Boolean Cutter")
    cases = [
        ("Bevel Target", {"type": "BEVEL", "name": "Bevel Preview", "width": 0.35, "segments": 4}),
        (
            "Subdivision Target",
            {"type": "SUBSURF", "name": "Subdivision Preview", "levels": 2, "render_levels": 2},
        ),
        (
            "Solidify Target",
            {"type": "SOLIDIFY", "name": "Solidify Preview", "thickness": 0.5, "offset": 0.0},
        ),
        (
            "Boolean Target",
            {
                "type": "BOOLEAN",
                "name": "Boolean Preview",
                "operation": "DIFFERENCE",
                "solver": "EXACT",
                "operand": exact_operand(cutter),
            },
        ),
    ]
    report["create_rollbacks"] = {
        definition["type"]: await create_then_rollback(client, name, definition)
        for name, definition in cases
    }


async def commit_baseline_stacks(
    client: BridgeClient,
    manager: ApplicationManager,
    report: dict[str, Any],
) -> None:
    stage("commit baseline Modifier stacks")
    context = await client.call("context.get", read_only=True)
    transaction = await begin(client, int(context["scene_generation"]), "0.10 baseline stacks")
    transaction_id = str(transaction["transaction_id"])
    generation = int(transaction["scene_generation"])
    cutter = await inspect_object(client, "Boolean Cutter")
    definitions = [
        (
            "Bevel Target",
            {"type": "BEVEL", "name": "Bevel Main", "width": 0.2, "segments": 3, "profile": 0.5},
        ),
        ("Bevel Target", {"type": "BEVEL", "name": "Delete Probe", "width": 0.03, "segments": 1}),
        (
            "Subdivision Target",
            {"type": "SUBSURF", "name": "Subdivision Main", "levels": 1, "render_levels": 1},
        ),
        (
            "Solidify Target",
            {"type": "SOLIDIFY", "name": "Solidify Main", "thickness": 0.25, "offset": 0.0},
        ),
        (
            "Boolean Target",
            {
                "type": "BOOLEAN",
                "name": "Boolean Main",
                "operation": "DIFFERENCE",
                "solver": "EXACT",
                "operand": exact_operand(cutter),
            },
        ),
        (
            "Order Target",
            {
                "type": "BEVEL",
                "name": "Bevel Order",
                "stack_index": 0,
                "width": 0.12,
                "segments": 2,
            },
        ),
        (
            "Order Target",
            {"type": "SUBSURF", "name": "Subdivision Order", "levels": 1, "render_levels": 1},
        ),
    ]
    writes = []
    for object_name, definition in definitions:
        inspected = await inspect_stack(client, object_name)
        result = await mutate(
            client,
            "modifier.create",
            create_params(transaction_id, inspected, definition),
            generation,
        )
        writes.append(result)
        generation = int(result["scene_generation"])
    committed = await mutate(
        client,
        "transaction.commit",
        {"transaction_id": transaction_id},
        generation,
    )
    saved = await manager.project_save()
    report["baseline_commit"] = {
        "writes": writes,
        "commit": committed,
        "save": saved,
    }


async def set_then_rollback(
    client: BridgeClient,
    object_name: str,
    modifier_name: str,
    settings: dict[str, Any],
) -> dict[str, Any]:
    before = await inspect_stack(client, object_name)
    item = modifier_item(before, modifier_name)
    transaction = await begin(client, int(before["scene_generation"]), f"0.10 set {modifier_name}")
    result = await mutate(
        client,
        "modifier.set",
        {**target_params(str(transaction["transaction_id"]), before, item), "settings": settings},
        int(transaction["scene_generation"]),
    )
    rolled_back = await rollback(
        client,
        str(transaction["transaction_id"]),
        int(result["scene_generation"]),
    )
    after = await inspect_stack(client, object_name)
    require_fingerprint(before, after, f"{modifier_name} setting rollback")
    return {"before": before, "set": result, "rollback": rolled_back, "after": after}


async def check_settings_move_and_shared(client: BridgeClient, report: dict[str, Any]) -> None:
    stage("typed settings, stack movement, and shared Mesh independence")
    report["setting_rollbacks"] = {
        "BEVEL": await set_then_rollback(
            client,
            "Bevel Target",
            "Bevel Main",
            {"type": "BEVEL", "width": 0.45, "segments": 5, "profile": 0.7},
        ),
        "SUBSURF": await set_then_rollback(
            client,
            "Subdivision Target",
            "Subdivision Main",
            {
                "type": "SUBSURF",
                "subdivision_type": "SIMPLE",
                "levels": 2,
                "render_levels": 2,
                "quality": 4,
            },
        ),
        "SOLIDIFY": await set_then_rollback(
            client,
            "Solidify Target",
            "Solidify Main",
            {"type": "SOLIDIFY", "thickness": -0.45, "offset": 0.75, "use_even_offset": True},
        ),
        "BOOLEAN": await set_then_rollback(
            client,
            "Boolean Target",
            "Boolean Main",
            {"type": "BOOLEAN", "operation": "UNION", "solver": "FAST", "double_threshold": 0.01},
        ),
    }

    before = await inspect_stack(client, "Order Target")
    item = modifier_item(before, "Subdivision Order")
    transaction = await begin(client, int(before["scene_generation"]), "0.10 move rollback")
    moved = await mutate(
        client,
        "modifier.move",
        {
            **target_params(str(transaction["transaction_id"]), before, item),
            "target_stack_index": 0,
        },
        int(transaction["scene_generation"]),
    )
    if moved["stack"][0]["session_identity"] != item["session_identity"]:
        raise RuntimeError("move did not retain the exact Modifier identity")
    moved_rollback = await rollback(
        client,
        str(transaction["transaction_id"]),
        int(moved["scene_generation"]),
    )
    after = await inspect_stack(client, "Order Target")
    require_fingerprint(before, after, "Modifier move rollback")
    report["move_rollback"] = {
        "before": before,
        "move": moved,
        "rollback": moved_rollback,
        "after": after,
    }

    shared_a = await inspect_stack(client, "Shared Mesh A")
    shared_b = await inspect_stack(client, "Shared Mesh B")
    if shared_a["mesh_identity"] != shared_b["mesh_identity"]:
        raise RuntimeError("shared Mesh fixture does not actually share object data")
    transaction = await begin(client, int(shared_a["scene_generation"]), "0.10 shared Mesh")
    created = await mutate(
        client,
        "modifier.create",
        create_params(
            str(transaction["transaction_id"]),
            shared_a,
            {"type": "BEVEL", "name": "Only A", "width": 0.18, "segments": 2},
        ),
        int(transaction["scene_generation"]),
    )
    during_b = await inspect_stack(client, "Shared Mesh B")
    if during_b["modifiers"]:
        raise RuntimeError("Modifier stack leaked across objects sharing Mesh data")
    shared_rollback = await rollback(
        client,
        str(transaction["transaction_id"]),
        int(created["scene_generation"]),
    )
    after_a = await inspect_stack(client, "Shared Mesh A")
    require_fingerprint(shared_a, after_a, "shared Mesh stack rollback")
    report["shared_mesh_independence"] = {
        "mesh_identity": shared_a["mesh_identity"],
        "mesh_users": shared_a["mesh_users"],
        "created": created,
        "other_during": during_b,
        "rollback": shared_rollback,
    }


async def expect_error(
    client: BridgeClient,
    command: str,
    params: dict[str, Any],
    generation: int,
    expected_code: str,
) -> dict[str, Any]:
    try:
        await mutate(client, command, params, generation)
    except BridgeError as exc:
        if exc.error.code != expected_code:
            raise
        return exc.error.model_dump(mode="json")
    raise RuntimeError(f"{command} unexpectedly succeeded; expected {expected_code}")


async def check_boolean_cycles(client: BridgeClient, report: dict[str, Any]) -> None:
    stage("Boolean self and transitive cycle rejection")
    cycle_a_stack = await inspect_stack(client, "Cycle A")
    cycle_a = await inspect_object(client, "Cycle A")
    direct_tx = await begin(client, int(cycle_a_stack["scene_generation"]), "0.10 direct cycle")
    direct_error = await expect_error(
        client,
        "modifier.create",
        create_params(
            str(direct_tx["transaction_id"]),
            cycle_a_stack,
            {"type": "BOOLEAN", "name": "Self Cycle", "operand": exact_operand(cycle_a)},
        ),
        int(direct_tx["scene_generation"]),
        "BOOLEAN_OPERAND_SELF",
    )
    direct_rollback = await rollback(
        client,
        str(direct_tx["transaction_id"]),
        int(direct_tx["scene_generation"]),
    )

    cycle_b = await inspect_object(client, "Cycle B")
    cycle_a_stack = await inspect_stack(client, "Cycle A")
    transitive_tx = await begin(
        client,
        int(cycle_a_stack["scene_generation"]),
        "0.10 transitive cycle",
    )
    created = await mutate(
        client,
        "modifier.create",
        create_params(
            str(transitive_tx["transaction_id"]),
            cycle_a_stack,
            {"type": "BOOLEAN", "name": "A uses B", "operand": exact_operand(cycle_b)},
        ),
        int(transitive_tx["scene_generation"]),
    )
    cycle_b_stack = await inspect_stack(client, "Cycle B")
    transitive_error = await expect_error(
        client,
        "modifier.create",
        create_params(
            str(transitive_tx["transaction_id"]),
            cycle_b_stack,
            {"type": "BOOLEAN", "name": "B uses A", "operand": exact_operand(cycle_a)},
        ),
        int(created["scene_generation"]),
        "BOOLEAN_CYCLE",
    )
    transitive_rollback = await rollback(
        client,
        str(transitive_tx["transaction_id"]),
        int(created["scene_generation"]),
    )
    report["boolean_cycles"] = {
        "direct": direct_error,
        "direct_rollback": direct_rollback,
        "transitive": transitive_error,
        "transitive_rollback": transitive_rollback,
    }


def comparison_request(
    inspected: dict[str, Any],
    item: dict[str, Any],
    property_name: str,
    values: tuple[Any, ...],
    *,
    view: str = "FRONT",
    orbit: dict[str, float] | None = None,
    display_mode: str = "SOLID",
) -> ComparisonRequest:
    return ComparisonRequest.model_validate(
        {
            "target": {
                "type": "modifier_setting",
                "object_name": inspected["object_name"],
                "expected_object_identity": inspected["object_identity"],
                "modifier_name": item["name"],
                "expected_modifier_identity": item["session_identity"],
                "expected_modifier_type": item["type"],
                "expected_stack_index": item["stack_index"],
                "expected_stack_fingerprint": inspected["stack_fingerprint"],
                "property": property_name,
            },
            "candidates": [
                {"label": chr(ord("A") + index), "value": value}
                for index, value in enumerate(values)
            ],
            "capture": {
                "object_name": inspected["object_name"],
                "view": view,
                "max_size": 512,
                "display_mode": display_mode,
                "overlays": "OFF",
                "orbit": orbit,
            },
        }
    )


async def check_comparisons(
    client: BridgeClient,
    artifact_directory: Path,
    report: dict[str, Any],
) -> None:
    stage("four typed Modifier comparisons")
    cases = [
        (
            "Bevel Target",
            "Bevel Main",
            "width",
            (0.0, 0.8),
            "FRONT",
            {"yaw_degrees": 32.0, "pitch_degrees": 20.0},
            "SOLID",
        ),
        (
            "Subdivision Target",
            "Subdivision Main",
            "levels",
            (0, 2),
            "FRONT",
            {"yaw_degrees": 25.0, "pitch_degrees": 15.0},
            "WIREFRAME",
        ),
        (
            "Solidify Target",
            "Solidify Main",
            "thickness",
            (-0.45, 0.75),
            "FRONT",
            {"yaw_degrees": 28.0, "pitch_degrees": 18.0},
            "SOLID",
        ),
        (
            "Boolean Target",
            "Boolean Main",
            "operation",
            ("UNION", "INTERSECT"),
            "FRONT",
            {"yaw_degrees": 22.0, "pitch_degrees": 12.0},
            "SOLID",
        ),
    ]
    comparisons: dict[str, Any] = {}
    for object_name, modifier_name, property_name, values, view, orbit, display_mode in cases:
        inspected = await inspect_stack(client, object_name)
        item = modifier_item(inspected, modifier_name)
        images, result = await run_lookdev_comparison(
            client,
            comparison_request(
                inspected,
                item,
                property_name,
                values,
                view=view,
                orbit=orbit,
                display_mode=display_mode,
            ),
        )
        if [entry["label"] for entry in result["items"]] != ["baseline", "A", "B"]:
            raise RuntimeError("Modifier comparison order changed")
        if not all(
            result[key] is True
            for key in ("context_unchanged", "object_unchanged", "target_restored")
        ):
            raise RuntimeError("Modifier comparison did not prove complete restoration")
        if not any(
            int(entry["difference"]["max_channel_difference"]) > 0 for entry in result["candidates"]
        ):
            raise RuntimeError(f"{modifier_name}.{property_name} produced no image difference")
        paths = []
        for index, image in enumerate(images):
            path = artifact_directory / f"compare-{modifier_name}-{property_name}-{index}.png"
            path.write_bytes(image)
            paths.append({"path": str(path), "sha256": sha256(path)})
        comparisons[f"{modifier_name}.{property_name}"] = {"result": result, "images": paths}
    report["comparisons"] = comparisons


async def recover_by_reload(manager: ApplicationManager) -> dict[str, Any]:
    return await manager.project_reload(save_current=False, use_scripts=False, load_ui=False)


async def check_conflicts(
    client: BridgeClient,
    manager: ApplicationManager,
    report: dict[str, Any],
) -> None:
    stage("private same-setting and order conflict injection")
    inspected = await inspect_stack(client, "Bevel Target")
    item = modifier_item(inspected, "Bevel Main")
    hook_result: dict[str, Any] = {}

    async def setting_hook(
        phase: str,
        label: str | None,
        details: dict[str, Any],
    ) -> None:
        del details
        if phase == "after_write" and label == "A":
            hook_result.update(
                await client.call(
                    "_test.modifier.touch",
                    {
                        "action": "setting",
                        "object_name": "Bevel Target",
                        "modifier_name": "Bevel Main",
                        "property": "width",
                        "value": 0.77,
                    },
                    read_only=False,
                )
            )

    try:
        await run_lookdev_comparison(
            client,
            comparison_request(inspected, item, "width", (0.4, 0.7)),
            _phase_hook=setting_hook,
        )
    except BridgeError as exc:
        if exc.error.code != "MODIFIER_STACK_CONFLICT":
            raise
        setting_error = exc.error.model_dump(mode="json")
    else:
        raise RuntimeError("injected Modifier setting conflict unexpectedly restored")
    preserved = await inspect_stack(client, "Bevel Target")
    if abs(float(modifier_item(preserved, "Bevel Main")["settings"]["width"]) - 0.77) > 1e-7:
        raise RuntimeError("Modifier setting conflict overwrote the injected user value")
    setting_reload = await recover_by_reload(manager)

    before = await inspect_stack(client, "Order Target")
    target = modifier_item(before, "Subdivision Order")
    transaction = await begin(client, int(before["scene_generation"]), "0.10 order conflict")
    moved = await mutate(
        client,
        "modifier.move",
        {
            **target_params(str(transaction["transaction_id"]), before, target),
            "target_stack_index": 0,
        },
        int(transaction["scene_generation"]),
    )
    order_hook = await client.call(
        "_test.modifier.touch",
        {
            "action": "move",
            "object_name": "Order Target",
            "modifier_name": "Bevel Order",
            "target_stack_index": 2,
        },
        read_only=False,
    )
    try:
        await rollback(
            client,
            str(transaction["transaction_id"]),
            int(moved["scene_generation"]),
        )
    except BridgeError as exc:
        if exc.error.code != "MODIFIER_STACK_CONFLICT":
            raise
        order_error = exc.error.model_dump(mode="json")
    else:
        raise RuntimeError("injected Modifier order conflict unexpectedly restored")
    preserved_order = [
        entry["name"] for entry in (await inspect_stack(client, "Order Target"))["modifiers"]
    ]
    if preserved_order != ["Subdivision Order", "Legacy Mirror", "Bevel Order"]:
        raise RuntimeError("Modifier order conflict overwrote the injected user order")
    order_reload = await recover_by_reload(manager)
    report["conflicts"] = {
        "setting_hook": hook_result,
        "setting_error": setting_error,
        "setting_preserved_width": 0.77,
        "setting_reload": setting_reload,
        "order_hook": order_hook,
        "order_error": order_error,
        "order_preserved": preserved_order,
        "order_reload": order_reload,
    }


async def disconnect_and_verify(
    client: BridgeClient,
    object_name: str,
    command: str,
    build_params: Any,
    label: str,
) -> dict[str, Any]:
    before = await inspect_stack(client, object_name)
    transaction = await begin(client, int(before["scene_generation"]), label)
    params = build_params(str(transaction["transaction_id"]), before)
    changed = await mutate(
        client,
        command,
        params,
        int(transaction["scene_generation"]),
    )
    await client.close()
    await asyncio.sleep(3.0)
    reconnected = await client.call("connection.ping", read_only=True)
    after = await inspect_stack(client, object_name)
    require_fingerprint(before, after, f"{label} disconnect rollback")
    return {
        "before_fingerprint": before["stack_fingerprint"],
        "changed": changed,
        "reconnected_instance": reconnected["instance_id"],
        "after_fingerprint": after["stack_fingerprint"],
        "restored": True,
    }


async def check_disconnect_rollbacks(client: BridgeClient, report: dict[str, Any]) -> None:
    stage("create, set, move, and pending-delete disconnect rollback")

    def create_probe(transaction_id: str, inspected: dict[str, Any]) -> dict[str, Any]:
        return create_params(
            transaction_id,
            inspected,
            {"type": "BEVEL", "name": "Disconnect Create", "width": 0.3, "segments": 3},
        )

    def set_probe(transaction_id: str, inspected: dict[str, Any]) -> dict[str, Any]:
        return {
            **target_params(transaction_id, inspected, modifier_item(inspected, "Bevel Main")),
            "settings": {"type": "BEVEL", "width": 0.68},
        }

    def move_probe(transaction_id: str, inspected: dict[str, Any]) -> dict[str, Any]:
        return {
            **target_params(
                transaction_id,
                inspected,
                modifier_item(inspected, "Subdivision Order"),
            ),
            "target_stack_index": 0,
        }

    def delete_probe(transaction_id: str, inspected: dict[str, Any]) -> dict[str, Any]:
        return target_params(
            transaction_id,
            inspected,
            modifier_item(inspected, "Delete Probe"),
        )

    report["disconnect_rollbacks"] = {
        "create": await disconnect_and_verify(
            client,
            "Disconnect Create Target",
            "modifier.create",
            create_probe,
            "0.10 disconnect create",
        ),
        "set": await disconnect_and_verify(
            client,
            "Bevel Target",
            "modifier.set",
            set_probe,
            "0.10 disconnect set",
        ),
        "move": await disconnect_and_verify(
            client,
            "Order Target",
            "modifier.move",
            move_probe,
            "0.10 disconnect move",
        ),
        "delete": await disconnect_and_verify(
            client,
            "Bevel Target",
            "modifier.delete",
            delete_probe,
            "0.10 disconnect delete",
        ),
    }


async def commit_delete_save_reload_render(
    client: BridgeClient,
    manager: ApplicationManager,
    artifact_directory: Path,
    report: dict[str, Any],
) -> None:
    stage("commit delete, save, reload, inspect, and Eevee render")
    before = await inspect_stack(client, "Bevel Target")
    item = modifier_item(before, "Delete Probe")
    transaction = await begin(client, int(before["scene_generation"]), "0.10 commit delete")
    deleted = await mutate(
        client,
        "modifier.delete",
        target_params(str(transaction["transaction_id"]), before, item),
        int(transaction["scene_generation"]),
    )
    if not deleted["modifier"]["pending_delete"]:
        raise RuntimeError("modifier.delete did not mark pending_delete before commit")
    committed = await mutate(
        client,
        "transaction.commit",
        {"transaction_id": transaction["transaction_id"]},
        int(deleted["scene_generation"]),
    )
    saved = await manager.project_save()
    reloaded = await manager.project_reload(save_current=False, use_scripts=False, load_ui=False)
    after = await inspect_stack(client, "Bevel Target")
    if any(entry["name"] == "Delete Probe" for entry in after["modifiers"]):
        raise RuntimeError("committed Modifier deletion did not persist through reload")
    camera = await inspect_object(client, "Modifier Camera")
    preview_bytes, preview = await request_render_preview(
        client,
        {
            "camera_name": "Modifier Camera",
            "expected_camera_identity": camera["session_identity"],
            "width": 480,
            "height": 320,
            "samples": 24,
            "transparent": False,
        },
        expected_scene_generation=int(after["scene_generation"]),
        idempotency_key=str(uuid4()),
    )
    path = artifact_directory / "modifier-authoring-final.png"
    path.write_bytes(preview_bytes)
    report["persistence"] = {
        "delete": deleted,
        "commit": committed,
        "save": saved,
        "reload": reloaded,
        "after": after,
        "preview": preview,
        "preview_path": str(path),
        "preview_sha256": sha256(path),
    }


def context_identity(context: dict[str, Any]) -> dict[str, Any]:
    return {
        key: context.get(key)
        for key in ("mode", "active_object", "selected_objects", "workspace", "scene")
    }


async def run(args: argparse.Namespace) -> dict[str, Any]:
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + f"-{uuid4().hex[:8]}"
    temporary_root = Path(tempfile.gettempdir()) / "blender-research-mcp-modifiers" / run_id
    temporary_root.mkdir(parents=True, exist_ok=False)
    artifact_directory = ROOT / "artifacts" / "live-smoke" / run_id
    artifact_directory.mkdir(parents=True, exist_ok=False)
    source = temporary_root / "modifier-source.blend"
    project = temporary_root / "modifier-project.blend"
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
            raise RuntimeError(f"smoke port is already in use: {args.port}")
        report["status_before"] = status_before
        stage("managed launch")
        launch = await manager.launch()
        launched = True
        report["launch"] = launch
        application = launch["application"]
        if launch["status"] != "launched":
            raise RuntimeError("cold managed launch did not return launched")
        if application["addon_version"] != "0.10.0" or not str(
            application["blender_version"]
        ).startswith("4.2.23"):
            raise RuntimeError("managed launch did not load Blender 4.2.23 with add-on 0.10.0")
        ping_before = await client.call("connection.ping", read_only=True)
        if int(ping_before["capability_versions"].get("modifier_authoring", 0)) < 1:
            raise RuntimeError("managed add-on did not advertise modifier_authoring: 1")
        if int(ping_before["capability_versions"].get("transactions", 0)) < 3:
            raise RuntimeError("managed add-on did not advertise transactions: 3")
        report["ping_before"] = ping_before
        report["project_open"] = await manager.project_open(
            str(project), save_current=False, use_scripts=False, load_ui=False
        )
        context_before = await client.call("context.get", read_only=True)
        report["context_before"] = context_before

        await check_four_creates(client, report)
        await commit_baseline_stacks(client, manager, report)
        await check_settings_move_and_shared(client, report)
        await check_boolean_cycles(client, report)
        await check_comparisons(client, artifact_directory, report)
        await check_conflicts(client, manager, report)
        await check_disconnect_rollbacks(client, report)
        await commit_delete_save_reload_render(client, manager, artifact_directory, report)

        context_after = await client.call("context.get", read_only=True)
        if context_identity(context_before) != context_identity(context_after):
            raise RuntimeError("Modifier operations did not preserve user context")
        ping_after = await client.call("connection.ping", read_only=True)
        if int(ping_after["heartbeat"]) <= int(ping_before["heartbeat"]):
            raise RuntimeError("Blender UI heartbeat did not advance")
        report["context_after"] = context_after
        report["ping_after"] = ping_after
        report["source_sha256_after"] = sha256(source)
        report["source_unchanged"] = report["source_sha256_after"] == source_hash_before
        if not report["source_unchanged"]:
            raise RuntimeError("source fixture changed during live acceptance")
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
    parser.add_argument("--port", type=int, default=9883)
    parser.add_argument("--timeout", type=float, default=90.0)
    args = parser.parse_args()
    try:
        report = asyncio.run(run(args))
    except BridgeError as exc:
        print(json.dumps(exc.error.model_dump(mode="json"), ensure_ascii=False, indent=2))
        raise
    report_path = Path(report["artifact_directory"]) / "report-0.10.0.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(report_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
