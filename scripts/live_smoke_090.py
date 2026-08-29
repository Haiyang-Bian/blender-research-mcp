"""Run the Blender 4.2 unified object-settings acceptance for release 0.9.0."""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import hashlib
import json
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
FIXTURE_BUILDER = ROOT / "scripts" / "create_object_settings_fixture.py"


def stage(name: str) -> None:
    print(f"[0.9 smoke] {name}", flush=True)


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
        raise RuntimeError(f"Could not build object-settings fixture; see {log_path}")


async def mutate(
    client: BridgeClient,
    command: str,
    params: dict[str, Any],
    generation: int,
    *,
    key: str | None = None,
) -> dict[str, Any]:
    return await client.call(
        command,
        params,
        expected_scene_generation=generation,
        idempotency_key=key or str(uuid4()),
        read_only=False,
    )


async def begin(client: BridgeClient, generation: int, label: str) -> dict[str, Any]:
    return await mutate(
        client,
        "transaction.begin",
        {"label": label, "viewport_id": None},
        generation,
    )


async def inspect_object(client: BridgeClient, name: str) -> dict[str, Any]:
    return await client.call("object.inspect", {"object_name": name}, read_only=True)


def data_patch(
    inspected: dict[str, Any],
    kind: str,
    values: dict[str, Any],
    *,
    allow_shared_data: bool = False,
) -> dict[str, Any]:
    data = inspected["data"]
    type_field = "light_type" if kind == "light" else "camera_type"
    expected_type_field = "expected_light_type" if kind == "light" else "expected_camera_type"
    return {
        "type": kind,
        "expected_data_identity": data["session_identity"],
        "expected_data_users": data["users"],
        expected_type_field: data["settings"][type_field],
        "allow_shared_data": allow_shared_data,
        **values,
    }


def object_set_params(
    transaction_id: str,
    inspected: dict[str, Any],
    patches: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "transaction_id": transaction_id,
        "object_name": inspected["name"],
        "expected_object_identity": inspected["session_identity"],
        "patches": patches,
    }


def require_restored(before: dict[str, Any], after: dict[str, Any]) -> None:
    keys = ("location", "rotation_euler_degrees", "scale", "visibility", "data")
    changed = [key for key in keys if before.get(key) != after.get(key)]
    if changed:
        raise RuntimeError(f"object settings were not restored: {changed}")


async def set_then_rollback(
    client: BridgeClient,
    name: str,
    patches: list[dict[str, Any]],
    label: str,
) -> dict[str, Any]:
    before = await inspect_object(client, name)
    transaction = await begin(client, int(before["scene_generation"]), label)
    result = await mutate(
        client,
        "object.set",
        object_set_params(str(transaction["transaction_id"]), before, patches),
        int(transaction["scene_generation"]),
    )
    if int(result["scene_generation"]) != int(transaction["scene_generation"]) + 1:
        raise RuntimeError("object.set did not advance generation exactly once")
    if result["changes"] != sorted(result["changes"], key=lambda item: item["path"]):
        raise RuntimeError("object.set changes were not path sorted")
    during = await inspect_object(client, name)
    rollback = await mutate(
        client,
        "transaction.rollback",
        {"transaction_id": transaction["transaction_id"]},
        int(result["scene_generation"]),
    )
    after = await inspect_object(client, name)
    require_restored(before, after)
    return {
        "before": before,
        "result": result,
        "during": during,
        "rollback": rollback,
        "after": after,
        "restored": True,
    }


async def check_typed_lights(client: BridgeClient, report: dict[str, Any]) -> None:
    stage("typed Point, Spot, Sun, and Area settings")
    values = {
        "Point Light": {"energy": 1250.0, "color": "#C9DEE5", "radius": 0.7},
        "Spot Light": {
            "energy": 1600.0,
            "color": "#EFF0EA",
            "radius": 0.5,
            "spot_size_degrees": 55.0,
            "spot_blend": 0.45,
        },
        "Sun Light": {"energy": 3.0, "color": "#EFF0EA", "angle_degrees": 2.0},
        "Area Light": {
            "energy": 1400.0,
            "color": "#C9DEE5",
            "shape": "RECTANGLE",
            "size": 4.0,
            "size_y": 2.0,
        },
    }
    results: dict[str, Any] = {}
    for name, settings in values.items():
        inspected = await inspect_object(client, name)
        results[name] = await set_then_rollback(
            client,
            name,
            [data_patch(inspected, "light", settings)],
            f"0.9 {name}",
        )
    report["typed_lights"] = results


