"""External orchestration for managed Blender application sessions."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

from blender_research_mcp.client import BridgeClient
from blender_research_mcp.constants import PACKAGE_VERSION, SESSION_DIRECTORY_NAME
from blender_research_mcp.errors import BridgeError, ErrorKind, bridge_error
from blender_research_mcp.session import manifest_candidates, pid_exists

BLENDER_EXECUTABLE_ENV = "BLENDER_RESEARCH_MCP_BLENDER_EXECUTABLE"
LAUNCH_TIMEOUT_ENV = "BLENDER_RESEARCH_MCP_LAUNCH_TIMEOUT_SECONDS"
PORT_ENV = "BLENDER_RESEARCH_MCP_PORT"
LAUNCH_ID_ENV = "BLENDER_RESEARCH_MCP_LAUNCH_ID"
ADDON_RESOURCE_ENV = "BLENDER_RESEARCH_MCP_ADDON_RESOURCE_PATH"
DEFAULT_LAUNCH_TIMEOUT_SECONDS = 90.0


class ManagedProcess(Protocol):
    pid: int

    def poll(self) -> int | None: ...


ProcessFactory = Callable[..., ManagedProcess]


@dataclass(frozen=True)
class ManagedResources:
    root: Path
    bootstrap: Path
    addon_path: Path
    content_hash: str


def _configured_path(value: str, *, source: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        resolved = shutil.which(value)
        if resolved is not None:
            path = Path(resolved)
    path = path.resolve()
    if not path.is_file():
        raise bridge_error(
            ErrorKind.NOT_FOUND,
            "BLENDER_EXECUTABLE_NOT_FOUND",
            f"Configured Blender executable does not exist: {path}",
            details={"source": source, "path": str(path)},
        )
    return path


def resolve_blender_executable(
    cli_value: str | None,
    *,
    environ: Mapping[str, str] | None = None,
    which: Callable[[str], str | None] = shutil.which,
) -> Path:
    """Resolve Blender using the documented CLI, environment, then PATH order."""
    env = os.environ if environ is None else environ
    if cli_value:
        return _configured_path(cli_value, source="cli")
    configured = env.get(BLENDER_EXECUTABLE_ENV)
    if configured:
        return _configured_path(configured, source="environment")
    discovered = which("blender")
    if discovered:
        return _configured_path(discovered, source="PATH")
    raise bridge_error(
        ErrorKind.PRECONDITION,
        "BLENDER_EXECUTABLE_NOT_CONFIGURED",
        (
            "Configure --blender-executable, set "
            f"{BLENDER_EXECUTABLE_ENV}, or add Blender to PATH"
        ),
    )


def resolve_launch_timeout(
    cli_value: float | None,
    *,
    environ: Mapping[str, str] | None = None,
) -> float:
    env = os.environ if environ is None else environ
    raw: float | str = (
        cli_value
        if cli_value is not None
        else env.get(LAUNCH_TIMEOUT_ENV, DEFAULT_LAUNCH_TIMEOUT_SECONDS)
    )
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{LAUNCH_TIMEOUT_ENV} must be a number") from exc
    if not 1.0 <= value <= 600.0:
        raise ValueError("launch timeout must be between 1 and 600 seconds")
    return value


def _development_addon_source() -> Path:
    return Path(__file__).resolve().parents[2] / "blender_addon" / "blender_research_mcp_addon"


def addon_resource_source() -> Path:
    packaged = Path(__file__).resolve().parent / "managed_addon" / "blender_research_mcp_addon"
    if packaged.is_dir():
        return packaged
    development = _development_addon_source()
    if development.is_dir():
        return development
    raise bridge_error(
        ErrorKind.INTERNAL,
        "APPLICATION_LAUNCH_FAILED",
        "Managed Blender add-on resources are missing from this installation",
    )


def _resource_digest(addon_source: Path, bootstrap_source: Path) -> str:
    digest = hashlib.sha256()
    for path in [bootstrap_source, *sorted(addon_source.rglob("*.py"))]:
        relative = (
            path.name
            if path == bootstrap_source
            else path.relative_to(addon_source).as_posix()
        )
        digest.update(relative.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def materialize_managed_resources(
    *,
    base_directory: Path | None = None,
    release_version: str | None = None,
) -> ManagedResources:
    """Atomically materialize fixed bootstrap and add-on resources for one release."""
    addon_source = addon_resource_source()
    bootstrap_source = Path(__file__).resolve().parent / "resources" / "managed_bootstrap.py"
    version = release_version or PACKAGE_VERSION
    content_hash = _resource_digest(addon_source, bootstrap_source)
    if base_directory is None:
        local_app_data = os.environ.get("LOCALAPPDATA")
        base_directory = (
            Path(local_app_data) / SESSION_DIRECTORY_NAME / "managed"
            if local_app_data
            else Path(tempfile.gettempdir()) / SESSION_DIRECTORY_NAME / "managed"
        )
    target = base_directory / version / content_hash
    bootstrap_target = target / "managed_bootstrap.py"
    addon_target = target / "addon" / "blender_research_mcp_addon"
    if not bootstrap_target.is_file() or not addon_target.is_dir():
        if target.exists():
            shutil.rmtree(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.parent / f".{content_hash}-{uuid4().hex}.tmp"
        temporary.mkdir(parents=False)
        try:
            shutil.copy2(bootstrap_source, temporary / "managed_bootstrap.py")
            shutil.copytree(addon_source, temporary / "addon" / addon_source.name)
            try:
                os.replace(temporary, target)
            except FileExistsError:
                shutil.rmtree(temporary)
                if not bootstrap_target.is_file() or not addon_target.is_dir():
                    raise
        except Exception:
            shutil.rmtree(temporary, ignore_errors=True)
            raise
    return ManagedResources(
        root=target,
        bootstrap=bootstrap_target,
        addon_path=addon_target.parent,
        content_hash=content_hash,
    )


class ApplicationManager:
    """Launch, identify, and summarize one Blender MCP session per server port."""

    def __init__(
        self,
        client: BridgeClient,
        *,
        blender_executable: str | None = None,
        launch_timeout: float = DEFAULT_LAUNCH_TIMEOUT_SECONDS,
        process_factory: ProcessFactory = subprocess.Popen,
        environ: Mapping[str, str] | None = None,
        resource_base: Path | None = None,
    ) -> None:
        self.client = client
        self.blender_executable = blender_executable
        self.launch_timeout = launch_timeout
        self.process_factory = process_factory
        self.environ = dict(os.environ if environ is None else environ)
        self.resource_base = resource_base
        self._launch_lock = asyncio.Lock()
        self._process: ManagedProcess | None = None

    async def close(self) -> None:
        await self.client.close()

    async def status(self) -> dict[str, Any]:
        try:
            handshake = await self.client.connect()
        except BridgeError as exc:
            if exc.error.code in {
                "SESSION_NOT_FOUND",
                "SESSION_STALE",
                "CONNECT_FAILED",
                "CONNECTION_LOST",
            }:
                await self.client.close()
                return {"running": False, "port": self.client.port}
            raise
        manifest = self.client.manifest
        assert manifest is not None
        project: dict[str, Any] | None = None
        capabilities = handshake.capability_versions.model_dump()
        if int(capabilities.get("project_lifecycle", 0)) >= 1:
            project = await self.client.call("project.status", read_only=True)
        return {
            "running": True,
            "pid": manifest.pid,
            "instance_id": manifest.instance_id,
            "blender_version": handshake.blender_version,
            "addon_version": handshake.addon_version,
            "port": manifest.port,
            "managed": manifest.launch_id is not None,
            "launch_id": manifest.launch_id,
            "project": project,
            "lifecycle_available": int(capabilities.get("application_lifecycle", 0)) >= 1,
            "capability_versions": capabilities,
        }

    async def _connect_lifecycle(self, capability: str) -> None:
        try:
            await self.client.connect()
        except BridgeError as exc:
            if exc.error.code in {
                "SESSION_NOT_FOUND",
                "SESSION_STALE",
                "CONNECT_FAILED",
                "CONNECTION_LOST",
            }:
                raise bridge_error(
                    ErrorKind.PRECONDITION,
                    "APPLICATION_NOT_RUNNING",
                    "Blender is not running with a compatible MCP session",
                ) from exc
            raise
        self.client.require_capability(capability, 1)

    @staticmethod
    def _project_path(
        value: str,
        *,
        must_exist: bool,
        parent_must_exist: bool = False,
    ) -> Path:
        path = Path(value)
        if not path.is_absolute() or path.suffix.lower() != ".blend":
            raise bridge_error(
                ErrorKind.VALIDATION,
                "PROJECT_PATH_INVALID",
                "Project path must be absolute and end in .blend",
                details={"path": value},
            )
        path = Path(os.path.realpath(path))
        if must_exist and not path.is_file():
            raise bridge_error(
                ErrorKind.NOT_FOUND,
                "PROJECT_NOT_FOUND",
                f"Blender project does not exist: {path}",
                details={"path": str(path)},
            )
        if parent_must_exist and not path.parent.is_dir():
            raise bridge_error(
                ErrorKind.VALIDATION,
                "PROJECT_PATH_INVALID",
                f"Project parent directory does not exist: {path.parent}",
                details={"path": str(path)},
            )
        return path

    async def project_status(self) -> dict[str, Any]:
        await self._connect_lifecycle("project_lifecycle")
        return await self.client.call("project.status", read_only=True)

    async def project_save(self, path: str | None = None) -> dict[str, Any]:
        await self._connect_lifecycle("project_lifecycle")
        target = (
            self._project_path(path, must_exist=False, parent_must_exist=True)
            if path is not None
            else None
        )
        return await self.client.call(
            "project.save",
            {"path": str(target) if target is not None else None},
            idempotency_key=str(uuid4()),
            read_only=False,
            deadline_ms=30_000,
        )

    async def _wait_for_project_operation(
        self,
        accepted: dict[str, Any],
        *,
        success_status: str,
    ) -> dict[str, Any]:
        operation_id = str(accepted["operation_id"])
        expected_path = str(accepted["path"])
        deadline = asyncio.get_running_loop().time() + self.launch_timeout
        last_status: dict[str, Any] | None = None
        await self.client.close()
        while asyncio.get_running_loop().time() < deadline:
            try:
                await self._connect_lifecycle("project_lifecycle")
                status = await self.client.call("project.status", read_only=True)
            except BridgeError as exc:
                if exc.error.code not in {
                    "APPLICATION_NOT_RUNNING",
                    "SESSION_NOT_FOUND",
                    "SESSION_STALE",
                    "CONNECT_FAILED",
                    "CONNECTION_LOST",
                    "REQUEST_TIMEOUT",
                }:
                    raise
                await self.client.close()
                await asyncio.sleep(0.1)
                continue
            last_status = status
            operation = status.get("last_operation")
            if isinstance(operation, dict) and operation.get("operation_id") == operation_id:
                if operation.get("status") == "failed":
                    raise bridge_error(
                        ErrorKind.BLENDER_API,
                        "PROJECT_OPEN_FAILED",
                        "Blender reported that the project lifecycle operation failed",
                        retryable=True,
                        details={"operation": operation, "accepted": accepted},
                    )
                if operation.get("status") == "succeeded":
                    actual_path = str(status.get("filepath") or "")
                    if os.path.normcase(os.path.realpath(actual_path)) != os.path.normcase(
                        os.path.realpath(expected_path)
                    ):
                        raise bridge_error(
                            ErrorKind.CONFLICT,
                            "PROJECT_PATH_MISMATCH",
                            "Blender opened a different project than requested",
                            details={
                                "expected_path": expected_path,
                                "actual_path": actual_path,
                                "operation": operation,
                            },
                        )
                    return {
                        "status": success_status,
                        "operation_id": operation_id,
                        "before": accepted.get("before"),
                        "after": status,
                        "transaction": accepted.get("transaction"),
                        "save": accepted.get("save"),
                        "path": actual_path,
                    }
            await asyncio.sleep(0.1)
        code = "PROJECT_OPEN_TIMEOUT"
        raise bridge_error(
            ErrorKind.TIMEOUT,
            code,
            f"Blender did not finish the project operation within {self.launch_timeout:g} seconds",
            retryable=True,
            details={"accepted": accepted, "last_status": last_status},
        )

    async def project_open(
        self,
        path: str,
        *,
        save_current: bool = True,
        save_current_as: str | None = None,
        use_scripts: bool = True,
        load_ui: bool = True,
    ) -> dict[str, Any]:
        await self._connect_lifecycle("project_lifecycle")
        target = self._project_path(path, must_exist=True)
        save_path = (
            self._project_path(
                save_current_as,
                must_exist=False,
                parent_must_exist=True,
            )
            if save_current_as is not None
            else None
        )
        accepted = await self.client.call(
            "project.open",
            {
                "path": str(target),
                "save_current": save_current,
                "save_current_as": str(save_path) if save_path is not None else None,
                "use_scripts": use_scripts,
                "load_ui": load_ui,
            },
            idempotency_key=str(uuid4()),
            read_only=False,
            deadline_ms=30_000,
        )
        if accepted.get("status") == "already_open":
            return accepted
        return await self._wait_for_project_operation(accepted, success_status="opened")

    async def project_reload(
        self,
        *,
        save_current: bool = False,
        use_scripts: bool = True,
        load_ui: bool = True,
    ) -> dict[str, Any]:
        await self._connect_lifecycle("project_lifecycle")
        accepted = await self.client.call(
            "project.reload",
            {
                "save_current": save_current,
                "use_scripts": use_scripts,
                "load_ui": load_ui,
            },
            idempotency_key=str(uuid4()),
            read_only=False,
            deadline_ms=30_000,
        )
        return await self._wait_for_project_operation(accepted, success_status="reloaded")

    @staticmethod
    def _instance_manifest_exists(port: int, instance_id: str) -> bool:
        for path in manifest_candidates(port):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if payload.get("instance_id") == instance_id:
                return True
        return False

    async def quit(
        self,
        *,
        save_current: bool = True,
        save_current_as: str | None = None,
    ) -> dict[str, Any]:
        await self._connect_lifecycle("application_lifecycle")
        manifest = self.client.manifest
        assert manifest is not None
        save_path = (
            self._project_path(
                save_current_as,
                must_exist=False,
                parent_must_exist=True,
            )
            if save_current_as is not None
            else None
        )
        accepted = await self.client.call(
            "application.quit",
            {
                "save_current": save_current,
                "save_current_as": str(save_path) if save_path is not None else None,
            },
            idempotency_key=str(uuid4()),
            read_only=False,
            deadline_ms=30_000,
        )
        await self.client.close()
        deadline = asyncio.get_running_loop().time() + self.launch_timeout
        while asyncio.get_running_loop().time() < deadline:
            managed_process = self._process
            if managed_process is not None and managed_process.pid == manifest.pid:
                process_running = managed_process.poll() is None
            else:
                process_running = pid_exists(manifest.pid)
            manifest_exists = self._instance_manifest_exists(
                manifest.port,
                manifest.instance_id,
            )
            if not process_running and not manifest_exists:
                self._process = None
                return {
                    "status": "quit",
                    "pid": manifest.pid,
                    "instance_id": manifest.instance_id,
                    "operation_id": accepted.get("operation_id"),
                    "before": accepted.get("before"),
                    "transaction": accepted.get("transaction"),
                    "save": accepted.get("save"),
                }
            await asyncio.sleep(0.1)
        raise bridge_error(
            ErrorKind.TIMEOUT,
            "APPLICATION_QUIT_TIMEOUT",
            f"Blender did not quit within {self.launch_timeout:g} seconds",
            retryable=True,
            details={
                "pid": manifest.pid,
                "instance_id": manifest.instance_id,
                "accepted": accepted,
            },
        )

    async def launch(self) -> dict[str, Any]:
        async with self._launch_lock:
            current = await self.status()
            if current["running"]:
                self.client.require_capability("application_lifecycle", 1)
                return {"status": "reused", "application": current}

            executable = resolve_blender_executable(
                self.blender_executable,
                environ=self.environ,
            )
            resources = materialize_managed_resources(base_directory=self.resource_base)
            launch_id = str(uuid4())
            log_directory = resources.root / "logs"
            log_directory.mkdir(parents=True, exist_ok=True)
            log_path = log_directory / f"launch-{launch_id}.log"
            child_env = dict(self.environ)
            child_env.update(
                {
                    PORT_ENV: str(self.client.port),
                    LAUNCH_ID_ENV: launch_id,
                    ADDON_RESOURCE_ENV: str(resources.addon_path),
                }
            )
            argv = [str(executable), "--python", str(resources.bootstrap)]
            try:
                with log_path.open("ab", buffering=0) as log:
                    process = self.process_factory(
                        argv,
                        env=child_env,
                        stdin=subprocess.DEVNULL,
                        stdout=log,
                        stderr=subprocess.STDOUT,
                        shell=False,
                    )
            except OSError as exc:
                raise bridge_error(
                    ErrorKind.UNAVAILABLE,
                    "APPLICATION_LAUNCH_FAILED",
                    f"Could not start Blender: {type(exc).__name__}",
                    details={"launch_id": launch_id, "log_path": str(log_path)},
                ) from exc
            self._process = process
            deadline = asyncio.get_running_loop().time() + self.launch_timeout
            last_error: BridgeError | None = None
            while asyncio.get_running_loop().time() < deadline:
                exit_code = process.poll()
                if exit_code not in {None, 0}:
                    raise bridge_error(
                        ErrorKind.UNAVAILABLE,
                        "APPLICATION_LAUNCH_FAILED",
                        "Blender exited before the managed MCP session became ready",
                        details={
                            "pid": process.pid,
                            "launch_id": launch_id,
                            "log_path": str(log_path),
                            "exit_code": exit_code,
                        },
                    )
                await self.client.close()
                try:
                    await self.client.connect()
                    manifest = self.client.manifest
                    if manifest is None or manifest.launch_id != launch_id:
                        await self.client.close()
                    else:
                        self.client.require_capability("application_lifecycle", 1)
                        status = await self.status()
                        return {
                            "status": "launched",
                            "pid": status["pid"],
                            "launcher_pid": process.pid,
                            "launch_id": launch_id,
                            "log_path": str(log_path),
                            "resource_hash": resources.content_hash,
                            "application": status,
                        }
                except BridgeError as exc:
                    if exc.error.code == "CAPABILITY_MISMATCH":
                        raise
                    last_error = exc
                    await self.client.close()
                await asyncio.sleep(0.1)
            details: dict[str, Any] = {
                "pid": process.pid,
                "launch_id": launch_id,
                "log_path": str(log_path),
            }
            if last_error is not None:
                details["last_error"] = last_error.error.model_dump(mode="json")
            raise bridge_error(
                ErrorKind.TIMEOUT,
                "APPLICATION_LAUNCH_TIMEOUT",
                f"Managed Blender did not become ready within {self.launch_timeout:g} seconds",
                retryable=True,
                details=details,
            )
