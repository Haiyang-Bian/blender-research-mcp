import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from blender_research_mcp.errors import BridgeError, ErrorKind, bridge_error
from blender_research_mcp.lifecycle import (
    ADDON_RESOURCE_ENV,
    BLENDER_EXECUTABLE_ENV,
    LAUNCH_ID_ENV,
    LAUNCH_TIMEOUT_ENV,
    PORT_ENV,
    ApplicationManager,
    materialize_managed_resources,
    resolve_blender_executable,
    resolve_launch_timeout,
)
from blender_research_mcp.protocol import CapabilityVersions, HandshakeResult


def test_executable_resolution_uses_cli_env_then_path(tmp_path: Path) -> None:
    cli = tmp_path / "cli-blender.exe"
    env = tmp_path / "env-blender.exe"
    path = tmp_path / "path-blender.exe"
    for candidate in (cli, env, path):
        candidate.write_bytes(b"")

    assert resolve_blender_executable(
        str(cli),
        environ={BLENDER_EXECUTABLE_ENV: str(env)},
        which=lambda _name: str(path),
    ) == cli
    assert resolve_blender_executable(
        None,
        environ={BLENDER_EXECUTABLE_ENV: str(env)},
        which=lambda _name: str(path),
    ) == env
    assert resolve_blender_executable(None, environ={}, which=lambda _name: str(path)) == path


def test_executable_resolution_reports_missing_configuration_and_bad_path(tmp_path: Path) -> None:
    with pytest.raises(BridgeError) as missing:
        resolve_blender_executable(None, environ={}, which=lambda _name: None)
    assert missing.value.error.code == "BLENDER_EXECUTABLE_NOT_CONFIGURED"

    bad = tmp_path / "missing.exe"
    with pytest.raises(BridgeError) as invalid:
        resolve_blender_executable(str(bad), environ={})
    assert invalid.value.error.code == "BLENDER_EXECUTABLE_NOT_FOUND"
    assert invalid.value.error.details["source"] == "cli"


def test_launch_timeout_uses_cli_then_environment() -> None:
    assert resolve_launch_timeout(12, environ={LAUNCH_TIMEOUT_ENV: "20"}) == 12
    assert resolve_launch_timeout(None, environ={LAUNCH_TIMEOUT_ENV: "20"}) == 20
    assert resolve_launch_timeout(None, environ={}) == 90
    with pytest.raises(ValueError):
        resolve_launch_timeout(None, environ={LAUNCH_TIMEOUT_ENV: "invalid"})
    with pytest.raises(ValueError):
        resolve_launch_timeout(0.5, environ={})


def test_managed_resources_are_versioned_hashed_and_repeatable(tmp_path: Path) -> None:
    first = materialize_managed_resources(
        base_directory=tmp_path,
        release_version="0.7.0",
    )
    second = materialize_managed_resources(
        base_directory=tmp_path,
        release_version="0.7.0",
    )

    assert first == second
    assert first.root == tmp_path / "0.7.0" / first.content_hash
    assert first.bootstrap.is_file()
    assert (first.addon_path / "blender_research_mcp_addon" / "__init__.py").is_file()


class FakeProcess:
    pid = 4321

    def __init__(self) -> None:
        self.exit_code: int | None = None

    def poll(self) -> int | None:
        return self.exit_code


class FakeClient:
    port = 9877

    def __init__(self) -> None:
        self.running = False
        self.manifest = None
        self.handshake = HandshakeResult(
            protocol=1,
            instance_id="managed-instance",
            blender_version="4.2.23",
            addon_version="0.7.0",
            capabilities=["project.status"],
            capability_versions=CapabilityVersions(
                transport=1,
                context=1,
                viewport_capture=3,
                viewport_raycast=1,
                geometry_inspection=1,
                lookdev_inspection=1,
                transactions=2,
                object_transform_scale=1,
                object_visibility=1,
                modifier_state=1,
                shape_key_value=1,
                material_input=1,
                project_lifecycle=1,
                application_lifecycle=1,
            ),
        )

    async def connect(self):
        if not self.running:
            raise bridge_error(ErrorKind.UNAVAILABLE, "SESSION_NOT_FOUND", "not running")
        return self.handshake

    async def close(self) -> None:
        return None

    def require_capability(self, name: str, version: int = 1) -> None:
        actual = self.handshake.capability_versions.model_dump()[name]
        assert actual >= version

    async def call(self, command: str, *, read_only: bool):
        assert read_only is True
        assert command == "project.status"
        return {
            "filepath": "",
            "is_saved": False,
            "is_dirty": False,
            "scene_generation": 0,
        }


def test_managed_launch_uses_exact_argv_environment_and_coalesces(tmp_path: Path) -> None:
    blender = tmp_path / "blender.exe"
    blender.write_bytes(b"")
    client = FakeClient()
    calls: list[dict[str, object]] = []
    process = FakeProcess()

    def process_factory(argv, **kwargs):
        calls.append({"argv": argv, **kwargs})
        environment = kwargs["env"]
        client.running = True
        client.manifest = SimpleNamespace(
            pid=process.pid,
            instance_id="managed-instance",
            port=9877,
            launch_id=environment[LAUNCH_ID_ENV],
        )
        return process

    manager = ApplicationManager(
        client,  # type: ignore[arg-type]
        blender_executable=str(blender),
        launch_timeout=1,
        process_factory=process_factory,
        environ={"LOCALAPPDATA": str(tmp_path)},
        resource_base=tmp_path / "resources",
    )

    async def launch_twice():
        return await asyncio.gather(manager.launch(), manager.launch())

    first, second = asyncio.run(launch_twice())

    assert {first["status"], second["status"]} == {"launched", "reused"}
    assert len(calls) == 1
    call = calls[0]
    argv = call["argv"]
    assert argv[0] == str(blender)
    assert argv[1] == "--python"
    assert Path(argv[2]).name == "managed_bootstrap.py"
    assert call["shell"] is False
    environment = call["env"]
    assert environment[PORT_ENV] == "9877"
    assert environment[ADDON_RESOURCE_ENV].endswith("addon")
    assert environment[LAUNCH_ID_ENV] == first["launch_id"]


def test_launch_reports_early_process_exit(tmp_path: Path) -> None:
    blender = tmp_path / "blender.exe"
    blender.write_bytes(b"")
    client = FakeClient()
    process = FakeProcess()
    process.exit_code = 12

    manager = ApplicationManager(
        client,  # type: ignore[arg-type]
        blender_executable=str(blender),
        launch_timeout=1,
        process_factory=lambda *_args, **_kwargs: process,
        environ={"LOCALAPPDATA": str(tmp_path)},
        resource_base=tmp_path / "resources",
    )

    with pytest.raises(BridgeError) as failed:
        asyncio.run(manager.launch())
    assert failed.value.error.code == "APPLICATION_LAUNCH_FAILED"
    assert failed.value.error.details["exit_code"] == 12
    assert Path(failed.value.error.details["log_path"]).is_file()
