"""Run the Blender 4.2 managed application/project lifecycle acceptance."""

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
from blender_research_mcp.lifecycle import ApplicationManager

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT.parent / "blender-projects" / "test-model.blend"
AUTORUN_FIXTURE_BUILDER = ROOT / "scripts" / "create_lifecycle_autorun_fixture.py"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def choose_object(context: dict[str, Any]) -> str:
    active = context.get("active_object")
    if isinstance(active, str) and active:
        return active
    selected = context.get("selected_objects")
    if isinstance(selected, list) and selected and isinstance(selected[0], str):
        return selected[0]
    raise RuntimeError("lifecycle smoke requires one active or selected object")


def stage(name: str) -> None:
    print(f"[0.7 smoke] {name}", flush=True)


def is_blender_4_2_23(version: object) -> bool:
    return isinstance(version, str) and version.startswith("4.2.23")


def create_autorun_fixture(
    blender_executable: Path,
    output: Path,
    marker: Path,
    token: str,
    log_path: Path,
) -> None:
    environment = dict(os.environ)
    environment["BLENDER_USER_CONFIG"] = str(output.parent / "fixture-config")
    environment["BLENDER_USER_SCRIPTS"] = str(output.parent / "fixture-scripts")
    command = [
        str(blender_executable.resolve(strict=True)),
        "--background",
        "--factory-startup",
        "--python-exit-code",
        "1",
        "--python",
        str(AUTORUN_FIXTURE_BUILDER),
        "--",
        "--output",
        str(output),
        "--marker",
        str(marker),
        "--token",
        token,
    ]
    with log_path.open("wb") as log:
        result = subprocess.run(  # noqa: S603 - fixed Blender executable and script
            command,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            env=environment,
            shell=False,
            check=False,
        )
    if result.returncode != 0 or not output.is_file():
        raise RuntimeError(
            f"Could not build trusted-script fixture; see {log_path}"
        )
    if marker.exists():
        raise RuntimeError("trusted script ran while building its fixture")


async def begin_scale_preview(
    client: BridgeClient,
    object_name: str,
    factor: float,
    label: str,
) -> tuple[dict[str, Any], dict[str, Any], float]:
    ping = await client.call("connection.ping", read_only=True)
    before = await client.call(
        "object.inspect",
        {"object_name": object_name},
        read_only=True,
    )
    original = float(before["scale"][0])
    target = original * factor
    transaction = await client.call(
        "transaction.begin",
        {"label": label, "viewport_id": None},
        expected_scene_generation=int(ping["scene_generation"]),
        idempotency_key=str(uuid4()),
        read_only=False,
    )
    writer = await client.call(
        "object.transform",
        {
            "transaction_id": transaction["transaction_id"],
            "object_name": object_name,
            "scale": {"x": target},
        },
        expected_scene_generation=int(transaction["scene_generation"]),
        idempotency_key=str(uuid4()),
        read_only=False,
    )
    return transaction, writer, original


