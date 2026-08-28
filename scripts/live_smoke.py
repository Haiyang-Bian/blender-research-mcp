"""Run the live Blender 4.2 first-vertical-slice acceptance through MCP stdio."""

from __future__ import annotations

import argparse
import base64
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
from PIL import Image

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
        "temporary_sha256_prepared": preparation["temporary_sha256"],
        "temporary_sha256_before": temporary_hash_before,
        "temporary_changed_during_setup": temporary_changed_during_setup,
        "source_file": str(source_blend),
    }
    async with stdio_client(server_parameters) as (read_stream, write_stream):  # noqa: SIM117
        async with ClientSession(
            read_stream,
            write_stream,
            read_timeout_seconds=timedelta(seconds=40),
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
            context_before, _ = await call_structured(session, "context.get")
            if Path(context_before["blend_file"]).resolve() != temporary_blend:
                raise RuntimeError(
                    "Blender is not displaying the prepared temporary blend file: "
                    f"{context_before['blend_file']}"
                )
            non_ascii_name = find_non_ascii_object(context_before)
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

            if args.foreground_delay > 0:
                await anyio.to_thread.run_sync(
                    input,
                    "Bring Blender to the foreground, then press Enter... ",
                )
                await anyio.sleep(args.foreground_delay)

            before_image_hashes: dict[str, str] = {}
            for view in ("FRONT", "RIGHT", "TOP"):
                metadata, capture = await call_structured(
                    session,
                    "viewport.capture",
                    {
                        "object_name": args.capture_object,
                        "view": view,
                        "max_size": 1000,
                    },
                )
                before_image_hashes[view] = save_image(
                    capture,
                    artifact_directory / f"before-{view.lower()}.png",
                )
                report[f"capture_before_{view.lower()}"] = metadata
            if len(set(before_image_hashes.values())) != len(before_image_hashes):
                raise RuntimeError("orthographic captures unexpectedly returned duplicate images")
            report["capture_before_sha256"] = before_image_hashes

            helper_before, _ = await call_structured(
                session,
                "object.inspect",
                {"object_name": args.transform_object},
            )
            generation = int(helper_before["scene_generation"])
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
                "viewport.capture",
                {
                    "object_name": args.capture_object,
                    "view": "FRONT",
                    "max_size": 1000,
                },
            )
            report["capture_after_transform_sha256"] = save_image(
                after_capture,
                artifact_directory / "after-transform-front.png",
            )
            generation_after_capture, _ = await call_structured(
                session,
                "connection.ping",
            )
            rolled_back, _ = await call_structured(
                session,
                "transaction.rollback",
                {
                    "transaction_id": transaction["transaction_id"],
                    "expected_scene_generation": generation_after_capture[
                        "scene_generation"
                    ],
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
                "viewport.capture",
                {
                    "object_name": args.capture_object,
                    "view": "FRONT",
                    "max_size": 1000,
                },
            )
            report["capture_after_rollback_sha256"] = save_image(
                rollback_capture,
                artifact_directory / "after-rollback-front.png",
            )
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
            report.update(
                {
                    "helper_before": helper_before,
                    "transaction": transaction,
                    "transform": transformed,
                    "capture_after_transform": after_metadata,
                    "generation_after_capture": generation_after_capture,
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
    parser.add_argument("--skip-restart", action="store_true")
    parser.add_argument(
        "--foreground-delay",
        type=float,
        default=0,
        help="pause before capture and wait this many seconds after confirmation",
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
    if args.foreground_delay < 0:
        parser.error("--foreground-delay must be non-negative")
    anyio.run(run, args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
