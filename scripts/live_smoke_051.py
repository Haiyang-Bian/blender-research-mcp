"""Run the Blender 4.2 bounded LookDev write acceptance through MCP stdio."""

from __future__ import annotations

import argparse
import json
import math
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import anyio
from live_smoke import (
    call_expected_error,
    call_structured,
    context_identity,
    find_non_ascii_object,
    foreground_process_id,
    git_status,
    image_difference_statistics,
    images_match_within_render_noise,
    save_image,
    sha256,
)
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from blender_research_mcp.session import load_manifest

ROOT = Path(__file__).resolve().parents[1]
REQUIRED_CAPABILITIES = {
    "viewport_capture": 3,
    "viewport_raycast": 1,
    "geometry_inspection": 1,
    "lookdev_inspection": 1,
    "transactions": 2,
    "object_transform_scale": 1,
    "object_visibility": 1,
    "modifier_state": 1,
    "shape_key_value": 1,
    "material_input": 1,
}
REQUIRED_TOOLS = {
    "connection.ping",
    "context.get",
    "object.lookdev.inspect",
    "material.inspect",
    "viewport.capture",
    "viewport.raycast",
    "transaction.begin",
    "object.visibility.set",
    "modifier.set_state",
    "shape_key.set_value",
    "material.set_input",
    "transaction.commit",
    "transaction.rollback",
}


def values_match(left: Any, right: Any) -> bool:
    if isinstance(left, bool) or isinstance(right, bool):
        return type(left) is type(right) and left == right
    if isinstance(left, list) or isinstance(right, list):
        if not isinstance(left, list) or not isinstance(right, list) or len(left) != len(right):
            return False
        return all(values_match(a, b) for a, b in zip(left, right, strict=True))
    if isinstance(left, int) or isinstance(right, int):
        return type(left) is type(right) and left == right
    return math.isclose(float(left), float(right), rel_tol=0.0, abs_tol=1e-7)


def choose_shape_key(lookdev: dict[str, Any]) -> tuple[dict[str, Any], float]:
    for shape_key in lookdev.get("shape_keys", []):
        if shape_key.get("driven"):
            continue
        current = float(shape_key["value"])
        minimum = float(shape_key["slider_min"])
        maximum = float(shape_key["slider_max"])
        span = maximum - minimum
        if span <= 1e-7:
            continue
        candidate = min(maximum, current + max(0.05, span * 0.1))
        if math.isclose(candidate, current, abs_tol=1e-7):
            candidate = max(minimum, current - max(0.05, span * 0.1))
        if not math.isclose(candidate, current, abs_tol=1e-7):
            return shape_key, float(candidate)
    raise RuntimeError(f"no bounded undriven shape key is available on {lookdev['name']}")


def choose_material_value(socket: dict[str, Any]) -> Any:
    kind = socket["socket_kind"]
    current = socket["value"]
    minimum = socket.get("minimum")
    maximum = socket.get("maximum")
    if kind == "BOOLEAN":
        return not bool(current)
    if kind == "INT":
        value = int(current)
        if maximum is None or value + 1 <= int(maximum):
            return value + 1
        if minimum is None or value - 1 >= int(minimum):
            return value - 1
        raise RuntimeError("integer material socket has no alternative value")

    def alternate(value: float) -> float:
        low = float(minimum) if minimum is not None else -math.inf
        high = float(maximum) if maximum is not None else math.inf
        finite_span = high - low if math.isfinite(low) and math.isfinite(high) else 0.0
        step = max(abs(value) * 0.1, finite_span * 0.05, 0.05)
        if value + step <= high:
            return float(value + step)
        if value - step >= low:
            return float(value - step)
        raise RuntimeError("floating material socket has no alternative value")

    if kind == "FLOAT":
        return alternate(float(current))
    if kind in {"VECTOR", "COLOR"}:
        result = [float(component) for component in current]
        result[0] = alternate(result[0])
        return result
    raise RuntimeError(f"unsupported writable socket kind: {kind}")


