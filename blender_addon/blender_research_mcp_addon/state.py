"""Main-thread state and semantic command dispatch."""

from __future__ import annotations

import contextlib
import math
import time
import uuid
from collections import OrderedDict
from collections.abc import Iterator
from typing import Any

import bpy

from .capture_model import CaptureBook, CaptureEvidence
from .context_ops import (
    ContextOperationError,
    capture_context,
    capture_viewport,
    context_summary,
    inspect_geometry,
    inspect_object,
    raycast_capture,
    restore_context,
    validate_context_snapshot,
)
from .generation import has_persistent_scene_update
from .runtime import ADDON_VERSION, ListenerRuntime
from .transaction_model import (
    IdempotencyCache,
    ScaleDelta,
    Transaction,
    TransactionBook,
    TransactionModelError,
    context_fingerprint,
    request_fingerprint,
)
from .wire import PROTOCOL_VERSION

CAPABILITIES = [
    "connection.ping",
    "context.get",
    "context.snapshot",
    "context.restore",
    "object.inspect",
    "object.geometry.inspect",
    "viewport.capture",
    "viewport.raycast",
    "transaction.begin",
    "transaction.commit",
    "transaction.rollback",
    "object.transform",
]
CAPABILITY_VERSIONS = {
    "transport": 1,
    "context": 1,
    "viewport_capture": 3,
    "viewport_raycast": 1,
    "geometry_inspection": 1,
    "transactions": 1,
    "object_transform_scale": 1,
}
MUTATION_COMMANDS = {
    "transaction.begin",
    "transaction.commit",
    "transaction.rollback",
    "object.transform",
}
AXIS_INDEX = {"x": 0, "y": 1, "z": 2}