async def run(args: argparse.Namespace) -> dict[str, Any]:
    source = args.source.resolve(strict=True)
    if source.suffix.lower() != ".blend":
        raise RuntimeError("--source must be a .blend file")
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ") + f"-{uuid4().hex[:8]}"
    temporary_root = Path(tempfile.gettempdir()) / "blender-research-mcp-lifecycle" / run_id
    temporary_root.mkdir(parents=True, exist_ok=False)
    artifact_directory = ROOT / "artifacts" / "live-smoke" / run_id
    artifact_directory.mkdir(parents=True, exist_ok=False)
    target_a = temporary_root / "target-a.blend"
    target_b = temporary_root / "target-b.blend"
    current_save = temporary_root / "startup-current.blend"
    save_as = temporary_root / "saved-as.blend"
    autorun_project = temporary_root / "trusted-autorun.blend"
    autorun_marker = temporary_root / "trusted-autorun.marker"
    autorun_token = f"trusted-{run_id}"
    source_hash_before = sha256(source)
    shutil.copy2(source, target_a)
    shutil.copy2(source, target_b)
    if sha256(target_a) != source_hash_before or sha256(target_b) != source_hash_before:
        raise RuntimeError("temporary project copies do not match the source hash")

    client = BridgeClient(port=args.port)
    manager = ApplicationManager(
        client,
        blender_executable=str(args.blender_executable),
        launch_timeout=args.timeout,
    )
    launched = False
    started = time.perf_counter()
    report: dict[str, Any] = {
        "run_id": run_id,
        "started_at": datetime.now(UTC).isoformat(),
        "source": str(source),
        "source_sha256_before": source_hash_before,
        "temporary_root": str(temporary_root),
        "artifact_directory": str(artifact_directory),
        "operations": [],
    }
    stage("building trusted project-script fixture")
    autorun_log = artifact_directory / "autorun-fixture.log"
    create_autorun_fixture(
        args.blender_executable,
        autorun_project,
        autorun_marker,
        autorun_token,
        autorun_log,
    )
    report["autorun_fixture"] = {
        "project": str(autorun_project),
        "marker": str(autorun_marker),
        "builder_log": str(autorun_log),
    }
    try:
        stage("confirming no existing MCP Blender session")
        status_before = await manager.status()
        if status_before["running"]:
            raise RuntimeError("port already advertises a Blender MCP session")
        report["status_before"] = status_before

        stage("launching managed Blender")
        launch = await manager.launch()
        launched = True
        report["launch"] = launch
        if launch["status"] != "launched":
            raise RuntimeError("cold application launch did not return launched")
        application = launch["application"]
        if application["addon_version"] != "0.7.0":
            raise RuntimeError("managed launch did not load the 0.7.0 add-on")
        if not is_blender_4_2_23(application["blender_version"]):
            raise RuntimeError("managed launch did not use Blender 4.2.23")
        capabilities = application["capability_versions"]
        if capabilities["project_lifecycle"] < 1 or capabilities["application_lifecycle"] < 1:
            raise RuntimeError("managed lifecycle capabilities are missing")
        stage(f"managed Blender ready pid={application['pid']}")
        reused = await manager.launch()
        if reused["status"] != "reused" or reused["application"]["pid"] != application["pid"]:
            raise RuntimeError("repeated application.launch did not reuse the session")
        report["reused_launch"] = reused

        stage("saving startup project and opening target with transaction commit")
        ping_start = await client.call("connection.ping", read_only=True)
        initial_save = await manager.project_save(str(current_save))
        report["operations"].append({"initial_save": initial_save})
        context = await client.call("context.get", read_only=True)
        startup_object = choose_object(context)
        startup_tx, startup_writer, _startup_original = await begin_scale_preview(
            client,
            startup_object,
            1.03125,
            "lifecycle:commit-before-open",
        )
        pre_open_status = await manager.project_status()
        pre_open_object = await client.call(
            "object.inspect",
            {"object_name": startup_object},
            read_only=True,
        )
        current_hash_before_open = sha256(current_save)
        opened = await manager.project_open(str(target_a))
        if opened["status"] != "opened":
            raise RuntimeError("default project.open did not report opened")
        if not opened.get("transaction") or opened["transaction"]["status"] != "committed":
            raise RuntimeError("project.open did not commit the active transaction")
        if opened.get("save", {}).get("status") != "saved":
            raise RuntimeError(
                "project.open did not save the dirty current project: "
                + json.dumps(
                    {
                        "pre_open_status": pre_open_status,
                        "pre_open_object": pre_open_object,
                        "save": opened.get("save"),
                    },
                    ensure_ascii=False,
                )
            )
        if sha256(current_save) == current_hash_before_open:
            raise RuntimeError("current project hash did not change after committed preview save")
        report["operations"].append(
            {
                "open_with_commit": opened,
                "transaction": startup_tx,
                "writer": startup_writer,
                "pre_open_status": pre_open_status,
                "pre_open_object": pre_open_object,
            }
        )

        stage("checking save, Save As, and already-open behavior")
        explicit_save = await manager.project_save()
        save_as_result = await manager.project_save(str(save_as))
        same_project = await manager.project_open(str(save_as))
        if same_project["status"] != "already_open":
            raise RuntimeError("opening the current path did not return already_open")
        report["operations"].append(
            {
                "explicit_save": explicit_save,
                "save_as": save_as_result,
                "same_project": same_project,
            }
        )

        stage("checking reload discard and reload save behavior")
        current_context = await client.call("context.get", read_only=True)
        reload_object = choose_object(current_context)
        _discard_tx, discard_writer, discard_original = await begin_scale_preview(
            client,
            reload_object,
            1.021,
            "lifecycle:reload-discard",
        )
        reload_discard = await manager.project_reload()
        discarded = await client.call(
            "object.inspect",
            {"object_name": reload_object},
            read_only=True,
        )
        if abs(float(discarded["scale"][0]) - discard_original) > 1e-6:
            raise RuntimeError("default reload did not discard the unsaved preview")
        if reload_discard["transaction"] is not None:
            raise RuntimeError("reload(save_current=false) unexpectedly committed the transaction")
        report["operations"].append(
            {"reload_discard": reload_discard, "writer": discard_writer}
        )

        _keep_tx, keep_writer, keep_original = await begin_scale_preview(
            client,
            reload_object,
            1.017,
            "lifecycle:reload-save",
        )
        reload_saved = await manager.project_reload(save_current=True)
        retained = await client.call(
            "object.inspect",
            {"object_name": reload_object},
            read_only=True,
        )
        expected_retained = keep_original * 1.017
        if abs(float(retained["scale"][0]) - expected_retained) > 1e-6:
            raise RuntimeError("reload(save_current=true) did not retain the committed preview")
        if (
            not reload_saved.get("transaction")
            or reload_saved["transaction"]["status"] != "committed"
        ):
            raise RuntimeError("saved reload did not commit the active transaction")
        report["operations"].append(
            {"reload_saved": reload_saved, "writer": keep_writer}
        )

        stage("checking project script and saved-UI loading flags")
        scripts_disabled = await manager.project_open(
            str(autorun_project),
            use_scripts=False,
            load_ui=False,
        )
        disabled_operation = scripts_disabled["after"]["last_operation"]
        if (
            disabled_operation["use_scripts"] is not False
            or disabled_operation["load_ui"] is not False
        ):
            raise RuntimeError("project.open did not preserve disabled script/UI intent")
        if autorun_marker.exists():
            raise RuntimeError("use_scripts=false executed the trusted project script")
        await manager.project_open(str(target_b))
        scripts_enabled = await manager.project_open(str(autorun_project))
        enabled_operation = scripts_enabled["after"]["last_operation"]
        if enabled_operation["use_scripts"] is not True or enabled_operation["load_ui"] is not True:
            raise RuntimeError("project.open defaults did not enable scripts and saved UI")
        marker_deadline = time.monotonic() + 2.0
        while time.monotonic() < marker_deadline and not autorun_marker.exists():
            await asyncio.sleep(0.05)
        marker_value = (
            autorun_marker.read_text(encoding="utf-8") if autorun_marker.exists() else None
        )
        if marker_value != autorun_token:
            raise RuntimeError("use_scripts=true did not execute the trusted project script")
        report["operations"].append(
            {
                "scripts_disabled": scripts_disabled,
                "scripts_enabled": scripts_enabled,
                "trusted_script_marker": {
                    "path": str(autorun_marker),
                    "token": autorun_token,
                },
            }
        )

        ping_end = await client.call("connection.ping", read_only=True)
        if int(ping_end["heartbeat"]) <= int(ping_start["heartbeat"]):
            raise RuntimeError("Blender UI heartbeat did not advance")
        report["heartbeat"] = {
            "before": ping_start["heartbeat"],
            "after": ping_end["heartbeat"],
        }
        stage("saving and quitting the first managed session")
        quit_result = await manager.quit()
        launched = False
        report["quit"] = quit_result
        status_after_quit = await manager.status()
        if status_after_quit["running"]:
            raise RuntimeError("application.status remained running after quit")
        report["status_after_quit"] = status_after_quit

        stage("launching a second cold managed session")
        second_launch = await manager.launch()
        launched = True
        if second_launch["status"] != "launched":
            raise RuntimeError("second cold launch did not return launched")
        report["second_launch"] = second_launch
        stage("quitting the second session without saving")
        report["second_quit"] = await manager.quit(save_current=False)
        launched = False

        source_hash_after = sha256(source)
        report["source_sha256_after"] = source_hash_after
        report["source_unchanged"] = source_hash_after == source_hash_before
        if not report["source_unchanged"]:
            raise RuntimeError("source blend file changed during lifecycle smoke")
        report["completed_at"] = datetime.now(UTC).isoformat()
        report["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 3)
        report["status"] = "passed"
        stage("passed")
        return report
    finally:
        if launched:
            with contextlib.suppress(Exception):
                await manager.quit(save_current=False)
        await manager.close()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--blender-executable", type=Path, required=True)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--port", type=int, default=9877)
    parser.add_argument("--timeout", type=float, default=90.0)
    args = parser.parse_args()
    report = asyncio.run(run(args))
    artifact_directory = Path(report["artifact_directory"])
    report_path = artifact_directory / "report-0.7.0.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(report_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