async def check_typed_cameras(client: BridgeClient, report: dict[str, Any]) -> None:
    stage("typed perspective and orthographic Camera settings")
    perspective = await inspect_object(client, "Perspective Camera")
    report["perspective_rollback"] = await set_then_rollback(
        client,
        "Perspective Camera",
        [
            {
                "type": "transform",
                "location": {"x": 7.5, "y": -9.5, "z": 6.5},
                "rotation_euler_degrees": {"z": 2.0},
            },
            data_patch(
                perspective,
                "camera",
                {
                    "lens": 70.0,
                    "sensor_width": 40.0,
                    "clip_start": 0.2,
                    "clip_end": 800.0,
                    "shift_x": 0.05,
                    "shift_y": -0.03,
                },
            ),
        ],
        "0.9 perspective rollback",
    )
    if report["perspective_rollback"]["result"]["delta_count"] != 2:
        raise RuntimeError("combined Camera transform/data call did not record two deltas")

    orthographic = await inspect_object(client, "Orthographic Camera")
    report["orthographic_rollback"] = await set_then_rollback(
        client,
        "Orthographic Camera",
        [
            {"type": "transform", "location": {"x": -7.0, "z": 6.0}},
            data_patch(
                orthographic,
                "camera",
                {
                    "ortho_scale": 10.0,
                    "clip_start": 0.2,
                    "clip_end": 900.0,
                    "shift_x": -0.1,
                    "shift_y": 0.05,
                },
            ),
        ],
        "0.9 orthographic rollback",
    )


async def check_shared_data(
    client: BridgeClient,
    name: str,
    peer_name: str,
    kind: str,
    values: dict[str, Any],
) -> dict[str, Any]:
    before = await inspect_object(client, name)
    peer_before = await inspect_object(client, peer_name)
    if before["data"]["users"] != 2 or not before["data"]["shared"]:
        raise RuntimeError(f"shared fixture has the wrong scope: {name}")
    transaction = await begin(client, int(before["scene_generation"]), f"0.9 shared {kind}")
    rejected: dict[str, Any]
    try:
        await mutate(
            client,
            "object.set",
            object_set_params(
                str(transaction["transaction_id"]),
                before,
                [data_patch(before, kind, values)],
            ),
            int(transaction["scene_generation"]),
        )
    except BridgeError as error:
        rejected = error.error.model_dump(mode="json")
        if error.error.code != "SHARED_OBJECT_DATA_CONFIRMATION_REQUIRED":
            raise
    else:
        raise RuntimeError("shared object data changed without explicit authorization")

    accepted = await mutate(
        client,
        "object.set",
        object_set_params(
            str(transaction["transaction_id"]),
            before,
            [data_patch(before, kind, values, allow_shared_data=True)],
        ),
        int(transaction["scene_generation"]),
    )
    peer_during = await inspect_object(client, peer_name)
    rollback = await mutate(
        client,
        "transaction.rollback",
        {"transaction_id": transaction["transaction_id"]},
        int(accepted["scene_generation"]),
    )
    after = await inspect_object(client, name)
    peer_after = await inspect_object(client, peer_name)
    require_restored(before, after)
    require_restored(peer_before, peer_after)
    return {
        "rejected": rejected,
        "accepted": accepted,
        "peer_during": peer_during,
        "rollback": rollback,
        "restored": True,
    }