async def begin_transaction(session: ClientSession, label: str) -> dict[str, Any]:
    ping, _ = await call_structured(session, "connection.ping")
    transaction, _ = await call_structured(
        session,
        "transaction.begin",
        {
            "expected_scene_generation": ping["scene_generation"],
            "idempotency_key": str(uuid.uuid4()),
            "label": label,
        },
    )
    return transaction


async def rollback_transaction(
    session: ClientSession,
    transaction_id: str,
    generation: int,
) -> dict[str, Any]:
    result, _ = await call_structured(
        session,
        "transaction.rollback",
        {
            "transaction_id": transaction_id,
            "expected_scene_generation": generation,
            "idempotency_key": str(uuid.uuid4()),
        },
    )
    return result


async def capture_evidence(
    session: ClientSession,
    object_name: str,
    artifact_directory: Path,
    label: str,
) -> tuple[dict[str, Any], str]:
    metadata, result = await call_structured(
        session,
        "viewport.capture",
        {
            "object_name": object_name,
            "view": "FRONT",
            "max_size": 800,
            "display_mode": "MATERIAL",
            "overlays": "OFF",
        },
    )
    image_hash = save_image(result, artifact_directory / f"{label}.png")
    return metadata, image_hash


async def assert_context_unchanged(
    session: ClientSession,
    baseline: dict[str, Any],
    label: str,
) -> dict[str, Any]:
    current, _ = await call_structured(session, "context.get")
    if context_identity(current) != context_identity(baseline):
        raise RuntimeError(f"user context changed during {label}")
    return current


async def inspect_material_candidate(
    session: ClientSession,
    lookdev_by_name: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any], dict[str, Any], Any]:
    candidates: list[tuple[int, dict[str, Any], dict[str, Any], Any]] = []
    for object_name, lookdev in lookdev_by_name.items():
        for slot in lookdev.get("material_slots", []):
            if slot.get("name") is None:
                continue
            material, _ = await call_structured(
                session,
                "material.inspect",
                {
                    "object_name": object_name,
                    "material_slot_index": slot["index"],
                },
            )
            for socket in material.get("sockets", []):
                if not socket.get("writable"):
                    continue
                try:
                    next_value = choose_material_value(socket)
                except RuntimeError:
                    continue
                kind_rank = {"FLOAT": 0, "COLOR": 1, "VECTOR": 2, "BOOLEAN": 3, "INT": 4}
                node_rank = {
                    "BSDF_PRINCIPLED": 0,
                    "MIX_RGB": 1,
                    "MATH": 2,
                    "VALUE": 3,
                    "RGB": 3,
                    "OUTPUT_MATERIAL": 20,
                    "TEX_IMAGE": 20,
                }
                rank = (
                    (0 if material["material_users"] == 1 else 100)
                    + node_rank.get(socket["node_type"], 10) * 5
                    + kind_rank.get(socket["socket_kind"], 4)
                )
                candidates.append((rank, material, socket, next_value))
    if not candidates:
        raise RuntimeError("no writable material input was discovered on inspected objects")
    _rank, material, socket, next_value = min(candidates, key=lambda item: item[0])
    return material, socket, next_value


async def initialize_session(
    session: ClientSession,
    report: dict[str, Any],
) -> dict[str, Any]:
    initialized = await session.initialize()
    tools = await session.list_tools()
    tool_names = [tool.name for tool in tools.tools]
    if not REQUIRED_TOOLS.issubset(tool_names):
        raise RuntimeError(f"MCP tool list is incomplete: {tool_names}")
    ping, _ = await call_structured(session, "connection.ping")
    for capability, minimum in REQUIRED_CAPABILITIES.items():
        actual = int(ping["capability_versions"].get(capability, 0))
        if actual < minimum:
            raise RuntimeError(f"Blender add-on advertises {capability} v{actual}, need v{minimum}")
    report["mcp"] = {
        "protocol_version": initialized.protocolVersion,
        "server": initialized.serverInfo.model_dump(mode="json"),
        "tools": tool_names,
    }
    return ping


