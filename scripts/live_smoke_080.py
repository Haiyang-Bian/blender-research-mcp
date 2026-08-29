"""Run the Blender 4.2 semantic scene-authoring acceptance for release 0.8.0."""

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

from PIL import Image, ImageChops, ImageStat

from blender_research_mcp.client import BridgeClient
from blender_research_mcp.errors import BridgeError
from blender_research_mcp.lifecycle import ApplicationManager
from blender_research_mcp.rendering import request_render_preview, request_render_save

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_BUILDER = ROOT / "scripts" / "create_authoring_blank_fixture.py"


def stage(name: str) -> None:
    print(f"[0.8 smoke] {name}", flush=True)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_blank_fixture(blender: Path, output: Path, log_path: Path) -> None:
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
        result = subprocess.run(  # noqa: S603 - fixed Blender executable and repository script
            command,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            shell=False,
            check=False,
        )
    if result.returncode != 0 or not output.is_file():
        raise RuntimeError(f"Could not build blank fixture; see {log_path}")


def make_textures(wave_path: Path, stars_path: Path) -> None:
    wave = Image.new("RGB", (512, 512))
    wave_pixels = []
    for y in range(512):
        for x in range(512):
            value = 128 + round(
                42 * math.sin(x * 0.083 + y * 0.019)
                + 24 * math.sin(y * 0.117 - x * 0.013)
            )
            bounded = max(0, min(255, value))
            wave_pixels.append((bounded, bounded, bounded))
    wave.putdata(wave_pixels)
    wave.save(wave_path, format="PNG", optimize=True)

    stars = Image.new("RGB", (1024, 512), (5, 12, 28))
    pixels = stars.load()
    for index in range(700):
        x = (index * 811 + index * index * 17) % 1024
        y = (index * 313 + index * index * 7) % 512
        brightness = 145 + (index * 29) % 111
        pixels[x, y] = (brightness, brightness, min(255, brightness + 16))
        if index % 37 == 0 and x + 1 < 1024:
            pixels[x + 1, y] = (brightness // 2, brightness // 2, brightness // 2)
    stars.save(stars_path, format="PNG", optimize=True)


def transform(
    location: tuple[float, float, float],
    rotation: tuple[float, float, float] = (0.0, 0.0, 0.0),
    scale: tuple[float, float, float] = (1.0, 1.0, 1.0),
) -> dict[str, dict[str, float]]:
    def vector(values: tuple[float, float, float]) -> dict[str, float]:
        return dict(zip(("x", "y", "z"), values, strict=True))

    return {
        "location": vector(location),
        "rotation_euler_degrees": vector(rotation),
        "scale": vector(scale),
    }


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


async def create(
    client: BridgeClient,
    transaction_id: str,
    generation: int,
    definition: dict[str, Any],
) -> dict[str, Any]:
    return await mutate(
        client,
        "object.create",
        {"transaction_id": transaction_id, "definition": definition},
        generation,
    )


async def inspect_scene(client: BridgeClient) -> dict[str, Any]:
    return await client.call(
        "scene.inspect",
        {
            "kinds": [
                "objects",
                "collections",
                "materials",
                "images",
                "world",
                "camera",
                "render",
            ],
            "name_filter": None,
            "limit": 256,
        },
        read_only=True,
    )


async def deterministic_transaction_checks(
    client: BridgeClient,
    manager: ApplicationManager,
    report: dict[str, Any],
) -> None:
    stage("deterministic structural rollback")
    baseline = await inspect_scene(client)
    transaction = await begin(client, int(baseline["scene_generation"]), "0.8 rollback")
    created = await create(
        client,
        str(transaction["transaction_id"]),
        int(transaction["scene_generation"]),
        {
            "type": "cube",
            "name": "Rollback Probe",
            "collection_name": None,
            "expected_collection_identity": None,
            "transform": transform((0.0, 0.0, 0.0)),
            "size": 1.0,
        },
    )
    rollback = await mutate(
        client,
        "transaction.rollback",
        {"transaction_id": transaction["transaction_id"]},
        int(created["scene_generation"]),
    )
    after = await inspect_scene(client)
    if any(item["name"] == "Rollback Probe" for item in after["objects"]):
        raise RuntimeError("created object survived structural rollback")
    report["deterministic_rollback"] = {
        "created": created["object"],
        "rollback": rollback,
        "restored": True,
    }

    stage("deterministic structure conflict")
    transaction = await begin(client, int(after["scene_generation"]), "0.8 conflict")
    created = await create(
        client,
        str(transaction["transaction_id"]),
        int(transaction["scene_generation"]),
        {
            "type": "cube",
            "name": "Conflict Probe",
            "collection_name": None,
            "expected_collection_identity": None,
            "transform": transform((0.0, 0.0, 0.0)),
            "size": 1.0,
        },
    )
    touch = await client.call(
        "_test.structure.touch",
        {"object_name": "Conflict Probe"},
        read_only=False,
    )
    try:
        await mutate(
            client,
            "transaction.commit",
            {"transaction_id": transaction["transaction_id"]},
            int(created["scene_generation"]),
        )
    except BridgeError as exc:
        if exc.error.code != "STRUCTURE_CONFLICT":
            raise
        conflict_code = exc.error.code
    else:
        raise RuntimeError("manual structural conflict unexpectedly committed")
    preserved = await client.call(
        "object.inspect",
        {"object_name": "Conflict Probe"},
        read_only=True,
    )
    if not math.isclose(float(preserved["location"][0]), 0.25, abs_tol=1e-7):
        raise RuntimeError("structural conflict overwrote the injected user value")
    reload_result = await manager.project_reload(
        save_current=False,
        use_scripts=False,
        load_ui=False,
    )
    report["structure_conflict"] = {
        "hook": touch,
        "code": conflict_code,
        "preserved_location": preserved["location"],
        "recovery_reload": reload_result,
    }

    stage("disconnect rollback")
    after_reload = await inspect_scene(client)
    transaction = await begin(
        client,
        int(after_reload["scene_generation"]),
        "0.8 disconnect rollback",
    )
    created = await create(
        client,
        str(transaction["transaction_id"]),
        int(transaction["scene_generation"]),
        {
            "type": "ico_sphere",
            "name": "Disconnect Probe",
            "collection_name": None,
            "expected_collection_identity": None,
            "transform": transform((0.0, 0.0, 0.0)),
            "radius": 1.0,
            "subdivisions": 2,
        },
    )
    await client.close()
    await asyncio.sleep(3.0)
    reconnected = await client.call("connection.ping", read_only=True)
    after_disconnect = await inspect_scene(client)
    if any(item["name"] == "Disconnect Probe" for item in after_disconnect["objects"]):
        raise RuntimeError("disconnect rollback left the created object in the scene")
    report["disconnect_rollback"] = {
        "created_identity": created["object"]["session_identity"],
        "reconnected_instance": reconnected["instance_id"],
        "restored": True,
    }


async def build_moonlit_scene(
    client: BridgeClient,
    manager: ApplicationManager,
    wave_path: Path,
    stars_path: Path,
    output_root: Path,
    artifact_directory: Path,
    report: dict[str, Any],
) -> None:
    stage("author moonlit-water scene")
    baseline = await inspect_scene(client)
    transaction = await begin(
        client,
        int(baseline["scene_generation"]),
        "author:moonlit-water",
    )
    transaction_id = str(transaction["transaction_id"])
    generation = int(transaction["scene_generation"])

    definitions = [
        {
            "type": "grid",
            "name": "Moonlit Water",
            "collection_name": None,
            "expected_collection_identity": None,
            "transform": transform((0.0, 3.0, 0.0)),
            "size": 24.0,
            "x_subdivisions": 64,
            "y_subdivisions": 64,
        },
        {
            "type": "uv_sphere",
            "name": "Moon",
            "collection_name": None,
            "expected_collection_identity": None,
            "transform": transform((0.0, 7.0, 5.0)),
            "radius": 1.6,
            "segments": 64,
            "ring_count": 32,
        },
        {
            "type": "camera",
            "name": "Moon Camera A",
            "collection_name": None,
            "expected_collection_identity": None,
            "transform": transform((9.0, -12.0, 6.0), (75.2, 0.0, 31.0)),
            "lens": 48.0,
            "sensor_width": 36.0,
        },
        {
            "type": "camera",
            "name": "Moon Camera B",
            "collection_name": None,
            "expected_collection_identity": None,
            "transform": transform((-8.0, -8.0, 4.5), (77.5, 0.0, -36.0)),
            "lens": 58.0,
            "sensor_width": 36.0,
        },
        {
            "type": "area_light",
            "name": "Moon Key",
            "collection_name": None,
            "expected_collection_identity": None,
            "transform": transform((0.0, 4.0, 9.0)),
            "energy": 1300.0,
            "color": "#C9DEE5",
            "size": 7.0,
            "spot_size_degrees": 45.0,
        },
        {
            "type": "point_light",
            "name": "Moon Fill",
            "collection_name": None,
            "expected_collection_identity": None,
            "transform": transform((0.0, 5.0, 5.0)),
            "energy": 550.0,
            "color": "#EFF0EA",
            "size": 1.0,
            "spot_size_degrees": 45.0,
        },
    ]
    objects: dict[str, dict[str, Any]] = {}
    for definition in definitions:
        result = await create(client, transaction_id, generation, definition)
        generation = int(result["scene_generation"])
        objects[str(definition["name"])] = result["object"]

    materials: dict[str, dict[str, Any]] = {}
    for definition in (
        {
            "name": "Moon Material",
            "base_color": {"type": "hex_srgb", "value": "#EFF0EA"},
            "metallic": 0.0,
            "roughness": 0.62,
            "ior": 1.45,
            "transmission": 0.0,
            "emission_color": {"type": "hex_srgb", "value": "#C9DEE5"},
            "emission_strength": 1.3,
            "alpha": 1.0,
        },
        {
            "name": "Water Material",
            "base_color": {"type": "hex_srgb", "value": "#214268"},
            "metallic": 0.18,
            "roughness": 0.28,
            "ior": 1.333,
            "transmission": 0.0,
            "emission_color": {"type": "hex_srgb", "value": "#214268"},
            "emission_strength": 0.03,
            "alpha": 1.0,
        },
    ):
        result = await mutate(
            client,
            "material.create",
            {"transaction_id": transaction_id, "definition": definition},
            generation,
        )
        generation = int(result["scene_generation"])
        materials[str(definition["name"])] = result["material"]

    for object_name, material_name in (
        ("Moon", "Moon Material"),
        ("Moonlit Water", "Water Material"),
    ):
        obj = objects[object_name]
        material = materials[material_name]
        result = await mutate(
            client,
            "material.assign",
            {
                "transaction_id": transaction_id,
                "object_name": object_name,
                "expected_object_identity": obj["session_identity"],
                "expected_data_identity": obj["data"]["session_identity"],
                "expected_data_users": obj["data"]["users"],
                "mode": "append",
                "slot_index": None,
                "expected_slot_material_identity": None,
                "material_name": material_name,
                "expected_material_identity": material["session_identity"],
                "expected_material_users": material["users"],
                "allow_shared_data": False,
            },
            generation,
        )
        generation = int(result["scene_generation"])

    images: dict[str, dict[str, Any]] = {}
    for label, path, colorspace in (
        ("waves", wave_path, "NON_COLOR"),
        ("stars", stars_path, "SRGB"),
    ):
        result = await mutate(
            client,
            "image.load",
            {
                "transaction_id": transaction_id,
                "path": str(path),
                "colorspace": colorspace,
            },
            generation,
        )
        generation = int(result["scene_generation"])
        images[label] = result["image"]

    water_inspect = await client.call(
        "material.inspect",
        {"object_name": "Moonlit Water", "material_slot_index": 0},
        read_only=True,
    )
    principled = next(
        item for item in water_inspect["nodes"] if item["bl_idname"] == "ShaderNodeBsdfPrincipled"
    )
    bind = await mutate(
        client,
        "material.texture.bind",
        {
            "transaction_id": transaction_id,
            "material_name": "Water Material",
            "expected_material_identity": water_inspect["material_identity"],
            "expected_material_users": water_inspect["material_users"],
            "node_name": principled["name"],
            "expected_node_identity": principled["session_identity"],
            "image_name": images["waves"]["name"],
            "expected_image_identity": images["waves"]["session_identity"],
            "expected_image_users": images["waves"]["users"],
            "channel": "bump",
            "coordinates": "GENERATED",
            "mapping": transform((0.0, 0.0, 0.0), (0.0, 0.0, 0.0), (6.0, 6.0, 1.0)),
            "replace_existing": False,
            "expected_link_identities": [],
            "allow_shared": False,
        },
        generation,
    )
    generation = int(bind["scene_generation"])

    world = await mutate(
        client,
        "world.set",
        {
            "transaction_id": transaction_id,
            "expected_world_identity": None,
            "expected_world_users": None,
            "color": {"type": "hex_srgb", "value": "#214268"},
            "strength": 0.22,
            "environment_image_name": images["stars"]["name"],
            "expected_environment_image_identity": images["stars"]["session_identity"],
            "expected_environment_image_users": images["stars"]["users"],
            "rotation_z_degrees": 18.0,
            "allow_shared": False,
        },
        generation,
    )
    generation = int(world["scene_generation"])

    camera_a = objects["Moon Camera A"]
    camera_set = await mutate(
        client,
        "scene.camera.set",
        {
            "transaction_id": transaction_id,
            "camera_name": "Moon Camera A",
            "expected_camera_identity": camera_a["session_identity"],
        },
        generation,
    )
    generation = int(camera_set["scene_generation"])

    preview_results: dict[str, dict[str, Any]] = {}
    preview_bytes: dict[str, bytes] = {}
    for label, camera_name in (("camera_a", "Moon Camera A"), ("camera_b", "Moon Camera B")):
        camera = objects[camera_name]
        image_bytes, result = await request_render_preview(
            client,
            {
                "camera_name": camera_name,
                "expected_camera_identity": camera["session_identity"],
                "width": 512,
                "height": 512,
                "samples": 16,
                "transparent": False,
            },
            expected_scene_generation=generation,
            idempotency_key=str(uuid4()),
        )
        generation = int(result["scene_generation"])
        path = artifact_directory / f"moonlit-water-{label}.png"
        path.write_bytes(image_bytes)
        preview_results[label] = result
        preview_bytes[label] = image_bytes

    with (
        Image.open(Path(artifact_directory / "moonlit-water-camera_a.png")) as left,
        Image.open(Path(artifact_directory / "moonlit-water-camera_b.png")) as right,
    ):
        difference = ImageChops.difference(left.convert("RGB"), right.convert("RGB"))
        extrema = difference.getextrema()
        mean = ImageStat.Stat(difference).mean
    maximum_difference = max(channel[1] for channel in extrema)
    mean_difference = sum(mean) / len(mean)
    if maximum_difference < 16 or mean_difference < 1.0:
        raise RuntimeError("the two Camera previews are not visually distinct")

    stage("commit and export reviewed renders")
    commit = await mutate(
        client,
        "transaction.commit",
        {"transaction_id": transaction_id},
        generation,
    )
    generation = int(commit["scene_generation"])
    exports = []
    for camera_name, filename in (
        ("Moon Camera A", "moonlit-water-a.png"),
        ("Moon Camera B", "moonlit-water-b.png"),
        ("Moon Camera A", "moonlit-water-a.exr"),
    ):
        camera = objects[camera_name]
        result = await request_render_save(
            client,
            {
                "camera_name": camera_name,
                "expected_camera_identity": camera["session_identity"],
                "path": str(output_root / filename),
                "width": 512,
                "height": 512,
                "samples": 16,
                "transparent": False,
            },
            expected_scene_generation=generation,
            idempotency_key=str(uuid4()),
        )
        generation = int(result["scene_generation"])
        exports.append(result)

    saved = await manager.project_save(None)
    pre_reload = await inspect_scene(client)
    reloaded = await manager.project_reload(
        save_current=False,
        use_scripts=False,
        load_ui=False,
    )
    post_reload = await inspect_scene(client)
    names = {item["name"] for item in post_reload["objects"]}
    required_names = {definition["name"] for definition in definitions}
    if not required_names <= names:
        raise RuntimeError("saved/reloaded scene is missing authored objects")
    reloaded_camera = next(
        item for item in post_reload["objects"] if item["name"] == "Moon Camera A"
    )
    rerender_bytes, rerender = await request_render_preview(
        client,
        {
            "camera_name": "Moon Camera A",
            "expected_camera_identity": reloaded_camera["session_identity"],
            "width": 256,
            "height": 256,
            "samples": 8,
            "transparent": False,
        },
        expected_scene_generation=int(post_reload["scene_generation"]),
        idempotency_key=str(uuid4()),
    )
    (artifact_directory / "moonlit-water-after-reload.png").write_bytes(rerender_bytes)

    report["moonlit_water"] = {
        "transaction_id": transaction_id,
        "delta_count": commit["delta_count"],
        "delta_kinds": commit["delta_kinds"],
        "objects": objects,
        "materials": materials,
        "images": images,
        "texture_binding": bind["binding"],
        "world": world["world"],
        "camera_set": camera_set["camera"],
        "previews": preview_results,
        "preview_difference": {
            "maximum_channel_difference": maximum_difference,
            "mean_absolute_difference": mean_difference,
        },
        "commit": commit,
        "exports": exports,
        "project_save": saved,
        "project_reload": reloaded,
        "pre_reload_identities": {
            item["name"]: item["session_identity"] for item in pre_reload["objects"]
        },
        "post_reload_identities": {
            item["name"]: item["session_identity"] for item in post_reload["objects"]
        },
        "rerender": rerender,
    }


async def run(args: argparse.Namespace) -> dict[str, Any]:
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + f"-{uuid4().hex[:8]}"
    temporary_root = Path(tempfile.gettempdir()) / "blender-research-mcp-authoring" / run_id
    temporary_root.mkdir(parents=True, exist_ok=False)
    artifact_directory = ROOT / "artifacts" / "live-smoke" / run_id
    artifact_directory.mkdir(parents=True, exist_ok=False)
    source = temporary_root / "blank-source.blend"
    project = temporary_root / "moonlit-water.blend"
    wave_path = temporary_root / "deterministic-waves.png"
    stars_path = temporary_root / "deterministic-stars.png"
    fixture_log = artifact_directory / "blank-fixture.log"
    build_blank_fixture(args.blender_executable, source, fixture_log)
    source_hash_before = sha256(source)
    shutil.copy2(source, project)
    make_textures(wave_path, stars_path)

    report: dict[str, Any] = {
        "run_id": run_id,
        "started_at": datetime.now(UTC).isoformat(),
        "temporary_root": str(temporary_root),
        "artifact_directory": str(artifact_directory),
        "source": str(source),
        "source_sha256_before": source_hash_before,
        "project": str(project),
        "texture_sha256": {"waves": sha256(wave_path), "stars": sha256(stars_path)},
    }
    previous_test_hooks = os.environ.get("BLENDER_RESEARCH_MCP_TEST_HOOKS")
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
            raise RuntimeError("cold application launch did not return launched")
        if application["addon_version"] != "0.8.0" or not str(
            application["blender_version"]
        ).startswith("4.2.23"):
            raise RuntimeError("managed launch did not load Blender 4.2.23 with add-on 0.8.0")
        ping_before = await client.call("connection.ping", read_only=True)
        versions = ping_before["capability_versions"]
        required = {
            "transactions": 3,
            "scene_inspection": 1,
            "object_authoring": 1,
            "material_authoring": 1,
            "image_assets": 1,
            "world_authoring": 1,
            "render_preview": 1,
            "render_export": 1,
        }
        if any(int(versions.get(name, 0)) < version for name, version in required.items()):
            raise RuntimeError("managed add-on did not advertise the 0.8 authoring vector")
        report["ping_before"] = ping_before

        opened = await manager.project_open(
            str(project),
            save_current=False,
            use_scripts=False,
            load_ui=False,
        )
        report["project_open"] = opened
        await deterministic_transaction_checks(client, manager, report)
        await build_moonlit_scene(
            client,
            manager,
            wave_path,
            stars_path,
            temporary_root,
            artifact_directory,
            report,
        )
        ping_after = await client.call("connection.ping", read_only=True)
        if int(ping_after["heartbeat"]) <= int(ping_before["heartbeat"]):
            raise RuntimeError("Blender UI heartbeat did not advance during authoring")
        report["ping_after"] = ping_after
        report["source_sha256_after"] = sha256(source)
        report["source_unchanged"] = report["source_sha256_after"] == source_hash_before
        if not report["source_unchanged"]:
            raise RuntimeError("blank source fixture changed during live acceptance")
        report["status"] = "passed"
        report["completed_at"] = datetime.now(UTC).isoformat()
        report["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 3)
        return report
    finally:
        if launched:
            with contextlib.suppress(Exception):
                await manager.quit(save_current=False)
        await manager.close()
        if previous_test_hooks is None:
            os.environ.pop("BLENDER_RESEARCH_MCP_TEST_HOOKS", None)
        else:
            os.environ["BLENDER_RESEARCH_MCP_TEST_HOOKS"] = previous_test_hooks


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--blender-executable", type=Path, required=True)
    parser.add_argument("--port", type=int, default=9880)
    parser.add_argument("--timeout", type=float, default=90.0)
    args = parser.parse_args()
    report = asyncio.run(run(args))
    report_path = Path(report["artifact_directory"]) / "report-0.8.0.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(report_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
