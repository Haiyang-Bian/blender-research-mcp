"""Run the live Blender 4.2 autonomous-observation acceptance through MCP stdio."""

from __future__ import annotations

import argparse
import base64
import ctypes
import hashlib
import json
import math
import subprocess
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import anyio
from mcp import ClientSession, StdioServerParameters, types
from mcp.client.stdio import stdio_client
from PIL import Image, ImageChops, ImageFilter, ImageStat

from blender_research_mcp.session import load_manifest

ROOT = Path(__file__).resolve().parents[1]
CONTEXT_KEYS = (
    "scene",
    "view_layer",
    "workspace",
    "window_id",
    "area_id",
    "region_id",
    "viewport_id",
    "mode",
    "active_object",
    "selected_objects",
    "frame_current",
    "active_camera",
    "view",
    "blend_file",
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def git_status(repository: Path) -> str:
    result = subprocess.run(
        ["git", "status", "--short", "--branch"],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.stdout.strip()


def context_identity(context: dict[str, Any]) -> dict[str, Any]:
    return {key: context.get(key) for key in CONTEXT_KEYS}


def object_identity(obj: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in obj.items() if key != "scene_generation"}


def result_text(result: types.CallToolResult) -> str:
    return "\n".join(
        block.text for block in result.content if isinstance(block, types.TextContent)
    )


async def call_structured(
    session: ClientSession,
    name: str,
    arguments: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], types.CallToolResult]:
    result = await session.call_tool(
        name,
        arguments or {},
        read_timeout_seconds=timedelta(seconds=40),
    )
    if result.isError:
        raise RuntimeError(f"{name} failed: {result_text(result)}")
    if not isinstance(result.structuredContent, dict):
        raise RuntimeError(f"{name} did not return structured content")
    return result.structuredContent, result


def save_image(result: types.CallToolResult, path: Path) -> str:
    image = next(
        (block for block in result.content if isinstance(block, types.ImageContent)),
        None,
    )
    if image is None:
        raise RuntimeError(f"viewport.capture did not return an image for {path.name}")
    path.write_bytes(base64.b64decode(image.data, validate=True))
    with Image.open(path) as opened:
        extrema = opened.convert("L").getextrema()
    if extrema is None or extrema[0] == extrema[1]:
        raise RuntimeError(f"viewport.capture returned a blank image for {path.name}")
    return sha256(path)


def save_bundle_images(
    result: types.CallToolResult,
    metadata: dict[str, Any],
    artifact_directory: Path,
    prefix: str,
) -> dict[str, str]:
    images = [block for block in result.content if isinstance(block, types.ImageContent)]
    captures = metadata.get("captures", [])
    if len(images) != len(captures):
        raise RuntimeError("observation.bundle image count does not match its metadata")
    hashes: dict[str, str] = {}
    for capture in captures:
        index = int(capture["content_index"])
        view = str(capture["view"])
        path = artifact_directory / f"{prefix}-{view.lower()}.png"
        single = types.CallToolResult(content=[images[index]])
        hashes[view] = save_image(single, path)
    return hashes


def maximum_pixel_difference(first: Path, second: Path) -> int:
    with Image.open(first) as first_image, Image.open(second) as second_image:
        left = first_image.convert("RGBA")
        right = second_image.convert("RGBA")
        if left.size != right.size:
            raise RuntimeError("foreground and background captures have different dimensions")
        extrema = ImageChops.difference(left, right).getextrema()
    return max(channel[1] for channel in extrema)


def image_difference_statistics(first: Path, second: Path) -> dict[str, float | int]:
    """Measure aligned images while suppressing stochastic rendered-view noise."""
    with Image.open(first) as first_image, Image.open(second) as second_image:
        first_rgb = first_image.convert("RGB")
        second_rgb = second_image.convert("RGB")
        if first_rgb.size != second_rgb.size:
            raise RuntimeError("foreground and obscured captures have different dimensions")
        difference = ImageChops.difference(first_rgb, second_rgb)
        statistics = ImageStat.Stat(difference)
        extrema = difference.getextrema()

        first_structure = first_rgb.convert("L").filter(ImageFilter.GaussianBlur(2.0))
        second_structure = second_rgb.convert("L").filter(ImageFilter.GaussianBlur(2.0))
        first_structure.thumbnail((256, 256), Image.Resampling.LANCZOS)
        second_structure.thumbnail((256, 256), Image.Resampling.LANCZOS)
        structure_difference = ImageChops.difference(first_structure, second_structure)
        structure_mean = ImageStat.Stat(structure_difference).mean[0]

    return {
        "max_channel_difference": max(channel[1] for channel in extrema),
        "mean_absolute_difference": sum(statistics.mean) / 3.0,
        "rms_difference": math.sqrt(sum(value * value for value in statistics.rms) / 3.0),
        "structure_mean_absolute_difference": structure_mean,
    }


def images_match_within_render_noise(statistics: dict[str, float | int]) -> bool:
    return (
        float(statistics["mean_absolute_difference"]) <= 1.0
        and float(statistics["structure_mean_absolute_difference"]) <= 0.5
    )


def foreground_process_id() -> int | None:
    if not hasattr(ctypes, "WinDLL"):
        return None
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    get_foreground_window = user32.GetForegroundWindow
    get_foreground_window.restype = ctypes.c_void_p
    get_window_pid = user32.GetWindowThreadProcessId
    get_window_pid.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
    get_window_pid.restype = ctypes.c_ulong
    window = get_foreground_window()
    if not window:
        return None
    process_id = ctypes.c_ulong()
    get_window_pid(window, ctypes.byref(process_id))
    return int(process_id.value) or None


def find_non_ascii_object(context: dict[str, Any]) -> str:
    candidates = [context.get("active_object"), *context.get("selected_objects", [])]
    for candidate in candidates:
        if isinstance(candidate, str) and any(ord(character) > 127 for character in candidate):
            return candidate
    raise RuntimeError(
        "Select an existing object with a Chinese or Japanese name before running the smoke test"
    )


async def run(args: argparse.Namespace) -> None:
    artifact_directory = args.artifact_directory.resolve(strict=True)
    temporary_blend = args.blend_file.resolve(strict=True)
    source_blend = args.source_file.resolve(strict=True)
    preparation = json.loads(
        (artifact_directory / "preparation.json").read_text(encoding="utf-8")
    )
    prepared_temporary_blend = Path(preparation["temporary_blend_file"]).resolve()
    if sha256(source_blend) != preparation["source_sha256"]:
        raise RuntimeError("source blend changed after smoke preparation")
    temporary_hash_before = sha256(temporary_blend)
    temporary_changed_during_setup = (
        temporary_hash_before != preparation["temporary_sha256"]
    )
    if temporary_changed_during_setup and not args.accept_current_temp_baseline:
        raise RuntimeError("temporary blend changed before smoke execution")

    server_parameters = StdioServerParameters(
        command="uv",
        args=["run", "--no-sync", "blender-research-mcp"],
        cwd=ROOT,
    )
    report: dict[str, Any] = {
        "run_id": preparation["run_id"],
        "started_at": datetime.now(UTC).isoformat(),
        "temporary_blend_file": str(temporary_blend),
        "prepared_temporary_blend_file": str(prepared_temporary_blend),
        "temporary_path_reused": temporary_blend != prepared_temporary_blend,
        "temporary_sha256_prepared": preparation["temporary_sha256"],
        "temporary_sha256_before": temporary_hash_before,
        "temporary_changed_during_setup": temporary_changed_during_setup,
        "source_file": str(source_blend),
    }
    async with stdio_client(server_parameters) as (read_stream, write_stream):  # noqa: SIM117
        async with ClientSession(
            read_stream,
            write_stream,
        read_timeout_seconds=timedelta(seconds=60),
        ) as session:
            initialized = await session.initialize()
            listed = await session.list_tools()
            tool_names = [tool.name for tool in listed.tools]
            required_tools = {
                "connection.ping",
                "context.get",
                "context.snapshot",
                "context.restore",
                "object.inspect",
                "viewport.capture",
                "observation.bundle",
                "transaction.begin",
                "object.transform",
                "transaction.commit",
                "transaction.rollback",
            }
            if not required_tools.issubset(tool_names):
                raise RuntimeError(f"MCP tool list is incomplete: {tool_names}")
            report["mcp"] = {
                "protocol_version": initialized.protocolVersion,
                "server": initialized.serverInfo.model_dump(mode="json"),
                "tools": tool_names,
            }

            ping_before, _ = await call_structured(session, "connection.ping")
            if int(ping_before["capability_versions"]["viewport_capture"]) < 2:
                raise RuntimeError("Blender add-on does not advertise off-screen capture v2")
            context_before, _ = await call_structured(session, "context.get")
            if Path(context_before["blend_file"]).resolve() != temporary_blend:
                raise RuntimeError(
                    "Blender is not displaying the prepared temporary blend file: "
                    f"{context_before['blend_file']}"
                )
            non_ascii_name = args.unicode_object or find_non_ascii_object(context_before)
            unicode_object, _ = await call_structured(
                session,
                "object.inspect",
                {"object_name": non_ascii_name},
            )
            capture_object_before, _ = await call_structured(
                session,
                "object.inspect",
                {"object_name": args.capture_object},
            )
            report["ping_before"] = ping_before
            report["context_before"] = context_before
            report["unicode_object"] = unicode_object
            report["capture_object_before"] = capture_object_before
            heartbeat_baseline = ping_before

            if args.restart_evidence is not None:
                prior_report = json.loads(
                    args.restart_evidence.resolve(strict=True).read_text(encoding="utf-8")
                )
                prior_before = prior_report["ping_before"]
                prior_after = prior_report["ping_after_restart"]
                if (
                    prior_before["instance_id"] == prior_after["instance_id"]
                    or prior_after["instance_id"] != ping_before["instance_id"]
                ):
                    raise RuntimeError(
                        "reused restart evidence does not match this Blender session"
                    )
                report["restart"] = {
                    "mode": "reused_from_interrupted_attempt",
                    "source": str(args.restart_evidence),
                    "before_instance_id": prior_before["instance_id"],
                    "after_instance_id": prior_after["instance_id"],
                }
                report["ping_after_restart"] = prior_after
            elif not args.skip_restart:
                await anyio.to_thread.run_sync(
                    input,
                    "Click 'Restart Bridge' in Blender, wait for Listening, then press Enter... ",
                )
                ping_after_restart, _ = await call_structured(session, "connection.ping")
                if ping_after_restart["instance_id"] == ping_before["instance_id"]:
                    raise RuntimeError("Restart Bridge did not rotate the add-on instance")
                report["restart"] = {
                    "mode": "interactive",
                    "before_instance_id": ping_before["instance_id"],
                    "after_instance_id": ping_after_restart["instance_id"],
                }
                report["ping_after_restart"] = ping_after_restart
                heartbeat_baseline = ping_after_restart
            else:
                report["restart"] = {"mode": "skipped"}

            foreground_reference_hash: str | None = None
            if args.verify_background:
                await anyio.to_thread.run_sync(
                    input,
                    "Bring Blender to the foreground, then press Enter... ",
                )
                await anyio.sleep(0.5)
                blender_pid = load_manifest().pid
                foreground_pid = foreground_process_id()
                if foreground_pid != blender_pid:
                    raise RuntimeError(
                        "Blender is not foreground: "
                        f"expected PID {blender_pid}, got {foreground_pid}"
                    )
                reference_metadata, reference_capture = await call_structured(
                    session,
                    "viewport.capture",
                    {"object_name": args.capture_object, "view": "FRONT", "max_size": 1000},
                )
                foreground_reference_hash = save_image(
                    reference_capture,
                    artifact_directory / "foreground-front.png",
                )
                report["foreground_capture"] = reference_metadata
                report["foreground_capture_sha256"] = foreground_reference_hash
                report["foreground_pid"] = foreground_pid
                await anyio.to_thread.run_sync(
                    input,
                    "Cover Blender with Codex or another window, then press Enter... ",
                )
                await anyio.sleep(0.5)
                background_pid = foreground_process_id()
                if background_pid == blender_pid:
                    raise RuntimeError("Blender still owns foreground focus")
                report["background_foreground_pid"] = background_pid

            bundle_before, bundle_before_result = await call_structured(
                session,
                "observation.bundle",
                {
                    "object_name": args.capture_object,
                    "views": ["FRONT", "RIGHT", "TOP"],
                    "max_size": 1000,
                },
            )
            before_image_hashes = save_bundle_images(
                bundle_before_result,
                bundle_before,
                artifact_directory,
                "before",
            )
            if len(set(before_image_hashes.values())) != len(before_image_hashes):
                raise RuntimeError("orthographic captures unexpectedly returned duplicate images")
            if args.verify_background:
                difference_statistics = image_difference_statistics(
                    artifact_directory / "foreground-front.png",
                    artifact_directory / "before-front.png",
                )
                if not images_match_within_render_noise(difference_statistics):
                    raise RuntimeError(
                        "foreground and obscured off-screen captures differ structurally: "
                        f"{difference_statistics}"
                    )
                report["foreground_background_difference"] = difference_statistics
            report["observation_before"] = bundle_before
            report["capture_before_sha256"] = before_image_hashes

            helper_before, _ = await call_structured(
                session,
                "object.inspect",
                {"object_name": args.transform_object},
            )
            generation = int(bundle_before["scene_generation"])
            transaction, _ = await call_structured(
                session,
                "transaction.begin",
                {
                    "expected_scene_generation": generation,
                    "idempotency_key": str(uuid.uuid4()),
                    "label": "V13 aperture width rollback smoke",
                },
            )
            original_z = float(helper_before["scale"][2])
            changed_z = round(original_z * 1.08, 6)
            transformed, _ = await call_structured(
                session,
                "object.transform",
                {
                    "transaction_id": transaction["transaction_id"],
                    "object_name": args.transform_object,
                    "scale": {"z": changed_z},
                    "expected_scene_generation": transaction["scene_generation"],
                    "idempotency_key": str(uuid.uuid4()),
                },
            )
            after_metadata, after_capture = await call_structured(
                session,
                "observation.bundle",
                {
                    "object_name": args.capture_object,
                    "views": ["FRONT"],
                    "max_size": 1000,
                },
            )
            report["capture_after_transform_sha256"] = save_bundle_images(
                after_capture,
                after_metadata,
                artifact_directory,
                "after-transform",
            )["FRONT"]
            transform_image_difference = image_difference_statistics(
                artifact_directory / "before-front.png",
                artifact_directory / "after-transform-front.png",
            )
            if images_match_within_render_noise(transform_image_difference):
                raise RuntimeError("scale preview did not produce visible image evidence")
            report["transform_image_difference"] = transform_image_difference
            rolled_back, _ = await call_structured(
                session,
                "transaction.rollback",
                {
                    "transaction_id": transaction["transaction_id"],
                    "expected_scene_generation": after_metadata["scene_generation"],
                    "idempotency_key": str(uuid.uuid4()),
                },
            )
            helper_rolled_back, _ = await call_structured(
                session,
                "object.inspect",
                {"object_name": args.transform_object},
            )
            if not math.isclose(
                float(helper_rolled_back["scale"][2]),
                original_z,
                rel_tol=0.0,
                abs_tol=1e-7,
            ):
                raise RuntimeError("rollback did not restore the aperture helper scale")
            rollback_metadata, rollback_capture = await call_structured(
                session,
                "observation.bundle",
                {
                    "object_name": args.capture_object,
                    "views": ["FRONT"],
                    "max_size": 1000,
                },
            )
            report["capture_after_rollback_sha256"] = save_bundle_images(
                rollback_capture,
                rollback_metadata,
                artifact_directory,
                "after-rollback",
            )["FRONT"]
            rollback_image_difference = image_difference_statistics(
                artifact_directory / "before-front.png",
                artifact_directory / "after-rollback-front.png",
            )
            if not images_match_within_render_noise(rollback_image_difference):
                raise RuntimeError(
                    "rollback image did not return to the rendered-view noise envelope: "
                    f"{rollback_image_difference}"
                )
            report["rollback_image_difference"] = rollback_image_difference
            context_after, _ = await call_structured(session, "context.get")
            if context_identity(context_after) != context_identity(context_before):
                raise RuntimeError("user context was not restored exactly")
            unicode_object_after, _ = await call_structured(
                session,
                "object.inspect",
                {"object_name": non_ascii_name},
            )
            capture_object_after, _ = await call_structured(
                session,
                "object.inspect",
                {"object_name": args.capture_object},
            )
            if object_identity(unicode_object_after) != object_identity(unicode_object):
                raise RuntimeError("active Unicode object state was not restored exactly")
            if object_identity(capture_object_after) != object_identity(capture_object_before):
                raise RuntimeError("capture target state was not restored exactly")
            if object_identity(helper_rolled_back) != object_identity(helper_before):
                raise RuntimeError("transformed helper state was not restored exactly")
            ping_after, _ = await call_structured(session, "connection.ping")
            if int(ping_after["heartbeat"]) <= int(heartbeat_baseline["heartbeat"]):
                raise RuntimeError("Blender UI heartbeat did not advance")
            if args.verify_background:
                background_pid_after = foreground_process_id()
                if background_pid_after == blender_pid:
                    raise RuntimeError("Blender regained foreground focus during background smoke")
                report["background_foreground_pid_after"] = background_pid_after
            report.update(
                {
                    "helper_before": helper_before,
                    "transaction": transaction,
                    "transform": transformed,
                    "capture_after_transform": after_metadata,
                    "rollback": rolled_back,
                    "helper_after_rollback": helper_rolled_back,
                    "capture_after_rollback": rollback_metadata,
                    "context_after": context_after,
                    "unicode_object_after": unicode_object_after,
                    "capture_object_after": capture_object_after,
                    "ping_after": ping_after,
                }
            )

    source_hash_after = sha256(source_blend)
    source_status_after = git_status(source_blend.parent)
    if source_hash_after != preparation["source_sha256"]:
        raise RuntimeError("source blend was modified during live smoke")
    if source_status_after != preparation["source_git_status"]:
        raise RuntimeError("source repository Git status changed during live smoke")
    temporary_hash_after = sha256(temporary_blend)
    if temporary_hash_after != temporary_hash_before:
        raise RuntimeError("temporary blend file was saved or modified during live smoke")
    report["source_sha256_after"] = source_hash_after
    report["source_git_status_after"] = source_status_after
    report["temporary_sha256_after"] = temporary_hash_after
    report["completed_at"] = datetime.now(UTC).isoformat()
    report["status"] = "passed"
    (artifact_directory / "report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(artifact_directory / "report.json")


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
    parser.add_argument(
        "--unicode-object",
        help="inspect this exact non-ASCII object name instead of requiring selection",
    )
    parser.add_argument("--skip-restart", action="store_true")
    parser.add_argument(
        "--verify-background",
        action="store_true",
        help="require explicit foreground and obscured-window capture checkpoints",
    )
    parser.add_argument(
        "--restart-evidence",
        type=Path,
        help="reuse a prior report whose post-restart instance is still active",
    )
    parser.add_argument(
        "--accept-current-temp-baseline",
        action="store_true",
        help=(
            "record the current temporary blend hash as the live-run baseline when "
            "setup changed the prepared copy"
        ),
    )
    args = parser.parse_args()
    if args.skip_restart and args.restart_evidence is not None:
        parser.error("--skip-restart and --restart-evidence are mutually exclusive")
    anyio.run(run, args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
