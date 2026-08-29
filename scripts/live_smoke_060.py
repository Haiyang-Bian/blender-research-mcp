"""Run the Blender 4.2 reversible comparative-preview acceptance."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import anyio
from live_smoke import (
    call_structured,
    context_identity,
    foreground_process_id,
    git_status,
    sha256,
)
from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client
from PIL import Image

from blender_research_mcp.client import BridgeClient
from blender_research_mcp.comparison import ComparisonRequest, run_lookdev_comparison
from blender_research_mcp.errors import BridgeError
from blender_research_mcp.observation import settle_scene_generation
from blender_research_mcp.session import load_manifest

ROOT = Path(__file__).resolve().parents[1]
REQUIRED_TOOLS = {
    "connection.ping",
    "context.get",
    "object.inspect",
    "object.lookdev.inspect",
    "material.inspect",
    "viewport.capture",
    "transaction.begin",
    "transaction.rollback",
    "shape_key.set_value",
    "material.set_input",
    "lookdev.compare",
}


def candidate_values(
    baseline: float,
    minimum: float,
    maximum: float,
    *,
    count: int = 3,
) -> list[float]:
    """Choose two or three bounded absolute values that are distinct from baseline."""
    if not all(math.isfinite(value) for value in (baseline, minimum, maximum)):
        raise RuntimeError("comparison target range is not finite")
    if minimum > baseline or baseline > maximum or maximum - minimum <= 1e-7:
        raise RuntimeError("comparison target has no usable bounded range")
    fractions = (0.2, 0.4, 0.6, 0.8)
    values = [minimum + (maximum - minimum) * fraction for fraction in fractions]
    distinct = [
        float(value)
        for value in values
        if not math.isclose(value, baseline, rel_tol=0.0, abs_tol=1e-7)
    ]
    result = distinct[:count]
    if len(result) < 2:
        raise RuntimeError("comparison target did not yield two distinct candidates")
    return result


def _target_value_from_lookdev(
    target: dict[str, Any],
    lookdev: dict[str, Any],
) -> float:
    shape_key = next(
        (
            item
            for item in lookdev.get("shape_keys", [])
            if item.get("name") == target["shape_key_name"]
        ),
        None,
    )
    if shape_key is None:
        raise RuntimeError("shape-key target disappeared during live smoke")
    return float(shape_key["value"])


async def current_target_value(
    caller: ClientSession | BridgeClient,
    target: dict[str, Any],
) -> float:
    if isinstance(caller, ClientSession):
        invoke = call_structured
    else:

        async def invoke(
            client: BridgeClient,
            name: str,
            arguments: dict[str, Any],
        ) -> tuple[dict[str, Any], None]:
            return await client.call(name, arguments, read_only=True), None

    if target["type"] == "shape_key_value":
        inspected, _ = await invoke(
            caller,
            "object.lookdev.inspect",
            {"object_name": target["object_name"]},
        )
        return _target_value_from_lookdev(target, inspected)
    inspected, _ = await invoke(
        caller,
        "material.inspect",
        {
            "object_name": target["object_name"],
            "material_slot_index": target["material_slot_index"],
        },
    )
    socket = next(
        (
            item
            for item in inspected.get("sockets", [])
            if item.get("node_name") == target["node_name"]
            and item.get("socket_identifier") == target["socket_identifier"]
        ),
        None,
    )
    if socket is None:
        raise RuntimeError("material socket target disappeared during live smoke")
    return float(socket["value"])


async def _inspect_object(
    session: ClientSession,
    object_name: str,
) -> dict[str, Any] | None:
    try:
        inspected, _ = await call_structured(
            session,
            "object.lookdev.inspect",
            {"object_name": object_name},
        )
    except RuntimeError:
        return None
    return inspected


async def discover_scalar_target(
    session: ClientSession,
    object_names: list[str],
    *,
    candidate_count: int,
) -> tuple[dict[str, Any], float, list[float], dict[str, Any]]:
    """Prefer an undriven Shape Key, then a writable FLOAT material socket."""
    inspected_objects: list[dict[str, Any]] = []
    for object_name in dict.fromkeys(object_names):
        inspected = await _inspect_object(session, object_name)
        if inspected is not None:
            inspected_objects.append(inspected)
            for shape_key in inspected.get("shape_keys", []):
                if shape_key.get("driven"):
                    continue
                baseline = float(shape_key["value"])
                minimum = float(shape_key["slider_min"])
                maximum = float(shape_key["slider_max"])
                try:
                    values = candidate_values(
                        baseline,
                        minimum,
                        maximum,
                        count=candidate_count,
                    )
                except RuntimeError:
                    continue
                target = {
                    "type": "shape_key_value",
                    "object_name": inspected["name"],
                    "expected_object_identity": inspected["session_identity"],
                    "shape_key_name": shape_key["name"],
                    "expected_shape_key_identity": shape_key["session_identity"],
                }
                return target, baseline, values, {
                    "inspection": inspected,
                    "selected_shape_key": shape_key,
                }

    material_candidates: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for inspected in inspected_objects:
        for slot in inspected.get("material_slots", []):
            if slot.get("name") is None:
                continue
            material, _ = await call_structured(
                session,
                "material.inspect",
                {
                    "object_name": inspected["name"],
                    "material_slot_index": slot["index"],
                },
            )
            for socket in material.get("sockets", []):
                if socket.get("writable") and socket.get("socket_kind") == "FLOAT":
                    material_candidates.append((material, socket))

    material_candidates.sort(key=lambda item: int(item[0]["material_users"]))
    for material, socket in material_candidates:
        baseline = float(socket["value"])
        minimum = socket.get("minimum")
        maximum = socket.get("maximum")
        if minimum is None or maximum is None:
            continue
        try:
            values = candidate_values(
                baseline,
                float(minimum),
                float(maximum),
                count=candidate_count,
            )
        except RuntimeError:
            continue
        allow_shared = False
        if int(material["material_users"]) > 1:
            answer = await anyio.to_thread.run_sync(
                input,
                "The discovered material is shared by "
                f"{material['material_users']} objects: {material['affected_objects']}. "
                "Type ALLOW SHARED to compare this exact socket: ",
            )
            if answer.strip().upper() != "ALLOW SHARED":
                continue
            allow_shared = True
        target = {
            "type": "material_input",
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
            "allow_shared": allow_shared,
        }
        return target, baseline, values, {
            "inspection": material,
            "selected_socket": socket,
        }
    raise RuntimeError(
        "no bounded undriven Shape Key or writable FLOAT material input was discovered"
    )


def comparison_arguments(
    target: dict[str, Any],
    values: list[float],
    capture_object: str,
    *,
    label_prefix: str = "candidate",
) -> dict[str, Any]:
    return {
        "target": target,
        "candidates": [
            {"label": f"{label_prefix}-{chr(ord('A') + index)}", "value": value}
            for index, value in enumerate(values)
        ],
        "capture": {
            "object_name": capture_object,
            "view": "FRONT",
            "max_size": 800,
            "display_mode": "MATERIAL",
            "overlays": "OFF",
        },
    }


def save_comparison_images(
    result: types.CallToolResult,
    metadata: dict[str, Any],
    artifact_directory: Path,
) -> list[dict[str, Any]]:
    images = [block for block in result.content if isinstance(block, types.ImageContent)]
    items = metadata.get("items")
    if not isinstance(items, list) or len(images) != len(items):
        raise RuntimeError("lookdev.compare image count does not match ordered items")
    saved: list[dict[str, Any]] = []
    for expected_index, (block, item) in enumerate(zip(images, items, strict=True)):
        if int(item["content_index"]) != expected_index:
            raise RuntimeError("lookdev.compare returned an invalid content_index order")
        raw = base64.b64decode(block.data, validate=True)
        label = str(item["label"])
        path = artifact_directory / f"comparison-{expected_index}-{label}.png"
        path.write_bytes(raw)
        with Image.open(path) as opened:
            extrema = opened.convert("L").getextrema()
        if extrema is None or extrema[0] == extrema[1]:
            raise RuntimeError(f"lookdev.compare returned a blank image for {label}")
        digest = hashlib.sha256(raw).hexdigest()
        if digest != item["capture"]["sha256"]:
            raise RuntimeError(f"lookdev.compare SHA-256 mismatch for {label}")
        for required in (
            "writer",
            "rollback",
            "difference",
            "elapsed_ms",
            "scene_generation_before",
            "scene_generation_after",
        ):
            if required not in item:
                raise RuntimeError(f"lookdev.compare item {label} lacks {required}")
        saved.append({"label": label, "path": str(path), "sha256": digest})
    return saved


async def initialize_session(
    session: ClientSession,
    report: dict[str, Any],
) -> dict[str, Any]:
    initialized = await session.initialize()
    listed = await session.list_tools()
    tools = {tool.name: tool for tool in listed.tools}
    if not REQUIRED_TOOLS.issubset(tools):
        raise RuntimeError(f"MCP tool list is incomplete: {sorted(tools)}")
    annotation = tools["lookdev.compare"].annotations
    if annotation is None or annotation.model_dump(exclude_none=True) != {
        "title": "Reversible preview mutation",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    }:
        raise RuntimeError("lookdev.compare annotations do not match the preview contract")
    ping, _ = await call_structured(session, "connection.ping")
    report["mcp"] = {
        "protocol_version": initialized.protocolVersion,
        "server": initialized.serverInfo.model_dump(mode="json"),
        "tools": sorted(tools),
        "lookdev_compare_annotations": annotation.model_dump(exclude_none=True),
    }
    return ping


async def verify_ui_checkpoint(
    client: BridgeClient,
    request: ComparisonRequest,
    report: dict[str, Any],
) -> None:
    confirmed = False

    async def phase_hook(
        phase: str,
        label: str | None,
        details: dict[str, Any],
    ) -> None:
        nonlocal confirmed
        del details
        if phase != "after_write" or confirmed:
            return
        answer = await anyio.to_thread.run_sync(
            input,
            "In Blender Scene Properties, confirm the active transaction label is "
            f"compare:{label}. The following capture/rollback commands should appear on "
            "the existing Running row. Type YES: ",
        )
        if answer.strip().upper() != "YES":
            raise RuntimeError("0.6 comparative-preview UI checkpoint was not confirmed")
        confirmed = True

    images, result = await run_lookdev_comparison(
        client,
        request,
        _phase_hook=phase_hook,
    )
    if not confirmed or len(images) != 2 or not result["target_restored"]:
        raise RuntimeError("UI checkpoint comparison did not restore cleanly")
    report["ui_checkpoint"] = {
        "confirmed": True,
        "transaction_label": f"compare:{request.candidates[0].label}",
        "target_restored": True,
    }


async def verify_property_conflict(
    client: BridgeClient,
    request: ComparisonRequest,
    baseline: float,
    conflict_value: float,
    report: dict[str, Any],
    *,
    verify_ui: bool,
) -> None:
    transaction_id: str | None = None
    first_value = float(request.candidates[0].value)
    ui_confirmed = False

    async def phase_hook(
        phase: str,
        label: str | None,
        details: dict[str, Any],
    ) -> None:
        nonlocal transaction_id, ui_confirmed
        if phase != "after_write" or label != request.candidates[0].label:
            return
        transaction_id = str(details["writer"]["transaction_id"])
        prefix = ""
        if verify_ui:
            prefix = (
                f"First confirm the Scene Properties label is compare:{label}. "
                "Then "
            )
        await anyio.to_thread.run_sync(
            input,
            prefix
            + "edit the same visible Blender property to the exact value "
            f"{conflict_value:.9g}, then press Enter: ",
        )
        actual = await current_target_value(client, request.target.model_dump(mode="json"))
        if not math.isclose(actual, conflict_value, rel_tol=0.0, abs_tol=1e-7):
            raise RuntimeError(
                f"manual conflict value is {actual!r}, expected {conflict_value!r}"
            )
        ui_confirmed = verify_ui

    try:
        await run_lookdev_comparison(client, request, _phase_hook=phase_hook)
    except BridgeError as exc:
        if exc.error.code != "PROPERTY_CONFLICT":
            raise
        error = exc.error.model_dump(mode="json")
    else:
        raise RuntimeError("manual same-property conflict unexpectedly succeeded")

    if transaction_id is None:
        raise RuntimeError("conflict hook did not record the active transaction")
    preserved = await current_target_value(client, request.target.model_dump(mode="json"))
    if not math.isclose(preserved, conflict_value, rel_tol=0.0, abs_tol=1e-7):
        raise RuntimeError("comparison overwrote the manual conflict value")
    await anyio.to_thread.run_sync(
        input,
        "Conflict was preserved. Set the same Blender property back to the active "
        f"transaction value {first_value:.9g}, then press Enter so rollback can clear it: ",
    )
    guard = await current_target_value(client, request.target.model_dump(mode="json"))
    if not math.isclose(guard, first_value, rel_tol=0.0, abs_tol=1e-7):
        raise RuntimeError("conflict recovery guard value was not restored")
    ping = await settle_scene_generation(client)
    rollback = await client.call(
        "transaction.rollback",
        {"transaction_id": transaction_id},
        expected_scene_generation=int(ping["scene_generation"]),
        idempotency_key=str(uuid.uuid4()),
        read_only=False,
    )
    restored = await current_target_value(client, request.target.model_dump(mode="json"))
    if not math.isclose(restored, baseline, rel_tol=0.0, abs_tol=1e-7):
        raise RuntimeError("conflict recovery rollback did not restore the baseline")
    report["property_conflict"] = {
        "error": error,
        "manual_value_preserved": preserved,
        "stopped_before_candidate": request.candidates[1].label,
        "recovery_rollback": rollback,
        "restored_value": restored,
        "ui_confirmed": ui_confirmed,
    }


async def verify_disconnect_rollback(
    request: ComparisonRequest,
    baseline: float,
    baseline_context: dict[str, Any],
    report: dict[str, Any],
) -> BridgeClient:
    client = BridgeClient()
    disconnected = False

    async def phase_hook(
        phase: str,
        label: str | None,
        details: dict[str, Any],
    ) -> None:
        nonlocal disconnected
        del label, details
        if phase == "after_write" and not disconnected:
            disconnected = True
            await client.close()
            await anyio.sleep(3.0)

    try:
        await run_lookdev_comparison(client, request, _phase_hook=phase_hook)
    except BridgeError as exc:
        failure = exc.error.model_dump(mode="json")
    else:
        raise RuntimeError("disconnect comparison unexpectedly completed without interruption")
    finally:
        await client.close()

    await anyio.sleep(0.5)
    verification = BridgeClient()
    restored = await current_target_value(
        verification,
        request.target.model_dump(mode="json"),
    )
    context = await verification.call("context.get", read_only=True)
    if not math.isclose(restored, baseline, rel_tol=0.0, abs_tol=1e-7):
        raise RuntimeError("disconnect rollback did not restore the target baseline")
    if context_identity(context) != context_identity(baseline_context):
        raise RuntimeError("disconnect rollback changed the user context")
    ping = await settle_scene_generation(verification)
    transaction = await verification.call(
        "transaction.begin",
        {"label": "0.6 disconnect rollback clearance probe"},
        expected_scene_generation=int(ping["scene_generation"]),
        idempotency_key=str(uuid.uuid4()),
        read_only=False,
    )
    clearance = await verification.call(
        "transaction.rollback",
        {"transaction_id": transaction["transaction_id"]},
        expected_scene_generation=int(transaction["scene_generation"]),
        idempotency_key=str(uuid.uuid4()),
        read_only=False,
    )
    report["disconnect_rollback"] = {
        "comparison_failure": failure,
        "restored_value": restored,
        "context_restored": True,
        "active_transaction_cleared": True,
        "clearance_probe": clearance,
    }
    return verification


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

    report: dict[str, Any] = {
        "run_id": preparation["run_id"],
        "started_at": datetime.now(UTC).isoformat(),
        "source_file": str(source_blend),
        "source_sha256_before": preparation["source_sha256"],
        "source_git_status_before": preparation["source_git_status"],
        "temporary_blend_file": str(temporary_blend),
        "temporary_sha256_before": temporary_hash_before,
    }
    server_parameters = StdioServerParameters(
        command="uv",
        args=["run", "--no-sync", "blender-research-mcp"],
        cwd=ROOT,
    )
    target: dict[str, Any]
    baseline: float
    values: list[float]
    capture_object: str
    baseline_context: dict[str, Any]
    async with stdio_client(server_parameters) as streams:  # noqa: SIM117
        async with ClientSession(
            *streams,
            read_timeout_seconds=timedelta(seconds=180),
        ) as session:
            ping_before = await initialize_session(session, report)
            report["ping_before"] = ping_before
            if ping_before["addon_version"] not in {"0.5.1", "0.6.0", "0.7.0", "0.8.0"}:
                raise RuntimeError(
                    f"unexpected compatible add-on version: {ping_before['addon_version']}"
                )
            if args.verify_ui and ping_before["addon_version"] not in {
                "0.6.0",
                "0.7.0",
                "0.8.0",
            }:
                raise RuntimeError("UI verification requires the 0.6.0+ add-on")
            baseline_context, _ = await call_structured(session, "context.get")
            if Path(baseline_context["blend_file"]).resolve() != temporary_blend:
                raise RuntimeError("Blender is not displaying the prepared temporary blend")
            object_names = [
                *args.target_object,
                baseline_context.get("active_object"),
                *baseline_context.get("selected_objects", []),
            ]
            object_names = [name for name in object_names if isinstance(name, str)]
            target, baseline, values, discovery = await discover_scalar_target(
                session,
                object_names,
                candidate_count=args.candidate_count,
            )
            capture_object = args.capture_object or target["object_name"]
            if await _inspect_object(session, capture_object) is None:
                raise RuntimeError(f"capture evidence object does not exist: {capture_object}")
            arguments = comparison_arguments(target, values, capture_object)
            report["discovered_target"] = discovery
            report["comparison_request"] = arguments
            if args.verify_non_focus_capture:
                await anyio.to_thread.run_sync(
                    input,
                    "Briefly cover Blender with another window without changing Blender state, "
                    "then press Enter: ",
                )
                if foreground_process_id() == load_manifest().pid:
                    raise RuntimeError("Blender still owns foreground focus")
                report["non_focus_capture_regression"] = True
            result = await session.call_tool(
                "lookdev.compare",
                arguments,
                read_timeout_seconds=timedelta(seconds=180),
            )
            if result.isError or not isinstance(result.structuredContent, dict):
                raise RuntimeError(f"lookdev.compare failed: {result}")
            comparison = result.structuredContent
            expected_labels = ["baseline", *[item["label"] for item in arguments["candidates"]]]
            if [item["label"] for item in comparison["items"]] != expected_labels:
                raise RuntimeError("lookdev.compare did not preserve baseline/candidate order")
            if not all(
                (
                    comparison["target_restored"],
                    comparison["context_unchanged"],
                    comparison["object_unchanged"],
                )
            ):
                raise RuntimeError("lookdev.compare did not prove complete restoration")
            report["comparison_result"] = comparison
            report["comparison_images"] = save_comparison_images(
                result,
                comparison,
                artifact_directory,
            )
            restored = await current_target_value(session, target)
            if not math.isclose(restored, baseline, rel_tol=0.0, abs_tol=1e-7):
                raise RuntimeError("public lookdev.compare did not restore the target")

    await anyio.sleep(0.5)
    direct = BridgeClient()
    try:
        if args.verify_conflict:
            conflict_values = values[:2]
            conflict_value = next(
                value
                for value in candidate_values(
                    baseline,
                    float(discovery["selected_shape_key"].get("slider_min", 0.0))
                    if "selected_shape_key" in discovery
                    else float(discovery["selected_socket"]["minimum"]),
                    float(discovery["selected_shape_key"].get("slider_max", 1.0))
                    if "selected_shape_key" in discovery
                    else float(discovery["selected_socket"]["maximum"]),
                    count=3,
                )
                if not any(
                    math.isclose(value, item, rel_tol=0.0, abs_tol=1e-7)
                    for item in (baseline, *conflict_values)
                )
            )
            conflict_request = ComparisonRequest.model_validate(
                comparison_arguments(
                    target,
                    conflict_values,
                    capture_object,
                    label_prefix="conflict",
                )
            )
            await verify_property_conflict(
                direct,
                conflict_request,
                baseline,
                conflict_value,
                report,
                verify_ui=args.verify_ui,
            )
        elif args.verify_ui:
            ui_request = ComparisonRequest.model_validate(
                comparison_arguments(
                    target,
                    values[:1],
                    capture_object,
                    label_prefix="UI-preview",
                )
            )
            await verify_ui_checkpoint(direct, ui_request, report)
    finally:
        await direct.close()

    final_client: BridgeClient | None = None
    if args.verify_disconnect:
        disconnect_request = ComparisonRequest.model_validate(
            comparison_arguments(
                target,
                values[:1],
                capture_object,
                label_prefix="disconnect",
            )
        )
        final_client = await verify_disconnect_rollback(
            disconnect_request,
            baseline,
            baseline_context,
            report,
        )
    else:
        final_client = BridgeClient()
    try:
        ping_after = await settle_scene_generation(final_client)
        context_after = await final_client.call("context.get", read_only=True)
        target_after = await current_target_value(final_client, target)
    finally:
        await final_client.close()
    if context_identity(context_after) != context_identity(baseline_context):
        raise RuntimeError("final user context differs from the baseline")
    if not math.isclose(target_after, baseline, rel_tol=0.0, abs_tol=1e-7):
        raise RuntimeError("final target value differs from the baseline")
    if int(ping_after["heartbeat"]) <= int(report["ping_before"]["heartbeat"]):
        raise RuntimeError("Blender UI heartbeat did not advance")

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
            "ping_after": ping_after,
            "context_after": context_after,
            "target_value_after": target_after,
            "source_sha256_after": source_hash_after,
            "source_git_status_after": source_status_after,
            "temporary_sha256_after": temporary_hash_after,
            "completed_at": datetime.now(UTC).isoformat(),
            "status": "passed",
        }
    )
    report_path = artifact_directory / "report-0.6.0.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(report_path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact-directory", type=Path, required=True)
    parser.add_argument("--blend-file", type=Path, required=True)
    parser.add_argument("--source-file", type=Path, required=True)
    parser.add_argument("--target-object", action="append", default=[])
    parser.add_argument("--capture-object")
    parser.add_argument("--candidate-count", type=int, choices=(2, 3), default=3)
    parser.add_argument("--verify-ui", action="store_true")
    parser.add_argument("--verify-conflict", action="store_true")
    parser.add_argument("--verify-disconnect", action="store_true")
    parser.add_argument("--verify-non-focus-capture", action="store_true")
    parser.add_argument("--accept-current-temp-baseline", action="store_true")
    anyio.run(run, parser.parse_args())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