async def check_noops(client: BridgeClient, report: dict[str, Any]) -> None:
    stage("unified and legacy no-op behavior")
    inspected = await inspect_object(client, "Perspective Camera")
    generation = int(inspected["scene_generation"])
    transaction = await begin(client, generation, "0.9 no-op")
    result = await mutate(
        client,
        "object.set",
        object_set_params(
            str(transaction["transaction_id"]),
            inspected,
            [data_patch(inspected, "camera", {"lens": inspected["data"]["settings"]["lens"]})],
        ),
        int(transaction["scene_generation"]),
    )
    if result["changed"] or result["delta_count"] != 0:
        raise RuntimeError("object.set no-op created a transaction delta")
    if int(result["scene_generation"]) != int(transaction["scene_generation"]):
        raise RuntimeError("object.set no-op advanced generation")
    rollback = await mutate(
        client,
        "transaction.rollback",
        {"transaction_id": transaction["transaction_id"]},
        int(result["scene_generation"]),
    )

    cube = await inspect_object(client, "Cube")
    transaction = await begin(client, int(cube["scene_generation"]), "0.9 legacy no-op")
    legacy = await mutate(
        client,
        "object.transform",
        {
            "transaction_id": transaction["transaction_id"],
            "object_name": "Cube",
            "expected_object_identity": None,
            "location": None,
            "rotation_euler_degrees": None,
            "scale": {"x": cube["scale"][0]},
        },
        int(transaction["scene_generation"]),
    )
    if legacy["changed"] or legacy["delta_count"] != 0:
        raise RuntimeError("legacy object.transform no-op created a delta")
    legacy_rollback = await mutate(
        client,
        "transaction.rollback",
        {"transaction_id": transaction["transaction_id"]},
        int(legacy["scene_generation"]),
    )
    report["noops"] = {
        "unified": result,
        "unified_rollback": rollback,
        "legacy": legacy,
        "legacy_rollback": legacy_rollback,
    }


def comparison_request(
    inspected: dict[str, Any],
    locator: dict[str, Any],
    values: tuple[Any, ...],
) -> ComparisonRequest:
    return ComparisonRequest.model_validate(
        {
            "target": {
                "type": "object_setting",
                "object_name": inspected["name"],
                "expected_object_identity": inspected["session_identity"],
                "locator": locator,
            },
            "candidates": [
                {"label": chr(ord("A") + index), "value": value}
                for index, value in enumerate(values)
            ],
            "capture": {
                "object_name": "Cube",
                "view": "FRONT",
                "max_size": 256,
                "display_mode": "SOLID",
                "overlays": "OFF",
            },
        }
    )


async def compare_settings(client: BridgeClient, report: dict[str, Any]) -> None:
    stage("typed object-setting comparisons")
    cases: list[tuple[str, str, tuple[Any, ...]]] = [
        ("Point Light", "energy", (600.0, 1500.0)),
        ("Point Light", "color", ("#C9DEE5", "#214268")),
        ("Area Light", "size", (1.5, 4.5)),
        ("Perspective Camera", "lens", (35.0, 85.0)),
    ]
    results: dict[str, Any] = {}
    for name, property_name, values in cases:
        inspected = await inspect_object(client, name)
        data = inspected["data"]
        kind = data["type"]
        type_field = "light_type" if kind == "light" else "camera_type"
        locator = {
            "type": kind,
            "expected_data_identity": data["session_identity"],
            "expected_data_users": data["users"],
            f"expected_{kind}_type": data["settings"][type_field],
            "allow_shared_data": False,
            "property": property_name,
        }
        images, result = await run_lookdev_comparison(
            client,
            comparison_request(inspected, locator, values),
        )
        if len(images) != len(values) + 1:
            raise RuntimeError("comparison returned the wrong image count")
        if [item["label"] for item in result["items"]] != ["baseline", "A", "B"]:
            raise RuntimeError("comparison item order changed")
        if not all(
            result[key] is True
            for key in ("context_unchanged", "object_unchanged", "target_restored")
        ):
            raise RuntimeError("comparison did not prove complete restoration")
        results[f"{name}.{property_name}"] = result
    report["comparisons"] = results


