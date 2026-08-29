"""Run the live Blender 4.2 spatial-diagnosis acceptance through MCP stdio."""

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


async def call_expected_error(
    session: ClientSession,
    name: str,
    arguments: dict[str, Any],
    expected_code: str,
) -> str:
    result = await session.call_tool(
        name,
        arguments,
        read_timeout_seconds=timedelta(seconds=40),
    )
    text = result_text(result)
    if not result.isError:
        raise RuntimeError(f"{name} unexpectedly succeeded; expected {expected_code}")
    if expected_code not in text:
        raise RuntimeError(
            f"{name} failed without expected code {expected_code}: {text}"
        )
    return text


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


def _finite_vector(value: Any, *, length: int, label: str) -> list[float]:
    if not isinstance(value, list) or len(value) != length:
        raise RuntimeError(f"{label} is not a {length}-component vector: {value!r}")
    vector = [float(component) for component in value]
    if not all(math.isfinite(component) for component in vector):
        raise RuntimeError(f"{label} contains a non-finite component: {value!r}")
    return vector


def validate_raycast(result: dict[str, Any], *, require_hit: bool = True) -> None:
    ray = result.get("ray")
    if not isinstance(ray, dict):
        raise RuntimeError("raycast result has no ray metadata")
    _finite_vector(ray.get("origin"), length=3, label="ray origin")
    direction = _finite_vector(ray.get("direction"), length=3, label="ray direction")
    if not math.isclose(math.sqrt(sum(value * value for value in direction)), 1.0, abs_tol=1e-5):
        raise RuntimeError("raycast direction is not unit length")
    if not math.isfinite(float(ray.get("max_distance", math.nan))):
        raise RuntimeError("raycast max distance is not finite")
    if not result.get("hit"):
        if require_hit:
            raise RuntimeError("raycast did not hit evaluated geometry")
        return
    hit_object = result.get("hit_object")
    if not isinstance(hit_object, dict) or not hit_object.get("name"):
        raise RuntimeError("raycast hit did not identify an object")
    _finite_vector(result.get("location"), length=3, label="hit location")
    normal = _finite_vector(result.get("normal"), length=3, label="hit normal")
    if not math.isclose(math.sqrt(sum(value * value for value in normal)), 1.0, abs_tol=1e-4):
        raise RuntimeError("raycast hit normal is not unit length")
    if int(result.get("face_index", -1)) < 0:
        raise RuntimeError("raycast hit has an invalid face index")
    if not math.isfinite(float(result.get("distance", math.nan))):
        raise RuntimeError("raycast hit distance is not finite")


async def find_raycast_hit(
    session: ClientSession,
    capture_id: str,
) -> dict[str, Any]:
    coordinates = (
        (0.5, 0.5),
        (0.4, 0.5),
        (0.6, 0.5),
        (0.5, 0.4),
        (0.5, 0.6),
        (0.4, 0.4),
        (0.6, 0.4),
        (0.4, 0.6),
        (0.6, 0.6),
    )
    misses: list[dict[str, Any]] = []
    for x, y in coordinates:
        result, _ = await call_structured(
            session,
            "viewport.raycast",
            {"capture_id": capture_id, "x": x, "y": y},
        )
        validate_raycast(result, require_hit=False)
        if result.get("hit"):
            validate_raycast(result)
            return result
        misses.append(result)
    raise RuntimeError(f"raycast grid did not hit evaluated geometry: {misses}")