async def wait_for_stable_context(
    session: ClientSession,
    *,
    timeout_seconds: float = 2.0,
) -> dict[str, Any]:
    deadline = anyio.current_time() + timeout_seconds
    previous: dict[str, Any] | None = None
    while anyio.current_time() < deadline:
        current, _ = await call_structured(session, "context.get")
        identity = context_identity(current)
        if identity == previous:
            return current
        previous = identity
        await anyio.sleep(0.2)
    raise RuntimeError("Blender context did not stabilize after the window focus transition")


async def prompt_for_hide_render(
    session: ClientSession,
    object_name: str,
    expected: bool,
    prompt: str,
) -> dict[str, Any]:
    for _attempt in range(3):
        await anyio.to_thread.run_sync(input, prompt)
        await anyio.sleep(0.3)
        lookdev, _ = await call_structured(
            session,
            "object.lookdev.inspect",
            {"object_name": object_name},
        )
        actual = bool(lookdev["visibility"]["hide_render"])
        if actual == expected:
            return lookdev
        print(
            f"Observed {object_name}.hide_render={actual}; expected {expected}. "
            "Change the Blender property itself and try again.",
            flush=True,
        )
    raise RuntimeError(f"{object_name}.hide_render was not changed to {expected}")


async def run(args: argparse.Namespace) -> None:
    artifact_directory = args.artifact_directory.resolve(strict=True)
    temporary_blend = args.blend_file.resolve(strict=True)
    source_blend = args.source_file.resolve(strict=True)
    preparation = json.loads(
        (artifact_directory / "preparation.json").read_text(encoding="utf-8")
    )
    if sha256(source_blend) != preparation["source_sha256"]:
        raise RuntimeError("source blend changed after smoke preparation")
    temporary_hash_before = sha256(temporary_blend)
    if (
        temporary_hash_before != preparation["temporary_sha256"]
        and not args.accept_current_temp_baseline
    ):
        raise RuntimeError("temporary blend changed before smoke execution")

    server_parameters = StdioServerParameters(
        command="uv",
        args=["run", "--no-sync", "blender-research-mcp"],
        cwd=ROOT,
    )
    report: dict[str, Any] = {
        "run_id": preparation["run_id"],
        "started_at": datetime.now(UTC).isoformat(),
        "source_file": str(source_blend),
        "source_sha256_before": preparation["source_sha256"],
        "source_git_status_before": preparation["source_git_status"],
        "temporary_blend_file": str(temporary_blend),
        "prepared_temporary_sha256": preparation["temporary_sha256"],
        "temporary_sha256_before": temporary_hash_before,
        "accepted_current_temp_baseline": bool(args.accept_current_temp_baseline),
    }
    disconnect_expected: dict[str, Any] = {}

    async with stdio_client(server_parameters) as streams:  # noqa: SIM117
        async with ClientSession(
            *streams,
            read_timeout_seconds=timedelta(seconds=60),
        ) as session:
            ping_before = await initialize_session(session, report)
            report["ping_before"] = ping_before
            if ping_before["addon_version"] != "0.5.1":
                raise RuntimeError(f"unexpected add-on version: {ping_before['addon_version']}")
            setup_context, _ = await call_structured(session, "context.get")
            if Path(setup_context["blend_file"]).resolve() != temporary_blend:
                raise RuntimeError("Blender is not displaying the prepared temporary blend")
            unicode_name = args.unicode_object or find_non_ascii_object(setup_context)

            if args.verify_ui:
                confirmation = await anyio.to_thread.run_sync(
                    input,
                    "Confirm the compact N-panel and the Scene Properties panel show the "
                    "0.5 write authority without splitting an Area; type YES: ",
                )
                if confirmation.strip().upper() != "YES":
                    raise RuntimeError("Blender 0.5 native UI checkpoint was not confirmed")
                report["ui_confirmation"] = True

            if args.verify_background:
                await anyio.to_thread.run_sync(
                    input,
                    "Bring Blender to the foreground without changing selection, "
                    "then press Enter: ",
                )
                await anyio.sleep(0.5)
                blender_pid = load_manifest().pid
                if foreground_process_id() != blender_pid:
                    raise RuntimeError("Blender is not the foreground process")
                await wait_for_stable_context(session)
                foreground_capture, foreground_result = await call_structured(
                    session,
                    "viewport.capture",
                    {
                        "object_name": args.capture_object,
                        "view": "FRONT",
                        "max_size": 800,
                        "display_mode": "MATERIAL",
                        "overlays": "OFF",
                    },
                )
                report["foreground_capture"] = foreground_capture
                report["foreground_capture_sha256"] = save_image(
                    foreground_result,
                    artifact_directory / "foreground-material.png",
                )
                await anyio.to_thread.run_sync(
                    input,
                    "Cover Blender with Codex or another window, then press Enter: ",
                )
                await anyio.sleep(0.5)
                if foreground_process_id() == blender_pid:
                    raise RuntimeError("Blender still owns foreground focus")
                await wait_for_stable_context(session)
                report["background_foreground_pid"] = foreground_process_id()

            context_before, _ = await call_structured(session, "context.get")
            report["context_before"] = context_before
            unicode_lookdev, _ = await call_structured(
                session,
                "object.lookdev.inspect",
                {"object_name": unicode_name},
            )
            visibility_name = args.visibility_object or args.capture_object
            visibility_lookdev, _ = await call_structured(
                session,
                "object.lookdev.inspect",
                {"object_name": visibility_name},
            )
            lookdev_by_name = {
                unicode_name: unicode_lookdev,
                visibility_name: visibility_lookdev,
            }
            for object_name in (args.capture_object, args.transform_object):
                if object_name in lookdev_by_name:
                    continue
                lookdev, _ = await call_structured(
                    session,
                    "object.lookdev.inspect",
                    {"object_name": object_name},
                )
                lookdev_by_name[object_name] = lookdev

            modifier_object = next(
                (item for item in lookdev_by_name.values() if item.get("modifiers")),
                None,
            )
            if modifier_object is None:
                raise RuntimeError("no modifier target was discovered")
            modifier = modifier_object["modifiers"][0]
            shape_key, shape_value = choose_shape_key(unicode_lookdev)
            material, socket, material_value = await inspect_material_candidate(
                session,
                lookdev_by_name,
            )
            if material["material_users"] > 1:
                answer = await anyio.to_thread.run_sync(
                    input,
                    "Material "
                    f"{material['material_name']} has {material['material_users']} users and "
                    f"affects {material['affected_objects']}. Type ALLOW SHARED to preview it: ",
                )
                if answer.strip().upper() != "ALLOW SHARED":
                    raise RuntimeError("shared material preview was not authorized")
            report["discovered_targets"] = {
                "unicode_object": unicode_lookdev,
                "visibility_object": visibility_lookdev,
                "modifier": {"object": modifier_object["name"], **modifier},
                "shape_key": shape_key,
                "material": material,
                "material_socket": socket,
            }

            baseline_capture, baseline_result = await call_structured(
                session,
                "viewport.capture",
                {
                    "object_name": args.capture_object,
                    "view": "FRONT",
                    "max_size": 800,
                    "display_mode": "MATERIAL",
                    "overlays": "OFF",
                },
            )
            report["baseline_capture"] = baseline_capture
            report["baseline_capture_sha256"] = save_image(
                baseline_result,
                artifact_directory / "baseline-material.png",
            )
            if args.verify_background:
                focus_statistics = image_difference_statistics(
                    artifact_directory / "foreground-material.png",
                    artifact_directory / "baseline-material.png",
                )
                if not images_match_within_render_noise(focus_statistics):
                    raise RuntimeError(
                        "foreground and obscured captures differ beyond render noise"
                    )
                report["focus_capture_difference"] = focus_statistics

            # Visibility preview and stale capture rejection.
            visibility_before = bool(visibility_lookdev["visibility"]["hide_render"])
            transaction = await begin_transaction(session, "0.5 visibility rollback smoke")
            visibility_write, _ = await call_structured(
                session,
                "object.visibility.set",
                {
                    "transaction_id": transaction["transaction_id"],
                    "object_name": visibility_name,
                    "expected_object_identity": visibility_lookdev["session_identity"],
                    "hide_render": not visibility_before,
                    "expected_scene_generation": transaction["scene_generation"],
                    "idempotency_key": str(uuid.uuid4()),
                },
            )
            stale_error = await call_expected_error(
                session,
                "viewport.raycast",
                {"capture_id": baseline_capture["capture_id"], "x": 0.5, "y": 0.5},
                "CAPTURE_STALE",
            )
            evidence, evidence_hash = await capture_evidence(
                session,
                args.capture_object,
                artifact_directory,
                "visibility-preview",
            )
            visibility_rollback = await rollback_transaction(
                session,
                transaction["transaction_id"],
                int(evidence["scene_generation"]),
            )
            visibility_after, _ = await call_structured(
                session,
                "object.lookdev.inspect",
                {"object_name": visibility_name},
            )
            if bool(visibility_after["visibility"]["hide_render"]) != visibility_before:
                raise RuntimeError("visibility rollback did not restore hide_render")
            await assert_context_unchanged(session, context_before, "visibility preview")
            report["visibility_preview"] = {
                "write": visibility_write,
                "evidence_sha256": evidence_hash,
                "rollback": visibility_rollback,
                "stale_capture_error": stale_error,
            }

            # Modifier preview.
            modifier_before = bool(modifier["show_viewport"])
            transaction = await begin_transaction(session, "0.5 modifier rollback smoke")
            modifier_write, _ = await call_structured(
                session,
                "modifier.set_state",
                {
                    "transaction_id": transaction["transaction_id"],
                    "object_name": modifier_object["name"],
                    "expected_object_identity": modifier_object["session_identity"],
                    "modifier_name": modifier["name"],
                    "expected_modifier_identity": modifier["session_identity"],
                    "show_viewport": not modifier_before,
                    "expected_scene_generation": transaction["scene_generation"],
                    "idempotency_key": str(uuid.uuid4()),
                },
            )
            evidence, evidence_hash = await capture_evidence(
                session,
                modifier_object["name"],
                artifact_directory,
                "modifier-preview",
            )
            modifier_rollback = await rollback_transaction(
                session,
                transaction["transaction_id"],
                int(evidence["scene_generation"]),
            )
            modifier_after, _ = await call_structured(
                session,
                "object.lookdev.inspect",
                {"object_name": modifier_object["name"]},
            )
            restored_modifier = next(
                item for item in modifier_after["modifiers"] if item["name"] == modifier["name"]
            )
            if bool(restored_modifier["show_viewport"]) != modifier_before:
                raise RuntimeError("modifier rollback did not restore show_viewport")
            await assert_context_unchanged(session, context_before, "modifier preview")
            report["modifier_preview"] = {
                "write": modifier_write,
                "evidence_sha256": evidence_hash,
                "rollback": modifier_rollback,
            }

            # Shape-key preview.
            shape_before = float(shape_key["value"])
            transaction = await begin_transaction(session, "0.5 shape key rollback smoke")
            shape_write, _ = await call_structured(
                session,
                "shape_key.set_value",
                {
                    "transaction_id": transaction["transaction_id"],
                    "object_name": unicode_name,
                    "expected_object_identity": unicode_lookdev["session_identity"],
                    "shape_key_name": shape_key["name"],
                    "expected_shape_key_identity": shape_key["session_identity"],
                    "value": shape_value,
                    "expected_scene_generation": transaction["scene_generation"],
                    "idempotency_key": str(uuid.uuid4()),
                },
            )
            evidence, evidence_hash = await capture_evidence(
                session,
                unicode_name,
                artifact_directory,
                "shape-key-preview",
            )
            shape_rollback = await rollback_transaction(
                session,
                transaction["transaction_id"],
                int(evidence["scene_generation"]),
            )
            shape_after, _ = await call_structured(
                session,
                "object.lookdev.inspect",
                {"object_name": unicode_name},
            )
            restored_shape = next(
                item for item in shape_after["shape_keys"] if item["name"] == shape_key["name"]
            )
            if not values_match(restored_shape["value"], shape_before):
                raise RuntimeError("shape-key rollback did not restore its value")
            await assert_context_unchanged(session, context_before, "shape-key preview")
            report["shape_key_preview"] = {
                "write": shape_write,
                "evidence_sha256": evidence_hash,
                "rollback": shape_rollback,
            }

            # Material-input preview.
            material_before = socket["value"]
            transaction = await begin_transaction(session, "0.5 material rollback smoke")
            material_write, _ = await call_structured(
                session,
                "material.set_input",
                {
                    "transaction_id": transaction["transaction_id"],
                    "object_name": material["object_name"],
                    "expected_object_identity": material["object_identity"],
                    "material_slot_index": material["material_slot_index"],
                    "material_name": material["material_name"],
                    "expected_material_identity": material["material_identity"],
                    "expected_material_users": material["material_users"],
                    "node_name": socket["node_name"],
                    "expected_node_identity": socket["node_identity"],
                    "socket_identifier": socket["socket_identifier"],
                    "expected_socket_identity": socket["socket_identity"],
                    "value": material_value,
                    "allow_shared": material["material_users"] > 1,
                    "expected_scene_generation": transaction["scene_generation"],
                    "idempotency_key": str(uuid.uuid4()),
                },
            )
            evidence, evidence_hash = await capture_evidence(
                session,
                material["object_name"],
                artifact_directory,
                "material-input-preview",
            )
            material_rollback = await rollback_transaction(
                session,
                transaction["transaction_id"],
                int(evidence["scene_generation"]),
            )
            material_after, _ = await call_structured(
                session,
                "material.inspect",
                {
                    "object_name": material["object_name"],
                    "material_slot_index": material["material_slot_index"],
                },
            )
            restored_socket = next(
                item
                for item in material_after["sockets"]
                if item["socket_identity"] == socket["socket_identity"]
            )
            if not values_match(restored_socket["value"], material_before):
                raise RuntimeError("material rollback did not restore the socket value")
            await assert_context_unchanged(session, context_before, "material preview")
            report["material_preview"] = {
                "write": material_write,
                "evidence_sha256": evidence_hash,
                "rollback": material_rollback,
            }

            # Manual same-property conflict on a non-active object so toggling its
            # Outliner render restriction does not need to disturb selection.
            conflict_target, _ = await call_structured(
                session,
                "object.lookdev.inspect",
                {"object_name": visibility_name},
            )
            conflict_before = bool(conflict_target["visibility"]["hide_render"])
            transaction = await begin_transaction(session, "0.5 property conflict smoke")
            conflict_write, _ = await call_structured(
                session,
                "object.visibility.set",
                {
                    "transaction_id": transaction["transaction_id"],
                    "object_name": visibility_name,
                    "expected_object_identity": conflict_target["session_identity"],
                    "hide_render": not conflict_before,
                    "expected_scene_generation": transaction["scene_generation"],
                    "idempotency_key": str(uuid.uuid4()),
                },
            )
            conflict_manual = await prompt_for_hide_render(
                session,
                visibility_name,
                conflict_before,
                f"In Blender, keep context unchanged and set {visibility_name}.hide_render "
                f"to {conflict_before}; then press Enter: ",
            )
            conflict_ping, _ = await call_structured(session, "connection.ping")
            conflict_error = await call_expected_error(
                session,
                "transaction.rollback",
                {
                    "transaction_id": transaction["transaction_id"],
                    "expected_scene_generation": conflict_ping["scene_generation"],
                    "idempotency_key": str(uuid.uuid4()),
                },
                "PROPERTY_CONFLICT",
            )
            conflict_guard = await prompt_for_hide_render(
                session,
                visibility_name,
                not conflict_before,
                f"Restore {visibility_name}.hide_render to {not conflict_before} without "
                "changing selection, mode, or viewport; then press Enter: ",
            )
            restored_ping, _ = await call_structured(session, "connection.ping")
            conflict_rollback = await rollback_transaction(
                session,
                transaction["transaction_id"],
                int(restored_ping["scene_generation"]),
            )
            conflict_after, _ = await call_structured(
                session,
                "object.lookdev.inspect",
                {"object_name": visibility_name},
            )
            if bool(conflict_after["visibility"]["hide_render"]) != conflict_before:
                raise RuntimeError("conflict recovery did not restore the original value")
            await assert_context_unchanged(session, context_before, "property conflict recovery")
            report["property_conflict"] = {
                "write": conflict_write,
                "manual_value": conflict_manual["visibility"]["hide_render"],
                "error": conflict_error,
                "guard_value": conflict_guard["visibility"]["hide_render"],
                "rollback": conflict_rollback,
            }

            # Leave one new typed delta active, then close stdio to trigger safe rollback.
            disconnect_before = bool(visibility_after["visibility"]["hide_render"])
            transaction = await begin_transaction(session, "0.5 disconnect rollback smoke")
            disconnect_write, _ = await call_structured(
                session,
                "object.visibility.set",
                {
                    "transaction_id": transaction["transaction_id"],
                    "object_name": visibility_name,
                    "expected_object_identity": visibility_after["session_identity"],
                    "hide_render": not disconnect_before,
                    "expected_scene_generation": transaction["scene_generation"],
                    "idempotency_key": str(uuid.uuid4()),
                },
            )
            disconnect_expected = {
                "object_name": visibility_name,
                "before": disconnect_before,
                "write": disconnect_write,
                "context_before": context_before,
                "heartbeat_before": ping_before["heartbeat"],
            }

    await anyio.sleep(3.0)
    async with stdio_client(server_parameters) as streams:  # noqa: SIM117
        async with ClientSession(
            *streams,
            read_timeout_seconds=timedelta(seconds=60),
        ) as session:
            ping_after = await initialize_session(session, report)
            disconnect_after, _ = await call_structured(
                session,
                "object.lookdev.inspect",
                {"object_name": disconnect_expected["object_name"]},
            )
            if (
                bool(disconnect_after["visibility"]["hide_render"])
                != disconnect_expected["before"]
            ):
                raise RuntimeError("disconnect rollback did not restore the visibility delta")
            context_after = await assert_context_unchanged(
                session,
                disconnect_expected["context_before"],
                "disconnect rollback",
            )
            verification_transaction = await begin_transaction(
                session,
                "0.5 disconnect rollback clearance probe",
            )
            verification_rollback = await rollback_transaction(
                session,
                verification_transaction["transaction_id"],
                int(verification_transaction["scene_generation"]),
            )
            if int(ping_after["heartbeat"]) <= int(disconnect_expected["heartbeat_before"]):
                raise RuntimeError("Blender UI heartbeat did not advance")
            report["disconnect_rollback"] = {
                "write": disconnect_expected["write"],
                "property_after_reconnect": disconnect_after["visibility"]["hide_render"],
                "active_transaction_cleared": True,
                "clearance_probe": verification_rollback,
            }
            report["context_after"] = context_after
            report["ping_after"] = ping_after

    source_hash_after = sha256(source_blend)
    source_status_after = git_status(source_blend.parent)
    temporary_hash_after = sha256(temporary_blend)
    if source_hash_after != preparation["source_sha256"]:
        raise RuntimeError("source blend was modified during live smoke")
    if source_status_after != preparation["source_git_status"]:
        raise RuntimeError("source repository Git status changed during live smoke")
    if temporary_hash_after != temporary_hash_before:
        raise RuntimeError("temporary blend was saved or modified during live smoke")
    report.update(
        {
            "source_sha256_after": source_hash_after,
            "source_git_status_after": source_status_after,
            "temporary_sha256_after": temporary_hash_after,
            "completed_at": datetime.now(UTC).isoformat(),
            "status": "passed",
        }
    )
    report_path = artifact_directory / "report-0.5.1.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(report_path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact-directory", type=Path, required=True)
    parser.add_argument("--blend-file", type=Path, required=True)
    parser.add_argument("--source-file", type=Path, required=True)
    parser.add_argument(
        "--capture-object",
        default="Portrait_ID_V13_SubjectFX_Sclera_L",
    )
    parser.add_argument(
        "--transform-object",
        default="Portrait_ID_V13_SubjectFX_ScleraAperture_L",
    )
    parser.add_argument("--visibility-object")
    parser.add_argument("--unicode-object")
    parser.add_argument("--verify-background", action="store_true")
    parser.add_argument("--verify-ui", action="store_true")
    parser.add_argument("--accept-current-temp-baseline", action="store_true")
    anyio.run(run, parser.parse_args())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