async def commit_save_reload_render(
    client: BridgeClient,
    manager: ApplicationManager,
    artifact_directory: Path,
    report: dict[str, Any],
) -> None:
    stage("commit, save, reload, inspect, and render")
    before = await inspect_object(client, "Perspective Camera")
    transaction = await begin(client, int(before["scene_generation"]), "0.9 Camera commit")
    result = await mutate(
        client,
        "object.set",
        object_set_params(
            str(transaction["transaction_id"]),
            before,
            [
                {
                    "type": "transform",
                    "location": {"x": 8.5, "y": -10.5, "z": 7.5},
                },
                data_patch(
                    before,
                    "camera",
                    {"lens": 65.0, "sensor_width": 38.0, "shift_x": 0.02},
                ),
            ],
        ),
        int(transaction["scene_generation"]),
    )
    commit = await mutate(
        client,
        "transaction.commit",
        {"transaction_id": transaction["transaction_id"]},
        int(result["scene_generation"]),
    )
    saved = await manager.project_save()
    reloaded = await manager.project_reload(
        save_current=False,
        use_scripts=False,
        load_ui=False,
    )
    after = await inspect_object(client, "Perspective Camera")
    if after["location"] != [8.5, -10.5, 7.5] or after["data"]["settings"]["lens"] != 65.0:
        raise RuntimeError("committed Camera settings did not persist through reload")
    preview_bytes, preview = await request_render_preview(
        client,
        {
            "camera_name": "Perspective Camera",
            "expected_camera_identity": after["session_identity"],
            "width": 320,
            "height": 256,
            "samples": 16,
            "transparent": False,
        },
        expected_scene_generation=int(after["scene_generation"]),
        idempotency_key=str(uuid4()),
    )
    preview_path = artifact_directory / "object-settings-preview.png"
    preview_path.write_bytes(preview_bytes)
    report["persistence"] = {
        "set": result,
        "commit": commit,
        "save": saved,
        "reload": reloaded,
        "after": after,
        "preview": preview,
        "preview_path": str(preview_path),
        "preview_sha256": sha256(preview_path),
    }


async def run(args: argparse.Namespace) -> dict[str, Any]:
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + f"-{uuid4().hex[:8]}"
    temporary_root = Path(tempfile.gettempdir()) / "blender-research-mcp-object-settings" / run_id
    temporary_root.mkdir(parents=True, exist_ok=False)
    artifact_directory = ROOT / "artifacts" / "live-smoke" / run_id
    artifact_directory.mkdir(parents=True, exist_ok=False)
    source = temporary_root / "object-settings-source.blend"
    project = temporary_root / "object-settings-project.blend"
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
    }
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
        if application["addon_version"] != "0.9.0" or not str(
            application["blender_version"]
        ).startswith("4.2.23"):
            raise RuntimeError("managed launch did not load Blender 4.2.23 with add-on 0.9.0")
        ping_before = await client.call("connection.ping", read_only=True)
        if int(ping_before["capability_versions"].get("object_settings", 0)) < 1:
            raise RuntimeError("managed add-on did not advertise object_settings: 1")
        report["ping_before"] = ping_before

        report["project_open"] = await manager.project_open(
            str(project),
            save_current=False,
            use_scripts=False,
            load_ui=False,
        )
        await check_typed_lights(client, report)
        await check_typed_cameras(client, report)
        stage("shared Light and Camera data")
        report["shared_light"] = await check_shared_data(
            client,
            "Shared Point 1",
            "Shared Point 2",
            "light",
            {"energy": 750.0},
        )
        report["shared_camera"] = await check_shared_data(
            client,
            "Shared Camera 1",
            "Shared Camera 2",
            "camera",
            {"lens": 72.0},
        )
        await check_noops(client, report)
        await compare_settings(client, report)
        await commit_save_reload_render(client, manager, artifact_directory, report)

        ping_after = await client.call("connection.ping", read_only=True)
        if int(ping_after["heartbeat"]) <= int(ping_before["heartbeat"]):
            raise RuntimeError("Blender UI heartbeat did not advance")
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--blender-executable", type=Path, required=True)
    parser.add_argument("--port", type=int, default=9882)
    parser.add_argument("--timeout", type=float, default=90.0)
    args = parser.parse_args()
    try:
        report = asyncio.run(run(args))
    except BridgeError as exc:
        print(
            json.dumps(exc.error.model_dump(mode="json"), ensure_ascii=False, indent=2),
            flush=True,
        )
        raise
    report_path = Path(report["artifact_directory"]) / "report-0.9.0.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(report_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