def validate_geometry_summary(result: dict[str, Any]) -> None:
    counts = result.get("counts")
    if not isinstance(counts, dict) or int(counts.get("polygons", 0)) <= 0:
        raise RuntimeError("geometry inspection returned no evaluated polygons")
    for key in ("vertices", "edges", "polygons", "loop_triangles"):
        if int(counts.get(key, -1)) < 0:
            raise RuntimeError(f"geometry inspection returned invalid {key} count")
    for bound_name in ("local_bounds", "world_bounds"):
        bounds = result.get(bound_name)
        if not isinstance(bounds, list) or len(bounds) != 8:
            raise RuntimeError(f"geometry inspection returned invalid {bound_name}")
        for index, corner in enumerate(bounds):
            _finite_vector(corner, length=3, label=f"{bound_name}[{index}]")


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
                "object.geometry.inspect",
                "viewport.capture",
                "viewport.raycast",
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
            capabilities = ping_before["capability_versions"]
            required_capabilities = {
                "viewport_capture": 3,
                "viewport_raycast": 1,
                "geometry_inspection": 1,
            }
            for capability, minimum in required_capabilities.items():
                if int(capabilities.get(capability, 0)) < minimum:
                    raise RuntimeError(
                        f"Blender add-on does not advertise {capability} v{minimum}"
                    )
            setup_context, _ = await call_structured(session, "context.get")
            if Path(setup_context["blend_file"]).resolve() != temporary_blend:
                raise RuntimeError(
                    "Blender is not displaying the prepared temporary blend file: "
                    f"{setup_context['blend_file']}"
                )
            non_ascii_name = args.unicode_object or find_non_ascii_object(setup_context)
            report["ping_before"] = ping_before
            report["setup_context"] = setup_context
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

            if args.verify_ui:
                confirmation = await anyio.to_thread.run_sync(
                    input,
                    "Confirm compact N-panel and full Scene Properties panel are visible, "
                    "and no Area was split; type YES: ",
                )
                if confirmation.strip().upper() != "YES":
                    raise RuntimeError("Blender native UI checkpoint was not confirmed")
                report["ui_confirmation"] = {
                    "compact_n_panel": True,
                    "scene_properties_panel": True,
                    "area_layout_unchanged": True,
                }

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
                    {
                        "object_name": args.capture_object,
                        "view": "FRONT",
                        "max_size": 1000,
                        "display_mode": "SOLID",
                        "overlays": "OFF",
                    },
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

            # Manual UI, focus, and perspective setup belongs outside the state-preservation
            # interval. From this point onward the user is asked not to touch Blender.
            context_before, _ = await call_structured(session, "context.get")
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
            report["context_before"] = context_before
            report["unicode_object"] = unicode_object
            report["capture_object_before"] = capture_object_before

            diagnostic_captures: dict[str, dict[str, Any]] = {}
            diagnostic_hashes: dict[str, str] = {}
            for display_mode in ("RENDERED", "SOLID", "WIREFRAME"):
                metadata, capture_result = await call_structured(
                    session,
                    "viewport.capture",
                    {
                        "object_name": args.capture_object,
                        "view": "FRONT",
                        "max_size": 1000,
                        "display_mode": display_mode,
                        "overlays": "OFF",
                    },
                )
                if metadata.get("display_mode") != display_mode:
                    raise RuntimeError(f"capture did not apply {display_mode} shading")
                if metadata.get("overlays") != "OFF":
                    raise RuntimeError("capture did not disable overlays")
                if metadata.get("projection_kind") != "ORTHO":
                    raise RuntimeError("semantic FRONT capture was not orthographic")
                diagnostic_captures[display_mode] = metadata
                diagnostic_hashes[display_mode] = save_image(
                    capture_result,
                    artifact_directory / f"diagnostic-front-{display_mode.lower()}.png",
                )
            if len(set(diagnostic_hashes.values())) != len(diagnostic_hashes):
                raise RuntimeError("diagnostic display modes returned duplicate images")

            diagnostic_differences = {
                mode: image_difference_statistics(
                    artifact_directory / "diagnostic-front-solid.png",
                    artifact_directory / f"diagnostic-front-{mode.lower()}.png",
                )
                for mode in ("RENDERED", "WIREFRAME")
            }
            if any(
                images_match_within_render_noise(statistics)
                for statistics in diagnostic_differences.values()
            ):
                raise RuntimeError(
                    "diagnostic display modes did not produce visible differences: "
                    f"{diagnostic_differences}"
                )

            orbit_metadata, orbit_capture = await call_structured(
                session,
                "viewport.capture",
                {
                    "object_name": args.capture_object,
                    "view": "FRONT",
                    "max_size": 1000,
                    "display_mode": "SOLID",
                    "overlays": "OFF",
                    "orbit": {"yaw_degrees": 30.0, "pitch_degrees": 15.0},
                },
            )
            orbit_hash = save_image(
                orbit_capture,
                artifact_directory / "diagnostic-front-orbit.png",
            )
            orbit_difference = image_difference_statistics(
                artifact_directory / "diagnostic-front-solid.png",
                artifact_directory / "diagnostic-front-orbit.png",
            )
            if images_match_within_render_noise(orbit_difference):
                raise RuntimeError("absolute yaw/pitch orbit did not visibly change the capture")
            if orbit_metadata.get("orbit") != {
                "yaw_degrees": 30.0,
                "pitch_degrees": 15.0,
            }:
                raise RuntimeError("capture did not report the requested absolute orbit")

            front_raycast = await find_raycast_hit(
                session,
                str(diagnostic_captures["SOLID"]["capture_id"]),
            )
            current_metadata, current_capture = await call_structured(
                session,
                "viewport.capture",
                {
                    "object_name": args.capture_object,
                    "view": "CURRENT",
                    "max_size": 1000,
                    "display_mode": "SOLID",
                    "overlays": "OFF",
                },
            )
            if current_metadata.get("projection_kind") != "PERSP":
                raise RuntimeError(
                    "CURRENT viewport is not perspective; switch the source VIEW_3D to "
                    "Perspective before running this smoke test"
                )
            current_hash = save_image(
                current_capture,
                artifact_directory / "diagnostic-current-perspective.png",
            )
            current_raycast = await find_raycast_hit(
                session,
                str(current_metadata["capture_id"]),
            )

            geometry_object_name = str(front_raycast["hit_object"]["name"])
            generation_before_geometry = int(
                (await call_structured(session, "connection.ping"))[0]["scene_generation"]
            )
            geometry_first, _ = await call_structured(
                session,
                "object.geometry.inspect",
                {"object_name": geometry_object_name},
            )
            geometry_second, _ = await call_structured(
                session,
                "object.geometry.inspect",
                {"object_name": geometry_object_name},
            )
            validate_geometry_summary(geometry_first)
            validate_geometry_summary(geometry_second)
            generation_after_geometry = int(
                (await call_structured(session, "connection.ping"))[0]["scene_generation"]
            )
            if generation_after_geometry != generation_before_geometry:
                raise RuntimeError("geometry inspection advanced the scene generation")
            stable_geometry_keys = (
                "session_identity",
                "counts",
                "local_bounds",
                "world_bounds",
                "surface_area_local",
                "edge_topology",
                "material_slots",
                "modifiers",
                "warnings",
            )
            if any(
                geometry_first.get(key) != geometry_second.get(key)
                for key in stable_geometry_keys
            ):
                raise RuntimeError("repeated geometry inspection was not stable")
            report["diagnostic_captures"] = diagnostic_captures
            report["diagnostic_capture_sha256"] = diagnostic_hashes
            report["diagnostic_differences"] = diagnostic_differences
            report["orbit_capture"] = orbit_metadata
            report["orbit_capture_sha256"] = orbit_hash
            report["orbit_difference"] = orbit_difference
            report["front_ortho_raycast"] = front_raycast
            report["current_perspective_capture"] = current_metadata
            report["current_perspective_capture_sha256"] = current_hash
            report["current_perspective_raycast"] = current_raycast
            report["geometry_inspection"] = geometry_first
            report["geometry_inspection_generation"] = {
                "before": generation_before_geometry,
                "after": generation_after_geometry,
            }

            bundle_before, bundle_before_result = await call_structured(
                session,
                "observation.bundle",
                {
                    "object_name": args.capture_object,
                    "views": ["FRONT", "RIGHT", "TOP"],
                    "max_size": 1000,
                    "display_mode": "SOLID",
                    "overlays": "OFF",
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
            stale_error = await call_expected_error(
                session,
                "viewport.raycast",
                {
                    "capture_id": diagnostic_captures["SOLID"]["capture_id"],
                    "x": 0.5,
                    "y": 0.5,
                },
                "CAPTURE_STALE",
            )
            after_metadata, after_capture = await call_structured(
                session,
                "observation.bundle",
                {
                    "object_name": args.capture_object,
                    "views": ["FRONT"],
                    "max_size": 1000,
                    "display_mode": "SOLID",
                    "overlays": "OFF",
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
            report["stale_capture_error"] = stale_error
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
                    "display_mode": "SOLID",
                    "overlays": "OFF",
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
            rollback_capture_id = str(rollback_metadata["captures"][0]["capture_id"])
            rollback_raycast = await find_raycast_hit(session, rollback_capture_id)
            report["rollback_raycast"] = rollback_raycast
            context_after, _ = await call_structured(session, "context.get")
            if context_identity(context_after) != context_identity(context_before):
                changed_fields = {
                    key: {
                        "before": context_identity(context_before).get(key),
                        "after": context_identity(context_after).get(key),
                    }
                    for key in CONTEXT_KEYS
                    if context_identity(context_before).get(key)
                    != context_identity(context_after).get(key)
                }
                raise RuntimeError(
                    f"user context was not restored exactly: {changed_fields}"
                )
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
        "--verify-ui",
        action="store_true",
        help="require manual confirmation of the compact and Scene Properties panels",
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
