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

from .authoring_ops import (
    AuthoringOperationError,
    create_object,
    duplicate_object,
    inspect_scene,
    object_summary,
    unlink_object,
)
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
from .lookdev_model import LookdevModelError, normalize_material_value
from .lookdev_ops import (
    inspect_material,
    inspect_object_lookdev,
    material_affected_objects,
    material_socket_is_driven,
    material_socket_kind,
    material_socket_range,
    material_socket_readonly,
    material_socket_value,
    read_property,
    require_modifier,
    require_object,
    require_shape_key,
    resolve_material_socket,
    restore_delta,
    session_identity,
    shape_key_is_driven,
)
from .project_ops import (
    ProjectOperationError,
    normalized_path,
    open_project,
    project_status,
    quit_application,
    save_project,
    transition_needs_save,
    validate_open_path,
    validate_save_path,
)
from .runtime import ADDON_VERSION, ListenerRuntime
from .structural_ops import (
    finalize_structural_delta,
    refresh_structure_guard_if_present,
    restore_structural_delta,
    validate_structural_transaction,
)
from .transaction_model import (
    IdempotencyCache,
    MaterialInputDelta,
    ModifierStateDelta,
    ObjectTransformDelta,
    ShapeKeyDelta,
    StructuralDelta,
    Transaction,
    TransactionBook,
    TransactionModelError,
    VisibilityDelta,
    context_fingerprint,
    request_fingerprint,
    values_equal,
)
from .wire import PROTOCOL_VERSION