class AddonState:
    def __init__(self) -> None:
        self.runtime = ListenerRuntime()
        self.scene_generation = 0
        self.heartbeat = 0
        self.active_command = ""
        self.last_command_ms = 0.0
        self.last_error = ""
        self.last_capture_backend = "gpu_offscreen"
        self.snapshots: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self.captures = CaptureBook(limit=32)
        self.transactions = TransactionBook()
        self.idempotency = IdempotencyCache()
        self._suppress_generation = 0
        self._disconnect_rollback_deadline: float | None = None

    def start(self) -> None:
        self.runtime.start()

    def stop(self) -> None:
        try:
            if self.transactions.active is not None:
                self._rollback_transaction(self.transactions.active)
        except Exception as exc:  # noqa: BLE001 - shutdown must still close sockets
            self.last_error = f"ROLLBACK_FAILED: {type(exc).__name__}: {exc}"
        finally:
            self.runtime.stop()
            self.snapshots.clear()
            self.captures.clear()

    def restart(self) -> None:
        self.stop()
        self.start()

    def tick(self) -> None:
        self.heartbeat += 1
        self.runtime.poll(self.dispatch, self.on_disconnect)
        if self._disconnect_rollback_deadline is not None:
            if self.runtime.connected:
                self._disconnect_rollback_deadline = None
            elif time.monotonic() >= self._disconnect_rollback_deadline:
                self._disconnect_rollback_deadline = None
                transaction = self.transactions.active
                if transaction is not None:
                    try:
                        transaction_id = transaction.transaction_id
                        self._rollback_transaction(transaction)
                        self.idempotency.remove_transaction(transaction_id)
                        self.transactions.last_status = "rolled_back_disconnect"
                    except Exception as exc:  # noqa: BLE001 - timer must remain registered
                        transaction.status = "conflicted"
                        self.transactions.last_status = "conflicted"
                        code = getattr(exc, "code", type(exc).__name__)
                        self.last_error = f"{code}: {exc}"

    def on_disconnect(self) -> None:
        if self.transactions.active is not None:
            self._disconnect_rollback_deadline = time.monotonic() + 2.0

    def on_file_loaded(self) -> None:
        self.snapshots.clear()
        self.captures.clear()
        if self.transactions.active is not None:
            self.transactions.abandon("abandoned_file_load")
            self.last_error = "TRANSACTION_ABANDONED: a different blend file was loaded"
        self.idempotency = IdempotencyCache()
        self._disconnect_rollback_deadline = None
        self.scene_generation += 1

    def on_depsgraph_update(self, depsgraph: Any) -> None:
        if self._suppress_generation == 0 and has_persistent_scene_update(depsgraph):
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
        command = str(request.get("command", ""))
        self.active_command = command
        idempotency_key: str | None = None
        fingerprint: str | None = None
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
            if command in MUTATION_COMMANDS:
                idempotency_key = request.get("idempotency_key")
                if not isinstance(idempotency_key, str) or not idempotency_key:
                    return self._error(
                        request_id,
                        "validation",
                        "IDEMPOTENCY_KEY_REQUIRED",
                        "Mutation commands require an idempotency_key",
                    )
                fingerprint = request_fingerprint(request)
                cached = self.idempotency.lookup(idempotency_key, fingerprint)
                if cached is not None:
                    cached["request_id"] = request_id
                    return cached
            result = self._handle(command, params, request)
            response = {
                "protocol": PROTOCOL_VERSION,
                "request_id": request_id,
                "ok": True,
                "scene_generation": self.scene_generation,
                "result": result,
            }
            if idempotency_key is not None and fingerprint is not None:
                self.idempotency.store(idempotency_key, fingerprint, response)
            return response
        except ContextOperationError as exc:
            self.last_error = f"{exc.code}: {exc}"
            return self._error(
                request_id,
                exc.kind,
                exc.code,
                str(exc),
                retryable=exc.retryable,
                details=exc.details,
            )
        except TransactionModelError as exc:
            self.last_error = f"{exc.code}: {exc}"
            kind = "conflict" if exc.code != "TRANSACTION_NOT_FOUND" else "not_found"
            return self._error(request_id, kind, exc.code, str(exc))
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

    def _handle(
        self,
        command: str,
        params: dict[str, Any],
        request: dict[str, Any],
    ) -> dict[str, Any]:
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
                "capability_versions": CAPABILITY_VERSIONS,
            }
        if command == "connection.ping":
            return {
                "protocol": PROTOCOL_VERSION,
                "instance_id": self.runtime.instance_id,
                "blender_version": bpy.app.version_string,
                "addon_version": ADDON_VERSION,
                "capabilities": CAPABILITIES,
                "capability_versions": CAPABILITY_VERSIONS,
                "capture_backend": self.last_capture_backend,
                "capture_focus_requirement": "none_when_window_exists",
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
        if command == "object.geometry.inspect":
            object_name = params.get("object_name")
            if not isinstance(object_name, str) or not object_name:
                raise ContextOperationError(
                    "OBJECT_NAME_INVALID",
                    "object_name must be a non-empty string",
                    kind="validation",
                )
            with self.suppress_generation():
                return inspect_geometry(object_name)
        if command == "viewport.capture":
            object_name = params.get("object_name")
            if not isinstance(object_name, str) or not object_name:
                raise ContextOperationError(
                    "OBJECT_NAME_INVALID",
                    "object_name must be a non-empty string",
                    kind="validation",
                )
            with self.suppress_generation():
                result, evidence_data = capture_viewport(
                    object_name,
                    str(params.get("view", "CURRENT")),
                    int(params.get("max_size", 800)),
                    params.get("viewport_id"),
                    str(params.get("display_mode", "CURRENT")),
                    str(params.get("overlays", "CURRENT")),
                    params.get("orbit"),
                )
            capture_id = str(uuid.uuid4())
            evidence = CaptureEvidence(
                capture_id=capture_id,
                scene_generation=self.scene_generation,
                **evidence_data,
            )
            self.captures.add(evidence)
            result["capture_id"] = capture_id
            result["capture_scene_generation"] = self.scene_generation
            self.last_capture_backend = str(result["backend"])
            return result
        if command == "viewport.raycast":
            capture_id = params.get("capture_id")
            if not isinstance(capture_id, str) or not capture_id:
                raise ContextOperationError(
                    "CAPTURE_ID_INVALID",
                    "capture_id must be a non-empty string",
                    kind="validation",
                )
            evidence = self.captures.get(capture_id)
            if evidence is None:
                raise ContextOperationError(
                    "CAPTURE_NOT_FOUND",
                    f"Capture evidence does not exist: {capture_id}",
                    kind="not_found",
                )
            if evidence.scene_generation != self.scene_generation:
                raise ContextOperationError(
                    "CAPTURE_STALE",
                    "The Blender scene changed after the capture was created",
                    kind="conflict",
                    retryable=True,
                    details={
                        "capture_scene_generation": evidence.scene_generation,
                        "current_scene_generation": self.scene_generation,
                    },
                )
            x = params.get("x")
            y = params.get("y")
            if (
                isinstance(x, bool)
                or not isinstance(x, (int, float))
                or isinstance(y, bool)
                or not isinstance(y, (int, float))
                or not 0.0 <= float(x) <= 1.0
                or not 0.0 <= float(y) <= 1.0
            ):
                raise ContextOperationError(
                    "RAYCAST_COORDINATE_INVALID",
                    "x and y must be finite normalized coordinates between 0 and 1",
                    kind="validation",
                )
            with self.suppress_generation():
                result = raycast_capture(evidence, float(x), float(y))
            result["scene_generation"] = self.scene_generation
            return result
        if command == "transaction.begin":
            self._require_scene_generation(request)
            with self.suppress_generation():
                snapshot = capture_context(params.get("viewport_id"))
            label = params.get("label")
            if label is not None and not isinstance(label, str):
                raise ContextOperationError(
                    "LABEL_INVALID",
                    "label must be a string or null",
                    kind="validation",
                )
            if isinstance(label, str) and len(label) > 200:
                raise ContextOperationError(
                    "LABEL_INVALID",
                    "label must not exceed 200 characters",
                    kind="validation",
                )
            transaction = self.transactions.begin(
                label=label,
                context_snapshot=snapshot,
                context_fingerprint=context_fingerprint(snapshot),
                scene_generation=self.scene_generation,
            )
            return self._transaction_result(transaction)
        if command == "object.transform":
            transaction = self._require_transaction(params, request)
            return self._transform_scale(transaction, params)
        if command == "transaction.commit":
            transaction = self._require_transaction(params, request)
            self._validate_transaction_guards(transaction)
            result = self._transaction_result(transaction)
            result["status"] = "committed"
            self.transactions.finish(transaction, "committed")
            return result
        if command == "transaction.rollback":
            transaction = self._require_transaction(params, request)
            return self._rollback_transaction(transaction)
        raise ContextOperationError(
            "COMMAND_NOT_FOUND",
            f"Unsupported command: {command}",
            kind="not_found",
        )

    def _require_scene_generation(self, request: dict[str, Any]) -> None:
        expected = request.get("expected_scene_generation")
        if not isinstance(expected, int) or expected < 0:
            raise ContextOperationError(
                "SCENE_GENERATION_REQUIRED",
                "Mutation commands require expected_scene_generation",
                kind="validation",
            )
        if expected != self.scene_generation:
            raise ContextOperationError(
                "STALE_SCENE",
                f"Expected scene generation {expected}, current is {self.scene_generation}",
                kind="conflict",
            )

    def _require_transaction(
        self,
        params: dict[str, Any],
        request: dict[str, Any],
    ) -> Transaction:
        self._require_scene_generation(request)
        transaction_id = params.get("transaction_id")
        if not isinstance(transaction_id, str) or not transaction_id:
            raise ContextOperationError(
                "TRANSACTION_ID_INVALID",
                "transaction_id must be a non-empty string",
                kind="validation",
            )
        return self.transactions.require(transaction_id)

    def _current_context_fingerprint(self, transaction: Transaction) -> str:
        with self.suppress_generation():
            current = capture_context(transaction.context_snapshot["viewport_id"])
        return context_fingerprint(current)

    def _validate_transaction_guards(self, transaction: Transaction) -> None:
        if self._current_context_fingerprint(transaction) != transaction.context_fingerprint:
            transaction.status = "conflicted"
            self.transactions.last_status = "conflicted"
            raise ContextOperationError(
                "CONTEXT_CONFLICT",
                "User context changed while the transaction was active",
                kind="conflict",
            )
        for (object_name, axis), expected in transaction.expected_scale().items():
            obj = bpy.data.objects.get(object_name)
            if obj is None:
                raise ContextOperationError(
                    "OBJECT_NOT_FOUND",
                    f"Transaction object no longer exists: {object_name}",
                    kind="conflict",
                )
            current = float(obj.scale[AXIS_INDEX[axis]])
            if not math.isclose(current, expected, rel_tol=0.0, abs_tol=1e-7):
                transaction.status = "conflicted"
                self.transactions.last_status = "conflicted"
                raise ContextOperationError(
                    "PROPERTY_CONFLICT",
                    f"Scale {object_name}.{axis} changed outside the transaction",
                    kind="conflict",
                )

    def _transform_scale(
        self,
        transaction: Transaction,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        self._validate_transaction_guards(transaction)
        object_name = params.get("object_name")
        scale = params.get("scale")
        if not isinstance(object_name, str) or not object_name:
            raise ContextOperationError(
                "OBJECT_NAME_INVALID",
                "object_name must be a non-empty string",
                kind="validation",
            )
        if not isinstance(scale, dict) or not scale or set(scale) - set(AXIS_INDEX):
            raise ContextOperationError(
                "SCALE_PATCH_INVALID",
                "scale must contain one or more of x, y, and z",
                kind="validation",
            )
        obj = bpy.data.objects.get(object_name)
        if obj is None:
            raise ContextOperationError(
                "OBJECT_NOT_FOUND",
                f"Object does not exist: {object_name}",
                kind="not_found",
            )
        if obj.library is not None and obj.override_library is None:
            raise ContextOperationError(
                "OBJECT_LINKED",
                f"Linked object cannot be transformed: {object_name}",
            )
        before: dict[str, float] = {}
        after: dict[str, float] = {}
        for axis, raw_value in scale.items():
            if isinstance(raw_value, bool) or not isinstance(raw_value, (int, float)):
                raise ContextOperationError(
                    "SCALE_VALUE_INVALID",
                    f"Scale {axis} must be a number",
                    kind="validation",
                )
            value = float(raw_value)
            if not math.isfinite(value) or not 0.000001 <= value <= 1000.0:
                raise ContextOperationError(
                    "SCALE_VALUE_INVALID",
                    f"Scale {axis} must be finite and between 0.000001 and 1000",
                    kind="validation",
                )
            index = AXIS_INDEX[axis]
            before[axis] = float(obj.scale[index])
            after[axis] = value
        with self.suppress_generation():
            for axis, value in after.items():
                obj.scale[AXIS_INDEX[axis]] = value
            bpy.context.view_layer.update()
        self.scene_generation += 1
        transaction.deltas.append(
            ScaleDelta(object_name=object_name, before=before, after=after)
        )
        transaction.status = "active"
        transaction.context_fingerprint = self._current_context_fingerprint(transaction)
        return {
            "transaction_id": transaction.transaction_id,
            "object_name": object_name,
            "changed_axes": sorted(after),
            "before": before,
            "after": after,
            "scale": list(obj.scale),
            "status": transaction.status,
        }

    def _rollback_transaction(self, transaction: Transaction) -> dict[str, Any]:
        self._validate_transaction_guards(transaction)
        validate_context_snapshot(transaction.context_snapshot)
        restored: list[dict[str, Any]] = []
        with self.suppress_generation():
            for delta in reversed(transaction.deltas):
                obj = bpy.data.objects.get(delta.object_name)
                if obj is None:
                    raise ContextOperationError(
                        "OBJECT_NOT_FOUND",
                        f"Transaction object no longer exists: {delta.object_name}",
                        kind="conflict",
                    )
                for axis, value in delta.before.items():
                    obj.scale[AXIS_INDEX[axis]] = value
                restored.append(
                    {"object_name": delta.object_name, "scale": delta.before.copy()}
                )
            bpy.context.view_layer.update()
            restore_context(transaction.context_snapshot)
        if transaction.deltas:
            self.scene_generation += 1
        result = {
            "transaction_id": transaction.transaction_id,
            "status": "rolled_back",
            "restored": restored,
            "context_restored": True,
        }
        self.transactions.finish(transaction, "rolled_back")
        return result

    def _transaction_result(self, transaction: Transaction) -> dict[str, Any]:
        return {
            "transaction_id": transaction.transaction_id,
            "label": transaction.label,
            "status": transaction.status,
            "started_generation": transaction.started_generation,
            "delta_count": len(transaction.deltas),
        }

    def _error(
        self,
        request_id: Any,
        kind: str,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
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
                "retryable": retryable,
                "details": details or {},
            },
        }
