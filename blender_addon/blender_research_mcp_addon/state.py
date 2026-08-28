"""Main-thread state and semantic command dispatch."""

from __future__ import annotations

import contextlib
import time
import uuid
from collections import OrderedDict
from collections.abc import Iterator
from typing import Any

import bpy

from .context_ops import (
    ContextOperationError,
    capture_context,
    capture_viewport,
    context_summary,
    inspect_object,
    restore_context,
)
from .runtime import ADDON_VERSION, ListenerRuntime
from .wire import PROTOCOL_VERSION

CAPABILITIES = [
    "connection.ping",
    "context.get",
    "context.snapshot",
    "context.restore",
    "object.inspect",
    "viewport.capture",
]


class AddonState:
    def __init__(self) -> None:
        self.runtime = ListenerRuntime()
        self.scene_generation = 0
        self.heartbeat = 0
        self.active_command = ""
        self.last_command_ms = 0.0
        self.last_error = ""
        self.snapshots: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._suppress_generation = 0

    def start(self) -> None:
        self.runtime.start()

    def stop(self) -> None:
        self.runtime.stop()
        self.snapshots.clear()

    def restart(self) -> None:
        self.stop()
        self.start()

    def tick(self) -> None:
        self.heartbeat += 1
        self.runtime.poll(self.dispatch, self.on_disconnect)

    def on_disconnect(self) -> None:
        pass

    def on_file_loaded(self) -> None:
        self.snapshots.clear()
        self.scene_generation += 1

    def on_depsgraph_update(self) -> None:
        if self._suppress_generation == 0:
            self.scene_generation += 1

    @contextlib.contextmanager
    def suppress_generation(self) -> Iterator[None]:
        self._suppress_generation += 1
        try:
            yield
        finally:
            self._suppress_generation -= 1

    def dispatch(self, request: dict[str, Any]) -> dict[str, Any]:
        request_id = request.get("request_id")
        started = time.perf_counter()
        self.active_command = str(request.get("command", ""))
        try:
            if request.get("protocol") != PROTOCOL_VERSION:
                return self._error(
                    request_id,
                    "protocol_version",
                    "PROTOCOL_MISMATCH",
                    f"Protocol {request.get('protocol')} is not supported",
                )
            params = request.get("params", {})
            if not isinstance(params, dict):
                return self._error(
                    request_id,
                    "validation",
                    "PARAMS_INVALID",
                    "params must be a JSON object",
                )
            result = self._handle(str(request.get("command", "")), params)
            return {
                "protocol": PROTOCOL_VERSION,
                "request_id": request_id,
                "ok": True,
                "scene_generation": self.scene_generation,
                "result": result,
            }
        except ContextOperationError as exc:
            self.last_error = f"{exc.code}: {exc}"
            return self._error(request_id, exc.kind, exc.code, str(exc))
        except Exception as exc:  # noqa: BLE001 - dispatch boundary
            self.last_error = f"{type(exc).__name__}: {exc}"
            return self._error(
                request_id,
                "blender_api",
                "BLENDER_COMMAND_FAILED",
                f"Blender command failed: {type(exc).__name__}",
            )
        finally:
            self.last_command_ms = (time.perf_counter() - started) * 1000
            self.active_command = ""

    def _handle(self, command: str, params: dict[str, Any]) -> dict[str, Any]:
        if command == "connection.hello":
            if params.get("expected_instance_id") != self.runtime.instance_id:
                raise ContextOperationError(
                    "INSTANCE_MISMATCH",
                    "Handshake instance does not match the running add-on",
                    kind="protocol_version",
                )
            if not (
                params.get("protocol_min") == PROTOCOL_VERSION
                and params.get("protocol_max") == PROTOCOL_VERSION
            ):
                raise ContextOperationError(
                    "PROTOCOL_MISMATCH",
                    "Client and add-on have no common protocol version",
                    kind="protocol_version",
                )
            return {
                "protocol": PROTOCOL_VERSION,
                "instance_id": self.runtime.instance_id,
                "blender_version": bpy.app.version_string,
                "addon_version": ADDON_VERSION,
                "capabilities": CAPABILITIES,
            }
        if command == "connection.ping":
            return {
                "protocol": PROTOCOL_VERSION,
                "instance_id": self.runtime.instance_id,
                "blender_version": bpy.app.version_string,
                "addon_version": ADDON_VERSION,
                "capabilities": CAPABILITIES,
                "heartbeat": self.heartbeat,
                "last_command_ms": self.last_command_ms,
            }
        if command == "context.get":
            with self.suppress_generation():
                return context_summary()
        if command == "context.snapshot":
            with self.suppress_generation():
                snapshot = capture_context(params.get("viewport_id"))
            snapshot_id = str(uuid.uuid4())
            self.snapshots[snapshot_id] = snapshot
            self.snapshots.move_to_end(snapshot_id)
            while len(self.snapshots) > 32:
                self.snapshots.popitem(last=False)
            return {"snapshot_id": snapshot_id, "context": snapshot}
        if command == "context.restore":
            snapshot_id = params.get("snapshot_id")
            snapshot = self.snapshots.get(str(snapshot_id))
            if snapshot is None:
                raise ContextOperationError(
                    "SNAPSHOT_NOT_FOUND",
                    f"Context snapshot does not exist: {snapshot_id}",
                    kind="not_found",
                )
            with self.suppress_generation():
                restore_context(snapshot)
            return {"snapshot_id": snapshot_id, "restored": True, "context": snapshot}
        if command == "object.inspect":
            object_name = params.get("object_name")
            if not isinstance(object_name, str) or not object_name:
                raise ContextOperationError(
                    "OBJECT_NAME_INVALID",
                    "object_name must be a non-empty string",
                    kind="validation",
                )
            return inspect_object(object_name)
        if command == "viewport.capture":
            object_name = params.get("object_name")
            if not isinstance(object_name, str) or not object_name:
                raise ContextOperationError(
                    "OBJECT_NAME_INVALID",
                    "object_name must be a non-empty string",
                    kind="validation",
                )
            with self.suppress_generation():
                return capture_viewport(
                    object_name,
                    str(params.get("view", "CURRENT")),
                    int(params.get("max_size", 800)),
                    params.get("viewport_id"),
                )
        raise ContextOperationError(
            "COMMAND_NOT_FOUND",
            f"Unsupported command: {command}",
            kind="not_found",
        )

    def _error(
        self,
        request_id: Any,
        kind: str,
        code: str,
        message: str,
    ) -> dict[str, Any]:
        return {
            "protocol": PROTOCOL_VERSION,
            "request_id": request_id,
            "ok": False,
            "scene_generation": self.scene_generation,
            "error": {
                "kind": kind,
                "code": code,
                "message": message,
                "retryable": False,
                "details": {},
            },
        }