CAPABILITIES = [
    "connection.ping",
    "context.get",
    "context.snapshot",
    "context.restore",
    "object.inspect",
    "scene.inspect",
    "object.geometry.inspect",
    "object.lookdev.inspect",
    "material.inspect",
    "viewport.capture",
    "viewport.raycast",
    "transaction.begin",
    "transaction.commit",
    "transaction.rollback",
    "object.transform",
    "object.create",
    "object.duplicate",
    "object.delete",
    "object.visibility.set",
    "modifier.set_state",
    "shape_key.set_value",
    "material.set_input",
    "project.status",
    "project.save",
    "project.open",
    "project.reload",
    "application.quit",
]
CAPABILITY_VERSIONS = {
    "transport": 1,
    "context": 1,
    "viewport_capture": 3,
    "viewport_raycast": 1,
    "geometry_inspection": 1,
    "lookdev_inspection": 1,
    "transactions": 3,
    "object_transform_scale": 1,
    "object_transform": 1,
    "scene_inspection": 1,
    "object_authoring": 1,
    "object_visibility": 1,
    "modifier_state": 1,
    "shape_key_value": 1,
    "material_input": 1,
    "project_lifecycle": 1,
    "application_lifecycle": 1,
}
MUTATION_COMMANDS = {
    "transaction.begin",
    "transaction.commit",
    "transaction.rollback",
    "object.transform",
    "object.create",
    "object.duplicate",
    "object.delete",
    "object.visibility.set",
    "modifier.set_state",
    "shape_key.set_value",
    "material.set_input",
    "project.save",
    "project.open",
    "project.reload",
    "application.quit",
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
        self.pending_lifecycle_operation: dict[str, Any] | None = None
        self.last_lifecycle_operation: dict[str, Any] | None = None

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
        self._perform_pending_lifecycle_operation()
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
        except ProjectOperationError as exc:
            self.last_error = f"{exc.code}: {exc}"
            return self._error(
                request_id,
                exc.kind,
                exc.code,
                str(exc),
                retryable=exc.retryable,
                details=exc.details,
            )
        except AuthoringOperationError as exc:
            self.last_error = f"{exc.code}: {exc}"
            return self._error(
                request_id,
                exc.kind,
                exc.code,
                str(exc),
                details=exc.details,
            )
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
        if command == "project.status":
            return self._project_status()
        if command == "project.save":
            return self._save_project(params)
        if command == "project.open":
            return self._open_project(params)
        if command == "project.reload":
            return self._reload_project(params)
        if command == "application.quit":
            return self._quit_application(params)
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
        if command == "scene.inspect":
            kinds = params.get("kinds")
            allowed_kinds = {
                "objects",
                "collections",
                "materials",
                "images",
                "world",
                "camera",
                "render",
            }
            if (
                not isinstance(kinds, list)
                or not kinds
                or len(kinds) > len(allowed_kinds)
                or any(not isinstance(kind, str) or kind not in allowed_kinds for kind in kinds)
                or len(set(kinds)) != len(kinds)
            ):
                raise AuthoringOperationError(
                    "SCENE_KINDS_INVALID",
                    "kinds must contain unique supported scene summary kinds",
                    kind="validation",
                )
            name_filter = params.get("name_filter")
            if name_filter is not None and (
                not isinstance(name_filter, str) or not name_filter or len(name_filter) > 255
            ):
                raise AuthoringOperationError(
                    "NAME_FILTER_INVALID",
                    "name_filter must be a non-empty string or null",
                    kind="validation",
                )
            limit = params.get("limit", 100)
            if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 256:
                raise AuthoringOperationError(
                    "SCENE_LIMIT_INVALID",
                    "limit must be an integer between 1 and 256",
                    kind="validation",
                )
            return inspect_scene(kinds, name_filter, limit)
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
        if command == "object.lookdev.inspect":
            object_name = params.get("object_name")
            if not isinstance(object_name, str) or not object_name:
                raise ContextOperationError(
                    "OBJECT_NAME_INVALID",
                    "object_name must be a non-empty string",
                    kind="validation",
                )
            with self.suppress_generation():
                return inspect_object_lookdev(object_name)
        if command == "material.inspect":
            object_name = params.get("object_name")
            material_slot_index = params.get("material_slot_index")
            if not isinstance(object_name, str) or not object_name:
                raise ContextOperationError(
                    "OBJECT_NAME_INVALID",
                    "object_name must be a non-empty string",
                    kind="validation",
                )
            if (
                isinstance(material_slot_index, bool)
                or not isinstance(material_slot_index, int)
                or not 0 <= material_slot_index < 64
            ):
                raise ContextOperationError(
                    "MATERIAL_SLOT_INDEX_INVALID",
                    "material_slot_index must be an integer between 0 and 63",
                    kind="validation",
                )
            with self.suppress_generation():
                return inspect_material(object_name, material_slot_index)
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
            return self._transform_object(transaction, params)
        if command == "object.create":
            transaction = self._require_transaction(params, request)
            self._validate_transaction_guards(transaction)
            definition = params.get("definition")
            if not isinstance(definition, dict):
                raise AuthoringOperationError(
                    "OBJECT_DEFINITION_INVALID",
                    "definition must be an object",
                    kind="validation",
                )
            with self.suppress_generation():
                obj, delta = create_object(transaction, definition)
                bpy.context.view_layer.update()
            self._record_delta(transaction, delta)
            return {
                "transaction_id": transaction.transaction_id,
                "object": object_summary(obj),
                "status": transaction.status,
                "delta_count": len(transaction.deltas),
                "delta_kinds": transaction.delta_kinds(),
            }
        if command == "object.duplicate":
            transaction = self._require_transaction(params, request)
            self._validate_transaction_guards(transaction)
            with self.suppress_generation():
                obj, delta = duplicate_object(
                    transaction,
                    source_name=str(params.get("source_name", "")),
                    expected_source_identity=self._required_identity(
                        params, "expected_source_identity"
                    ),
                    name=str(params.get("name", "")),
                    linked_data=params.get("linked_data") is True,
                    collection_name=params.get("collection_name"),
                    expected_collection_identity=params.get("expected_collection_identity"),
                    transform=params.get("transform"),
                )
                bpy.context.view_layer.update()
            self._record_delta(transaction, delta)
            return {
                "transaction_id": transaction.transaction_id,
                "object": object_summary(obj),
                "linked_data": params.get("linked_data") is True,
                "status": transaction.status,
                "delta_count": len(transaction.deltas),
                "delta_kinds": transaction.delta_kinds(),
            }
        if command == "object.delete":
            transaction = self._require_transaction(params, request)
            self._validate_transaction_guards(transaction)
            with self.suppress_generation():
                obj, delta = unlink_object(
                    transaction,
                    object_name=str(params.get("object_name", "")),
                    expected_object_identity=self._required_identity(
                        params, "expected_object_identity"
                    ),
                )
                before_commit = object_summary(obj)
                bpy.context.view_layer.update()
            self._record_delta(transaction, delta)
            return {
                "transaction_id": transaction.transaction_id,
                "object": before_commit,
                "status": "unlinked_pending_commit",
                "delta_count": len(transaction.deltas),
                "delta_kinds": transaction.delta_kinds(),
            }
        if command == "object.visibility.set":
            transaction = self._require_transaction(params, request)
            return self._set_object_visibility(transaction, params)
        if command == "modifier.set_state":
            transaction = self._require_transaction(params, request)
            return self._set_modifier_state(transaction, params)
        if command == "shape_key.set_value":
            transaction = self._require_transaction(params, request)
            return self._set_shape_key_value(transaction, params)
        if command == "material.set_input":
            transaction = self._require_transaction(params, request)
            return self._set_material_input(transaction, params)
        if command == "transaction.commit":
            transaction = self._require_transaction(params, request)
            self._validate_transaction_guards(transaction)
            result = self._transaction_result(transaction)
            finalized: list[dict[str, Any]] = []
            with self.suppress_generation():
                for delta in transaction.structural_deltas():
                    item = finalize_structural_delta(delta)
                    if item is not None:
                        finalized.append(item)
            if finalized:
                self.scene_generation += 1
            result["status"] = "committed"
            result["finalized"] = finalized
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

    def _project_status(self) -> dict[str, Any]:
        transaction = self.transactions.active
        return project_status(
            self.scene_generation,
            self._transaction_result(transaction) if transaction is not None else None,
            self.last_lifecycle_operation,
        )

    def project_summary(self) -> dict[str, Any]:
        return self._project_status()

    def _commit_active_transaction_for_lifecycle(self) -> dict[str, Any] | None:
        transaction = self.transactions.active
        if transaction is None:
            return None
        self._validate_transaction_guards(transaction)
        result = self._transaction_result(transaction)
        finalized: list[dict[str, Any]] = []
        with self.suppress_generation():
            for delta in transaction.structural_deltas():
                item = finalize_structural_delta(delta)
                if item is not None:
                    finalized.append(item)
        if finalized:
            self.scene_generation += 1
        result["status"] = "committed"
        result["finalized"] = finalized
        self.transactions.finish(transaction, "committed")
        return result

    @staticmethod
    def _optional_save_path(params: dict[str, Any], name: str) -> str | None:
        value = params.get(name)
        if value is None:
            return None
        return str(validate_save_path(value))

    @staticmethod
    def _boolean_param(params: dict[str, Any], name: str, default: bool) -> bool:
        value = params.get(name, default)
        if type(value) is not bool:
            raise ProjectOperationError(
                "PARAMS_INVALID",
                f"{name} must be a boolean",
                kind="validation",
            )
        return value

    def _save_project(self, params: dict[str, Any]) -> dict[str, Any]:
        path = self._optional_save_path(params, "path")
        before = self._project_status()
        transaction = self._commit_active_transaction_for_lifecycle()
        saved = save_project(path)
        self.scene_generation += 1
        operation = {
            "operation_id": str(uuid.uuid4()),
            "kind": "save",
            "status": "succeeded",
            "path": saved["path"],
        }
        self.last_lifecycle_operation = operation
        return {
            "status": "saved",
            "operation_id": operation["operation_id"],
            "before": before,
            "after": self._project_status(),
            "transaction": transaction,
            "save": saved,
        }

    def _prepare_current_for_transition(
        self,
        *,
        save_current: bool,
        save_current_as: str | None,
    ) -> tuple[dict[str, Any] | None, dict[str, Any]]:
        if not save_current:
            return None, {"status": "skipped", "reason": "save_current_false"}
        transaction = self._commit_active_transaction_for_lifecycle()
        current = self._project_status()
        if not transition_needs_save(current["is_dirty"], transaction):
            return transaction, {"status": "skipped", "reason": "clean"}
        if not current["filepath"] and save_current_as is None:
            raise ProjectOperationError(
                "CURRENT_PROJECT_UNTITLED",
                "The dirty current project is untitled; provide save_current_as",
            )
        saved = save_project(save_current_as)
        self.scene_generation += 1
        return transaction, saved

    def _schedule_lifecycle_operation(
        self,
        *,
        kind: str,
        path: str | None,
        use_scripts: bool | None = None,
        load_ui: bool | None = None,
    ) -> dict[str, Any]:
        if self.pending_lifecycle_operation is not None:
            raise ProjectOperationError(
                "LIFECYCLE_OPERATION_PENDING",
                "Another lifecycle operation is already pending",
                kind="conflict",
                retryable=True,
                details={"operation": self.pending_lifecycle_operation},
            )
        operation = {
            "operation_id": str(uuid.uuid4()),
            "kind": kind,
            "status": "accepted",
            "path": path,
            "use_scripts": use_scripts,
            "load_ui": load_ui,
        }
        self.pending_lifecycle_operation = operation
        self.last_lifecycle_operation = dict(operation)
        return operation

    def _open_project(self, params: dict[str, Any]) -> dict[str, Any]:
        target = validate_open_path(params.get("path"))
        save_current = self._boolean_param(params, "save_current", True)
        save_current_as = self._optional_save_path(params, "save_current_as")
        use_scripts = self._boolean_param(params, "use_scripts", True)
        load_ui = self._boolean_param(params, "load_ui", True)
        before = self._project_status()
        transaction, saved = self._prepare_current_for_transition(
            save_current=save_current,
            save_current_as=save_current_as,
        )
        current = str(bpy.data.filepath or "")
        if current and normalized_path(current) == normalized_path(str(target)):
            operation = {
                "operation_id": str(uuid.uuid4()),
                "kind": "open",
                "status": "already_open",
                "path": str(target),
            }
            self.last_lifecycle_operation = operation
            return {
                "status": "already_open",
                "operation_id": operation["operation_id"],
                "path": str(target),
                "before": before,
                "after": self._project_status(),
                "transaction": transaction,
                "save": saved,
            }
        operation = self._schedule_lifecycle_operation(
            kind="open",
            path=str(target),
            use_scripts=use_scripts,
            load_ui=load_ui,
        )
        return {
            "status": "accepted",
            "operation_id": operation["operation_id"],
            "path": str(target),
            "before": before,
            "transaction": transaction,
            "save": saved,
        }

    def _reload_project(self, params: dict[str, Any]) -> dict[str, Any]:
        current = str(bpy.data.filepath or "")
        if not current:
            raise ProjectOperationError(
                "PROJECT_RELOAD_UNAVAILABLE",
                "The current project is untitled and cannot be reloaded",
            )
        target = validate_open_path(current)
        save_current = self._boolean_param(params, "save_current", False)
        use_scripts = self._boolean_param(params, "use_scripts", True)
        load_ui = self._boolean_param(params, "load_ui", True)
        before = self._project_status()
        transaction, saved = self._prepare_current_for_transition(
            save_current=save_current,
            save_current_as=None,
        )
        operation = self._schedule_lifecycle_operation(
            kind="reload",
            path=str(target),
            use_scripts=use_scripts,
            load_ui=load_ui,
        )
        return {
            "status": "accepted",
            "operation_id": operation["operation_id"],
            "path": str(target),
            "before": before,
            "transaction": transaction,
            "save": saved,
        }

    def _quit_application(self, params: dict[str, Any]) -> dict[str, Any]:
        save_current = self._boolean_param(params, "save_current", True)
        save_current_as = self._optional_save_path(params, "save_current_as")
        before = self._project_status()
        transaction, saved = self._prepare_current_for_transition(
            save_current=save_current,
            save_current_as=save_current_as,
        )
        operation = self._schedule_lifecycle_operation(kind="quit", path=None)
        return {
            "status": "accepted",
            "operation_id": operation["operation_id"],
            "before": before,
            "transaction": transaction,
            "save": saved,
        }

    def _perform_pending_lifecycle_operation(self) -> None:
        operation = self.pending_lifecycle_operation
        if operation is None:
            return
        self.pending_lifecycle_operation = None
        running = dict(operation)
        running["status"] = "running"
        self.last_lifecycle_operation = running
        self.active_command = f"lifecycle:{operation['kind']}"
        try:
            kind = str(operation["kind"])
            if kind in {"open", "reload"}:
                open_project(
                    str(operation["path"]),
                    use_scripts=bool(operation["use_scripts"]),
                    load_ui=bool(operation["load_ui"]),
                )
            elif kind == "quit":
                quit_application()
            else:
                raise ProjectOperationError(
                    "BLENDER_COMMAND_FAILED",
                    f"Unknown pending lifecycle operation: {kind}",
                    kind="internal",
                )
            succeeded = dict(operation)
            succeeded["status"] = "succeeded"
            self.last_lifecycle_operation = succeeded
        except ProjectOperationError as exc:
            failed = dict(operation)
            failed.update(
                {
                    "status": "failed",
                    "error": {
                        "kind": exc.kind,
                        "code": exc.code,
                        "message": str(exc),
                        "retryable": exc.retryable,
                        "details": exc.details,
                    },
                }
            )
            self.last_lifecycle_operation = failed
            self.last_error = f"{exc.code}: {exc}"
        finally:
            self.active_command = ""

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
        for reference, expected in transaction.expected_properties().items():
            current = read_property(reference)
            if not values_equal(current, expected):
                transaction.status = "conflicted"
                self.transactions.last_status = "conflicted"
                raise ContextOperationError(
                    "PROPERTY_CONFLICT",
                    f"{reference.kind}.{reference.attribute} changed outside the transaction",
                    kind="conflict",
                    details={
                        "kind": reference.kind,
                        "target": list(reference.target),
                        "attribute": reference.attribute,
                        "expected": expected,
                        "actual": current,
                    },
                )
        validate_structural_transaction(transaction)

    def _transform_object(
        self,
        transaction: Transaction,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        self._validate_transaction_guards(transaction)
        transaction.ensure_capacity()
        object_name = params.get("object_name")
        if not isinstance(object_name, str) or not object_name:
            raise ContextOperationError(
                "OBJECT_NAME_INVALID",
                "object_name must be a non-empty string",
                kind="validation",
            )
        raw_patches = {
            "location": params.get("location"),
            "rotation_euler": params.get("rotation_euler_degrees"),
            "scale": params.get("scale"),
        }
        patches = {name: value for name, value in raw_patches.items() if value is not None}
        if not patches:
            raise ContextOperationError(
                "TRANSFORM_PATCH_INVALID",
                "location, rotation_euler_degrees, and/or scale is required",
                kind="validation",
            )
        if any(
            not isinstance(patch, dict)
            or not patch
            or set(patch) - set(AXIS_INDEX)
            for patch in patches.values()
        ):
            raise ContextOperationError(
                "TRANSFORM_PATCH_INVALID",
                "Each transform patch must contain one or more of x, y, and z",
                kind="validation",
            )
        object_identity = params.get("expected_object_identity")
        if "location" in patches or "rotation_euler" in patches:
            object_identity = self._required_identity(params, "expected_object_identity")
        if object_identity is not None:
            if not isinstance(object_identity, str) or not object_identity:
                raise ContextOperationError(
                    "TARGET_IDENTITY_REQUIRED",
                    "expected_object_identity must be a non-empty session identity",
                    kind="validation",
                )
            obj = require_object(object_name, object_identity)
        else:
            obj = bpy.data.objects.get(object_name)
            if obj is None:
                raise ContextOperationError(
                    "OBJECT_NOT_FOUND",
                    f"Object does not exist: {object_name}",
                    kind="not_found",
                )
        self._require_mutable_object(obj)
        before: dict[str, dict[str, float]] = {}
        after: dict[str, dict[str, float]] = {}
        for channel, patch in patches.items():
            before[channel] = {}
            after[channel] = {}
            target = getattr(obj, channel)
            for axis, raw_value in patch.items():
                if isinstance(raw_value, bool) or not isinstance(raw_value, (int, float)):
                    raise ContextOperationError(
                        "TRANSFORM_VALUE_INVALID",
                        f"Transform {channel}.{axis} must be a number",
                        kind="validation",
                    )
                value = float(raw_value)
                if not math.isfinite(value):
                    raise ContextOperationError(
                        "TRANSFORM_VALUE_INVALID",
                        f"Transform {channel}.{axis} must be finite",
                        kind="validation",
                    )
                if channel == "scale" and not 0.000001 <= value <= 1000.0:
                    raise ContextOperationError(
                        "SCALE_VALUE_INVALID",
                        f"Scale {axis} must be between 0.000001 and 1000",
                        kind="validation",
                    )
                if channel == "location" and abs(value) > 1_000_000:
                    raise ContextOperationError(
                        "LOCATION_VALUE_INVALID",
                        f"Location {axis} must be between -1000000 and 1000000",
                        kind="validation",
                    )
                if channel == "rotation_euler" and abs(value) > 360_000:
                    raise ContextOperationError(
                        "ROTATION_VALUE_INVALID",
                        f"Rotation {axis} must be between -360000 and 360000 degrees",
                        kind="validation",
                    )
                index = AXIS_INDEX[axis]
                before[channel][axis] = float(target[index])
                after[channel][axis] = math.radians(value) if channel == "rotation_euler" else value
        with self.suppress_generation():
            for channel, patch in after.items():
                target = getattr(obj, channel)
                for axis, value in patch.items():
                    target[AXIS_INDEX[axis]] = value
            bpy.context.view_layer.update()
            refresh_structure_guard_if_present(transaction, "object", obj)
        self._record_delta(
            transaction,
            ObjectTransformDelta(
                object_name=object_name,
                object_identity=session_identity("object", obj),
                before=before,
                after=after,
            ),
        )
        return {
            "transaction_id": transaction.transaction_id,
            "object_name": object_name,
            "object_identity": session_identity("object", obj),
            "changed": {channel: sorted(values) for channel, values in after.items()},
            "before": before,
            "after": after,
            "location": list(obj.location),
            "rotation_euler_degrees": [
                math.degrees(float(value)) for value in obj.rotation_euler
            ],
            "scale": list(obj.scale),
            "status": transaction.status,
            "delta_count": len(transaction.deltas),
            "delta_kinds": transaction.delta_kinds(),
        }

    @staticmethod
    def _required_identity(params: dict[str, Any], name: str) -> str:
        value = params.get(name)
        if not isinstance(value, str) or not value:
            raise ContextOperationError(
                "TARGET_IDENTITY_REQUIRED",
                f"{name} must be a non-empty session identity",
                kind="validation",
            )
        return value

    @staticmethod
    def _require_mutable_object(obj: Any) -> None:
        if obj.library is not None and obj.override_library is None:
            raise ContextOperationError(
                "OBJECT_LINKED",
                f"Linked object cannot be modified: {obj.name}",
            )

    def _record_delta(self, transaction: Transaction, delta: Any) -> None:
        transaction.record(delta)
        self.scene_generation += 1
        transaction.status = "active"
        transaction.context_fingerprint = self._current_context_fingerprint(transaction)

    def _set_object_visibility(
        self,
        transaction: Transaction,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        self._validate_transaction_guards(transaction)
        object_name = params.get("object_name")
        if not isinstance(object_name, str) or not object_name:
            raise ContextOperationError(
                "OBJECT_NAME_INVALID",
                "object_name must be a non-empty string",
                kind="validation",
            )
        object_identity = self._required_identity(params, "expected_object_identity")
        patch = params.get("visibility")
        allowed = {"hide_viewport", "hide_render"}
        if not isinstance(patch, dict) or not patch or set(patch) - allowed:
            raise ContextOperationError(
                "WRITE_PATCH_EMPTY",
                "visibility must set hide_viewport and/or hide_render",
                kind="validation",
            )
        if any(type(value) is not bool for value in patch.values()):
            raise ContextOperationError(
                "VISIBILITY_VALUE_INVALID",
                "visibility values must be booleans",
                kind="validation",
            )
        obj = require_object(object_name, object_identity)
        self._require_mutable_object(obj)
        if patch.get("hide_viewport") is True and (
            obj.select_get() or bpy.context.view_layer.objects.active == obj
        ):
            raise ContextOperationError(
                "VISIBILITY_CONTEXT_CONFLICT",
                "Cannot hide the active or selected object without changing user context",
                kind="precondition",
            )
        before = {attribute: bool(getattr(obj, attribute)) for attribute in patch}
        after = {attribute: bool(value) for attribute, value in patch.items()}
        changed = sorted(
            attribute for attribute in after if before[attribute] != after[attribute]
        )
        if changed:
            with self.suppress_generation():
                for attribute in changed:
                    setattr(obj, attribute, after[attribute])
                bpy.context.view_layer.update()
            self._record_delta(
                transaction,
                VisibilityDelta(
                    object_name=object_name,
                    object_identity=object_identity,
                    before={attribute: before[attribute] for attribute in changed},
                    after={attribute: after[attribute] for attribute in changed},
                ),
            )
        return {
            "transaction_id": transaction.transaction_id,
            "object_name": object_name,
            "object_identity": object_identity,
            "changed_fields": changed,
            "before": before,
            "after": after,
            "visibility": {
                "hide_viewport": bool(obj.hide_viewport),
                "hide_render": bool(obj.hide_render),
            },
            "status": transaction.status,
            "delta_count": len(transaction.deltas),
            "delta_kinds": transaction.delta_kinds(),
        }

    def _set_modifier_state(
        self,
        transaction: Transaction,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        self._validate_transaction_guards(transaction)
        object_name = params.get("object_name")
        modifier_name = params.get("modifier_name")
        if not isinstance(object_name, str) or not object_name:
            raise ContextOperationError(
                "OBJECT_NAME_INVALID",
                "object_name must be a non-empty string",
                kind="validation",
            )
        if not isinstance(modifier_name, str) or not modifier_name:
            raise ContextOperationError(
                "MODIFIER_NAME_INVALID",
                "modifier_name must be a non-empty string",
                kind="validation",
            )
        object_identity = self._required_identity(params, "expected_object_identity")
        modifier_identity = self._required_identity(params, "expected_modifier_identity")
        patch = params.get("state")
        allowed = {"show_viewport", "show_render"}
        if not isinstance(patch, dict) or not patch or set(patch) - allowed:
            raise ContextOperationError(
                "WRITE_PATCH_EMPTY",
                "state must set show_viewport and/or show_render",
                kind="validation",
            )
        if any(type(value) is not bool for value in patch.values()):
            raise ContextOperationError(
                "MODIFIER_STATE_INVALID",
                "modifier state values must be booleans",
                kind="validation",
            )
        obj, modifier = require_modifier(
            object_name,
            object_identity,
            modifier_name,
            modifier_identity,
        )
        self._require_mutable_object(obj)
        before = {attribute: bool(getattr(modifier, attribute)) for attribute in patch}
        after = {attribute: bool(value) for attribute, value in patch.items()}
        changed = sorted(
            attribute for attribute in after if before[attribute] != after[attribute]
        )
        if changed:
            with self.suppress_generation():
                for attribute in changed:
                    setattr(modifier, attribute, after[attribute])
                bpy.context.view_layer.update()
            self._record_delta(
                transaction,
                ModifierStateDelta(
                    object_name=object_name,
                    object_identity=object_identity,
                    modifier_name=modifier_name,
                    modifier_identity=modifier_identity,
                    before={attribute: before[attribute] for attribute in changed},
                    after={attribute: after[attribute] for attribute in changed},
                ),
            )
        return {
            "transaction_id": transaction.transaction_id,
            "object_name": object_name,
            "object_identity": object_identity,
            "modifier_name": modifier_name,
            "modifier_identity": modifier_identity,
            "changed_fields": changed,
            "before": before,
            "after": after,
            "state": {
                "show_viewport": bool(modifier.show_viewport),
                "show_render": bool(modifier.show_render),
            },
            "status": transaction.status,
            "delta_count": len(transaction.deltas),
            "delta_kinds": transaction.delta_kinds(),
        }

    def _set_shape_key_value(
        self,
        transaction: Transaction,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        self._validate_transaction_guards(transaction)
        object_name = params.get("object_name")
        shape_key_name = params.get("shape_key_name")
        raw_value = params.get("value")
        if not isinstance(object_name, str) or not object_name:
            raise ContextOperationError(
                "OBJECT_NAME_INVALID",
                "object_name must be a non-empty string",
                kind="validation",
            )
        if not isinstance(shape_key_name, str) or not shape_key_name:
            raise ContextOperationError(
                "SHAPE_KEY_NAME_INVALID",
                "shape_key_name must be a non-empty string",
                kind="validation",
            )
        if isinstance(raw_value, bool) or not isinstance(raw_value, (int, float)):
            raise ContextOperationError(
                "SHAPE_KEY_VALUE_INVALID",
                "shape key value must be a finite number",
                kind="validation",
            )
        value = float(raw_value)
        if not math.isfinite(value):
            raise ContextOperationError(
                "SHAPE_KEY_VALUE_INVALID",
                "shape key value must be a finite number",
                kind="validation",
            )
        object_identity = self._required_identity(params, "expected_object_identity")
        shape_key_identity = self._required_identity(params, "expected_shape_key_identity")
        obj, key_block = require_shape_key(
            object_name,
            object_identity,
            shape_key_name,
            shape_key_identity,
        )
        self._require_mutable_object(obj)
        if obj.type != "MESH":
            raise ContextOperationError(
                "OBJECT_LOOKDEV_UNSUPPORTED",
                f"Shape key writes only support MESH objects: {object_name}",
            )
        shape_keys = obj.data.shape_keys
        if key_block == shape_keys.reference_key:
            raise ContextOperationError(
                "SHAPE_KEY_BASIS_FORBIDDEN",
                "The Basis shape key cannot be assigned a preview value",
            )
        if shape_keys.library is not None or obj.data.library is not None:
            raise ContextOperationError(
                "OBJECT_LINKED",
                f"Linked shape key data cannot be modified: {object_name}",
            )
        if shape_key_is_driven(shape_keys, key_block):
            raise ContextOperationError(
                "SHAPE_KEY_DRIVEN",
                f"Shape key is controlled by a driver: {object_name}.{shape_key_name}",
            )
        minimum = float(key_block.slider_min)
        maximum = float(key_block.slider_max)
        if not minimum <= value <= maximum:
            raise ContextOperationError(
                "SHAPE_KEY_VALUE_OUT_OF_RANGE",
                f"Shape key value must be between {minimum} and {maximum}",
                kind="validation",
                details={"minimum": minimum, "maximum": maximum, "value": value},
            )
        before = float(key_block.value)
        changed = not values_equal(before, value)
        if changed:
            with self.suppress_generation():
                key_block.value = value
                bpy.context.view_layer.update()
            self._record_delta(
                transaction,
                ShapeKeyDelta(
                    object_name=object_name,
                    object_identity=object_identity,
                    shape_key_name=shape_key_name,
                    shape_key_identity=shape_key_identity,
                    before=before,
                    after=value,
                ),
            )
        return {
            "transaction_id": transaction.transaction_id,
            "object_name": object_name,
            "object_identity": object_identity,
            "shape_key_name": shape_key_name,
            "shape_key_identity": shape_key_identity,
            "changed": changed,
            "before": before,
            "after": value,
            "value": float(key_block.value),
            "slider_min": minimum,
            "slider_max": maximum,
            "status": transaction.status,
            "delta_count": len(transaction.deltas),
            "delta_kinds": transaction.delta_kinds(),
        }

    def _set_material_input(
        self,
        transaction: Transaction,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        self._validate_transaction_guards(transaction)
        object_name = params.get("object_name")
        material_slot_index = params.get("material_slot_index")
        material_name = params.get("material_name")
        node_name = params.get("node_name")
        socket_identifier = params.get("socket_identifier")
        for parameter_name, value in {
            "object_name": object_name,
            "material_name": material_name,
            "node_name": node_name,
            "socket_identifier": socket_identifier,
        }.items():
            if not isinstance(value, str) or not value:
                raise ContextOperationError(
                    "MATERIAL_TARGET_INVALID",
                    f"{parameter_name} must be a non-empty string",
                    kind="validation",
                )
        if (
            isinstance(material_slot_index, bool)
            or not isinstance(material_slot_index, int)
            or not 0 <= material_slot_index < 64
        ):
            raise ContextOperationError(
                "MATERIAL_SLOT_INDEX_INVALID",
                "material_slot_index must be an integer between 0 and 63",
                kind="validation",
            )
        expected_material_users = params.get("expected_material_users")
        if (
            isinstance(expected_material_users, bool)
            or not isinstance(expected_material_users, int)
            or expected_material_users < 1
        ):
            raise ContextOperationError(
                "MATERIAL_USERS_INVALID",
                "expected_material_users must be a positive integer",
                kind="validation",
            )
        allow_shared = params.get("allow_shared", False)
        if type(allow_shared) is not bool:
            raise ContextOperationError(
                "MATERIAL_SHARED_FLAG_INVALID",
                "allow_shared must be a boolean",
                kind="validation",
            )
        object_identity = self._required_identity(params, "expected_object_identity")
        material_identity = self._required_identity(params, "expected_material_identity")
        node_identity = self._required_identity(params, "expected_node_identity")
        socket_identity = self._required_identity(params, "expected_socket_identity")
        obj, material, node, socket = resolve_material_socket(
            object_name,
            object_identity,
            str(material_slot_index),
            material_name,
            material_identity,
            node_name,
            node_identity,
            socket_identifier,
            socket_identity,
        )
        node_tree = material.node_tree
        if material.library is not None or node_tree is None or node_tree.library is not None:
            raise ContextOperationError(
                "MATERIAL_LINKED",
                f"Linked or unavailable material node data cannot be modified: {material_name}",
                kind="precondition",
            )
        actual_users = int(material.users)
        if actual_users != expected_material_users:
            raise ContextOperationError(
                "MATERIAL_USERS_CONFLICT",
                "Material user count changed after it was inspected",
                kind="conflict",
                details={"expected": expected_material_users, "actual": actual_users},
            )
        affected_objects = material_affected_objects(material)
        if actual_users > 1 and not allow_shared:
            raise ContextOperationError(
                "SHARED_MATERIAL_CONFIRMATION_REQUIRED",
                "Shared material writes require allow_shared=true and an exact user count",
                kind="precondition",
                details={
                    "material_users": actual_users,
                    "affected_objects": affected_objects,
                },
            )
        socket_kind = material_socket_kind(socket)
        if socket_kind is None or not hasattr(socket, "default_value"):
            raise ContextOperationError(
                "MATERIAL_SOCKET_UNSUPPORTED",
                f"Unsupported material input socket: {node_name}.{socket_identifier}",
                kind="precondition",
            )
        if socket.is_linked:
            raise ContextOperationError(
                "MATERIAL_SOCKET_LINKED",
                f"Linked material input cannot be assigned: {node_name}.{socket_identifier}",
                kind="precondition",
            )
        if material_socket_is_driven(node_tree, socket):
            raise ContextOperationError(
                "MATERIAL_SOCKET_DRIVEN",
                f"Driven material input cannot be assigned: {node_name}.{socket_identifier}",
                kind="precondition",
            )
        if material_socket_readonly(socket):
            raise ContextOperationError(
                "MATERIAL_SOCKET_READONLY",
                f"Read-only material input cannot be assigned: {node_name}.{socket_identifier}",
                kind="precondition",
            )
        minimum, maximum = material_socket_range(socket, socket_kind)
        try:
            after = normalize_material_value(
                socket_kind,
                params.get("value"),
                minimum=minimum,
                maximum=maximum,
            )
        except LookdevModelError as exc:
            raise ContextOperationError(
                exc.code,
                str(exc),
                kind="validation",
                details=exc.details,
            ) from exc
        before = material_socket_value(socket, socket_kind)
        changed = not values_equal(before, after)
        if changed:
            with self.suppress_generation():
                socket.default_value = after
                node_tree.update_tag()
                bpy.context.view_layer.update()
            self._record_delta(
                transaction,
                MaterialInputDelta(
                    object_name=object_name,
                    object_identity=object_identity,
                    material_slot_index=material_slot_index,
                    material_name=material_name,
                    material_identity=material_identity,
                    node_name=node_name,
                    node_identity=node_identity,
                    socket_identifier=socket_identifier,
                    socket_identity=socket_identity,
                    socket_kind=socket_kind,
                    before=before,
                    after=after,
                ),
            )
        return {
            "transaction_id": transaction.transaction_id,
            "object_name": obj.name,
            "object_identity": object_identity,
            "material_slot_index": material_slot_index,
            "material_name": material.name,
            "material_identity": material_identity,
            "material_users": actual_users,
            "shared_material": actual_users > 1,
            "affected_objects": affected_objects,
            "node_name": node.name,
            "node_identity": node_identity,
            "socket_identifier": socket.identifier,
            "socket_identity": socket_identity,
            "socket_kind": socket_kind,
            "changed": changed,
            "before": before,
            "after": after,
            "value": material_socket_value(socket, socket_kind),
            "minimum": minimum,
            "maximum": maximum,
            "status": transaction.status,
            "delta_count": len(transaction.deltas),
            "delta_kinds": transaction.delta_kinds(),
        }

    def _rollback_transaction(self, transaction: Transaction) -> dict[str, Any]:
        self._validate_transaction_guards(transaction)
        validate_context_snapshot(transaction.context_snapshot)
        restored: list[dict[str, Any]] = []
        with self.suppress_generation():
            for delta in reversed(transaction.deltas):
                if isinstance(delta, StructuralDelta):
                    restored.append(restore_structural_delta(delta))
                else:
                    restored.append(restore_delta(delta))
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
            "delta_kinds": transaction.delta_kinds(),
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
