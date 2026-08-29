"""Authenticated listener and cross-thread request queue."""

from __future__ import annotations

import contextlib
import hmac
import json
import os
import queue
import secrets
import socket
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .wire import MAX_RESPONSE_BYTES, PROTOCOL_VERSION, FrameDecoder, FramingError, encode_frame

HOST = "127.0.0.1"
DEFAULT_PORT = 9877
ADDON_VERSION = "0.8.0"
ZERO_REQUEST_ID = "00000000-0000-0000-0000-000000000000"
LAUNCH_ID_ENV = "BLENDER_RESEARCH_MCP_LAUNCH_ID"


@dataclass
class PendingRequest:
    request: dict[str, Any]
    event: threading.Event
    response: dict[str, Any] | None = None
    cancelled: bool = False
    started: bool = False


def runtime_directory() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if local_app_data:
        return Path(local_app_data) / "blender-research-mcp" / "runtime"
    return Path.home() / ".cache" / "blender-research-mcp" / "runtime"


class ListenerRuntime:
    """Own the socket thread without importing or calling bpy."""

    def __init__(self, port: int = DEFAULT_PORT) -> None:
        self.port = port
        self.instance_id = ""
        self.session_token = ""
        self.status = "stopped"
        self.last_error = ""
        self.connected = False
        self.last_scene_generation = 0
        self._requests: queue.Queue[PendingRequest] = queue.Queue()
        self._disconnect_pending = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._listener: socket.socket | None = None
        self._client: socket.socket | None = None
        self._socket_lock = threading.Lock()

    @property
    def manifest_path(self) -> Path:
        return runtime_directory() / f"session-{self.port}.json"

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self.instance_id = str(uuid.uuid4())
        self.session_token = secrets.token_urlsafe(32)
        self.last_error = ""
        self.status = "starting"
        self._stop.clear()
        self._disconnect_pending.clear()
        self._thread = threading.Thread(
            target=self._run,
            name="BlenderResearchMCPListener",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        with self._socket_lock:
            for sock in (self._client, self._listener):
                if sock is not None:
                    with contextlib.suppress(OSError):
                        sock.shutdown(socket.SHUT_RDWR)
                    with contextlib.suppress(OSError):
                        sock.close()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        self._thread = None
        self._listener = None
        self._client = None
        self.connected = False
        self.status = "stopped"
        self._remove_manifest()
        while True:
            try:
                pending = self._requests.get_nowait()
            except queue.Empty:
                break
            pending.response = self._error_response(
                pending.request,
                kind="unavailable",
                code="LISTENER_STOPPED",
                message="Blender listener stopped before the request completed",
                retryable=True,
            )
            pending.event.set()

    def poll(
        self,
        dispatcher: Callable[[dict[str, Any]], dict[str, Any]],
        on_disconnect: Callable[[], None],
        max_requests: int = 4,
    ) -> None:
        if self._disconnect_pending.is_set():
            self._disconnect_pending.clear()
            on_disconnect()
        for _index in range(max_requests):
            try:
                pending = self._requests.get_nowait()
            except queue.Empty:
                return
            if pending.cancelled:
                pending.event.set()
                continue
            if self._stop.is_set():
                pending.response = self._error_response(
                    pending.request,
                    kind="unavailable",
                    code="LISTENER_STOPPED",
                    message="Blender listener is stopping",
                    retryable=True,
                )
            else:
                try:
                    pending.started = True
                    pending.response = dispatcher(pending.request)
                    self.last_scene_generation = int(
                        pending.response.get("scene_generation", self.last_scene_generation)
                    )
                except Exception as exc:  # noqa: BLE001 - boundary must always answer
                    pending.response = self._error_response(
                        pending.request,
                        kind="internal",
                        code="DISPATCH_FAILED",
                        message=f"Command dispatch failed: {type(exc).__name__}",
                    )
            pending.event.set()

    def _run(self) -> None:
        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            listener.bind((HOST, self.port))
            listener.listen(1)
            listener.settimeout(0.25)
            with self._socket_lock:
                self._listener = listener
            self._write_manifest()
            self.status = "listening"
            while not self._stop.is_set():
                try:
                    client, _address = listener.accept()
                except TimeoutError:
                    continue
                except OSError:
                    break
                with self._socket_lock:
                    self._client = client
                self.status = "client_connected"
                try:
                    self._serve_client(client)
                finally:
                    self.connected = False
                    self.status = "listening" if not self._stop.is_set() else "stopping"
                    self._disconnect_pending.set()
                    with self._socket_lock:
                        self._client = None
                    with contextlib.suppress(OSError):
                        client.close()
        except OSError as exc:
            self.last_error = f"{type(exc).__name__}: {exc}"
            self.status = "error"
        finally:
            with contextlib.suppress(OSError):
                listener.close()
            self._remove_manifest()

    def _serve_client(self, client: socket.socket) -> None:
        client.settimeout(0.25)
        decoder = FrameDecoder()
        while not self._stop.is_set():
            try:
                data = client.recv(64 * 1024)
            except TimeoutError:
                continue
            except OSError:
                return
            if not data:
                return
            try:
                requests = decoder.feed(data)
            except FramingError:
                return
            for request in requests:
                if not hmac.compare_digest(
                    str(request.get("session_token", "")),
                    self.session_token,
                ):
                    self._send(
                        client,
                        self._error_response(
                            request,
                            kind="authentication",
                            code="AUTHENTICATION_FAILED",
                            message="Session token was rejected",
                        ),
                    )
                    continue
                self.connected = True
                self.status = "connected"
                pending = PendingRequest(request=request, event=threading.Event())
                self._requests.put(pending)
                deadline_ms = request.get("deadline_ms", 5000)
                if not isinstance(deadline_ms, int):
                    deadline_ms = 5000
                deadline = time.monotonic() + min(max(deadline_ms, 100), 30_000) / 1000
                while not self._stop.is_set() and not pending.event.wait(0.05):
                    if time.monotonic() >= deadline and not pending.started:
                        pending.cancelled = True
                        break
                if pending.response is None:
                    pending.response = self._error_response(
                        request,
                        kind="timeout",
                        code="MAIN_THREAD_TIMEOUT",
                        message=(
                            "Blender main thread did not dispatch the command "
                            "before its deadline"
                        ),
                        retryable=True,
                    )
                if not self._send(client, pending.response):
                    return

    def _send(self, client: socket.socket, response: dict[str, Any]) -> bool:
        try:
            client.sendall(encode_frame(response, MAX_RESPONSE_BYTES))
        except (OSError, FramingError):
            return False
        return True

    def _error_response(
        self,
        request: dict[str, Any],
        *,
        kind: str,
        code: str,
        message: str,
        retryable: bool = False,
    ) -> dict[str, Any]:
        return {
            "protocol": PROTOCOL_VERSION,
            "request_id": request.get("request_id", ZERO_REQUEST_ID),
            "ok": False,
            "scene_generation": self.last_scene_generation,
            "error": {
                "kind": kind,
                "code": code,
                "message": message,
                "retryable": retryable,
                "details": {},
            },
        }

    def _write_manifest(self) -> None:
        target = self.manifest_path
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_suffix(f".{self.instance_id}.tmp")
        manifest = {
            "protocol": PROTOCOL_VERSION,
            "host": HOST,
            "port": self.port,
            "pid": os.getpid(),
            "instance_id": self.instance_id,
            "session_token": self.session_token,
            "addon_version": ADDON_VERSION,
            "created_at": datetime.now(UTC).isoformat(),
        }
        launch_id = os.environ.get(LAUNCH_ID_ENV)
        if launch_id:
            manifest["launch_id"] = launch_id
        temporary.write_text(json.dumps(manifest), encoding="utf-8")
        with contextlib.suppress(OSError):
            temporary.chmod(0o600)
        os.replace(temporary, target)

    def _remove_manifest(self) -> None:
        target = self.manifest_path
        try:
            manifest = json.loads(target.read_text(encoding="utf-8"))
            if manifest.get("instance_id") == self.instance_id:
                target.unlink(missing_ok=True)
        except (OSError, json.JSONDecodeError):
            pass
